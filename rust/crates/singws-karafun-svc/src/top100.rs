//! W5: the decisions of `karafun_top100.py` (the chart refresh: once-a-day rule, state file, what is sent, how the answer is read, how the chart read waits),
//! transcribed from the Python (the oracle). Pure and offline: no network, no file, no browser. The "fewer than 20 songs" refusal is the SERVER's rule
//! (`top100_sync.php`); this side only surfaces the server's reason.
use serde_json::{json, Value};

pub const EVERY_SECONDS: f64 = 20.0 * 3600.0;
pub const READ_TIMEOUT_S: f64 = 150.0;
const SYNC_PATH: &str = "/api/v1/top100_sync.php";

type R<T> = Result<T, String>;

fn truthy(v: &Value) -> bool {
    crate::truthy(v)
}

/// `last_success(path)` from the file's bytes (None = no file): 0.0 for anything unreadable.
pub fn last_success(file: Option<&[u8]>) -> R<f64> {
    let Some(bytes) = file else { return Ok(0.0) };
    let Ok(text) = std::str::from_utf8(bytes) else { return Ok(0.0) };
    if text.contains("NaN") || text.contains("Infinity") {
        return Err("non-standard JSON constants".into());
    }
    let Ok(v) = serde_json::from_str::<Value>(text) else { return Ok(0.0) };
    let Some(obj) = v.as_object() else { return Ok(0.0) };
    match obj.get("last_success") {
        None => Ok(0.0),
        Some(Value::Bool(b)) => Ok(if *b { 1.0 } else { 0.0 }),
        Some(Value::Number(n)) => match n.as_f64() {
            Some(f) if f.is_finite() => Ok(f),
            _ => Err("number out of range".into()),
        },
        Some(Value::String(_)) => Err("string-valued number".into()),
        Some(_) => Ok(0.0),
    }
}

pub fn is_due(now: f64, last: f64, every: f64) -> bool {
    now - last >= every
}

/// Python `repr(float)` for the range where Rust's shortest digits agree; else declined.
fn py_float(x: f64) -> R<String> {
    if !x.is_finite() || (x != 0.0 && (x.abs() < 1e-4 || x.abs() >= 1e15)) {
        return Err("float spelling not modelled".into());
    }
    Ok(format!("{x:?}"))
}

/// `json.dumps(str)` with the default ensure_ascii=True.
fn dump_str(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                let mut buf = [0u16; 2];
                for u in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{:04x}", u));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn dump_number(v: &Value) -> R<String> {
    match v {
        Value::Number(n) if n.is_i64() || n.is_u64() => Ok(n.to_string()),
        Value::Number(n) => py_float(n.as_f64().ok_or("number")?),
        _ => Err("seconds not a number".into()),
    }
}

/// `json.dumps({"source": source, "songs": songs})` for songs shaped `{title, artist, seconds}` (that key order).
pub fn body(source: &str, songs: &[Value]) -> R<String> {
    let mut items = Vec::new();
    for s in songs {
        let o = s.as_object().ok_or("song not an object")?;
        if o.len() != 3 {
            return Err("song keys not modelled".into());
        }
        let t = o.get("title").and_then(|x| x.as_str()).ok_or("title")?;
        let a = o.get("artist").and_then(|x| x.as_str()).ok_or("artist")?;
        let sec = dump_number(o.get("seconds").ok_or("seconds")?)?;
        items.push(format!("{{\"title\": {}, \"artist\": {}, \"seconds\": {}}}", dump_str(t), dump_str(a), sec));
    }
    Ok(format!("{{\"source\": {}, \"songs\": [{}]}}", dump_str(source), items.join(", ")))
}

fn py_str_scalar(v: &Value) -> R<String> {
    match v {
        Value::String(s) => Ok(s.clone()),
        Value::Bool(b) => Ok(if *b { "True" } else { "False" }.into()),
        Value::Number(n) if n.is_i64() || n.is_u64() => Ok(n.to_string()),
        _ => Err("error text spelling not modelled".into()),
    }
}

/// `upload(config, songs, source=, post=)` over a scripted answer. `resp`: {"status": n, "body": text|null} or {"raises": ClassName}.
/// Answer: {"request": {...}|null, "result": [ok, reason]}.
pub fn upload(config: &Value, songs: &[Value], source: &str, resp: &Value) -> R<Value> {
    let get = |k: &str| config.get(k).filter(|v| truthy(v));
    let (Some(base), Some(user), Some(key)) = (get("base"), get("user"), get("key")) else {
        return Ok(json!({"request": null, "result": [false, "not connected to the SingWS server (Settings > Network)"]}));
    };
    let (base, user, key) = (py_str_scalar(base)?, py_str_scalar(user)?, py_str_scalar(key)?);
    let request = json!({"url": format!("{base}{SYNC_PATH}"), "user": user, "body": body(source, songs)?,
                         "headers": {"X-API-Key": key, "Content-Type": "application/json", "Accept": "application/json"}, "timeout": 30});
    if let Some(class) = resp.get("raises").and_then(|c| c.as_str()) {
        return Ok(json!({"request": request, "result": [false, class]}));
    }
    let status = resp.get("status").and_then(|s| s.as_i64()).ok_or("status")?;
    let text = resp.get("body").and_then(|b| b.as_str());
    if text.map_or(false, |t| t.contains("NaN") || t.contains("Infinity")) {
        return Err("non-standard JSON constants".into());
    }
    let parsed: Option<Value> = text.and_then(|t| serde_json::from_str(t).ok()); // None = response.json() raises JSONDecodeError
    // `(response.json() or {})`: a falsy body becomes {}; a truthy non-object has no .get (AttributeError)
    let attr_error = |v: &Value| truthy(v) && !v.is_object();
    let object = |v: &Value| if truthy(v) { v.clone() } else { json!({}) };
    if status == 200 {
        let Some(p) = &parsed else { return Ok(json!({"request": request, "result": [false, "JSONDecodeError"]})) };
        if attr_error(p) {
            return Ok(json!({"request": request, "result": [false, "AttributeError"]}));
        }
        let o = object(p);
        if o.get("ok").map_or(false, truthy) {
            return Ok(json!({"request": request, "result": [true, format!("{} songs sent", songs.len())]}));
        }
    }
    // the inner try: any failure reading the body gives "HTTP <status>"
    let reason = match &parsed {
        Some(p) if !attr_error(p) => {
            let o = object(p);
            match o.get("error") {
                Some(e) if truthy(e) => py_str_scalar(e)?,
                _ => format!("HTTP {status}"),
            }
        }
        _ => format!("HTTP {status}"),
    };
    Ok(json!({"request": request, "result": [false, reason]}))
}

/// `refresh(...)` over scripted collaborators. `req`: force, now, last (the state file's last_success), reader {"songs": [...]} | {"error": text}, sender [ok, reason].
/// Answer: {"result": [ok, message], "remember": when|null, "read": bool, "sent": bool}.
pub fn refresh(req: &Value) -> R<Value> {
    let force = req.get("force").and_then(|x| x.as_bool()).unwrap_or(false);
    let now = req.get("now").and_then(|x| x.as_f64()).unwrap_or(0.0);
    let last = req.get("last").and_then(|x| x.as_f64()).unwrap_or(0.0);
    if !force && !is_due(now, last, EVERY_SECONDS) {
        return Ok(json!({"result": [true, "not due"], "remember": null, "read": false, "sent": false}));
    }
    let reader = req.get("reader").cloned().unwrap_or(json!({}));
    if let Some(e) = reader.get("error").and_then(|e| e.as_str()) {
        if !e.is_ascii() {
            return Err("non-ASCII error text".into());
        }
        return Ok(json!({"result": [false, format!("could not read the chart: {e}")], "remember": null, "read": true, "sent": false}));
    }
    let (ok, reason) = match req.get("sender").and_then(|s| s.as_array()) {
        Some(a) if a.len() == 2 => (a[0].as_bool().unwrap_or(false), a[1].as_str().unwrap_or("").to_string()),
        _ => return Err("sender".into()),
    };
    Ok(json!({"result": [ok, reason], "remember": if ok { json!(now) } else { Value::Null }, "read": true, "sent": true}))
}

/// `read_chart` over a scripted page: `deliveries` [[t, message]] (seconds after the start, sorted), `timeout`. The loop asks the page for the chart every 4 s
/// (the first ask at once) and waits in 0.5 s slices; the first chart answer wins, an error counts only if nothing came before it and it is not "still starting".
/// Answer: {"asks": [t...], "result": {"songs": [...]} | {"error": text}, "end": t}.
pub fn read_chart(req: &Value) -> R<Value> {
    let timeout = req.get("timeout").and_then(|x| x.as_f64()).unwrap_or(READ_TIMEOUT_S);
    let empty = vec![];
    let deliveries: Vec<(f64, &Value)> = req.get("deliveries").and_then(|d| d.as_array()).unwrap_or(&empty).iter().map(|d| (d[0].as_f64().unwrap_or(0.0), &d[1])).collect();
    let mut songs: Option<Value> = None;
    let mut error: Option<String> = None;
    let mut done = false;
    let mut next = 0usize;
    let mut now = 0.0f64;
    let mut asks: Vec<f64> = Vec::new();
    let mut last_asked = -100.0f64;
    let mut deliver_until = |limit: f64, now: &mut f64, songs: &mut Option<Value>, error: &mut Option<String>, done: &mut bool| -> R<()> {
        // `done.wait(0.5)`: messages arriving during the slice are handled in order; the wait ends early once `done` is set
        while next < deliveries.len() && deliveries[next].0 <= limit && !*done {
            let (t, m) = deliveries[next];
            next += 1;
            *now = now.max(t);
            match m.get("type").and_then(|x| x.as_str()) {
                Some("top") => {
                    let s = m.get("songs").cloned().unwrap_or(Value::Null);
                    if truthy(&s) && !s.is_array() {
                        return Err("songs not a list".into());
                    }
                    *songs = Some(if truthy(&s) { s } else { json!([]) });
                    *done = true;
                }
                Some("error") => {
                    let text = match m.get("message") {
                        Some(Value::String(s)) if !s.is_empty() => s.clone(),
                        Some(Value::Null) | None => "KaraFun's player reported an error".to_string(),
                        Some(Value::String(_)) => "KaraFun's player reported an error".to_string(),
                        _ => return Err("error text not a string".into()),
                    };
                    if !(text.contains("still starting") || songs.is_some() || error.is_some()) {
                        *error = Some(text);
                        *done = true;
                    }
                }
                _ => {}
            }
        }
        Ok(())
    };
    while !done && now - 0.0 < timeout {
        if now - last_asked >= 4.0 {
            asks.push(now);
            last_asked = now;
        }
        let limit = now + 0.5;
        deliver_until(limit, &mut now, &mut songs, &mut error, &mut done)?;
        if !done {
            now = limit;
        }
    }
    let end = now;
    let result = match (&songs, &error) {
        (Some(Value::Array(a)), _) if !a.is_empty() => json!({"songs": songs}),
        (_, Some(e)) => json!({"error": e}),
        _ => json!({"error": "KaraFun's chart could not be read in time"}),
    };
    Ok(json!({"asks": asks, "result": result, "end": end}))
}

pub fn handle(req: &Value) -> Value {
    let r: R<Value> = (|| match req.get("op").and_then(|o| o.as_str()).unwrap_or("") {
        "last_success" => {
            let bytes: Option<Vec<u8>> = req.get("hex").and_then(|h| h.as_str()).map(|h| (0..h.len() / 2).map(|i| u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap_or(0)).collect());
            Ok(json!({"v": last_success(bytes.as_deref())?}))
        }
        "is_due" => Ok(json!({"v": is_due(req["now"].as_f64().unwrap_or(0.0), req["last"].as_f64().unwrap_or(0.0), req.get("every").and_then(|x| x.as_f64()).unwrap_or(EVERY_SECONDS))})),
        "remember_text" => Ok(json!({"v": format!("{{\"last_success\": {}}}", py_float(req["when"].as_f64().unwrap_or(0.0))?)})),
        "upload" => {
            let songs: Vec<Value> = req.get("songs").and_then(|s| s.as_array()).cloned().unwrap_or_default();
            upload(req.get("config").unwrap_or(&json!({})), &songs, req.get("source").and_then(|s| s.as_str()).unwrap_or("KaraFun Top US"), req.get("resp").unwrap_or(&json!({})))
        }
        "refresh" => refresh(req),
        "read_chart" => read_chart(req),
        o => Err(format!("unknown op {o}")),
    })();
    match r {
        Ok(v) => v,
        Err(e) => json!({"declined": e}),
    }
}
