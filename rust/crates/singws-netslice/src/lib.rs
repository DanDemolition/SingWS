//! Stage E step 4: small network decisions of the app, rebuilt in Rust from a snapshot of the same inputs (shadow only).
//!
//! `probe`: `probe_network_sync_status(base_url, tenant, api_key, timeout_sec)` in 0.2.18.1.py. Its Python code asks `requests` for up to five URLs and
//! turns the answers into one result dict. Here the host (Python) hands over the inputs plus what each call ANSWERED (a transcript); this crate replays the
//! decision: the requests it would make (url, params, headers, timeout, in order) and the result dict. The shadow compares both with what Python really did.
//! `location`: `KaraokeApp._session_location_payload` (the payload that registers this laptop's session location). The host hands over the app's settings AFTER the
//! call (a detection, when it ran, has already been written there) plus the clock; this crate rebuilds the payload (or `null`) from them.
//! `songbook`: the request `SongbookUploadThread.run` makes (url, form fields, the CSV it writes, headers, timeout), built from the tracks; `support_log`: the support-log
//! upload request of `send_log_package_to_developer`; `multipart`: the exact multipart/form-data body `requests` builds for a boundary. All three BUILD only, never send.
//! Anything not modelled (non-ASCII text, a transcript that does not fit the plan) answers `{"unsupported": reason}`, which the shadow skips.

use serde_json::{json, Map, Value};

type R<T> = Result<T, String>;

pub fn build(req: &Value) -> Value {
    let out = match req.get("op").and_then(Value::as_str) {
        Some("probe") => probe(req),
        Some("location") => location(req),
        Some("songbook") => songbook(req),
        Some("support_log") => support_log(req),
        Some("multipart") => multipart(req),
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

/// `_network_normalize_base_url`: strip, drop trailing slashes, add https:// when there is no scheme.
fn normalize_base(raw: &str) -> String {
    let base = raw.trim_matches(py_space).trim_end_matches('/').to_string();
    if !base.is_empty() && !base.contains("://") { format!("https://{base}") } else { base }
}

/// Python `float(text)` for ASCII text without underscores (those, and anything Rust would read differently, are declined by the caller).
fn py_float(t: &str) -> Option<f64> {
    if t.contains('_') {
        return None;
    }
    t.parse::<f64>().ok()
}

fn setting_str(settings: &Value, key: &str) -> R<String> {
    match settings.get(key) {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(_) => Err(format!("setting {key} is not text")),
    }
}

fn location(req: &Value) -> R<Value> {
    if !all_ascii(req) {
        return Err("non-ASCII text".into());
    }
    let st = req.get("settings").filter(|v| v.is_object()).ok_or("no settings")?;
    let now = req.get("now").and_then(Value::as_i64).ok_or("no now")?;
    let base_url = normalize_base(&setting_str(st, "base_url")?);
    // settings.get("user", settings.get("tenant", "")): a present "user" wins, even when empty
    let tenant = if st.get("user").is_some() { setting_str(st, "user")? } else { setting_str(st, "tenant")? };
    let tenant = tenant.trim_matches(py_space).to_string();
    let api_key = setting_str(st, "api_key")?.trim_matches(py_space).to_string();
    let lat_raw = setting_str(st, "session_location_latitude")?.trim_matches(py_space).to_string();
    let lng_raw = setting_str(st, "session_location_longitude")?.trim_matches(py_space).to_string();
    let mut source = setting_str(st, "session_location_source")?.trim_matches(py_space).to_string();
    if source.is_empty() {
        source = "manual".to_string();
    }
    if let Some(o) = req.get("source_override") {
        // the one local override the app makes without saving it: detection failed and the saved coordinates were used
        if o != &json!("manual_fallback") {
            return Err("unknown source override".into());
        }
        source = "manual_fallback".to_string();
    }
    let accuracy_raw = setting_str(st, "session_location_accuracy_meters")?.trim_matches(py_space).to_string();
    let detected_at = match st.get("session_location_detected_at") {
        None | Some(Value::Null) => 0,
        Some(v) => v.as_i64().ok_or("detected_at is not an integer")?,
    };
    if lat_raw.contains('_') || lng_raw.contains('_') || accuracy_raw.contains('_') {
        return Err("underscore in a number".into());
    }
    let (Some(lat), Some(lng)) = (py_float(&lat_raw), py_float(&lng_raw)) else { return Ok(json!({"payload": null})) };
    if !((-90.0..=90.0).contains(&lat) && (-180.0..=180.0).contains(&lng)) {
        return Ok(json!({"payload": null}));
    }
    let session_id = setting_str(st, "session_location_session_id")?.trim_matches(py_space).to_string();
    if session_id.is_empty() {
        return Err("the app saves a session id before it returns".into());
    }
    let ttl = match st.get("session_location_ttl_minutes") {
        None | Some(Value::Null) => 720,
        Some(v) => v.as_i64().ok_or("ttl is not an integer")?,
    };
    let ttl = if ttl == 0 { 720 } else { ttl };
    let ttl = ttl.min(1440).max(5);
    let accuracy = if accuracy_raw.is_empty() {
        Value::Null
    } else {
        json!(py_float(&accuracy_raw).ok_or("accuracy is not a number")?)
    };
    Ok(json!({"payload": {
        "base_url": base_url,
        "tenant": tenant,
        "api_key": api_key,
        "session_id": session_id,
        "latitude": lat,
        "longitude": lng,
        "location_source": source,
        "accuracy_meters": accuracy,
        "detected_at": if detected_at != 0 { detected_at } else { now },
        "ttl_minutes": ttl,
    }}))
}

/// csv.writer (excel dialect, QUOTE_MINIMAL) for one field.
fn csv_field(f: &str) -> String {
    if f.contains([',', '"', '\r', '\n']) {
        format!("\"{}\"", f.replace('"', "\"\""))
    } else {
        f.to_string()
    }
}

fn songbook(req: &Value) -> R<Value> {
    let base = normalize_base(req.get("base_url").and_then(Value::as_str).ok_or("base_url is not text")?);
    let tenant = req.get("tenant").and_then(Value::as_str).ok_or("tenant is not text")?.trim_matches(py_space).to_string();
    let key = req.get("api_key").and_then(Value::as_str).ok_or("api_key is not text")?.trim_matches(py_space).to_string();
    let tracks = req.get("tracks").and_then(Value::as_array).ok_or("tracks is not a list")?;
    let fold = req.get("fold").and_then(Value::as_object).ok_or("no fold table")?;
    // (stripped, lowercased) of a raw value, as Python computed them
    let folded = |raw: &str| -> R<(String, String)> {
        let f = fold.get(raw).and_then(Value::as_array).ok_or("a value has no fold entry")?;
        Ok((f.first().and_then(Value::as_str).ok_or("bad fold entry")?.to_string(), f.get(1).and_then(Value::as_str).ok_or("bad fold entry")?.to_string()))
    };
    let mut seen = std::collections::HashSet::new();
    let mut rows: Vec<(String, String, String, String)> = Vec::new(); // artist, title, lower artist, lower title
    for t in tracks {
        let Some(t) = t.as_object() else { continue }; // not a dict: skipped
        let get = |k: &str| -> R<String> {
            match t.get(k) {
                None => Ok(String::new()),
                Some(Value::String(s)) => Ok(s.clone()),
                Some(_) => Err(format!("track {k} is not text")),
            }
        };
        let (artist, la) = folded(&get("artist")?)?;
        let (title, lt) = folded(&get("title")?)?;
        if artist.is_empty() || title.is_empty() {
            continue;
        }
        if !seen.insert((la.clone(), lt.clone())) {
            continue;
        }
        rows.push((artist, title, la, lt));
    }
    rows.sort_by(|a, b| (&a.2, &a.3).cmp(&(&b.2, &b.3)));
    if rows.is_empty() {
        return Err("no rows from the tracks (the app would read its song index)".into());
    }
    let mut csv = String::from("Artist,Title\r\n");
    for (a, t, _, _) in &rows {
        if a.contains('\0') || t.contains('\0') {
            return Err("NUL in a field".into());
        }
        csv.push_str(&format!("{},{}\r\n", csv_field(a), csv_field(t)));
    }
    let headers = if key.is_empty() { json!({}) } else { json!({"X-API-Key": key}) };
    Ok(json!({
        "url": format!("{base}/api/v1/upload_songbook.php"),
        "data": {"user": tenant, "tenant": tenant},
        "files": {"songbook_csv": ["songbook.csv", csv, "text/csv"]},
        "headers": headers,
        "timeout": 180,
    }))
}

fn opt_text(v: Option<&Value>) -> R<String> {
    match v {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(_) => Err("a setting is not text".into()),
    }
}

fn support_log(req: &Value) -> R<Value> {
    if !all_ascii(req.get("settings").unwrap_or(&Value::Null)) {
        return Err("non-ASCII settings".into());
    }
    let st = req.get("settings").filter(|v| v.is_object()).ok_or("no settings")?;
    let base = normalize_base(&opt_text(st.get("base_url"))?);
    let user = if st.get("user").is_some() { opt_text(st.get("user"))? } else { opt_text(st.get("tenant"))? };
    let user = user.trim_matches(py_space).to_string();
    let key = opt_text(st.get("api_key"))?.trim_matches(py_space).to_string();
    if base.is_empty() || user.is_empty() || key.is_empty() {
        return Ok(json!({"request": null, "refusal": "not connected"}));
    }
    let package = req.get("package").and_then(Value::as_object).ok_or("no package")?;
    let name = package.get("name").and_then(Value::as_str).ok_or("package name")?;
    let text = |k: &str| req.get(k).and_then(Value::as_str).map(str::to_string).ok_or_else(|| format!("{k} is not text"));
    Ok(json!({"request": {
        "url": format!("{base}/api/v1/support_logs.php"),
        "data": {"user": user, "reason": text("reason")?, "app_version": text("app_version")?, "build_id": text("app_version")?, "platform": text("platform")?, "session_window": text("window")?},
        "files": {"logs": [name, {"len": package.get("len").cloned().unwrap_or(Value::Null), "sha256": package.get("sha256").cloned().unwrap_or(Value::Null)}, "application/zip"]},
        "headers": {"X-API-Key": key, "Accept": "application/json"},
        "timeout": 30,
    }}))
}

/// The multipart/form-data body `requests` (urllib3) builds: the data fields first, then the files, each part `--boundary`, headers, a blank line, the content.
fn multipart(req: &Value) -> R<Value> {
    let boundary = req.get("boundary").and_then(Value::as_str).ok_or("no boundary")?;
    let plain = |s: &str| s.bytes().all(|b| (0x20..0x7f).contains(&b) && b != b'"' && b != b'\\');
    let mut body: Vec<u8> = Vec::new();
    for f in req.get("fields").and_then(Value::as_array).ok_or("no fields")? {
        let (n, v) = (f.get(0).and_then(Value::as_str).ok_or("field name")?, f.get(1).and_then(Value::as_str).ok_or("field value")?);
        if !plain(n) {
            return Err("field name needs escaping".into());
        }
        body.extend(format!("--{boundary}\r\nContent-Disposition: form-data; name=\"{n}\"\r\n\r\n").as_bytes());
        body.extend(v.as_bytes());
        body.extend(b"\r\n");
    }
    for f in req.get("files").and_then(Value::as_array).ok_or("no files")? {
        let (n, fname, ctype) = (f.get(0).and_then(Value::as_str).ok_or("n")?, f.get(1).and_then(Value::as_str).ok_or("fn")?, f.get(2).and_then(Value::as_str).ok_or("ct")?);
        if !plain(n) || !plain(fname) {
            return Err("file name needs escaping".into());
        }
        let hex = f.get(3).and_then(Value::as_str).ok_or("content_hex")?;
        if hex.len() % 2 != 0 || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("bad hex".into());
        }
        body.extend(format!("--{boundary}\r\nContent-Disposition: form-data; name=\"{n}\"; filename=\"{fname}\"\r\nContent-Type: {ctype}\r\n\r\n").as_bytes());
        body.extend((0..hex.len()).step_by(2).map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap()));
        body.extend(b"\r\n");
    }
    body.extend(format!("--{boundary}--\r\n").as_bytes());
    Ok(json!({"body_hex": body.iter().map(|b| format!("{b:02x}")).collect::<String>()}))
}

fn int_status(status: &Value) -> Option<i64> {
    status.as_i64()
}

fn probe(req: &Value) -> R<Value> {
    if !all_ascii(req) {
        return Err("non-ASCII text".into());
    }
    let base = normalize_base(text(req, "base_url")?);
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
    fn app_whitespace_includes_the_ascii_separators() {
        let mut r = good("k");
        r["tenant"] = json!("\u{1c}wsk\u{1f}");
        assert_eq!(build(&r)["result"]["tenant"], "wsk");
        r["tenant"] = json!("a");
        assert_eq!(build(&r)["result"]["tenant"], "a");
        assert_eq!(build(&r)["calls"].as_array().unwrap().len(), 5);
    }

    #[test]
    fn bodies_are_cut_to_300_characters_and_tilde_is_printable() {
        let long = "x".repeat(400);
        let calls = vec![resp(500, &long, false), resp(200, "1", false), resp(200, "", false), resp(200, "", false), resp(200, "", false)];
        let out = build(&req("k", calls));
        assert_eq!(out["result"]["checks"]["request_intake"]["body"].as_str().unwrap().len(), 300);
        assert_eq!(py_repr("a~b"), "'a~b'");
        assert_eq!(py_repr("a\u{7f}"), "'a\\x7f'");
    }

    #[test]
    fn a_missing_now_is_unsupported() {
        let mut r = good("k");
        r.as_object_mut().unwrap().remove("now");
        assert!(build(&r).get("unsupported").is_some());
    }

    fn loc(extra: Value) -> Value {
        let mut st = json!({"base_url": " example.test/ ", "user": " wsk ", "api_key": " k ", "session_location_latitude": "40.5", "session_location_longitude": "-73.25",
                            "session_location_source": "auto_detected", "session_location_accuracy_meters": "12.5", "session_location_detected_at": 1700000000,
                            "session_location_session_id": "abc123", "session_location_ttl_minutes": 60});
        for (k, v) in extra.as_object().unwrap() {
            st[k] = v.clone();
        }
        build(&json!({"op": "location", "settings": st, "now": 1800000000}))
    }

    #[test]
    fn the_location_payload_is_built_from_the_saved_settings() {
        let p = &loc(json!({}))["payload"];
        assert_eq!(p["base_url"], "https://example.test");
        assert_eq!(p["tenant"], "wsk");
        assert_eq!(p["api_key"], "k");
        assert_eq!(p["latitude"], 40.5);
        assert_eq!(p["longitude"], -73.25);
        assert_eq!(p["location_source"], "auto_detected");
        assert_eq!(p["accuracy_meters"], 12.5);
        assert_eq!(p["detected_at"], 1700000000);
        assert_eq!(p["ttl_minutes"], 60);
        let keys: Vec<&String> = p.as_object().unwrap().keys().collect();
        assert_eq!(keys, ["base_url", "tenant", "api_key", "session_id", "latitude", "longitude", "location_source", "accuracy_meters", "detected_at", "ttl_minutes"]);
    }

    #[test]
    fn location_defaults_clamps_and_rejections() {
        assert_eq!(loc(json!({"session_location_detected_at": 0}))["payload"]["detected_at"], 1800000000);
        assert_eq!(loc(json!({"session_location_detected_at": null}))["payload"]["detected_at"], 1800000000);
        assert_eq!(loc(json!({"session_location_source": "  "}))["payload"]["location_source"], "manual");
        assert_eq!(loc(json!({"session_location_accuracy_meters": ""}))["payload"]["accuracy_meters"], Value::Null);
        assert_eq!(loc(json!({"session_location_ttl_minutes": 0}))["payload"]["ttl_minutes"], 720);
        assert_eq!(loc(json!({"session_location_ttl_minutes": 2}))["payload"]["ttl_minutes"], 5);
        assert_eq!(loc(json!({"session_location_ttl_minutes": 99999}))["payload"]["ttl_minutes"], 1440);
        assert_eq!(loc(json!({"session_location_latitude": "90"}))["payload"]["latitude"], 90.0);
        assert_eq!(loc(json!({"session_location_latitude": "90.000001"}))["payload"], Value::Null);
        assert_eq!(loc(json!({"session_location_longitude": "-180"}))["payload"]["longitude"], -180.0);
        assert_eq!(loc(json!({"session_location_longitude": "180.5"}))["payload"], Value::Null);
        assert_eq!(loc(json!({"session_location_latitude": "nan"}))["payload"], Value::Null);
        assert_eq!(loc(json!({"session_location_latitude": ""}))["payload"], Value::Null);
        assert_eq!(loc(json!({"session_location_longitude": "x"}))["payload"], Value::Null);
        assert_eq!(loc(json!({"user": "", "tenant": "t"}))["payload"]["tenant"], "");
        let mut st = json!({"tenant": "t2", "session_location_latitude": "1", "session_location_longitude": "2", "session_location_session_id": "s"});
        st["base_url"] = json!("b.test");
        assert_eq!(build(&json!({"op": "location", "settings": st, "now": 5}))["payload"]["tenant"], "t2");
        assert!(loc(json!({"session_location_session_id": " "})).get("unsupported").is_some());
        assert!(loc(json!({"session_location_latitude": "1_0"})).get("unsupported").is_some());
        assert!(loc(json!({"session_location_ttl_minutes": "60"})).get("unsupported").is_some());
        assert!(loc(json!({"session_location_latitude": 5})).get("unsupported").is_some());
    }

    #[test]
    fn a_failed_detection_is_the_only_source_override() {
        let st = json!({"session_location_latitude": "1", "session_location_longitude": "2", "session_location_session_id": "s"});
        let ok = build(&json!({"op": "location", "settings": st, "now": 5, "source_override": "manual_fallback"}));
        assert_eq!(ok["payload"]["location_source"], "manual_fallback");
        assert!(build(&json!({"op": "location", "settings": st, "now": 5, "source_override": "other"})).get("unsupported").is_some());
    }

    fn tracks_req(tracks: Value, fold: Value, key: &str) -> Value {
        json!({"op": "songbook", "base_url": " b.test/ ", "tenant": " wsk ", "api_key": key, "tracks": tracks, "fold": fold})
    }

    #[test]
    fn songbook_rows_are_deduped_sorted_and_csv_quoted() {
        let tracks = json!([{"artist": "Zed", "title": "A, B"}, {"artist": " abba ", "title": "Hi \"x\""}, {"artist": "ABBA", "title": "hi \"X\""}, "skip", {"artist": "", "title": "t"}, {"title": "no artist"}, {"artist": "Beyonce", "title": "Line\nBreak"}]);
        let fold = json!({"Zed": ["Zed", "zed"], "A, B": ["A, B", "a, b"], " abba ": ["abba", "abba"], "Hi \"x\"": ["Hi \"x\"", "hi \"x\""], "ABBA": ["ABBA", "abba"], "hi \"X\"": ["hi \"X\"", "hi \"x\""],
                          "": ["", ""], "t": ["t", "t"], "no artist": ["no artist", "no artist"], "Beyonce": ["Beyonce", "beyonce"], "Line\nBreak": ["Line\nBreak", "line\nbreak"]});
        let out = build(&tracks_req(tracks, fold, " k "));
        assert_eq!(out["url"], "https://b.test/api/v1/upload_songbook.php");
        assert_eq!(out["data"], json!({"user": "wsk", "tenant": "wsk"}));
        assert_eq!(out["headers"], json!({"X-API-Key": "k"}));
        assert_eq!(out["timeout"], 180);
        assert_eq!(out["files"]["songbook_csv"][2], "text/csv");
        assert_eq!(out["files"]["songbook_csv"][1], "Artist,Title\r\nabba,\"Hi \"\"x\"\"\"\r\nBeyonce,\"Line\nBreak\"\r\nZed,\"A, B\"\r\n");
        assert_eq!(build(&tracks_req(json!([{"artist": "Zed", "title": "t"}]), json!({"Zed": ["Zed", "zed"], "t": ["t", "t"]}), ""))["headers"], json!({}));
    }

    #[test]
    fn songbook_declines_what_it_cannot_model() {
        assert!(build(&tracks_req(json!([]), json!({}), "k")).get("unsupported").is_some());
        assert!(build(&tracks_req(json!([{"artist": 5, "title": "t"}]), json!({}), "k")).get("unsupported").is_some());
        assert!(build(&tracks_req(json!([{"artist": "a", "title": "t"}]), json!({}), "k")).get("unsupported").is_some());
    }

    fn log_req(settings: Value) -> Value {
        json!({"op": "support_log", "settings": settings, "reason": "manual", "app_version": "1.0", "platform": "Darwin arm64 14.0", "window": "w",
               "package": {"name": "p.zip", "len": 5, "sha256": "ab"}})
    }

    #[test]
    fn support_log_request_and_refusal() {
        let out = build(&log_req(json!({"base_url": "b.test/", "tenant": " wsk ", "api_key": " k "})));
        let r = &out["request"];
        assert_eq!(r["url"], "https://b.test/api/v1/support_logs.php");
        assert_eq!(r["data"], json!({"user": "wsk", "reason": "manual", "app_version": "1.0", "build_id": "1.0", "platform": "Darwin arm64 14.0", "session_window": "w"}));
        assert_eq!(r["files"]["logs"], json!(["p.zip", {"len": 5, "sha256": "ab"}, "application/zip"]));
        assert_eq!(r["headers"], json!({"X-API-Key": "k", "Accept": "application/json"}));
        assert_eq!(r["timeout"], 30);
        assert_eq!(build(&log_req(json!({"base_url": "b.test", "api_key": "k"})))["request"], Value::Null);
        assert_eq!(build(&log_req(json!({"base_url": "", "user": "u", "api_key": "k"})))["request"], Value::Null);
        assert_eq!(build(&log_req(json!({"base_url": "b", "user": "u", "api_key": " "})))["request"], Value::Null);
        assert_eq!(build(&log_req(json!({"base_url": "b", "user": "", "tenant": "t", "api_key": "k"})))["request"], Value::Null);
    }

    #[test]
    fn multipart_layout() {
        let out = build(&json!({"op": "multipart", "boundary": "BB", "fields": [["user", "u"], ["a", ""]], "files": [["f", "x.csv", "text/csv", "0a0d"]]}));
        let hex = out["body_hex"].as_str().unwrap();
        let bytes: Vec<u8> = (0..hex.len()).step_by(2).map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap()).collect();
        let text = String::from_utf8_lossy(&bytes).to_string();
        assert_eq!(text, "--BB\r\nContent-Disposition: form-data; name=\"user\"\r\n\r\nu\r\n--BB\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\n\r\n--BB\r\nContent-Disposition: form-data; name=\"f\"; filename=\"x.csv\"\r\nContent-Type: text/csv\r\n\r\n\n\r\r\n--BB--\r\n");
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
