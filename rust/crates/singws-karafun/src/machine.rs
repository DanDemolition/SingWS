//! The small decision methods that sit between the KaraFun worker and the machine (0.2.18.1.py), each proven against the real Python method on the same inputs:
//!  - `_apply_verified_duration` (a scraped result outranks machine metadata; a duration the host set by hand always wins),
//!  - `_karafun_apple_events_preflight` (the answer text and the process id behind its last `|`),
//!  - `_karafun_target_audio_output_name` + `_ensure_karafun_audio_output` (the AirPlay/display guard).
//! The primitives themselves (AppleScript, `pgrep`, `open`, CGEvent) are not here: they are the scripted answers the worker is driven with.
//! tools/karafun_machine_parity.py compares each with the real Python (read by AST).
use crate::ladder::py_repr;
use crate::pure::{normalize_duration_seconds, Duration};
use crate::py_strip;
use serde_json::{json, Value};

fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map_or(true, |f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// Python `str(value or "")` for the text-or-missing values these methods read.
fn text_or_empty(v: Option<&Value>) -> Result<String, String> {
    match v {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(_) => Err("a field that is not text".to_string()),
    }
}

pub struct DurationOut {
    pub applied: bool,
    pub log: Vec<String>,
}

/// `_apply_verified_duration(entry, value, source=...)`; `Err` = a value this port declines to guess.
pub fn apply_verified_duration(entry: &mut Value, value: &Value, source: &Value) -> Result<DurationOut, String> {
    let mut out = DurationOut { applied: false, log: vec![] };
    let Value::Object(e) = entry else { return Ok(out) };
    let seconds = match normalize_duration_seconds(value) {
        Duration::Seconds(s) => s,
        Duration::None => return Ok(out),
        Duration::Decline => return Err("a duration shape Python's isdigit accepts".to_string()),
    };
    let source_text = py_strip(&text_or_empty(Some(source))?).to_string();
    let scraped = source_text == "karafun_result";
    let current = match e.get("duration") {
        None => Duration::None,
        Some(v) => normalize_duration_seconds(v),
    };
    if matches!(current, Duration::Decline) {
        return Err("a duration shape Python's isdigit accepts".to_string());
    }
    let estimated = truthy(e.get("duration_estimated"));
    if let (Duration::Seconds(_), false) = (&current, estimated) {
        let host_set = py_strip(&text_or_empty(e.get("duration_source"))?).to_lowercase().contains("manual");
        if host_set || !scraped {
            return Ok(out);
        }
    }
    if let (true, Duration::Seconds(cur)) = (scraped, &current) {
        if *cur != seconds {
            let title = text_or_empty(e.get("title"))?;
            let Some(r) = py_repr(&title) else { return Err("a character whose Python repr is not modelled".to_string()) };
            out.log.push(format!("[KARAFUN] duration corrected from {cur}s to {seconds}s (was_estimated={}) title={r}", estimated as i32));
        }
    }
    e.insert("duration".into(), json!(seconds));
    e.insert("duration_estimated".into(), json!(false));
    let stored = if truthy(Some(source)) { text_or_empty(Some(source))? } else { "verified_metadata".to_string() };
    e.insert("duration_source".into(), json!(stored));
    out.applied = true;
    Ok(out)
}

/// `_karafun_apple_events_preflight` given what the script answered: (ok, text, process id).
pub fn apple_events_preflight(ok: bool, stdout: &str, stderr: &str) -> Result<(bool, String, i64), String> {
    if ok {
        let last = stdout.split('|').last().unwrap_or("");
        let t = py_strip(last);
        if t.chars().any(|c| !c.is_ascii()) || t.contains('_') {
            return Err("a process id Python's int() may accept in another script".to_string());
        }
        let pid = t.strip_prefix('+').unwrap_or(t);
        let pid = pid.parse::<i64>().unwrap_or(0);
        return Ok((true, if stdout.is_empty() { "AUTHORIZED".to_string() } else { stdout.to_string() }, pid));
    }
    let text = if !stderr.is_empty() { stderr } else if !stdout.is_empty() { stdout } else { "Apple Events authorization failed" };
    Ok((false, text.to_string(), 0))
}

/// `_ensure_karafun_audio_output`: (ok, text, log lines). `explicit`, `follow` and `safe_local` are what the settings and the local-route lookup said.
pub fn ensure_audio_output(explicit: &Value, follow: Option<&Value>, safe_local: &Value) -> Result<(bool, String, Vec<String>), String> {
    let mut log = vec![];
    let explicit = py_strip(&text_or_empty(Some(explicit))?).to_string();
    let target = if !explicit.is_empty() {
        explicit
    } else if follow.map_or(true, |v| truthy(Some(v))) {
        text_or_empty(Some(safe_local))?
    } else {
        String::new()
    };
    // `_karafun_target_audio_output_name` returns the explicit setting stripped, else the local route UNSTRIPPED (or "")
    if target.is_empty() {
        log.push("[KARAFUN-AUDIO] using KaraFun's saved output; SingWS has no named local route to compare".to_string());
        return Ok((true, "KaraFun saved output".to_string(), log));
    }
    if !target.is_ascii() {
        return Err("a non-ASCII output name (casefold/isalnum not modelled)".to_string());
    }
    let key: String = target.to_lowercase().chars().filter(|c| c.is_ascii_alphanumeric()).collect();
    if ["airplay", "apple tv", "bravia", "television", "display"].iter().any(|w| key.contains(w)) {
        let Some(r) = py_repr(&target) else { return Err("a character whose Python repr is not modelled".to_string()) };
        return Ok((false, format!("KaraFun audio output {r} looks like a display/AirPlay route. Choose headphones or a USB interface."), log));
    }
    let Some(r) = py_repr(&target) else { return Err("a character whose Python repr is not modelled".to_string()) };
    log.push(format!("[KARAFUN-AUDIO] trusting saved KaraFun route; expected local output={r} settings_ui_opened=0"));
    Ok((true, target, log))
}

pub fn run(sc: &Value) -> Value {
    let kind = sc["kind"].as_str().unwrap_or("");
    let declined = |why: String| json!({"declined": why});
    match kind {
        "apply_duration" => {
            let mut entry = sc["entry"].clone();
            match apply_verified_duration(&mut entry, &sc["value"], &sc["source"]) {
                Ok(o) => json!({"result": o.applied, "entry": entry, "log": o.log}),
                Err(why) => declined(why),
            }
        }
        "preflight" => {
            let s = |k: &str| sc[k].as_str().unwrap_or("").to_string();
            match apple_events_preflight(sc["ok"].as_bool().unwrap_or(false), &s("stdout"), &s("stderr")) {
                Ok((ok, text, pid)) => json!({"result": [ok, text], "pid": pid}),
                Err(why) => declined(why),
            }
        }
        "audio" => match ensure_audio_output(&sc["explicit"], sc.get("follow"), &sc["safe_local"]) {
            Ok((ok, text, log)) => json!({"result": [ok, text], "log": log}),
            Err(why) => declined(why),
        },
        _ => declined(format!("unknown kind {kind}")),
    }
}
