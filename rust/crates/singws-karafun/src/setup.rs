//! The SETUP at the top of `KaraokeApp._automate_karafun_search_and_play`'s worker (0.2.18.1.py: from the first `_require_current_session()` to `if not audio_ok:`): stale
//! per-attempt flags are cleared, KaraFun is launched when it is not running, the cold-start wait and the Apple-events and audio-output checks run. Sequential code against the
//! same scripted `World` as flow.rs; tools/karafun_setup_parity.py executes the REAL Python statements (read by AST) against the same scripted answers.
use crate::flow::World;
use serde_json::{json, Map, Value};

const STALE: [&str; 10] = [
    "karafun_native_retry_sent", "karafun_native_first", "karafun_ax_retry_sent", "karafun_display_started_at", "karafun_display_duration", "karafun_paused_at",
    "karafun_queued_for_adjustment", "karafun_kt_dirty", "karafun_kt_busy", "karafun_native_reclick",
];

pub struct Scenario {
    pub entry: Map<String, Value>,
    /// `_last_karafun_launch_error`: None = the attribute does not exist (Python's getattr default is the empty string, so None and "" behave alike).
    pub launch_error: Option<String>,
    pub end_session_at: Option<i64>,
}

pub struct Outcome {
    pub result: Value,
    pub entry: Map<String, Value>,
    pub world: Value,
}

fn pair(w: &mut World, kind: &str) -> (bool, String) {
    let v = w.ask(kind);
    (v[0].as_bool().unwrap_or(false), v[1].as_str().unwrap_or("").to_string())
}

fn setup(w: &mut World, sc: &Scenario, entry: &mut Map<String, Value>) -> Result<bool, String> {
    w.require()?;
    for k in STALE {
        entry.remove(k);
    }
    entry.insert("karafun_key_current".into(), json!(0));
    entry.insert("karafun_tempo_current".into(), json!(100));
    w.call("process_running", vec![], json!({}));
    let was_running = w.ask("running").as_bool().unwrap_or(false);
    if !was_running {
        w.call("ui_thread", vec![], json!({}));
    }
    w.call("open_karafun", vec![], json!({}));
    if !w.ask("open").as_bool().unwrap_or(false) {
        let e = sc.launch_error.clone().unwrap_or_default();
        return Err(if e.is_empty() { "KaraFun could not be opened".to_string() } else { e });
    }
    w.require()?;
    let current = w.ask("session_current").as_bool().unwrap_or(false);
    w.call("wait_until_ready", vec![], json!({"session_current": current, "was_running": was_running}));
    let err = w.ask("wait");
    if let Some(msg) = err.as_str() {
        if !msg.is_empty() {
            return Err(msg.to_string());
        }
    }
    w.require()?;
    w.call("preflight", vec![], json!({}));
    let (permission_ok, permission_error) = pair(w, "preflight");
    if !permission_ok {
        return Err(permission_error);
    }
    w.call("audio_output", vec![], json!({}));
    let (audio_ok, audio_result) = pair(w, "audio");
    if !audio_ok {
        return Err(format!("KaraFun audio safety check failed: {audio_result}"));
    }
    Ok(was_running)
}

pub fn run(sc: &Scenario, answers: &[Value]) -> Outcome {
    let mut w = World { answers, pos: 0, calls: vec![], log: vec![], sleeps: vec![], t: 100.0, requires: 0, end_session_at: sc.end_session_at, order_error: None, declined: None };
    let mut entry = sc.entry.clone();
    let result = match setup(&mut w, sc, &mut entry) {
        Ok(r) => json!({"ok": true, "was_running": r}),
        Err(msg) => json!({"ok": false, "message": msg}),
    };
    let world = json!({"calls": w.calls, "order_error": w.order_error, "declined": w.declined, "consumed": w.pos});
    Outcome { result, entry, world }
}
