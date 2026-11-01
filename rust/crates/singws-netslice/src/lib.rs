//! Stage E step 4: small network decisions of the app, rebuilt in Rust from a snapshot of the same inputs (shadow only).
//!
//! `probe`: `probe_network_sync_status(base_url, tenant, api_key, timeout_sec)` in 0.2.18.1.py. Its Python code asks `requests` for up to five URLs and
//! turns the answers into one result dict. Here the host (Python) hands over the inputs plus what each call ANSWERED (a transcript); this crate replays the
//! decision: the requests it would make (url, params, headers, timeout, in order) and the result dict. The shadow compares both with what Python really did.
//! Anything not modelled (non-ASCII text, a transcript that does not fit the plan) answers `{"unsupported": reason}`, which the shadow skips.

use serde_json::{json, Map, Value};

type R<T> = Result<T, String>;

pub fn build(req: &Value) -> Value {
    let out = match req.get("op").and_then(Value::as_str) {
        Some("probe") => probe(req),
        Some(other) => Err(format!("unknown op {other}")),
        None => Err("no op".to_string()),
    };
    out.unwrap_or_else(|e| json!({"unsupported": e}))
}

/// Python's `str.strip()` on ASCII: space, \t \n \v \f \r and \x1c-\x1f.
fn py_space(c: char) -> bool {
    matches!(c, '\t'..='\r' | '\x1c'..='\x1f' | ' ')
}

fn all_ascii(v: &Value) -> bool {
    match v {
        Value::String(s) => s.is_ascii(),
        Value::Array(a) => a.iter().all(all_ascii),
        Value::Object(o) => o.iter().all(|(k, v)| k.is_ascii() && all_ascii(v)),
        _ => true,
    }
}

/// `repr(str)` for ASCII text (what `{body!r}` prints).
fn py_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::from(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (' '..='~').contains(&c) => out.push(c),
            c => out.push_str(&format!("\\x{:02x}", c as u32)),
        }
    }
    out.push(quote);
    out
}

fn text<'a>(req: &'a Value, key: &str) -> R<&'a str> {
    req.get(key).and_then(Value::as_str).ok_or_else(|| format!("{key} is not a string"))
}

fn clip300(s: &str) -> String {
    s.chars().take(300).collect()
}

struct Probe<'a> {
    calls: &'a [Value],
    next: usize,
    planned: Vec<Value>,
    checks: Map<String, Value>,
    errors: Vec<String>,
}

/// What one call answered, in the transcript's words.
enum Outcome {
    Response { status: Value, body: String, json_ok: bool, json_error: String },
    Timeout(String),
    Connection(String, String), // exception type name, text
    Other(String, String), // exception type name, text
}

impl Probe<'_> {
    /// Plan one request (the app must have made exactly this one) and read what it answered.
    fn call(&mut self, url: &str, params: Value, headers: Option<&Value>, timeout: f64) -> R<Outcome> {
        self.planned.push(json!({"url": url, "params": params, "headers": headers.cloned().unwrap_or(Value::Null), "timeout": timeout}));
        let rec = self.calls.get(self.next).ok_or("the transcript has fewer calls than the plan")?;
        self.next += 1;
        let o = rec.get("outcome").ok_or("a call has no outcome")?;
        Ok(match o.get("kind").and_then(Value::as_str) {
            Some("response") => Outcome::Response {
                status: o.get("status").cloned().unwrap_or(Value::Null),
                body: clip300(o.get("body").and_then(Value::as_str).unwrap_or("")),
                json_ok: o.get("json_ok").and_then(Value::as_bool).unwrap_or(false),
                json_error: o.get("json_error").and_then(Value::as_str).unwrap_or("").to_string(),
            },
            Some("timeout") => Outcome::Timeout(o.get("text").and_then(Value::as_str).unwrap_or("").to_string()),
            Some("connection") => Outcome::Connection(
                o.get("type").and_then(Value::as_str).unwrap_or("ConnectionError").to_string(),
                o.get("text").and_then(Value::as_str).unwrap_or("").to_string(),
            ),
            Some("other") => Outcome::Other(
                o.get("type").and_then(Value::as_str).unwrap_or("Exception").to_string(),
                o.get("text").and_then(Value::as_str).unwrap_or("").to_string(),
            ),
            _ => return Err("an outcome has no known kind".into()),
        })
    }

    fn record(&mut self, name: &str, ok: bool, status: Value, message: &str, body: &str, skipped: bool) {
        self.checks.insert(name.to_string(), json!({"ok": ok, "status": status, "message": message, "body": body, "skipped": skipped}));
        if skipped || ok {
            return;
        }
        let shown = match &status {
            Value::Null => "None".to_string(),
            other => other.to_string(),
        };
        let mut detail = format!("{name}: failed status={shown} {message}").trim_matches(py_space).to_string();
        if !body.is_empty() {
            detail.push_str(&format!(" body={}", py_repr(body)));
        }
        self.errors.push(detail);
    }
}

fn int_status(status: &Value) -> Option<i64> {
    status.as_i64()
}

fn probe(req: &Value) -> R<Value> {
    if !all_ascii(req) {
        return Err("non-ASCII text".into());
    }
    let base_in = text(req, "base_url")?.trim_matches(py_space).trim_end_matches('/').to_string();
    let base = if !base_in.is_empty() && !base_in.contains("://") { format!("https://{base_in}") } else { base_in };
    let user = text(req, "tenant")?.trim_matches(py_space).to_string();
    let key = text(req, "api_key")?.trim_matches(py_space).to_string();
    let timeout = match req.get("timeout_sec") {
        Some(Value::Number(n)) => {
            let f = n.as_f64().ok_or("timeout is not finite")?;
            let f = if f == 0.0 { 6.0 } else { f };
            if !f.is_finite() {
                return Err("timeout is not finite".into());
            }
            f.max(1.0)
        }
        _ => return Err("timeout_sec is not a number".into()),
    };
    let now = req.get("now").and_then(Value::as_i64);
    let calls: &[Value] = req.get("calls").and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[]);
    let mut p = Probe { calls, next: 0, planned: Vec::new(), checks: Map::new(), errors: Vec::new() };

    let ep = |path: &str| if base.is_empty() { String::new() } else { format!("{base}{path}") };
    let endpoints = json!({
        "request_intake": ep("/get_requests.php"),
        "accepting": if base.is_empty() { String::new() } else { format!("{base}/tenants/{user}/accepting.txt") },
        "host_controls": ep("/api/v1/host_commands.php"),
        "singer_history": ep("/api/v1/singer_history_sync.php"),
        "rotation": ep("/post_rotation.php"),
        "queue_clear": ep("/api/v1/clear_remote_queue.php"),
        "queue_order": ep("/api/v1/set_remote_request_order.php"),
        "request_modifiers": ep("/api/v1/set_remote_request_modifiers.php"),
    });
    let mut result = Map::new();
    for (k, v) in [("ok", json!(false)), ("connected", json!(false)), ("partial", json!(false)), ("message", json!("")), ("base_url", json!(base)), ("tenant", json!(user)), ("accepting", Value::Null)] {
        result.insert(k.into(), v);
    }

    let finish = |p: Probe, mut result: Map<String, Value>, endpoints: Value| -> Value {
        result.insert("checks".into(), Value::Object(p.checks));
        result.insert("errors".into(), json!(p.errors));
        result.insert("endpoints".into(), endpoints);
        // Python builds the dict with checks / errors / endpoints after "accepting"; the order below is the app's.
        let mut ordered = Map::new();
        for k in ["ok", "connected", "partial", "message", "base_url", "tenant", "accepting", "checks", "errors", "endpoints"] {
            if let Some(v) = result.get(k) {
                ordered.insert(k.into(), v.clone());
            }
        }
        json!({"result": Value::Object(ordered), "calls": p.planned})
    };

    if base.is_empty() || !(base.starts_with("http://") || base.starts_with("https://")) {
        p.record("config", false, Value::Null, "Missing or invalid Base URL", "", false);
        result.insert("message".into(), json!("Missing or invalid Base URL"));
        return Ok(finish(p, result, endpoints));
    }
    if user.is_empty() {
        p.record("config", false, Value::Null, "Missing User ID", "", false);
        result.insert("message".into(), json!("Missing User ID"));
        return Ok(finish(p, result, endpoints));
    }

    let mut headers = Map::new();
    headers.insert("Accept".into(), json!("application/json"));
    headers.insert("User-Agent".into(), json!("SingWS/network-sync"));
    if !key.is_empty() {
        headers.insert("X-API-Key".into(), json!(key));
    }
    let headers = Value::Object(headers);

    // request intake: authoritative for "connected"
    let url = endpoints["request_intake"].as_str().unwrap().to_string();
    match p.call(&url, json!({"user": user, "sync": "1"}), Some(&headers), timeout)? {
        Outcome::Response { status, body, json_ok, json_error } => match int_status(&status) {
            Some(200) => {
                if json_ok {
                    p.record("request_intake", true, status, "request intake reachable", "", false)
                } else {
                    p.record("request_intake", false, status, &format!("invalid JSON: {json_error}"), &body, false)
                }
            }
            Some(401) | Some(403) => p.record("request_intake", false, status, "request intake rejected credentials", &body, false),
            _ => p.record("request_intake", false, status, "request intake failed", &body, false),
        },
        Outcome::Timeout(e) => p.record("request_intake", false, Value::Null, &format!("timeout after {timeout:.1}s: {e}"), "", false),
        Outcome::Connection(_, e) => p.record("request_intake", false, Value::Null, &format!("connection error: {e}"), "", false),
        Outcome::Other(t, e) => p.record("request_intake", false, Value::Null, &format!("{t}: {e}"), "", false),
    }

    // accepting.txt
    let url = endpoints["accepting"].as_str().unwrap().to_string();
    let t5 = timeout.min(5.0);
    let now = now.ok_or("no 'now' for the accepting probe")?;
    match p.call(&url, json!({"_": now}), None, t5)? {
        Outcome::Response { status, body, .. } => {
            if int_status(&status) == Some(200) {
                let txt = body.trim_matches(py_space);
                if txt == "0" || txt == "1" {
                    result.insert("accepting".into(), json!(txt == "1"));
                    p.record("accepting", true, status, &format!("accepting={txt}"), "", false);
                } else {
                    p.record("accepting", false, status, "unexpected accepting.txt body", &body, false);
                }
            } else {
                p.record("accepting", false, status, "accepting state unavailable", &body, false);
            }
        }
        Outcome::Timeout(e) => p.record("accepting", false, Value::Null, &format!("timeout after {t5:.1}s: {e}"), "", false),
        // no separate connection branch here: a ConnectionError (or a subclass such as SSLError) is reported like any other exception, by its own type name
        Outcome::Connection(t, e) => p.record("accepting", false, Value::Null, &format!("{t}: {e}"), "", false),
        Outcome::Other(t, e) => p.record("accepting", false, Value::Null, &format!("{t}: {e}"), "", false),
    }

    let t4 = timeout.min(4.0);
    // host controls (only with an API key)
    if !key.is_empty() {
        let url = endpoints["host_controls"].as_str().unwrap().to_string();
        match p.call(&url, json!({"user": user, "action": "poll"}), Some(&headers), t4)? {
            Outcome::Response { status, body, .. } => {
                let ok = int_status(&status) == Some(200);
                p.record("host_controls", ok, status, "host controls poll", if ok { "" } else { &body }, false);
            }
            Outcome::Timeout(e) => p.record("host_controls", false, Value::Null, &format!("timeout after {t4:.1}s: {e}"), "", false),
            Outcome::Connection(t, e) => p.record("host_controls", false, Value::Null, &format!("{t}: {e}"), "", false),
            Outcome::Other(t, e) => p.record("host_controls", false, Value::Null, &format!("{t}: {e}"), "", false),
        }
    } else {
        p.record("host_controls", true, Value::Null, "API key not configured", "", true);
    }

    // read-only reachability probes: 400/405 still prove the endpoint exists
    for name in ["singer_history", "rotation"] {
        let url = endpoints[name].as_str().unwrap().to_string();
        match p.call(&url, json!({"user": user, "_probe": "1"}), Some(&headers), t4)? {
            Outcome::Response { status, body, .. } => {
                let ok = matches!(int_status(&status), Some(200) | Some(400) | Some(405));
                p.record(name, ok, status, if ok { "endpoint reachable" } else { "endpoint unavailable" }, if ok { "" } else { &body }, false);
            }
            Outcome::Timeout(e) => p.record(name, false, Value::Null, &format!("timeout after {t4:.1}s: {e}"), "", false),
            Outcome::Connection(t, e) => p.record(name, false, Value::Null, &format!("{t}: {e}"), "", false),
            Outcome::Other(t, e) => p.record(name, false, Value::Null, &format!("{t}: {e}"), "", false),
        }
    }

    let request_ok = p.checks.get("request_intake").and_then(|c| c.get("ok")).and_then(Value::as_bool).unwrap_or(false);
    result.insert("connected".into(), json!(request_ok));
    result.insert("ok".into(), json!(request_ok));
    let failures: Vec<String> = p
        .checks
        .iter()
        .filter(|(n, c)| n.as_str() != "request_intake" && !c["ok"].as_bool().unwrap_or(false) && !c["skipped"].as_bool().unwrap_or(false))
        .map(|(n, _)| n.clone())
        .collect();
    result.insert("partial".into(), json!(request_ok && !failures.is_empty()));
    let message = if request_ok && !failures.is_empty() {
        format!("Connected; partial sync issue: {}", failures.join(", "))
    } else if request_ok {
        "Connected / Synced".to_string()
    } else {
        p.errors.first().cloned().unwrap_or_else(|| "Sync failed".to_string())
    };
    result.insert("message".into(), json!(message));
    Ok(finish(p, result, endpoints))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn resp(status: i64, body: &str, json_ok: bool) -> Value {
        json!({"outcome": {"kind": "response", "status": status, "body": body, "json_ok": json_ok, "json_error": "Expecting value: line 1 column 1 (char 0)"}})
    }
    fn exc(kind: &str, ty: &str, text: &str) -> Value {
        json!({"outcome": {"kind": kind, "type": ty, "text": text}})
    }
    fn req(key: &str, calls: Vec<Value>) -> Value {
        json!({"op": "probe", "base_url": "example.test/", "tenant": " wsk ", "api_key": key, "timeout_sec": 6.0, "now": 1700000000, "calls": calls})
    }
    fn good(key: &str) -> Value {
        let mut calls = vec![resp(200, "{}", true), resp(200, "1\n", false)];
        if !key.is_empty() {
            calls.push(resp(200, "", false));
        }
        calls.push(resp(405, "", false));
        calls.push(resp(200, "", false));
        req(key, calls)
    }

    #[test]
    fn a_healthy_server_is_connected_and_synced() {
        let out = build(&good("k"));
        let r = &out["result"];
        assert_eq!(r["message"], "Connected / Synced");
        assert_eq!(r["ok"], true);
        assert_eq!(r["accepting"], true);
        assert_eq!(r["partial"], false);
        assert_eq!(r["base_url"], "https://example.test");
        assert_eq!(r["tenant"], "wsk");
        assert_eq!(out["calls"].as_array().unwrap().len(), 5);
        assert_eq!(out["calls"][0]["headers"]["X-API-Key"], "k");
        assert_eq!(out["calls"][1]["url"], "https://example.test/tenants/wsk/accepting.txt");
        assert_eq!(out["calls"][1]["params"]["_"], 1700000000);
        assert_eq!(out["calls"][1]["timeout"], 5.0);
        assert_eq!(out["calls"][2]["timeout"], 4.0);
        assert_eq!(r["errors"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn without_a_key_host_controls_is_skipped_and_not_a_failure() {
        let out = build(&good(""));
        assert_eq!(out["calls"].as_array().unwrap().len(), 4);
        assert_eq!(out["result"]["checks"]["host_controls"]["skipped"], true);
        assert_eq!(out["result"]["message"], "Connected / Synced");
    }

    #[test]
    fn intake_failure_reports_the_first_error_and_quotes_the_body() {
        let mut calls = vec![resp(403, "it's \"no\"\n", false), resp(404, "", false), resp(200, "", false), resp(200, "", false), resp(200, "", false)];
        calls[0]["outcome"]["body"] = json!("it's \"no\"\n");
        let out = build(&req("k", calls));
        let r = &out["result"];
        assert_eq!(r["ok"], false);
        assert_eq!(r["message"], "request_intake: failed status=403 request intake rejected credentials body='it\\'s \"no\"\\n'".replace("'it\\'s", "'it\\'s"));
    }

    #[test]
    fn partial_when_a_supporting_endpoint_fails() {
        let calls = vec![resp(200, "{}", true), resp(200, "0", false), resp(500, "oops", false), resp(404, "nf", false), resp(200, "", false)];
        let out = build(&req("k", calls));
        let r = &out["result"];
        assert_eq!(r["partial"], true);
        assert_eq!(r["accepting"], false);
        assert_eq!(r["message"], "Connected; partial sync issue: host_controls, singer_history");
        assert_eq!(r["checks"]["host_controls"]["body"], "oops");
        assert_eq!(r["errors"][1], "singer_history: failed status=404 endpoint unavailable body='nf'");
    }

    #[test]
    fn exceptions_are_worded_per_call_site() {
        let calls = vec![exc("timeout", "ReadTimeout", "slow"), exc("connection", "ConnectionError", "refused"), exc("connection", "SSLError", "refused"), exc("other", "ValueError", "bad"), exc("timeout", "T", "t2")];
        let out = build(&req("k", calls));
        let c = &out["result"]["checks"];
        assert_eq!(c["request_intake"]["message"], "timeout after 6.0s: slow");
        assert_eq!(c["accepting"]["message"], "ConnectionError: refused");
        assert_eq!(c["host_controls"]["message"], "SSLError: refused");
        assert_eq!(c["singer_history"]["message"], "ValueError: bad");
        assert_eq!(c["rotation"]["message"], "timeout after 4.0s: t2");
        assert_eq!(out["result"]["errors"][0], "request_intake: failed status=None timeout after 6.0s: slow");
        let calls = vec![exc("connection", "ConnectionError", "refused"), resp(200, "1", false), resp(200, "", false), resp(200, "", false), resp(200, "", false)];
        let out = build(&req("k", calls));
        assert_eq!(out["result"]["checks"]["request_intake"]["message"], "connection error: refused");
    }

    #[test]
    fn invalid_json_on_200_fails_intake() {
        let out = build(&req("", vec![resp(200, "<html>", false), resp(200, "1", false), resp(200, "", false), resp(200, "", false)]));
        assert_eq!(out["result"]["message"], "request_intake: failed status=200 invalid JSON: Expecting value: line 1 column 1 (char 0) body='<html>'");
    }

    #[test]
    fn config_problems_make_no_calls() {
        let mut r = good("k");
        r["base_url"] = json!("  ");
        let out = build(&r);
        assert_eq!(out["result"]["message"], "Missing or invalid Base URL");
        assert_eq!(out["calls"].as_array().unwrap().len(), 0);
        assert_eq!(out["result"]["endpoints"]["accepting"], "");
        let mut r = good("k");
        r["base_url"] = json!("ftp://x");
        assert_eq!(build(&r)["result"]["message"], "Missing or invalid Base URL");
        let mut r = good("k");
        r["tenant"] = json!(" ");
        assert_eq!(build(&r)["result"]["message"], "Missing User ID");
    }

    #[test]
    fn timeout_floor_and_default() {
        let mut r = good("k");
        r["timeout_sec"] = json!(0.2);
        assert_eq!(build(&r)["calls"][0]["timeout"], 1.0);
        r["timeout_sec"] = json!(0);
        assert_eq!(build(&r)["calls"][0]["timeout"], 6.0);
        r["timeout_sec"] = json!(3.0);
        assert_eq!(build(&r)["calls"][1]["timeout"], 3.0);
        assert_eq!(build(&r)["calls"][2]["timeout"], 3.0);
    }

    #[test]
    fn unmodelled_cases_are_unsupported_not_wrong() {
        let mut r = good("k");
        r["tenant"] = json!("wsk\u{e9}");
        assert!(build(&r).get("unsupported").is_some());
        let mut r = good("k");
        r["calls"] = json!([]);
        assert!(build(&r).get("unsupported").is_some());
        assert!(build(&json!({"op": "nope"})).get("unsupported").is_some());
    }

    #[test]
    fn repr_matches_python() {
        assert_eq!(py_repr("a'b"), "\"a'b\"");
        assert_eq!(py_repr("a'b\"c"), "'a\\'b\"c'");
        assert_eq!(py_repr("x\u{1}\n\\"), "'x\\x01\\n\\\\'");
    }

    #[test]
    fn accepting_body_is_stripped_of_whitespace_only() {
        let calls = vec![resp(200, "{}", true), resp(200, " 1\t\n", false), resp(200, "", false), resp(200, "", false), resp(200, "", false)];
        assert_eq!(build(&req("k", calls))["result"]["accepting"], true);
        let calls = vec![resp(200, "{}", true), resp(200, "01", false), resp(200, "", false), resp(200, "", false), resp(200, "", false)];
        let out = build(&req("k", calls));
        assert_eq!(out["result"]["checks"]["accepting"]["message"], "unexpected accepting.txt body");
        assert_eq!(out["result"]["accepting"], Value::Null);
    }
}
