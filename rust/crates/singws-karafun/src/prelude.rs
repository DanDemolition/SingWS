//! The part of `KaraokeApp._automate_karafun_search_and_play` between the search ladder and the activation (0.2.18.1.py: from `parts = found.split("|")` to the end of
//! `if needs_adjustment:`): the `found` answer is checked, the verified duration replaces the estimate, the match is logged and, when the song needs a key/tempo change, the
//! result is queued paused and KaraFun's Playback menu is driven. Sequential code against the same scripted `World` as flow.rs; tools/karafun_prelude_parity.py executes the
//! REAL Python statements (read by AST) against the same scripted answers and compares the traces.
use crate::flow::World;
use crate::ladder::py_repr;
use crate::py_strip;
use serde_json::{json, Map, Value};

macro_rules! repr {
    ($w:expr, $s:expr) => {
        match py_repr($s) {
            Some(r) => r,
            None => {
                $w.declined = Some("a character whose Python repr is not modelled".to_string());
                return Err("declined".to_string());
            }
        }
    };
}

pub struct Scenario {
    pub auto_key_tempo: bool,
    pub found: String,
    pub last_error: String,
    pub needs_adjustment: bool,
    pub adjustment_signature: String,
    pub requested_key: i64,
    pub requested_tempo: i64,
    pub selected_query: String,
    pub entry: Map<String, Value>,
    pub end_session_at: Option<i64>,
}

pub struct Outcome {
    pub result: Value,
    pub entry: Map<String, Value>,
    pub world: Value,
}

/// Python's `a or b` for strings.
fn or_str(first: &str, second: &str) -> String {
    if first.is_empty() { second.to_string() } else { first.to_string() }
}

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn prelude(w: &mut World, sc: &Scenario, entry: &mut Map<String, Value>) -> Result<bool, String> {
    let parts: Vec<&str> = sc.found.split('|').collect();
    if parts.len() < 3 || !["FOUND", "TITLE_ONLY", "FIRST"].contains(&parts[0]) {
        return Err(or_str(&sc.last_error, "No acceptable KaraFun match"));
    }
    let raw = if parts.len() > 3 { parts[3] } else { "" };
    w.call("clock_seconds", vec![json!(raw)], json!({}));
    let secs = w.ask("clock").as_f64().unwrap_or(0.0); // None and 0.0 are both falsy
    if secs != 0.0 && secs > 0.0 {
        w.call("apply_verified_duration", vec![json!(secs)], json!({"source": "karafun_result"}));
        if w.ask("apply_duration").as_bool().unwrap_or(false) {
            if parts.len() <= 3 {
                return Err("list index out of range".to_string());
            }
            let r = repr!(w, parts[3]);
            w.diag(format!("[KARAFUN-AUTO] selected duration seconds={} raw={r} replaced_estimate=1", secs.trunc() as i64));
        }
    }
    let qr = repr!(w, &sc.selected_query);
    if parts[0] == "FIRST" {
        w.diag(format!("[KARAFUN-AUTO] selected first visible KaraFun result query={qr}"));
    } else if parts[0] == "TITLE_ONLY" {
        w.diag(format!("[KARAFUN-AUTO] selected KaraFun title without artist confirmation query={qr}"));
    } else {
        w.diag(format!("[KARAFUN-AUTO] selected exact KaraFun title+artist query={qr}"));
    }
    let mut queued = false;
    if sc.needs_adjustment {
        let fx: f64 = parts[1].parse().map_err(|_| "declined".to_string()).map_err(|e| { w.declined = Some("a coordinate Python would reject with its own error text".into()); e })?;
        let fy: f64 = parts[2].parse().map_err(|_| "declined".to_string()).map_err(|e| { w.declined = Some("a coordinate Python would reject with its own error text".into()); e })?;
        let point = (fx.trunc() as i64, fy.trunc() as i64);
        entry.insert("karafun_result_activation_point".into(), json!([point.0, point.1]));
        if !w.mouse_click(point.0, point.1) {
            return Err("Could not select the KaraFun result for key/tempo setup".into());
        }
        w.call("play_now_queue_script", vec![], json!({}));
        let (queued_ok, queued_result, queued_error) = w.session_script("play-now-queue-script", 20);
        if !queued_ok || py_strip(&queued_result) != "QUEUED_PAUSED" {
            let why = clip(&or_str(&queued_error, &or_str(&queued_result, "unknown")), 120);
            w.diag(format!("[KARAFUN-AUTO] could not prepare key/tempo setup; playing without it: {why}"));
            w.call("notify", vec![json!(format!("KaraFun key/tempo could not be set (key {:+}, tempo {}%). Adjust it in KaraFun.", sc.requested_key, sc.requested_tempo))], json!({"level": "warning"}));
            entry.insert("karafun_adjustment_applied".into(), json!(sc.adjustment_signature));
        } else {
            w.diag("[KARAFUN-AUTO] adjustment result queued and paused before Ready".into());
            queued = true;
            entry.insert("karafun_queued_for_adjustment".into(), json!(true));
            let key_steps = sc.requested_key.abs();
            let tempo_steps = (sc.requested_tempo - 100).abs();
            let key_item = if sc.requested_key > 0 { "Key Up" } else { "Key Down" };
            let tempo_item = if sc.requested_tempo > 100 { "Tempo Up" } else { "Tempo Down" };
            w.call("adjust_menu_script", vec![json!(key_item), json!(key_steps), json!(tempo_item), json!(tempo_steps)], json!({}));
            let adjust_lines = format!("adjust:{key_item}:{key_steps}:{tempo_item}:{tempo_steps}");
            let mut auto_adjusted = false;
            if sc.auto_key_tempo {
                for attempt in [1, 2] {
                    w.require()?;
                    let timeout = 10.0 + key_steps as f64 * 0.3 + tempo_steps as f64 * 0.25;
                    let (adjust_ok, adjust_result, adjust_error) = w.session_script_f(&adjust_lines, timeout);
                    let done = py_strip(&adjust_result).to_string();
                    auto_adjusted = adjust_ok && done == format!("ADJUSTED|{key_steps}|{tempo_steps}");
                    let shown = clip(&or_str(&done, &adjust_error), 80);
                    let r = repr!(w, &shown);
                    w.diag(format!("[KARAFUN-AUTO] automatic key/tempo attempt={attempt} key={:+} tempo={}% result={r} ok={}", sc.requested_key, sc.requested_tempo, auto_adjusted as i32));
                    if auto_adjusted {
                        break;
                    }
                    w.sleep(0.5);
                }
            }
            if auto_adjusted {
                entry.insert("karafun_adjustment_applied".into(), json!(sc.adjustment_signature));
                entry.insert("karafun_key_current".into(), json!(sc.requested_key));
                entry.insert("karafun_tempo_current".into(), json!(sc.requested_tempo));
                w.call("ui_thread", vec![], json!({}));
            } else {
                let lead = if sc.auto_key_tempo { "KaraFun key/tempo could not be set automatically " } else { "Automatic KaraFun key/tempo is turned off " };
                w.call("notify", vec![json!(format!("{lead}(key {:+}, tempo {}%). Adjust it in KaraFun.", sc.requested_key, sc.requested_tempo))], json!({"level": "warning"}));
            }
        }
    }
    Ok(queued)
}

pub fn run(sc: &Scenario, answers: &[Value]) -> Outcome {
    let mut w = World { answers, pos: 0, calls: vec![], log: vec![], sleeps: vec![], t: 100.0, requires: 0, end_session_at: sc.end_session_at, order_error: None, declined: None };
    let mut entry = sc.entry.clone();
    let r = prelude(&mut w, sc, &mut entry);
    let result = match r {
        Ok(q) => json!({"ok": true, "queued": q}),
        Err(msg) => json!({"ok": false, "message": msg}),
    };
    let world = json!({"calls": w.calls, "log": w.log, "sleeps": w.sleeps, "final_clock": w.t, "order_error": w.order_error, "declined": w.declined, "consumed": w.pos});
    Outcome { result, entry, world }
}
