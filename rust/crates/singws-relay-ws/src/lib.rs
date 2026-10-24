//! Runs the relay lifecycle ([`singws_relay::Worker`]) over a real WebSocket: connect, read text frames, classify them like the Python workers do,
//! reconnect after the single-shot timer, stop on request, and (host channel) send compact JSON.
//!
//! All decisions come from the pure crate that is proven against the app's real worker classes; this file only performs the I/O the commands ask for.
//! Qt reports a failed socket as an error signal followed by a disconnect, so an I/O error here feeds both events in that order.

use std::net::{TcpStream, ToSocketAddrs};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::Value;
use singws_relay::{classify_host_message, classify_request_message, Command, Event, Kind, RequestRelayAction, Worker, RECONNECT_DELAY_MS};
use singws_store::{dumps, Style};
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{Message, WebSocket};

type Socket = WebSocket<MaybeTlsStream<TcpStream>>;

/// What the owner of the client is told (the Python workers' Qt signals).
#[derive(Debug, Clone, PartialEq)]
pub enum Output {
    Status(bool, String),
    RequestsAvailable(String),
    History(String),
    Command(Value),
    /// diagnostic text (the Python workers `print` these)
    Log(String),
}

#[derive(Debug, Clone)]
pub struct Config {
    pub kind: Kind,
    pub url: String,
    /// the reconnect delay; the app uses 5000 ms (tests shorten it)
    pub reconnect_ms: u64,
    pub connect_timeout: Duration,
    /// how long one blocking read waits before the loop looks at the stop flag and the timer again
    pub poll: Duration,
}

impl Config {
    pub fn new(kind: Kind, url: impl Into<String>) -> Self {
        Config { kind, url: url.into(), reconnect_ms: RECONNECT_DELAY_MS, connect_timeout: Duration::from_secs(10), poll: Duration::from_millis(50) }
    }
}

pub struct Handle {
    stop: Arc<AtomicBool>,
    connected: Arc<AtomicBool>,
    outbound: Sender<String>,
    thread: Option<JoinHandle<()>>,
}

impl Handle {
    pub fn is_connected(&self) -> bool {
        self.connected.load(Ordering::SeqCst)
    }
    /// `HostControlRelayWorker.send_json`: compact separators, false when not connected.
    pub fn send_json(&self, obj: &Value) -> bool {
        if !self.is_connected() {
            return false;
        }
        match dumps(obj, &Style { compact: true, ..Style::default() }) {
            Ok(text) => self.outbound.send(text).is_ok(),
            Err(_) => false,
        }
    }
    pub fn stop(mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

fn host_port(url: &str) -> Result<(String, u16, bool), String> {
    let (scheme, rest) = url.split_once("://").ok_or("no scheme")?;
    let secure = scheme == "wss";
    let authority = rest.split(['/', '?']).next().unwrap_or("");
    let (host, port) = match authority.rsplit_once(':') {
        Some((h, p)) => (h.to_string(), p.parse::<u16>().map_err(|_| "bad port")?),
        None => (authority.to_string(), if secure { 443 } else { 80 }),
    };
    Ok((host, port, secure))
}

fn open(config: &Config) -> Result<Socket, String> {
    let (host, port, secure) = host_port(&config.url)?;
    if secure && !cfg!(feature = "tls") {
        return Err("wss:// needs the `tls` feature".into());
    }
    let addr = (host.as_str(), port).to_socket_addrs().map_err(|e| e.to_string())?.next().ok_or("no address")?;
    let stream = TcpStream::connect_timeout(&addr, config.connect_timeout).map_err(|e| e.to_string())?;
    stream.set_nodelay(true).ok();
    // the handshake itself must not hang forever either
    stream.set_read_timeout(Some(config.connect_timeout)).ok();
    // wss:// goes through rustls (`tls` feature); plain ws:// is wrapped in the same stream type so one Socket type serves both
    #[cfg(feature = "tls")]
    let (socket, _response) = tungstenite::client_tls(config.url.as_str(), stream).map_err(|e| e.to_string())?;
    #[cfg(not(feature = "tls"))]
    let (socket, _response) = tungstenite::client(config.url.as_str(), MaybeTlsStream::Plain(stream)).map_err(|e| e.to_string())?;
    set_poll(&socket, config.poll);
    Ok(socket)
}

fn set_poll(socket: &Socket, poll: Duration) {
    match socket.get_ref() {
        MaybeTlsStream::Plain(s) => {
            let _ = s.set_read_timeout(Some(poll));
        }
        #[cfg(feature = "tls")]
        MaybeTlsStream::Rustls(s) => {
            let _ = s.get_ref().set_read_timeout(Some(poll));
        }
        _ => {}
    }
}

fn is_timeout(e: &tungstenite::Error) -> bool {
    matches!(e, tungstenite::Error::Io(io) if matches!(io.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut))
}

/// Start the client on its own thread; `sink` receives every [`Output`].
pub fn spawn(config: Config, sink: impl Fn(Output) + Send + 'static) -> Handle {
    let stop = Arc::new(AtomicBool::new(false));
    let connected = Arc::new(AtomicBool::new(false));
    let (tx, rx): (Sender<String>, Receiver<String>) = mpsc::channel();
    let (stop2, connected2) = (stop.clone(), connected.clone());
    let thread = thread::Builder::new()
        .name("singws-relay-ws".into())
        .spawn(move || run(config, stop2, connected2, rx, sink))
        .expect("spawn relay thread");
    Handle { stop, connected, outbound: tx, thread: Some(thread) }
}

struct Runner<'a> {
    config: &'a Config,
    worker: Worker,
    socket: Option<Socket>,
    deadline: Option<Instant>,
    sink: &'a dyn Fn(Output),
    connected: &'a AtomicBool,
}

impl Runner<'_> {
    /// Feed an event to the state machine and perform the commands it returns; connection results become new events.
    fn feed(&mut self, event: Event) {
        let mut queue = vec![event];
        while let Some(event) = queue.pop() {
            for command in self.worker.step(&event) {
                match command {
                    Command::AbortOldSocket => self.socket = None,
                    Command::OpenSocket => match open(self.config) {
                        Ok(socket) => {
                            self.socket = Some(socket);
                            queue.push(Event::SocketConnected);
                        }
                        Err(e) => {
                            (self.sink)(Output::Log(format!("connect failed: {e}")));
                            self.socket = None;
                            // Qt: the failed handshake raises an error signal, then a disconnect
                            queue.insert(0, Event::SocketDisconnected);
                            queue.push(Event::SocketError);
                        }
                    },
                    Command::StartReconnectTimer(_) => self.deadline = Some(Instant::now() + Duration::from_millis(self.config.reconnect_ms)),
                    Command::StopReconnectTimer => self.deadline = None,
                    Command::CloseSocket => {
                        if let Some(mut s) = self.socket.take() {
                            let _ = s.close(None);
                            let _ = s.flush();
                        }
                    }
                    Command::EmitStatus(ok, text) => (self.sink)(Output::Status(ok, text.to_string())),
                    Command::EmitRequestsAvailable(reason) => (self.sink)(Output::RequestsAvailable(reason.to_string())),
                }
            }
            self.connected.store(self.worker.is_connected(), Ordering::SeqCst);
        }
    }

    fn on_text(&self, text: &str) {
        match self.config.kind {
            Kind::Request => match classify_request_message(text) {
                RequestRelayAction::RequestsAvailable => (self.sink)(Output::RequestsAvailable("relay".into())),
                RequestRelayAction::History(t) => (self.sink)(Output::History(t)),
                RequestRelayAction::Hello => (self.sink)(Output::Log("hello".into())),
                RequestRelayAction::Ignored(t) => (self.sink)(Output::Log(format!("ignoring message type={t:?}"))),
                RequestRelayAction::NotJson => (self.sink)(Output::Log("ignoring non-JSON message".into())),
            },
            Kind::Host => {
                if let Some(command) = classify_host_message(text) {
                    (self.sink)(Output::Command(command));
                }
            }
        }
    }
}

fn run(config: Config, stop: Arc<AtomicBool>, connected: Arc<AtomicBool>, outbound: Receiver<String>, sink: impl Fn(Output)) {
    let mut r = Runner { config: &config, worker: Worker::new(config.kind), socket: None, deadline: None, sink: &sink, connected: &connected };
    r.feed(Event::Start);
    loop {
        if stop.load(Ordering::SeqCst) {
            r.feed(Event::Stop);
            break;
        }
        if r.deadline.is_some_and(|d| Instant::now() >= d) {
            r.deadline = None;
            r.feed(Event::TimerFired);
        }
        let Some(socket) = r.socket.as_mut() else {
            thread::sleep(config.poll);
            continue;
        };
        while let Ok(text) = outbound.try_recv() {
            if let Err(e) = socket.send(Message::Text(text.into())) {
                (r.sink)(Output::Log(format!("send failed: {e}")));
            }
        }
        match socket.read() {
            Ok(Message::Text(text)) => r.on_text(text.as_str()),
            Ok(Message::Close(_)) => {
                r.socket = None;
                r.feed(Event::SocketDisconnected);
            }
            Ok(_) => {}
            Err(e) if is_timeout(&e) => {}
            Err(tungstenite::Error::ConnectionClosed | tungstenite::Error::AlreadyClosed) => {
                r.socket = None;
                r.feed(Event::SocketDisconnected);
            }
            Err(e) => {
                (r.sink)(Output::Log(format!("socket error: {e}")));
                r.socket = None;
                // Qt: errorOccurred then disconnected
                r.feed(Event::SocketError);
                r.feed(Event::SocketDisconnected);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn host_and_port_parsing() {
        assert_eq!(host_port("ws://127.0.0.1:8080/relay?x=1").unwrap(), ("127.0.0.1".into(), 8080, false));
        assert_eq!(host_port("wss://wskar.com/relay").unwrap(), ("wskar.com".into(), 443, true));
        assert_eq!(host_port("ws://localhost/relay").unwrap(), ("localhost".into(), 80, false));
        assert!(host_port("nope").is_err());
        assert!(host_port("ws://h:99999/").is_err());
    }

    #[test]
    fn a_closed_port_fails_fast_and_never_hangs_the_runner() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        let mut config = Config::new(Kind::Request, format!("ws://127.0.0.1:{port}/relay?user=u&token=t&app_version=1"));
        config.reconnect_ms = 30;
        let seen = Arc::new(std::sync::Mutex::new(Vec::new()));
        let seen2 = seen.clone();
        let handle = spawn(config, move |o| seen2.lock().unwrap().push(o));
        thread::sleep(Duration::from_millis(400));
        assert!(!handle.is_connected());
        handle.stop();
        let seen = seen.lock().unwrap();
        let failures = seen.iter().filter(|o| matches!(o, Output::Log(t) if t.starts_with("connect failed"))).count();
        let statuses = seen.iter().filter(|o| matches!(o, Output::Status(false, _))).count();
        assert!(failures >= 3, "kept retrying on the timer: {failures}");
        assert_eq!(statuses, failures, "every failed attempt reports a disconnect status like the request worker");
    }

    #[test]
    fn wss_is_refused_without_the_tls_feature() {
        if !cfg!(feature = "tls") {
            let config = Config::new(Kind::Request, "wss://127.0.0.1:1/relay");
            assert!(open(&config).is_err());
        }
    }
}
