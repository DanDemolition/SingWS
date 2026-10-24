//! Stage D4b: the Rust server-call sender. Mirrors what the app's `requests` usage means:
//!  * the request on the wire comes from `singws-wire` (already proven byte-exact against `requests`' preparation);
//!  * `timeout=N` means a connect timeout of N and a timeout of N for EACH read (no total deadline), not a whole-call limit;
//!  * every call passes the admission gate of `network_lifecycle.ShutdownRequests`: once shutdown has begun new calls fail at once, and
//!    `wait_for_idle` blocks until every call in flight has finished;
//!  * a non-2xx status is a normal response (requests only raises for transport failures); redirects are followed (up to 30, like requests).
//! Streaming responses, multipart bodies and custom headers are not supported and are DECLINED, never approximated.

use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

#[derive(Debug, PartialEq)]
pub enum HttpError {
    /// The gate is closed (`requests.exceptions.ConnectionError("SingWS is shutting down")` in the app).
    ShuttingDown,
    /// Could not connect (refused, DNS, TLS handshake).
    Connect(String),
    /// Connect timeout or a read that waited longer than the timeout.
    Timeout(String),
    /// The request cannot be reproduced exactly (see singws-wire); nothing was sent.
    Decline(String),
    /// Any other transport failure (reset, malformed response, too many redirects ...).
    Other(String),
}

impl std::fmt::Display for HttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HttpError::ShuttingDown => write!(f, "SingWS is shutting down"),
            HttpError::Connect(m) => write!(f, "connect: {m}"),
            HttpError::Timeout(m) => write!(f, "timeout: {m}"),
            HttpError::Decline(m) => write!(f, "decline: {m}"),
            HttpError::Other(m) => write!(f, "other: {m}"),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Request {
    pub method: String,
    pub url: String,
    pub params: Value,
    pub data: Value,
    pub json: Value,
    /// seconds; used for the connect timeout and for each read
    pub timeout: f64,
}

#[derive(Debug)]
pub struct Response {
    pub status: u16,
    pub url: String,
    /// lower-cased names, in arrival order
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

/// `ShutdownRequests`' admission gate and in-flight counter.
#[derive(Default)]
pub struct Gate {
    state: std::sync::Mutex<GateState>,
    idle: std::sync::Condvar,
}

#[derive(Default)]
struct GateState {
    closing: bool,
    active: usize,
}

pub struct Admission<'a>(&'a Gate);

impl Gate {
    pub fn admit(&self) -> Result<Admission<'_>, HttpError> {
        let mut s = self.state.lock().unwrap();
        if s.closing {
            return Err(HttpError::ShuttingDown);
        }
        s.active += 1;
        Ok(Admission(self))
    }
    pub fn begin_shutdown(&self) {
        self.state.lock().unwrap().closing = true;
    }
    /// Blocks until no call is in flight (application exit only).
    pub fn wait_for_idle(&self) {
        let mut s = self.state.lock().unwrap();
        while s.active != 0 {
            s = self.idle.wait(s).unwrap();
        }
    }
    pub fn active(&self) -> usize {
        self.state.lock().unwrap().active
    }
}

impl Drop for Admission<'_> {
    fn drop(&mut self) {
        let mut s = self.0.state.lock().unwrap();
        s.active -= 1;
        self.0.idle.notify_all();
    }
}

pub struct Client {
    gate: Arc<Gate>,
    // reqwest applies connect/read timeouts per client, so one client is kept per distinct timeout
    clients: std::sync::Mutex<HashMap<u64, reqwest::Client>>,
    // reqwest's blocking client has no per-read timeout, so the async client (which has) is driven from a private runtime
    rt: tokio::runtime::Runtime,
    // Trust anchors. None = the operating system's verifier; Some = ONLY these (the PEM bundle Python's requests uses, certifi's cacert.pem).
    roots: Option<Vec<reqwest::Certificate>>,
}

impl Default for Client {
    fn default() -> Self {
        Self::new(Arc::new(Gate::default()))
    }
}

fn classify(err: &reqwest::Error) -> HttpError {
    let text = format!("{err}");
    if err.is_timeout() {
        HttpError::Timeout(text)
    } else if err.is_connect() {
        HttpError::Connect(text)
    } else {
        HttpError::Other(text)
    }
}

impl Client {
    pub fn new(gate: Arc<Gate>) -> Self {
        Self::build(gate, None)
    }

    /// Trust exactly the certificates in `pem_bundle` (matches `requests` + certifi, which ignores the system keychain).
    pub fn with_roots(gate: Arc<Gate>, pem_bundle: &[u8]) -> Result<Self, HttpError> {
        let certs = reqwest::Certificate::from_pem_bundle(pem_bundle).map_err(|e| HttpError::Other(format!("root bundle: {e}")))?;
        if certs.is_empty() {
            return Err(HttpError::Other("root bundle holds no certificates".into()));
        }
        Ok(Self::build(gate, Some(certs)))
    }

    fn build(gate: Arc<Gate>, roots: Option<Vec<reqwest::Certificate>>) -> Self {
        Client {
            roots,
            gate,
            clients: Default::default(),
            rt: tokio::runtime::Builder::new_multi_thread().worker_threads(1).enable_all().build().expect("runtime"),
        }
    }

    pub fn gate(&self) -> &Arc<Gate> {
        &self.gate
    }

    fn client_for(&self, timeout: f64) -> Result<reqwest::Client, HttpError> {
        if !(timeout.is_finite() && timeout > 0.0) {
            return Err(HttpError::Decline(format!("timeout must be a positive number, got {timeout}")));
        }
        let key = (timeout * 1000.0).round() as u64;
        let mut map = self.clients.lock().unwrap();
        if let Some(c) = map.get(&key) {
            return Ok(c.clone());
        }
        let t = Duration::from_millis(key.max(1));
        let mut builder = reqwest::Client::builder();
        if let Some(roots) = &self.roots {
            builder = builder.tls_certs_only(roots.iter().cloned());
        }
        let c = builder
            .connect_timeout(t)
            .read_timeout(t)
            .redirect(reqwest::redirect::Policy::limited(30))
            .user_agent("SingWS")
            .build()
            .map_err(|e| HttpError::Other(e.to_string()))?;
        map.insert(key, c.clone());
        Ok(c)
    }

    pub fn send(&self, req: &Request) -> Result<Response, HttpError> {
        let _admission = self.gate.admit()?;
        let prepared = singws_wire::prepare(&req.method, &req.url, &req.params, &req.data, &req.json).map_err(HttpError::Decline)?;
        if !(prepared.url.starts_with("http://") || prepared.url.starts_with("https://")) {
            return Err(HttpError::Decline("only http and https URLs are sent".into()));
        }
        let client = self.client_for(req.timeout)?;
        let method = reqwest::Method::from_bytes(prepared.method.as_bytes()).map_err(|e| HttpError::Decline(e.to_string()))?;
        let mut builder = client.request(method, &prepared.url).header("Accept", "*/*");
        if let Some(ct) = prepared.content_type {
            builder = builder.header("Content-Type", ct);
        }
        match (prepared.body, prepared.content_length) {
            // requests sends an EMPTY body that carries a Content-Type (every form value was None) as a chunked body with no data.
            (Some(body), None) if body.is_empty() => {
                let empty = futures_util::stream::empty::<Result<Vec<u8>, std::io::Error>>();
                builder = builder.body(reqwest::Body::wrap_stream(empty));
            }
            (Some(body), _) => builder = builder.body(body),
            // a bodiless POST/PUT/... declares Content-Length: 0 explicitly (reqwest would send no length header at all)
            (None, Some(0)) => builder = builder.header("Content-Length", "0"),
            (None, _) => {}
        }
        self.rt.block_on(async move {
            let resp = builder.send().await.map_err(|e| classify(&e))?;
            let status = resp.status().as_u16();
            let url = resp.url().to_string();
            let headers = resp
                .headers()
                .iter()
                .map(|(k, v)| (k.as_str().to_string(), String::from_utf8_lossy(v.as_bytes()).into_owned()))
                .collect();
            // requests reports a failure while reading the body (even a timeout) as ConnectionError, not Timeout
            let body = resp.bytes().await.map_err(|e| HttpError::Other(format!("body: {e}")))?.to_vec();
            Ok(Response { status, url, headers, body })
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::Instant;

    /// One-shot local server: answers every connection with `reply` after `delay`, on 127.0.0.1 only.
    fn serve(reply: &'static [u8], delay: Duration) -> String {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        std::thread::spawn(move || {
            for s in l.incoming().flatten() {
                std::thread::spawn(move || {
                    let mut s = s;
                    let mut buf = [0u8; 4096];
                    let _ = s.read(&mut buf);
                    std::thread::sleep(delay);
                    let _ = s.write_all(reply);
                });
            }
        });
        format!("http://127.0.0.1:{port}/x")
    }

    fn get(url: &str, timeout: f64) -> Request {
        Request { method: "GET".into(), url: url.into(), params: Value::Null, data: Value::Null, json: Value::Null, timeout }
    }

    const OK: &[u8] = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nhi";

    #[test]
    fn gate_rejects_new_work_after_shutdown_and_drains_in_flight_calls() {
        let gate = Arc::new(Gate::default());
        let a = gate.admit().unwrap();
        assert_eq!(gate.active(), 1);
        gate.begin_shutdown();
        assert_eq!(gate.admit().err(), Some(HttpError::ShuttingDown));
        let drained = Arc::new(AtomicBool::new(false));
        let (g2, d2) = (gate.clone(), drained.clone());
        let waiter = std::thread::spawn(move || {
            g2.wait_for_idle();
            d2.store(true, Ordering::SeqCst);
        });
        std::thread::sleep(Duration::from_millis(50));
        assert!(!drained.load(Ordering::SeqCst), "wait_for_idle must block while a call is in flight");
        drop(a);
        waiter.join().unwrap();
        assert!(drained.load(Ordering::SeqCst));
        assert_eq!(gate.active(), 0);
    }

    #[test]
    fn a_failed_call_releases_the_gate() {
        let client = Client::default();
        let url = serve(OK, Duration::ZERO);
        client.send(&get(&url, 2.0)).unwrap();
        assert_eq!(client.gate().active(), 0);
        let declined = client.send(&get("ftp://127.0.0.1/x", 2.0));
        assert!(matches!(declined, Err(HttpError::Decline(_))));
        assert_eq!(client.gate().active(), 0, "a declined call must not leak an in-flight count");
        let refused = client.send(&get("http://127.0.0.1:1/x", 1.0));
        assert!(refused.is_err());
        assert_eq!(client.gate().active(), 0);
        client.gate().begin_shutdown();
        assert_eq!(client.send(&get(&url, 2.0)).err(), Some(HttpError::ShuttingDown));
        client.gate().wait_for_idle();
    }

    #[test]
    fn a_response_is_returned_with_status_headers_and_body() {
        let client = Client::default();
        let r = client.send(&get(&serve(OK, Duration::ZERO), 2.0)).unwrap();
        assert_eq!(r.status, 200);
        assert_eq!(r.body, b"hi");
        assert!(r.headers.iter().any(|(k, v)| k == "content-length" && v == "2"));
    }

    #[test]
    fn a_non_2xx_status_is_a_response_not_an_error() {
        let client = Client::default();
        let r = client.send(&get(&serve(b"HTTP/1.1 503 Busy\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", Duration::ZERO), 2.0)).unwrap();
        assert_eq!(r.status, 503);
    }

    #[test]
    fn silence_longer_than_the_timeout_is_a_timeout_and_returns_promptly() {
        let client = Client::default();
        let url = serve(OK, Duration::from_secs(3));
        let t = Instant::now();
        let r = client.send(&get(&url, 0.4));
        assert!(matches!(r, Err(HttpError::Timeout(_))), "{r:?}");
        assert!(t.elapsed() < Duration::from_millis(2500));
    }

    #[test]
    fn nonpositive_or_nonfinite_timeouts_are_declined_before_anything_is_sent() {
        let client = Client::default();
        for t in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert!(matches!(client.send(&get("http://127.0.0.1:1/x", t)), Err(HttpError::Decline(_))), "{t}");
        }
    }

    #[test]
    fn one_client_is_kept_per_distinct_timeout() {
        let client = Client::default();
        client.client_for(1.0).unwrap();
        client.client_for(1.0).unwrap();
        client.client_for(2.5).unwrap();
        assert_eq!(client.clients.lock().unwrap().len(), 2);
    }

    #[test]
    fn a_root_bundle_without_certificates_is_refused() {
        assert!(Client::with_roots(Default::default(), b"not a certificate").is_err());
    }

    #[test]
    fn concurrent_calls_are_all_counted_and_all_released() {
        let client = Arc::new(Client::default());
        let url = serve(OK, Duration::from_millis(100));
        let handles: Vec<_> = (0..6)
            .map(|_| {
                let (c, u) = (client.clone(), url.clone());
                std::thread::spawn(move || c.send(&get(&u, 3.0)).map(|r| r.status))
            })
            .collect();
        std::thread::sleep(Duration::from_millis(30));
        assert!(client.gate().active() >= 1);
        for h in handles {
            assert_eq!(h.join().unwrap().unwrap(), 200);
        }
        assert_eq!(client.gate().active(), 0);
    }
}
