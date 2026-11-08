//! The INTAKE of `KaraokeApp._automate_karafun_search_and_play` (0.2.18.1.py: from `state = ...` to the `playback search queries` log line): the duplicate guard, the title check,
//! the key/tempo clamps, the entry's pending state and the logged search queries. tools/karafun_intake_parity.py executes the REAL Python statements (read by AST) against the same
//! scenario and compares the result, the entry, the calls and the log. Python's `int()` / `float()` on the value types a caller can pass are modelled; forms that are not
//! (non-ASCII digits, underscores) are declined.
use crate::geometry::py_float;
use crate::ladder::py_repr;
use crate::py_strip;
use serde_json::{json, Map, Value};

pub struct Scenario {
    pub entry: Map<String, Value>,
    pub artist: String,
    pub title: String,
    pub key: Value,
    pub tempo: Value,
    pub request_uid: String,
    pub queries: Vec<String>,
}

pub struct Outcome {
    pub result: Value,
    pub entry: Map<String, Value>,
    pub calls: Vec<Value>,
    pub log: Vec<String>,
    pub declined: Option<String>,
}

/// Python truthiness of a JSON value.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map_or(true, |f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

enum Int {
    Value(f64),
    /// int() raised (ValueError / OverflowError)
    Raised,
    Decline,
}

/// Python `int(v)` for None-free JSON values, returned as a number that is only compared with small bounds (so a huge value is just a huge f64).
fn py_int(v: &Value) -> Int {
    match v {
        Value::Bool(b) => Int::Value(if *b { 1.0 } else { 0.0 }),
        Value::Number(n) => match n.as_f64() {
            Some(f) if f.is_finite() => Int::Value(f.trunc()),
            _ => Int::Raised,
        },
        Value::String(s) => {
            let t = py_strip(s);
            if t.chars().any(|c| !c.is_ascii()) || t.contains('_') {
                return Int::Decline;
            }
            let (neg, digits) = match t.strip_prefix('-') {
                Some(r) => (true, r),
                None => (false, t.strip_prefix('+').unwrap_or(t)),
            };
            if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_digit()) {
                return Int::Raised;
            }
            let trimmed = digits.trim_start_matches('0');
            let mag = if trimmed.is_empty() { 0.0 } else if trimmed.len() > 18 { 1e30 } else { trimmed.parse::<i64>().unwrap_or(0) as f64 };
            Int::Value(if neg { -mag } else { mag })
        }
        _ => Int::Raised,
    }
}

fn clamp(v: f64, lo: f64, hi: f64) -> i64 {
    v.min(hi).max(lo) as i64
}

pub fn run(sc: &Scenario) -> Outcome {
    let mut entry = sc.entry.clone();
    let mut calls: Vec<Value> = vec![];
    let mut log: Vec<String> = vec![];
    let mut declined: Option<String> = None;
    macro_rules! bail {
        ($why:expr) => {{
            declined = Some($why.to_string());
            return Outcome { result: json!({}), entry, calls, log, declined };
        }};
    }
    let state_raw = entry.get("karafun_submission_state").filter(|v| truthy(v)).cloned();
    let state_text = match &state_raw {
        Some(Value::String(s)) => s.clone(),
        None => String::new(),
        _ => bail!("a non-string state"),
    };
    if !state_text.is_ascii() {
        bail!("a non-ASCII state (Python lower() is not modelled)");
    }
    let state = py_strip(&state_text).to_lowercase();
    let pending_at = match entry.get("karafun_pending_at") {
        None => 0.0,
        Some(v) if !truthy(v) => 0.0,
        Some(Value::Bool(_)) => 1.0,
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        Some(Value::String(s)) => match py_float(s) {
            Ok(f) => f,
            Err(true) => bail!("a pending_at text whose float() form is not modelled"),
            Err(false) => {
                let r = match py_repr(s) {
                    Some(r) => r,
                    None => bail!("a character whose Python repr is not modelled"),
                };
                return Outcome { result: json!({"raised": format!("could not convert string to float: {r}")}), entry, calls, log, declined };
            }
        },
        _ => bail!("a pending_at type"),
    };
    let now = 1_700_000_100.0_f64;
    if state == "karafun_queued" || state == "karafun_completed" || (state == "karafun_pending" && (now - pending_at) < 30.0) {
        log.push(format!("[KARAFUN-AUTO] duplicate prevented state={state}"));
        return Outcome { result: json!({"returned": false}), entry, calls, log, declined };
    }
    let (artist, title) = (sc.artist.clone(), sc.title.clone());
    if title.is_empty() {
        entry.insert("karafun_submission_state".into(), json!("karafun_manual_action_required"));
        return Outcome { result: json!({"returned": false}), entry, calls, log, declined };
    }
    let requested_key = if !truthy(&sc.key) {
        0
    } else {
        match py_int(&sc.key) {
            Int::Value(f) => clamp(f, -12.0, 12.0),
            Int::Raised => 0,
            Int::Decline => bail!("a key text whose int() form is not modelled"),
        }
    };
    let tempo_value = if sc.tempo.is_null() { json!(100) } else { sc.tempo.clone() };
    let requested_tempo = match py_int(&tempo_value) {
        Int::Value(f) => clamp(f, 50.0, 200.0),
        Int::Raised => 100,
        Int::Decline => bail!("a tempo text whose int() form is not modelled"),
    };
    let signature = format!("key={requested_key};tempo={requested_tempo}");
    let needs_adjustment = requested_key != 0 || requested_tempo != 100;
    entry.insert("karafun_submission_state".into(), json!("karafun_pending"));
    entry.insert("karafun_pending_at".into(), json!(now));
    entry.remove("karafun_explicit_play_sent");
    calls.push(json!(["set_status", ["launching"], {"message": "Launching KaraFun playback"}]));
    calls.push(json!(["ensure_id", [], {}]));
    let safe_title = py_strip(&title).to_string();
    calls.push(json!(["queries_for_entry", [], {}]));
    let mut queries = sc.queries.clone();
    if queries.is_empty() {
        let joined = [artist.as_str(), title.as_str()].iter().filter(|p| !p.is_empty()).cloned().collect::<Vec<_>>().join(" ");
        queries = vec![py_strip(&joined).to_string()];
    }
    let mut reprs = vec![];
    for q in &queries {
        match py_repr(q) {
            Some(r) => reprs.push(r),
            None => bail!("a character whose Python repr is not modelled"),
        }
    }
    let tr = match py_repr(&title) {
        Some(r) => r,
        None => bail!("a character whose Python repr is not modelled"),
    };
    log.push(format!("[KARAFUN-AUTO] playback search queries=[{}] title={tr}", reprs.join(", ")));
    let result = json!({"returned": null, "requested_key": requested_key, "requested_tempo": requested_tempo, "adjustment_signature": signature, "needs_adjustment": needs_adjustment,
                        "request_uid": sc.request_uid, "safe_title": safe_title, "search_queries": queries});
    Outcome { result, entry, calls, log, declined }
}
