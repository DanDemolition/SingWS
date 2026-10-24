//! The pure logic of the app's two WebSocket relay clients (`RelayRequestWorker`, `HostControlRelayWorker`), reproduced so a Rust client can
//! be proven equivalent without opening a socket:
//!
//! * [`relay_url`]: the `wss://host/relay?...` URL exactly as Qt's `QUrl`/`QUrlQuery` build it (Qt percent-encodes each value once with
//!   `toPercentEncoding`, lower-cases the host, keeps an explicit port, uses `ws` only when the base scheme is `http`);
//! * [`classify_request_message`] / [`classify_host_message`]: what each worker does with an incoming text frame;
//! * [`Worker`]: the connect / reconnect / stop state machine, as commands to execute (open a socket, start the 5 s single-shot timer, emit a
//!   signal ...) so the I/O layer stays trivial and the decisions stay testable.

use serde_json::Value;

pub const RECONNECT_DELAY_MS: u64 = 5000;

/// `_network_normalize_base_url`.
pub fn normalize_base_url(base_url: &str) -> String {
    let base = base_url.trim().trim_end_matches('/').to_string();
    if !base.is_empty() && !base.contains("://") { format!("https://{base}") } else { base }
}

/// `QUrl.toPercentEncoding(s)`: everything except `A-Za-z0-9-._~` is percent-encoded (upper-case hex of the UTF-8 bytes).
pub fn percent_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.' | b'_' | b'~') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// The relay URL. `host_channel` adds `role=app&channel=host` (HostControlRelayWorker). Anything this function cannot reproduce exactly the way
/// Qt does (non-ASCII hosts that Qt would punycode, IPv6 literals, userinfo, empty hosts, ports out of range) is an error, never a guess.
pub fn relay_url(base_url: &str, user_id: &str, api_key: &str, app_version: &str, host_channel: bool) -> Result<String, String> {
    let base = normalize_base_url(base_url);
    let (scheme, rest) = base.split_once("://").ok_or("base URL has no scheme")?;
    let scheme = scheme.to_ascii_lowercase();
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if authority.contains('@') || authority.starts_with('[') {
        return Err("userinfo and IPv6 hosts are not reproduced".into());
    }
    let (host, port) = match authority.rsplit_once(':') {
        Some((h, p)) if !p.is_empty() => {
            let port: u32 = p.parse().map_err(|_| "port is not a number".to_string())?;
            if !(1..=65535).contains(&port) {
                return Err("port out of range".into());
            }
            (h, Some(port))
        }
        Some((h, _)) => (h, None),
        None => (authority, None),
    };
    if host.is_empty() {
        return Err("empty host".into());
    }
    if !host.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.')) {
        return Err("host contains characters Qt would normalise (IDN, percent, ...)".into());
    }
    let user = if user_id.trim().is_empty() { "default" } else { user_id.trim() };
    let mut query = Vec::new();
    if host_channel {
        query.push("role=app".to_string());
        query.push("channel=host".to_string());
    }
    query.push(format!("user={}", percent_encode(user)));
    query.push(format!("token={}", percent_encode(api_key.trim())));
    query.push(format!("app_version={}", percent_encode(app_version.trim())));
    let ws = if scheme != "http" { "wss" } else { "ws" };
    let port = port.map(|p| format!(":{p}")).unwrap_or_default();
    Ok(format!("{ws}://{}{port}/relay?{}", host.to_ascii_lowercase(), query.join("&")))
}

/// Log-safe form of a relay URL: the token value replaced by `***`.
pub fn redact(url: &str) -> String {
    match url.split_once('?') {
        None => url.to_string(),
        Some((head, query)) => {
            let q: Vec<String> = query.split('&').map(|kv| if kv.starts_with("token=") { "token=***".to_string() } else { kv.to_string() }).collect();
            format!("{head}?{}", q.join("&"))
        }
    }
}

/// What `RelayRequestWorker._on_text_message` does with a frame.
#[derive(Debug, Clone, PartialEq)]
pub enum RequestRelayAction {
    /// `hello`: only logged.
    Hello,
    /// `requests_available`: emit `requests_available("relay")`.
    RequestsAvailable,
    /// one of the `history_*` types: emit `history_available(<type>)`.
    History(String),
    /// any other type, or JSON that is not an object (type ""): logged and dropped.
    Ignored(String),
    /// not JSON at all: logged and dropped.
    NotJson,
}

const HISTORY_TYPES: [&str; 7] = [
    "history_added", "history_updated", "history_deleted", "history_reordered", "history_preferred_brand_changed", "history_favorite_changed",
    "history_bulk_sync",
];

/// Python's `str(value)` for the JSON scalar kinds a `type` field can hold (used by `str(... or "").strip().lower()`).
fn py_str(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::Bool(b) => (if *b { "True" } else { "False" }).to_string(),
        Value::String(s) => s.clone(),
        Value::Number(n) => n.as_i64().map(|i| i.to_string()).or_else(|| n.as_u64().map(|u| u.to_string())).unwrap_or_else(|| n.to_string()),
        other => other.to_string(),
    }
}

fn py_strip_lower(s: &str) -> String {
    s.trim().to_lowercase()
}

pub fn classify_request_message(text: &str) -> RequestRelayAction {
    let Ok(data) = serde_json::from_str::<Value>(text) else { return RequestRelayAction::NotJson };
    // `(data or {}).get("type", "") or ""`, only for objects; anything else has type ""
    let msg_type = match &data {
        Value::Object(map) => {
            let v = map.get("type").cloned().unwrap_or(Value::String(String::new()));
            let falsy = matches!(&v, Value::Null | Value::Bool(false)) || v.as_str().is_some_and(str::is_empty)
                || v.as_i64() == Some(0) || v.as_array().is_some_and(Vec::is_empty) || v.as_object().is_some_and(|m| m.is_empty());
            py_strip_lower(&if falsy { String::new() } else { py_str(&v) })
        }
        _ => String::new(),
    };
    match msg_type.as_str() {
        "hello" => RequestRelayAction::Hello,
        "requests_available" => RequestRelayAction::RequestsAvailable,
        t if HISTORY_TYPES.contains(&t) => RequestRelayAction::History(t.to_string()),
        other => RequestRelayAction::Ignored(other.to_string()),
    }
}

/// `HostControlRelayWorker._on_text_message`: a JSON object whose `str(type).strip().lower()` is `command` is delivered whole.
pub fn classify_host_message(text: &str) -> Option<Value> {
    let data = serde_json::from_str::<Value>(text).ok()?;
    let map = data.as_object()?;
    // `str(data.get("type", ""))`: a missing type is "", null is "None"
    let t = match map.get("type") {
        None => String::new(),
        Some(Value::Null) => "None".to_string(),
        Some(v) => py_str(v),
    };
    (py_strip_lower(&t) == "command").then_some(data)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Request,
    Host,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    Start,
    Stop,
    /// the single-shot reconnect timer fired
    TimerFired,
    SocketConnected,
    SocketDisconnected,
    SocketError,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    /// tear down the previous socket (blockSignals + abort + deleteLater) before a new one
    AbortOldSocket,
    OpenSocket,
    StartReconnectTimer(u64),
    StopReconnectTimer,
    /// stop(): close and abort the live socket
    CloseSocket,
    EmitStatus(bool, &'static str),
    EmitRequestsAvailable(&'static str),
}

/// The lifecycle shared by both workers. Feed it events, execute the returned commands.
#[derive(Debug, Clone)]
pub struct Worker {
    kind: Kind,
    closing: bool,
    connected: bool,
    has_socket: bool,
    timer_active: bool,
}

impl Worker {
    pub fn new(kind: Kind) -> Self {
        Worker { kind, closing: false, connected: false, has_socket: false, timer_active: false }
    }
    pub fn is_connected(&self) -> bool {
        self.connected
    }
    pub fn timer_active(&self) -> bool {
        self.timer_active
    }

    fn open_socket(&mut self, out: &mut Vec<Command>) {
        if self.closing {
            return;
        }
        if self.has_socket {
            out.push(Command::AbortOldSocket);
        }
        self.has_socket = true;
        out.push(Command::OpenSocket);
    }

    fn schedule_reconnect(&mut self, out: &mut Vec<Command>) {
        if self.closing {
            return;
        }
        if !self.timer_active {
            self.timer_active = true;
            out.push(Command::StartReconnectTimer(RECONNECT_DELAY_MS));
        }
    }

    pub fn step(&mut self, event: &Event) -> Vec<Command> {
        let mut out = Vec::new();
        match event {
            Event::Start => {
                self.closing = false;
                self.open_socket(&mut out);
            }
            Event::Stop => {
                self.closing = true;
                self.connected = false;
                if self.timer_active {
                    out.push(Command::StopReconnectTimer);
                }
                self.timer_active = false;
                if self.has_socket {
                    out.push(Command::CloseSocket);
                }
                self.has_socket = false;
            }
            Event::TimerFired => {
                // single-shot: it fires once, then is inactive; the slot is _open_socket
                if self.timer_active {
                    self.timer_active = false;
                    self.open_socket(&mut out);
                }
            }
            Event::SocketConnected => {
                self.connected = true;
                match self.kind {
                    Kind::Request => {
                        out.push(Command::EmitStatus(true, "Relay connected"));
                        out.push(Command::EmitRequestsAvailable("connect")); // recover anything submitted while disconnected
                    }
                    Kind::Host => out.push(Command::EmitStatus(true, "Host relay connected")),
                }
            }
            Event::SocketDisconnected => {
                self.connected = false;
                if !self.closing {
                    if self.kind == Kind::Request {
                        out.push(Command::EmitStatus(false, "Relay disconnected; reconnecting"));
                    }
                    self.schedule_reconnect(&mut out);
                }
            }
            Event::SocketError => {
                self.connected = false;
                if !self.closing {
                    self.schedule_reconnect(&mut out);
                }
            }
        }
        out
    }
}

fn kind_of(s: &str) -> Option<Kind> {
    match s {
        "request" => Some(Kind::Request),
        "host" => Some(Kind::Host),
        _ => None,
    }
}

fn event_of(s: &str) -> Option<Event> {
    Some(match s {
        "start" => Event::Start,
        "stop" => Event::Stop,
        "timer" => Event::TimerFired,
        "connected" => Event::SocketConnected,
        "disconnected" => Event::SocketDisconnected,
        "error" => Event::SocketError,
        _ => return None,
    })
}

fn command_text(c: &Command) -> String {
    match c {
        Command::AbortOldSocket => "abort_old".into(),
        Command::OpenSocket => "open".into(),
        Command::StartReconnectTimer(ms) => format!("timer_start({ms})"),
        Command::StopReconnectTimer => "timer_stop".into(),
        Command::CloseSocket => "close".into(),
        Command::EmitStatus(ok, text) => format!("status({},{text})", if *ok { "True" } else { "False" }),
        Command::EmitRequestsAvailable(reason) => format!("requests_available({reason})"),
    }
}

/// CLI bridge. Requests (batch = array):
/// `{"op":"url","base_url","user","key","version","host":bool}`, `{"op":"request_message","text"}`, `{"op":"host_message","text"}`,
/// `{"op":"lifecycle","kind":"request|host","events":["start",...]}` (returns the command trace per event).
pub fn handle(req: &Value) -> Value {
    use serde_json::json;
    let s = |k: &str| req.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    match req.get("op").and_then(Value::as_str).unwrap_or("") {
        "url" => match relay_url(&s("base_url"), &s("user"), &s("key"), &s("version"), req.get("host").and_then(Value::as_bool).unwrap_or(false)) {
            Ok(u) => json!({"ok": true, "url": u, "redacted": redact(&u)}),
            Err(e) => json!({"ok": false, "error": e}),
        },
        "request_message" => match classify_request_message(&s("text")) {
            RequestRelayAction::Hello => json!({"ok": true, "action": "hello"}),
            RequestRelayAction::RequestsAvailable => json!({"ok": true, "action": "requests_available"}),
            RequestRelayAction::History(t) => json!({"ok": true, "action": "history", "type": t}),
            RequestRelayAction::Ignored(t) => json!({"ok": true, "action": "ignored", "type": t}),
            RequestRelayAction::NotJson => json!({"ok": true, "action": "not_json"}),
        },
        "host_message" => match classify_host_message(&s("text")) {
            Some(v) => json!({"ok": true, "command": v}),
            None => json!({"ok": true, "command": null}),
        },
        "lifecycle" => {
            let Some(kind) = kind_of(&s("kind")) else { return json!({"ok": false, "error": "bad kind"}) };
            let mut worker = Worker::new(kind);
            let mut trace = Vec::new();
            for e in req.get("events").and_then(Value::as_array).cloned().unwrap_or_default() {
                let Some(event) = e.as_str().and_then(event_of) else { return json!({"ok": false, "error": "bad event"}) };
                trace.push(worker.step(&event).iter().map(command_text).collect::<Vec<_>>());
            }
            json!({"ok": true, "trace": trace, "connected": worker.is_connected(), "timer_active": worker.timer_active()})
        }
        _ => json!({"ok": false, "error": "unknown op"}),
    }
}

pub fn handle_batch(input: &Value) -> Value {
    match input {
        Value::Array(items) => Value::Array(items.iter().map(handle).collect()),
        other => handle(other),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn urls_match_what_qt_builds() {
        assert_eq!(relay_url("https://beta.wskar.com", "dan", "k3y", "1.0.1.4", false).unwrap(),
                   "wss://beta.wskar.com/relay?user=dan&token=k3y&app_version=1.0.1.4");
        assert_eq!(relay_url("https://wskar.com", "Dan & Co", "a b+c%d", "1.0.1.4", false).unwrap(),
                   "wss://wskar.com/relay?user=Dan%20%26%20Co&token=a%20b%2Bc%25d&app_version=1.0.1.4");
        assert_eq!(relay_url("http://localhost:8080", "é日😀", "~._-/?#=&", "v 1", false).unwrap(),
                   "ws://localhost:8080/relay?user=%C3%A9%E6%97%A5%F0%9F%98%80&token=~._-%2F%3F%23%3D%26&app_version=v%201");
        assert_eq!(relay_url("https://Wskar.COM:8443/", "u", "", "", false).unwrap(), "wss://wskar.com:8443/relay?user=u&token=&app_version=");
        assert_eq!(relay_url("wskar.com", "  ", "k", "1", true).unwrap(), "wss://wskar.com/relay?role=app&channel=host&user=default&token=k&app_version=1");
    }

    #[test]
    fn unreproducible_bases_are_errors() {
        // note: "https://" is NOT here. The app's normalizer turns it into "https://https:" (it strips the trailing slashes, sees no "://", prepends
        // one), and Rust reproduces that quirk exactly, like Python and Qt do.
        for bad in ["", "https://:80", "https://exa mple.com", "https://münchen.de", "https://user@host.com", "https://[::1]:8080", "https://host:99999", "https://host:abc"] {
            assert!(relay_url(bad, "u", "k", "1", false).is_err(), "{bad}");
        }
    }

    #[test]
    fn the_token_is_redacted_for_logs() {
        let u = relay_url("https://wskar.com", "dan", "SECRET%", "1", false).unwrap();
        assert_eq!(redact(&u), "wss://wskar.com/relay?user=dan&token=***&app_version=1");
        assert!(!redact(&u).contains("SECRET"));
    }

    #[test]
    fn request_messages_follow_the_workers_dispatch() {
        use RequestRelayAction::*;
        assert_eq!(classify_request_message(r#"{"type":"hello","user":"dan"}"#), Hello);
        assert_eq!(classify_request_message(r#"{"type":" Requests_Available "}"#), RequestsAvailable);
        assert_eq!(classify_request_message(r#"{"type":"HISTORY_UPDATED"}"#), History("history_updated".into()));
        assert_eq!(classify_request_message(r#"{"type":"history_bulk_sync","n":3}"#), History("history_bulk_sync".into()));
        assert_eq!(classify_request_message(r#"{"type":"nope"}"#), Ignored("nope".into()));
        assert_eq!(classify_request_message(r#"{"type":null}"#), Ignored("".into()));
        assert_eq!(classify_request_message(r#"{"type":0}"#), Ignored("".into()));
        assert_eq!(classify_request_message(r#"{"type":7}"#), Ignored("7".into()));
        assert_eq!(classify_request_message(r#"{}"#), Ignored("".into()));
        assert_eq!(classify_request_message("[1,2]"), Ignored("".into()));
        assert_eq!(classify_request_message("\"hello\""), Ignored("".into()));
        assert_eq!(classify_request_message("not json"), NotJson);
        assert_eq!(classify_request_message(""), NotJson);
    }

    #[test]
    fn host_messages_deliver_only_commands() {
        assert!(classify_host_message(r#"{"type":"command","id":4,"name":"pause"}"#).is_some());
        assert!(classify_host_message(r#"{"type":" COMMAND "}"#).is_some());
        for no in [r#"{"type":"ack"}"#, r#"{"type":null}"#, r#"{}"#, "[1]", "nope", r#"{"type":["command"]}"#] {
            assert!(classify_host_message(no).is_none(), "{no}");
        }
    }

    fn run(kind: Kind, events: &[Event]) -> Vec<Vec<Command>> {
        let mut w = Worker::new(kind);
        events.iter().map(|e| w.step(e)).collect()
    }

    #[test]
    fn connect_disconnect_reconnect_cycle_of_the_request_worker() {
        use Command::*;
        use Event::*;
        let t = run(Kind::Request, &[Start, SocketConnected, SocketDisconnected, TimerFired, SocketConnected]);
        assert_eq!(t[0], vec![OpenSocket]);
        assert_eq!(t[1], vec![EmitStatus(true, "Relay connected"), EmitRequestsAvailable("connect")]);
        assert_eq!(t[2], vec![EmitStatus(false, "Relay disconnected; reconnecting"), StartReconnectTimer(5000)]);
        assert_eq!(t[3], vec![AbortOldSocket, OpenSocket]);
        assert_eq!(t[4][0], EmitStatus(true, "Relay connected"));
    }

    #[test]
    fn the_host_worker_has_its_own_texts_and_no_disconnect_status() {
        use Command::*;
        use Event::*;
        let t = run(Kind::Host, &[Start, SocketConnected, SocketDisconnected]);
        assert_eq!(t[1], vec![EmitStatus(true, "Host relay connected")]);
        assert_eq!(t[2], vec![StartReconnectTimer(5000)]);
    }

    #[test]
    fn a_single_shot_timer_is_never_started_twice_and_error_plus_disconnect_reconnect_once() {
        use Command::*;
        use Event::*;
        let t = run(Kind::Request, &[Start, SocketError, SocketDisconnected, SocketError]);
        assert_eq!(t[1], vec![StartReconnectTimer(5000)]);
        assert_eq!(t[2], vec![EmitStatus(false, "Relay disconnected; reconnecting")]);
        assert_eq!(t[3], Vec::<Command>::new());
    }

    #[test]
    fn stop_wins_over_everything_and_start_revives() {
        use Command::*;
        use Event::*;
        let t = run(Kind::Request, &[Start, SocketError, Stop, TimerFired, SocketDisconnected, SocketError, Start]);
        assert_eq!(t[2], vec![StopReconnectTimer, CloseSocket]);
        assert_eq!(t[3], Vec::<Command>::new(), "the stopped timer never reopens a socket");
        assert_eq!(t[4], Vec::<Command>::new(), "no status and no reconnect while closing");
        assert_eq!(t[5], Vec::<Command>::new());
        assert_eq!(t[6], vec![OpenSocket], "the socket was dropped by stop(), so start opens a fresh one without aborting");
    }

    #[test]
    fn a_timer_that_is_not_running_can_never_open_a_socket() {
        // a single-shot QTimer that is not active cannot fire, so the real-class differential never produces this event; the rule is defensive
        let t = run(Kind::Request, &[Event::TimerFired, Event::Start, Event::TimerFired]);
        assert_eq!(t[0], Vec::<Command>::new());
        assert_eq!(t[2], Vec::<Command>::new(), "a live socket and no scheduled retry: a stray timer event does nothing");
    }

    #[test]
    fn a_second_start_replaces_the_live_socket() {
        use Command::*;
        let t = run(Kind::Host, &[Event::Start, Event::Start]);
        assert_eq!(t[1], vec![AbortOldSocket, OpenSocket]);
    }

    #[test]
    fn bridge_ops() {
        let v = handle(&serde_json::json!({"op": "lifecycle", "kind": "request", "events": ["start", "error"]}));
        assert_eq!(v["trace"], serde_json::json!([["open"], ["timer_start(5000)"]]));
        assert_eq!(handle(&serde_json::json!({"op": "url", "base_url": "https://wskar.com", "user": "dan", "key": "k", "version": "1"}))["ok"], true);
        assert_eq!(handle(&serde_json::json!({"op": "lifecycle", "kind": "x", "events": []}))["ok"], false);
        assert_eq!(handle(&serde_json::json!({"op": "?"}))["ok"], false);
    }
}
