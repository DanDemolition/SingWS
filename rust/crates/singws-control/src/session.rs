//! The two HTTP calls that lead to a relay connection: the host PIN login (a PHP session cookie, kept in memory only) and the short-lived websocket token.

use serde_json::{Value, json};
use singws_http::{Client, Gate, HttpError, Request};
use std::sync::Arc;

#[derive(Debug, Clone, PartialEq)]
pub enum SessionError {
    /// the PIN is wrong
    InvalidPin,
    /// the server only lets the venue's network log in (`lan_only`)
    LanOnly,
    /// no valid session (never logged in, or it expired)
    Unauthenticated,
    /// the server has no relay secret configured
    NotConfigured,
    TenantMismatch,
    Network(String),
    /// an answer that is not the documented JSON
    Protocol(String),
    /// the local gate is closed (the app is shutting down)
    ShuttingDown,
}

impl std::fmt::Display for SessionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SessionError::InvalidPin => write!(f, "invalid PIN"),
            SessionError::LanOnly => write!(f, "the server only allows logins from the venue network"),
            SessionError::Unauthenticated => write!(f, "not logged in"),
            SessionError::NotConfigured => write!(f, "the server's relay is not configured"),
            SessionError::TenantMismatch => write!(f, "tenant mismatch"),
            SessionError::Network(m) => write!(f, "network: {m}"),
            SessionError::Protocol(m) => write!(f, "unexpected answer: {m}"),
            SessionError::ShuttingDown => write!(f, "shutting down"),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct TokenInfo {
    pub token: String,
    pub ws_url: String,
    pub expires_at: i64,
    pub tenant: String,
}

/// A relay address is usable when it is ws:// or wss://, and an https server never hands out a plain ws:// one (no downgrade).
pub(crate) fn relay_address_ok(base: &str, ws_url: &str) -> bool {
    let scheme_ok = ws_url.starts_with("wss://") || ws_url.starts_with("ws://");
    let secure_ok = !base.starts_with("https://") || ws_url.starts_with("wss://");
    scheme_ok && secure_ok
}

pub struct Session {
    base: String,
    user: String,
    http: Client,
}

fn map_http(e: HttpError) -> SessionError {
    match e {
        HttpError::ShuttingDown => SessionError::ShuttingDown,
        other => SessionError::Network(other.to_string()),
    }
}

impl Session {
    /// `base_url` is the server root such as `https://wskar.com` (no path). Plain http is accepted so tests can use 127.0.0.1.
    pub fn new(base_url: &str, user: &str) -> Result<Session, String> {
        let base = base_url.trim().trim_end_matches('/').to_string();
        if !(base.starts_with("https://") || base.starts_with("http://")) {
            return Err("base URL must start with https:// (or http:// for a local test server)".into());
        }
        if user.trim().is_empty() {
            return Err("user is required".into());
        }
        Ok(Session { base, user: user.trim().to_string(), http: Client::with_cookies(Arc::new(Gate::default())) })
    }

    pub fn base_url(&self) -> &str {
        &self.base
    }

    pub(crate) fn user(&self) -> &str {
        &self.user
    }

    /// GET a JSON endpoint with the session cookie.
    pub(crate) fn get_json(&self, path: &str, params: Value) -> Result<(u16, Value), SessionError> {
        self.json_call("GET", path, params, Value::Null)
    }

    /// GET raw bytes (an image) with the session cookie.
    pub(crate) fn get_bytes(&self, path: &str, params: Value) -> Result<(u16, Vec<u8>), SessionError> {
        let req = Request { method: "GET".into(), url: format!("{}{}", self.base, path), params, data: Value::Null, json: Value::Null, timeout: 4.0 };
        let resp = self.http.send(&req).map_err(map_http)?;
        Ok((resp.status, resp.body))
    }

    fn json_call(&self, method: &str, path: &str, params: Value, data: Value) -> Result<(u16, Value), SessionError> {
        let req = Request { method: method.into(), url: format!("{}{}", self.base, path), params, data, json: Value::Null, timeout: 6.0 };
        let resp = self.http.send(&req).map_err(map_http)?;
        let body: Value = serde_json::from_slice(&resp.body).map_err(|_| SessionError::Protocol(format!("HTTP {} with a body that is not JSON", resp.status)))?;
        Ok((resp.status, body))
    }

    /// POST the PIN. The PIN is sent in the request body, never in the URL.
    pub fn login(&self, pin: &str) -> Result<(), SessionError> {
        if pin.trim().is_empty() {
            return Err(SessionError::InvalidPin);
        }
        let (status, body) = self.json_call("POST", "/api/v1/host_commands.php", json!({"action": "login", "user": self.user}), json!({"pin": pin}))?;
        if body.get("ok").and_then(Value::as_bool) == Some(true) {
            return Ok(());
        }
        match body.get("error").and_then(Value::as_str).unwrap_or("") {
            "invalid_pin" => Err(SessionError::InvalidPin),
            "lan_only" => Err(SessionError::LanOnly),
            other => Err(SessionError::Protocol(format!("login refused (HTTP {status}): {other}"))),
        }
    }

    /// Ask for a websocket token (valid about 60 s). The session cookie from `login` authenticates the call.
    pub fn token(&self) -> Result<TokenInfo, SessionError> {
        let (status, body) = self.json_call("GET", "/v2/api/ws-token.php", json!({"user": self.user}), Value::Null)?;
        if body.get("ok").and_then(Value::as_bool) != Some(true) {
            return Err(match body.get("error").and_then(Value::as_str).unwrap_or("") {
                "unauthenticated" => SessionError::Unauthenticated,
                "tenant_mismatch" => SessionError::TenantMismatch,
                "ws_not_configured" => SessionError::NotConfigured,
                other => SessionError::Protocol(format!("token refused (HTTP {status}): {other}")),
            });
        }
        let s = |k: &str| body.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let info = TokenInfo { token: s("token"), ws_url: s("ws_url"), expires_at: body.get("expires_at").and_then(Value::as_i64).unwrap_or(0), tenant: s("tenant") };
        if info.token.is_empty() || info.tenant.is_empty() {
            return Err(SessionError::Protocol("token answer without a token or tenant".into()));
        }
        if !relay_address_ok(&self.base, &info.ws_url) {
            return Err(SessionError::Protocol(format!("unusable relay address {:?}", info.ws_url)));
        }
        Ok(info)
    }

    /// Forget the session cookie (local logout).
    pub fn forget(&mut self) {
        self.http.clear_cookies();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;

    /// A canned server on 127.0.0.1: each connection gets the next (status, body, extra header) answer; the requests it saw are collected.
    fn stub(answers: Vec<(u16, &'static str, &'static str)>) -> (String, Arc<std::sync::Mutex<Vec<String>>>) {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        let seen = Arc::new(std::sync::Mutex::new(Vec::new()));
        let seen2 = seen.clone();
        std::thread::spawn(move || {
            let mut answers = answers.into_iter();
            for s in l.incoming().flatten() {
                let mut s = s;
                let mut buf = vec![0u8; 8192];
                let n = s.read(&mut buf).unwrap_or(0);
                seen2.lock().unwrap().push(String::from_utf8_lossy(&buf[..n]).into_owned());
                let (status, body, extra) = answers.next().unwrap_or((500, "{}", ""));
                let _ = write!(s, "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{extra}\r\n{body}", body.len());
            }
        });
        (format!("http://127.0.0.1:{port}"), seen)
    }

    #[test]
    fn construction_requires_a_real_base_url_and_user() {
        assert!(Session::new("ftp://x", "dan").is_err());
        assert!(Session::new("wskar.com", "dan").is_err());
        assert!(Session::new("https://wskar.com", "  ").is_err());
        let s = Session::new(" https://wskar.com/ ", " dan ").unwrap();
        assert_eq!(s.base_url(), "https://wskar.com");
    }

    #[test]
    fn the_pin_travels_in_the_body_not_the_url_and_a_good_login_succeeds() {
        let (base, seen) = stub(vec![(200, r#"{"ok":true,"expires_at":1}"#, "Set-Cookie: PHPSESSID=abc; path=/\r\n")]);
        let s = Session::new(&base, "dan").unwrap();
        s.login("4321").unwrap();
        let req = seen.lock().unwrap()[0].clone();
        assert!(req.starts_with("POST /api/v1/host_commands.php?action=login&user=dan HTTP/1.1"), "{req}");
        assert!(req.ends_with("pin=4321"), "the PIN is the form body: {req}");
        assert!(!req.lines().next().unwrap().contains("4321"), "and not in the request line");
    }

    #[test]
    fn login_failures_are_told_apart() {
        for (status, body, want) in [
            (401, r#"{"ok":false,"error":"invalid_pin"}"#, SessionError::InvalidPin),
            (403, r#"{"ok":false,"error":"lan_only"}"#, SessionError::LanOnly),
        ] {
            let (base, _) = stub(vec![(status, body, "")]);
            assert_eq!(Session::new(&base, "dan").unwrap().login("1"), Err(want));
        }
        let (base, _) = stub(vec![(500, "<html>oops</html>", "")]);
        assert!(matches!(Session::new(&base, "dan").unwrap().login("1"), Err(SessionError::Protocol(_))));
        let (base, _) = stub(vec![(400, r#"{"ok":false,"error":"missing_user"}"#, "")]);
        assert!(matches!(Session::new(&base, "dan").unwrap().login("1"), Err(SessionError::Protocol(m)) if m.contains("missing_user")));
        let s = Session::new("http://127.0.0.1:1", "dan").unwrap();
        assert!(matches!(s.login("1"), Err(SessionError::Network(_))), "nothing listening");
        assert_eq!(s.login("   "), Err(SessionError::InvalidPin), "an empty PIN never leaves the machine");
    }

    #[test]
    fn token_answers_map_to_errors_and_a_good_one_is_validated() {
        let cases = [
            (401, r#"{"ok":false,"error":"unauthenticated"}"#, SessionError::Unauthenticated),
            (403, r#"{"ok":false,"error":"tenant_mismatch"}"#, SessionError::TenantMismatch),
            (503, r#"{"ok":false,"error":"ws_not_configured"}"#, SessionError::NotConfigured),
        ];
        for (status, body, want) in cases {
            let (base, _) = stub(vec![(status, body, "")]);
            assert_eq!(Session::new(&base, "dan").unwrap().token(), Err(want));
        }
        let (base, seen) = stub(vec![(200, r#"{"ok":true,"token":"T.S","ws_url":"ws:\/\/127.0.0.1:9\/relay","expires_at":99,"tenant":"dan"}"#, "")]);
        let info = Session::new(&base, "dan").unwrap().token().unwrap();
        assert_eq!(info, TokenInfo { token: "T.S".into(), ws_url: "ws://127.0.0.1:9/relay".into(), expires_at: 99, tenant: "dan".into() });
        assert!(seen.lock().unwrap()[0].starts_with("GET /v2/api/ws-token.php?user=dan HTTP/1.1"));
        for body in [
            r#"{"ok":true,"token":"","ws_url":"ws://h/relay","tenant":"dan"}"#,
            r#"{"ok":true,"token":"t","ws_url":"ws://h/relay","tenant":""}"#,
            r#"{"ok":true,"token":"t","ws_url":"http://h/relay","tenant":"dan"}"#,
            r#"{"ok":true,"token":"t","ws_url":"","tenant":"dan"}"#,
        ] {
            let (base, _) = stub(vec![(200, Box::leak(body.to_string().into_boxed_str()), "")]);
            assert!(matches!(Session::new(&base, "dan").unwrap().token(), Err(SessionError::Protocol(_))), "{body}");
        }
    }

    #[test]
    fn the_preview_calls_use_the_session_and_map_their_answers() {
        let (base, seen) = stub(vec![
            (200, r#"{"ok":true,"enabled":true,"active":true,"updated_at_ms":9,"playback_state":"playing","image_url":"/api/show-screen/snapshot.php?user=dan&image=1&v=77"}"#, ""),
            (401, r#"{"ok":false,"error":"unauthenticated"}"#, ""),
            (200, "<html>not a picture</html>", ""),
            (404, "gone", ""),
        ]);
        let s = Session::new(&base, "dan").unwrap();
        let m = s.snapshot_meta().unwrap();
        assert!(m.active && m.playing() && m.image_version.as_deref() == Some("77"));
        assert_eq!(s.snapshot_meta(), Err(SessionError::Unauthenticated));
        assert!(matches!(s.snapshot_image("77"), Err(SessionError::Protocol(_))), "an answer that is not a JPEG is refused");
        assert!(matches!(s.snapshot_image("77"), Err(SessionError::Protocol(m)) if m.contains("404")));
        let reqs = seen.lock().unwrap().clone();
        assert!(reqs[0].starts_with("GET /api/show-screen/snapshot.php?user=dan HTTP/1.1"), "{}", reqs[0]);
        assert!(reqs[2].starts_with("GET /api/show-screen/snapshot.php?") && reqs[2].contains("image=1") && reqs[2].contains("v=77"), "{}", reqs[2]);
    }

    #[test]
    fn an_https_server_can_never_hand_out_a_plain_ws_relay() {
        assert!(!relay_address_ok("https://wskar.com", "ws://x/relay"));
        assert!(relay_address_ok("https://wskar.com", "wss://wskar.com/relay"));
        assert!(relay_address_ok("http://127.0.0.1:1", "ws://127.0.0.1:2/relay"));
        assert!(relay_address_ok("http://127.0.0.1:1", "wss://wskar.com/relay"));
        assert!(!relay_address_ok("http://127.0.0.1:1", "http://x/relay"));
        assert!(!relay_address_ok("https://wskar.com", ""));
    }
}
