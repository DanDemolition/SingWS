//! Four KaraFun automation methods of 0.2.18.1.py as sequential code against a scripted world: `_karafun_wait_until_ready`, `_karafun_press_play_control`,
//! `_karafun_playback_menu_state` and `_karafun_activate_result_for_playback`. tools/karafun_methods_parity.py runs the REAL methods (copied out of the class by AST) against the same
//! scripted answers and compares the return value or raised error, every call, log line, sleep and the entry's final fields.
use crate::ladder::py_repr;
use crate::py_strip;
use serde_json::{json, Map, Value};

pub struct World<'a> {
    pub answers: &'a [Value],
    pub pos: usize,
    pub calls: Vec<Value>,
    pub log: Vec<String>,
    pub sleeps: Vec<f64>,
    pub t: f64,
    pub order_error: Option<String>,
    pub declined: Option<String>,
}

impl<'a> World<'a> {
    fn ask(&mut self, kind: &str) -> Value {
        let a = self.answers.get(self.pos).cloned();
        self.pos += 1;
        match a {
            Some(a) if a["kind"] == kind => a["v"].clone(),
            Some(a) => {
                self.order_error.get_or_insert(format!("expected {kind} got {}", a["kind"]));
                Value::Null
            }
            None => {
                self.order_error.get_or_insert(format!("answers exhausted at {kind}"));
                Value::Null
            }
        }
    }
    fn call(&mut self, name: &str, args: Vec<Value>, kw: Value) {
        self.calls.push(json!([name, args, kw]));
    }
    fn sleep(&mut self, s: f64) {
        self.sleeps.push(s);
        self.t += s;
    }
    fn diag(&mut self, s: String) {
        self.log.push(s);
    }
    /// `_run_karafun_applescript_sync(script, timeout=...)`: (ok, text, error).
    fn osa(&mut self, script: Value, timeout: i64) -> (bool, String, String) {
        self.call("osa", vec![script], json!({"timeout": timeout}));
        let v = self.ask("osa");
        (v[0].as_bool().unwrap_or(false), v[1].as_str().unwrap_or("").to_string(), v[2].as_str().unwrap_or("").to_string())
    }
    fn mouse_click(&mut self, x: i64, y: i64) -> bool {
        self.call("mouse_click", vec![json!(x), json!(y)], json!({"clicks": 1}));
        self.ask("mouse_click").as_bool().unwrap_or(false)
    }
    fn native_double_click(&mut self, x: i64, y: i64) -> bool {
        self.call("native_double_click", vec![json!(x), json!(y)], json!({}));
        self.ask("double_click").as_bool().unwrap_or(false)
    }
}

pub struct Outcome {
    pub raised: Option<String>,
    pub value: Value,
    pub entry: Value,
}

fn or_str(first: &str, second: &str) -> String {
    if first.is_empty() { second.to_string() } else { first.to_string() }
}

/// `_karafun_wait_until_ready(was_running=..., session_is_current=..., timeout=...)`
pub fn wait_until_ready(w: &mut World, was_running: bool, timeout: f64) -> Result<(), String> {
    let started = w.t;
    let deadline = started + timeout;
    let mut last_state = String::new();
    loop {
        let current = w.ask("session_current").as_bool().unwrap_or(false);
        if !current {
            return Err("KaraFun launch cancelled: playback session ended".into());
        }
        w.call("main_window_state", vec![], json!({}));
        let state = w.ask("window_state").as_str().unwrap_or("").to_string();
        if state == "ERROR" {
            w.diag("[KARAFUN] readiness check unavailable; continuing to the permission check".into());
            return Ok(());
        }
        if state == "READY" {
            if !was_running {
                w.sleep(2.0);
            }
            let msg = format!("[KARAFUN] ready after {:.1}s cold={}", w.t - started, i32::from(!was_running));
            w.diag(msg);
            return Ok(());
        }
        if state != last_state {
            let r = match py_repr(&state) {
                Some(_) => state.clone(),
                None => {
                    w.declined = Some("unmodelled character".into());
                    return Err("declined".into());
                }
            };
            w.diag(format!("[KARAFUN] waiting for KaraFun to finish starting ({r})"));
            last_state = state;
        }
        if w.t >= deadline {
            return Err(format!("KaraFun did not finish starting within {} seconds. Open KaraFun, make sure it is signed in, then start the song again.", timeout.trunc() as i64));
        }
        w.sleep(0.5);
    }
}

/// `_karafun_press_play_control()` -> (ok, error)
pub fn press_play_control(w: &mut World) -> (bool, String) {
    let (ok, played, err) = w.osa(json!("press-play-script"), 10);
    if !ok {
        return (false, or_str(&err, "KaraFun play automation failed"));
    }
    let stripped = py_strip(&played);
    if stripped == "ALREADY_PLAYING" || stripped == "MENU_PLAY" {
        w.diag(format!("[KARAFUN-AUTO] playback menu action={played}"));
        return (true, String::new());
    }
    let parts: Vec<&str> = played.split('|').collect();
    if parts.len() != 3 || !(parts[0] == "PLAY_NEXT" || parts[0] == "PLAY") {
        let last = played.splitn(2, '|').last().unwrap_or("");
        return (false, or_str(last, "KaraFun did not expose a play control"));
    }
    w.diag(format!("[KARAFUN-AUTO] pressing KaraFun control mode={} x={} y={}", parts[0], parts[1], parts[2]));
    let (fx, fy) = match (parts[1].trim().parse::<f64>(), parts[2].trim().parse::<f64>()) {
        (Ok(a), Ok(b)) if a.is_finite() && b.is_finite() => (a, b),
        _ => {
            w.declined = Some("a coordinate Python would reject with its own error text".into());
            return (false, "declined".into());
        }
    };
    if !w.mouse_click(fx.trunc() as i64, fy.trunc() as i64) {
        return (false, "Could not click the KaraFun play control".into());
    }
    (true, String::new())
}

/// `_karafun_playback_menu_state()` -> PLAYING | IDLE | UNKNOWN
pub fn playback_menu_state(w: &mut World) -> String {
    let (ok, result, _e) = w.osa(json!(["menu-state-script"]), 4);
    if !result.is_ascii() {
        w.declined = Some("non-ASCII state text".into());
        return "UNKNOWN".into();
    }
    let state = if ok { py_strip(&result).to_uppercase() } else { "UNKNOWN".into() };
    if state == "PLAYING" || state == "IDLE" { state } else { "UNKNOWN".into() }
}

pub struct ActivateInput {
    pub active: Value,
    pub native_double_click: bool,
    pub change_session_on_resolve: bool,
    pub point: Option<(i64, i64)>,
    pub reuse_point: bool,
}

/// `_karafun_activate_result_for_playback(activation_point, reuse_point)` -> (ok, error); the active entry (as the app holds it) is returned for comparison.
pub fn activate_result_for_playback(w: &mut World, inp: &ActivateInput) -> (bool, String, Value) {
    let mut active = inp.active.clone();
    let Some(active_map) = active.as_object().cloned() else {
        return (false, "KaraFun playback session is no longer active".into(), Value::Null);
    };
    let Some(entry0) = active_map.get("entry").and_then(|e| e.as_object()).cloned() else {
        return (false, "KaraFun song is unavailable".into(), Value::Null);
    };
    let mut entry: Map<String, Value> = entry0;
    let artist = entry.get("artist").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let title = entry.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if title.is_empty() {
        return (false, "KaraFun song title is unavailable".into(), Value::Object(entry));
    }
    let (x, y);
    if inp.reuse_point && inp.point.is_some() {
        let p = inp.point.unwrap();
        x = p.0;
        y = p.1;
    } else {
        let (ok, result, err) = w.osa(json!(format!("resolve:{title}:{artist}:True:True:")), 8);
        if inp.change_session_on_resolve {
            active = json!({"entry": {}});
            return (false, "KaraFun playback session changed during activation".into(), json!({}));
        }
        let stripped = py_strip(&result);
        let parts: Vec<&str> = stripped.split('|').collect();
        if !ok || parts.len() < 3 || parts[0] != "FOUND" {
            return (false, or_str(&err, "Matched KaraFun song could not be verified after screen handoff"), Value::Object(entry));
        }
        let parse = |s: &str| -> Option<i64> {
            let t = py_strip(s);
            if t.chars().any(|c| !c.is_ascii()) || t.contains('_') {
                return None;
            }
            t.parse::<f64>().ok().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)
        };
        match (parse(parts[1]), parse(parts[2])) {
            (Some(a), Some(b)) => {
                x = a;
                y = b;
            }
            _ => {
                let looks_nonascii = parts[1].chars().chain(parts[2].chars()).any(|c| !c.is_ascii() || c == '_');
                if looks_nonascii {
                    w.declined = Some("a number form Rust does not model".into());
                }
                return (false, "KaraFun result position is invalid".into(), Value::Object(entry));
            }
        }
    }
    let _ = active;
    entry.insert("karafun_result_activation_point".into(), json!([x, y]));
    if inp.native_double_click {
        w.call("raise_results", vec![], json!({}));
        w.sleep(0.35);
        w.call("frontmost", vec![], json!({}));
        let front = w.ask("frontmost").as_str().unwrap_or("").to_string();
        let Some(front_repr) = py_repr(&front) else {
            w.declined = Some("unmodelled character in a repr".into());
            return (false, "declined".into(), Value::Object(entry));
        };
        w.diag(format!("[KARAFUN-AUTO] click context frontmost={front_repr} x={x} y={y}"));
        if w.native_double_click(x, y) {
            entry.insert("karafun_native_retry_sent".into(), json!(true));
            entry.insert("karafun_native_first".into(), json!(true));
            w.diag(format!("[KARAFUN-AUTO] result double-click sent natively (learned) x={x} y={y}"));
            return (true, String::new(), Value::Object(entry));
        }
    }
    let (click_ok, click_result, click_error) = w.osa(json!(format!("dbl:{x}:{y}")), 5);
    if !click_ok || py_strip(&click_result) != "DOUBLE_CLICKED" {
        if !w.native_double_click(x, y) {
            return (false, or_str(&click_error, "Could not double-click the KaraFun result"), Value::Object(entry));
        }
        w.diag("[KARAFUN-AUTO] accessibility double-click failed; native fallback sent".into());
    } else {
        w.diag(format!("[KARAFUN-AUTO] result double-click sent through accessibility x={x} y={y}"));
    }
    (true, String::new(), Value::Object(entry))
}

pub fn run(method: &str, sc: &Value, answers: &[Value]) -> Value {
    let mut w = World { answers, pos: 0, calls: vec![], log: vec![], sleeps: vec![], t: 100.0, order_error: None, declined: None };
    let mut out = Outcome { raised: None, value: Value::Null, entry: Value::Null };
    match method {
        "_karafun_wait_until_ready" => {
            let timeout = sc["timeout"].as_f64().unwrap_or(60.0);
            if let Err(e) = wait_until_ready(&mut w, sc["was_running"].as_bool().unwrap_or(false), timeout) {
                out.raised = Some(e);
            }
        }
        "_karafun_press_play_control" => {
            let (ok, e) = press_play_control(&mut w);
            out.value = json!([ok, e]);
        }
        "_karafun_playback_menu_state" => out.value = json!(playback_menu_state(&mut w)),
        _ => {
            let point = sc["point"].as_array().and_then(|a| Some((a.first()?.as_i64()?, a.get(1)?.as_i64()?)));
            let inp = ActivateInput {
                active: sc["active"].clone(),
                native_double_click: sc["settings"]["karafun_native_double_click"].as_bool().unwrap_or(false),
                change_session_on_resolve: sc["change_session_on_resolve"].as_bool().unwrap_or(false),
                point,
                reuse_point: sc["reuse_point"].as_bool().unwrap_or(false),
            };
            let (ok, e, entry) = activate_result_for_playback(&mut w, &inp);
            out.value = json!([ok, e]);
            out.entry = entry;
        }
    }
    json!({"out": {"raised": out.raised, "value": out.value}, "calls": w.calls, "log": w.log, "sleeps": w.sleeps, "final_clock": w.t, "entry": out.entry,
           "consumed": w.pos, "order_error": w.order_error, "declined": w.declined})
}
