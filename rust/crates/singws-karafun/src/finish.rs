//! `_finish`, the closure at the end of `KaraokeApp._automate_karafun_search_and_play`'s worker (0.2.18.1.py): it turns the flow's result into the entry's final state (queued or
//! failed), the status, the notifications and the failure clean-up. Pure decisions over a recorded list of calls; tools/karafun_finish_parity.py executes the REAL Python closure (read by
//! AST) against the same scenario and compares the entry, the calls and the log lines.
use crate::ladder::py_repr;
use serde_json::{json, Map, Value};

pub struct Scenario {
    /// `int(time.time())` when the entry is submitted
    pub now: i64,
    pub ok: bool,
    pub message: String,
    pub current: bool,
    pub handoff_error: Option<String>,
    pub needs_attention: bool,
    pub apple_err: bool,
    pub ax_err: bool,
    pub capture_session: bool,
    pub adjustment_signature: String,
    pub request_uid: String,
    pub requested_key: i64,
    pub requested_tempo: i64,
    pub title: String,
    pub entry: Map<String, Value>,
}

pub struct Outcome {
    pub entry: Map<String, Value>,
    pub calls: Vec<Value>,
    pub log: Vec<String>,
    pub declined: Option<String>,
}

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Python `str(value or "")` for the entry values this closure compares.
fn py_str_or_empty(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) if n.as_f64().map_or(false, |f| f != 0.0) => n.to_string(),
        Some(Value::Bool(true)) => "True".to_string(),
        _ => String::new(),
    }
}

pub fn run(sc: &Scenario) -> Outcome {
    let mut entry = sc.entry.clone();
    let mut calls: Vec<Value> = vec![];
    let mut log: Vec<String> = vec![];
    let mut declined = None;
    macro_rules! repr {
        ($s:expr) => {
            match py_repr($s) {
                Some(r) => r,
                None => {
                    declined = Some("a character whose Python repr is not modelled".to_string());
                    return Outcome { entry, calls, log, declined };
                }
            }
        };
    }
    if !sc.current {
        log.push("[KARAFUN-AUTO] discarded result from ended playback session".into());
        return Outcome { entry, calls, log, declined };
    }
    if sc.ok {
        entry.insert("karafun_submission_state".into(), json!("karafun_queued"));
        entry.insert("karafun_submitted_at".into(), json!(sc.now));
        entry.insert("karafun_idempotency_key".into(), json!(sc.request_uid));
        entry.remove("karafun_pending_at");
        calls.push(json!(["set_status", ["playing"], {"message": "Playing in KaraFun", "notify": null}]));
        let t = repr!(&sc.title);
        log.push(format!("[KARAFUN-AUTO] search, queue, and play succeeded title={t}"));
        calls.push(json!(["handoff_show_screen", [], {}]));
        if let Some(e) = &sc.handoff_error {
            log.push(format!("[KARAFUN] post-play show-screen handoff failed: {e}"));
        }
        let applied = py_str_or_empty(entry.get("karafun_adjustment_applied")) == sc.adjustment_signature;
        calls.push(json!(["needs_attention", [sc.requested_key, sc.requested_tempo, sc.message, applied], {}]));
        if sc.needs_attention {
            calls.push(json!(["notify", ["KaraFun started, but key/tempo controls need manual adjustment."], {"level": "warning"}]));
        } else {
            calls.push(json!(["notify", [format!("KaraFun queued and started: {}", sc.title)], {"level": "success"}]));
        }
    } else {
        entry.insert("karafun_submission_state".into(), json!("karafun_failed"));
        entry.remove("karafun_pending_at");
        entry.insert("karafun_submission_error".into(), json!(clip(&sc.message, 240)));
        if sc.apple_err {
            calls.push(json!(["apple_events_setup", [], {"notify": true}]));
            calls.push(json!(["set_status", ["permission"], {"message": "apple events setup text", "notify": false}]));
        } else if sc.ax_err {
            calls.push(json!(["accessibility_setup", [], {"notify": true}]));
            calls.push(json!(["set_status", ["permission"], {"message": "accessibility setup text", "notify": false}]));
        } else {
            calls.push(json!(["set_status", ["failed"], {"message": format!("KaraFun automation failed: {}", sc.message), "notify": false}]));
        }
        let t = repr!(&sc.title);
        let e = repr!(&clip(&sc.message, 240));
        log.push(format!("[KARAFUN-AUTO] failed title={t} error={e}"));
        if sc.capture_session {
            calls.push(json!(["activate_host", [], {}]));
            calls.push(json!(["stop_capture", [], {}]));
        }
        if !(sc.apple_err || sc.ax_err) {
            calls.push(json!(["notify", [format!("KaraFun automation failed: {} Press Stop in SingWS to return the song to the queue.", sc.message)], {"level": "error"}]));
        }
    }
    calls.push(json!(["schedule_save", [0], {}]));
    calls.push(json!(["update_queue_display", [], {}]));
    Outcome { entry, calls, log, declined }
}
