//! The relay client: log in, fetch a token, join the room as `role=browser`, hand every message to the owner, send validated commands, and
//! report "no answer" when the app does not acknowledge in time. A dropped connection is retried after one delay with a FRESH token
//! (tokens live about a minute); a refused PIN stops the retries, because trying again cannot help.

use crate::protocol::{Incoming, browser_url, build_command, new_request_id, parse_incoming};
use crate::session::{Session, SessionError};
use serde_json::Value;
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{Message, WebSocket};

type Socket = WebSocket<MaybeTlsStream<TcpStream>>;

#[derive(Debug, Clone, PartialEq)]
pub enum Output {
    /// connection state for the UI: (connected to the relay, short text)
    Status(bool, String),
    /// the PIN was accepted (a session cookie is held in memory)
    LoggedIn,
    /// the PIN was refused or the server will not let this network log in: no further attempts are made
    LoginRefused(String),
    Message(Incoming),
    /// no acknowledgement for this command within `ack_timeout`
    NoAnswer { request_id: String },
    Log(String),
}

#[derive(Clone)]
pub struct Config {
    pub base_url: String,
    pub user: String,
    /// kept in memory only; never written anywhere
    pub pin: String,
    pub reconnect_ms: u64,
    pub ack_timeout: Duration,
    pub connect_timeout: Duration,
    pub poll: Duration,
}

impl Config {
    pub fn new(base_url: impl Into<String>, user: impl Into<String>, pin: impl Into<String>) -> Self {
        Config {
            base_url: base_url.into(),
            user: user.into(),
            pin: pin.into(),
            reconnect_ms: 5000,
            ack_timeout: Duration::from_secs(3),
            connect_timeout: Duration::from_secs(8),
            poll: Duration::from_millis(40),
        }
    }
}

// a Config is cloned into the thread; keep the PIN out of any Debug output
impl std::fmt::Debug for Config {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Config {{ base_url: {:?}, user: {:?}, pin: <hidden> }}", self.base_url, self.user)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum SendError {
    NotConnected,
    Invalid(String),
}

impl std::fmt::Display for SendError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SendError::NotConnected => write!(f, "not connected to the relay"),
            SendError::Invalid(m) => write!(f, "{m}"),
        }
    }
}

struct Shared {
    stop: AtomicBool,
    connected: AtomicBool,
    pending: Mutex<Vec<(String, Instant)>>,
}

pub struct Handle {
    shared: Arc<Shared>,
    outbound: Sender<String>,
    user: Mutex<String>,
    thread: Option<JoinHandle<()>>,
}

impl Handle {
    pub fn is_connected(&self) -> bool {
        self.shared.connected.load(Ordering::SeqCst)
    }

    /// Validate, build and queue one command. Returns its request id; the result arrives as `Output::Message(Incoming::Ack)`, an `Error`, or `NoAnswer`.
    pub fn send(&self, command: &str, args: &Value, expect_queue_sig: Option<&str>) -> Result<String, SendError> {
        self.send_with_id(command, args, expect_queue_sig, &new_request_id())
    }

    /// Like [`Handle::send`] with a caller-chosen id: resending the SAME id is how a retry stays safe (the app applies an id once).
    pub fn send_with_id(&self, command: &str, args: &Value, expect_queue_sig: Option<&str>, request_id: &str) -> Result<String, SendError> {
        if !self.is_connected() {
            return Err(SendError::NotConnected);
        }
        let id = request_id.to_string();
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        let user = self.user.lock().unwrap().clone();
        let msg = build_command(&user, command, args, expect_queue_sig, &id, now).map_err(SendError::Invalid)?;
        let text = serde_json::to_string(&msg).map_err(|e| SendError::Invalid(e.to_string()))?;
        self.shared.pending.lock().unwrap().push((id.clone(), Instant::now()));
        self.outbound.send(text).map_err(|_| SendError::NotConnected)?;
        Ok(id)
    }

    pub fn stop(mut self) {
        self.shared.stop.store(true, Ordering::SeqCst);
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

fn open(url: &str, connect_timeout: Duration, poll: Duration) -> Result<Socket, String> {
    let (host, port, secure) = host_port(url)?;
    if secure && !cfg!(feature = "tls") {
        return Err("wss:// needs the `tls` feature".into());
    }
    let addr = (host.as_str(), port).to_socket_addrs().map_err(|e| e.to_string())?.next().ok_or("no address")?;
    let stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| e.to_string())?;
    stream.set_nodelay(true).ok();
    stream.set_read_timeout(Some(connect_timeout)).ok();
    #[cfg(feature = "tls")]
    let (socket, _r) = tungstenite::client_tls(url, stream).map_err(|e| e.to_string())?;
    #[cfg(not(feature = "tls"))]
    let (socket, _r) = tungstenite::client(url, MaybeTlsStream::Plain(stream)).map_err(|e| e.to_string())?;
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
    Ok(socket)
}

fn is_timeout(e: &tungstenite::Error) -> bool {
    matches!(e, tungstenite::Error::Io(io) if matches!(io.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut))
}

/// Start the client on its own thread; `sink` receives every [`Output`].
pub fn spawn(config: Config, sink: impl Fn(Output) + Send + 'static) -> Handle {
    let shared = Arc::new(Shared { stop: AtomicBool::new(false), connected: AtomicBool::new(false), pending: Mutex::new(Vec::new()) });
    let (tx, rx) = mpsc::channel::<String>();
    let shared2 = shared.clone();
    let user = config.user.clone();
    let thread = thread::Builder::new().name("singws-control".into()).spawn(move || run(config, shared2, rx, sink)).expect("spawn control thread");
    Handle { shared, outbound: tx, user: Mutex::new(user), thread: Some(thread) }
}

/// Sleep in small steps so a stop request is honoured quickly. Returns true when stopped.
fn wait(shared: &Shared, total: Duration) -> bool {
    let end = Instant::now() + total;
    while Instant::now() < end {
        if shared.stop.load(Ordering::SeqCst) {
            return true;
        }
        thread::sleep(Duration::from_millis(20));
    }
    shared.stop.load(Ordering::SeqCst)
}

fn run(config: Config, shared: Arc<Shared>, outbound: Receiver<String>, sink: impl Fn(Output)) {
    let mut session = match Session::new(&config.base_url, &config.user) {
        Ok(s) => s,
        Err(e) => {
            sink(Output::LoginRefused(e));
            return;
        }
    };
    let mut logged_in = false;
    let delay = Duration::from_millis(config.reconnect_ms);
    while !shared.stop.load(Ordering::SeqCst) {
        if !logged_in {
            match session.login(&config.pin) {
                Ok(()) => {
                    logged_in = true;
                    sink(Output::LoggedIn);
                }
                Err(e @ (SessionError::InvalidPin | SessionError::LanOnly | SessionError::NotConfigured | SessionError::TenantMismatch)) => {
                    sink(Output::Status(false, format!("Login refused: {e}")));
                    sink(Output::LoginRefused(e.to_string()));
                    // retrying cannot help; idle until stopped
                    while !wait(&shared, Duration::from_millis(200)) {}
                    return;
                }
                Err(e) => {
                    sink(Output::Status(false, format!("Cannot reach the server: {e}")));
                    if wait(&shared, delay) {
                        return;
                    }
                    continue;
                }
            }
        }
        let info = match session.token() {
            Ok(i) => i,
            Err(SessionError::Unauthenticated) => {
                // the session expired (eight hours) or the server forgot it: log in again
                session.forget();
                logged_in = false;
                continue;
            }
            Err(e) => {
                sink(Output::Status(false, format!("Cannot get a relay token: {e}")));
                if wait(&shared, delay) {
                    return;
                }
                continue;
            }
        };
        let url = browser_url(&info.ws_url, &info.tenant, &info.token);
        let mut socket = match open(&url, config.connect_timeout, config.poll) {
            Ok(s) => s,
            Err(e) => {
                sink(Output::Log(format!("relay connect failed: {e}")));
                sink(Output::Status(false, "Cannot reach the relay".into()));
                if wait(&shared, delay) {
                    return;
                }
                continue;
            }
        };
        shared.connected.store(true, Ordering::SeqCst);
        sink(Output::Status(true, "Connected to the relay".into()));
        // anything queued while we were disconnected belongs to a view that no longer exists
        while outbound.try_recv().is_ok() {}
        let mut why = String::from("Disconnected");
        loop {
            if shared.stop.load(Ordering::SeqCst) {
                let _ = socket.close(None);
                let _ = socket.flush();
                shared.connected.store(false, Ordering::SeqCst);
                return;
            }
            while let Ok(text) = outbound.try_recv() {
                if let Err(e) = socket.send(Message::Text(text.into())) {
                    sink(Output::Log(format!("send failed: {e}")));
                }
            }
            // acknowledgement timeouts
            {
                let mut pending = shared.pending.lock().unwrap();
                let now = Instant::now();
                let (late, keep): (Vec<_>, Vec<_>) = pending.drain(..).partition(|(_, at)| now.duration_since(*at) >= config.ack_timeout);
                *pending = keep;
                drop(pending);
                for (id, _) in late {
                    sink(Output::NoAnswer { request_id: id });
                }
            }
            match socket.read() {
                Ok(Message::Text(text)) => {
                    let msg = parse_incoming(text.as_str());
                    match &msg {
                        Incoming::Ack(a) => shared.pending.lock().unwrap().retain(|(id, _)| id != &a.request_id),
                        Incoming::Error { request_id: Some(r), .. } => shared.pending.lock().unwrap().retain(|(id, _)| id != r),
                        _ => {}
                    }
                    sink(Output::Message(msg));
                }
                Ok(Message::Close(_)) => break,
                Ok(_) => {}
                Err(e) if is_timeout(&e) => {}
                Err(tungstenite::Error::ConnectionClosed | tungstenite::Error::AlreadyClosed) => break,
                Err(e) => {
                    why = format!("Disconnected: {e}");
                    break;
                }
            }
        }
        shared.connected.store(false, Ordering::SeqCst);
        // commands that were in flight can no longer be answered on this connection
        let lost: Vec<String> = shared.pending.lock().unwrap().drain(..).map(|(id, _)| id).collect();
        for id in lost {
            sink(Output::NoAnswer { request_id: id });
        }
        sink(Output::Status(false, why));
        if wait(&shared, delay) {
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_pin_never_appears_in_debug_output() {
        let c = Config::new("https://wskar.com", "dan", "s3cret-pin");
        let text = format!("{c:?}");
        assert!(!text.contains("s3cret-pin") && text.contains("<hidden>"), "{text}");
    }

    #[test]
    fn defaults_match_the_design() {
        let c = Config::new("https://x", "u", "p");
        assert_eq!(c.reconnect_ms, 5000, "the app's single-shot reconnect delay");
        assert_eq!(c.ack_timeout, Duration::from_secs(3));
    }

    #[test]
    fn host_and_port_parsing() {
        assert_eq!(host_port("ws://127.0.0.1:8790/relay?x=1").unwrap(), ("127.0.0.1".into(), 8790, false));
        assert_eq!(host_port("wss://wskar.com/relay").unwrap(), ("wskar.com".into(), 443, true));
        assert!(host_port("relay").is_err());
        assert!(host_port("ws://h:70000/").is_err());
    }

    #[test]
    fn a_bad_base_url_is_reported_and_the_thread_ends_without_connecting() {
        let seen = Arc::new(Mutex::new(Vec::new()));
        let s2 = seen.clone();
        let h = spawn(Config::new("not a url", "dan", "1234"), move |o| s2.lock().unwrap().push(o));
        std::thread::sleep(Duration::from_millis(200));
        assert!(!h.is_connected());
        assert_eq!(h.send("play_pause", &serde_json::json!({}), None), Err(SendError::NotConnected));
        h.stop();
        assert!(seen.lock().unwrap().iter().any(|o| matches!(o, Output::LoginRefused(_))));
    }

    #[test]
    fn nothing_is_sent_while_disconnected_and_a_stopped_client_joins_quickly() {
        let mut c = Config::new("http://127.0.0.1:1", "dan", "1234");
        c.reconnect_ms = 50;
        let seen = Arc::new(Mutex::new(Vec::new()));
        let s2 = seen.clone();
        let h = spawn(c, move |o| s2.lock().unwrap().push(o));
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(h.send("queue.noop", &serde_json::json!({}), None), Err(SendError::NotConnected));
        assert!(matches!(h.send("rm", &serde_json::json!({}), None), Err(SendError::NotConnected)), "connection is checked before validation, so an invalid command is also just 'not connected'");
        let t = Instant::now();
        h.stop();
        assert!(t.elapsed() < Duration::from_secs(2), "stop is honoured promptly");
        let seen = seen.lock().unwrap();
        assert!(seen.iter().filter(|o| matches!(o, Output::Status(false, t) if t.starts_with("Cannot reach the server"))).count() >= 2, "it keeps retrying the network");
    }
}
