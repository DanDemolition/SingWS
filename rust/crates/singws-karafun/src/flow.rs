//! The part of `KaraokeApp._automate_karafun_search_and_play` AFTER the search ladder (0.2.18.1.py: from the `_require_current_session()` before "activating KaraFun
//! result" to `result = {"ok": True, ...}`): activation, hand-off ordering, play, verify, key/tempo. Written as sequential code against a scripted `World` (answers come
//! from the caller; every call, log line and sleep is recorded), so the same code can later run against the real KaraFun. tools/karafun_flow_parity.py executes the REAL
//! Python statements (read by AST) against the same scripted answers and compares the traces.
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
    pub requires: i64,
    pub end_session_at: Option<i64>,
    pub order_error: Option<String>,
    pub declined: Option<String>,
    /// Whole-method mode (worker.rs): the closures run for real, the UI thread runs inline, every read of the session counts.
    pub unified: bool,
    pub bgm_scheduled: bool,
    pub handoff_scheduled: bool,
    pub event_flag: bool,
    /// settings["karafun_native_double_click"]
    pub native: bool,
    /// the session dict's `host_focus_restored` key: None = never set
    pub session_restored: Option<bool>,
    /// Live mode (dev Mac only): answers come from the real KaraFun, given the question and the calls recorded so far; sleeps really sleep and the clock follows the wall clock.
    pub live: Option<Box<dyn FnMut(&str, &[Value]) -> Value>>,
    pub t0: Option<std::time::Instant>,
}

impl<'a> World<'a> {
    pub fn new(answers: &'a [Value], end_session_at: Option<i64>) -> World<'a> {
        World { answers, pos: 0, calls: vec![], log: vec![], sleeps: vec![], t: 100.0, requires: 0, end_session_at, order_error: None, declined: None, unified: false,
                bgm_scheduled: false, handoff_scheduled: false, event_flag: false, native: false, session_restored: None, live: None, t0: None }
    }
    /// One read of the session object: counts, and says whether the session is still the current one.
    pub(crate) fn session_read(&mut self) -> bool {
        self.requires += 1;
        !matches!(self.end_session_at, Some(n) if self.requires >= n)
    }
    /// `_session_is_current()`: a counted read in whole-method mode, a scripted answer in the per-stretch harnesses.
    pub(crate) fn session_current(&mut self) -> bool {
        if self.unified { self.session_read() } else { self.ask("session_current").as_bool().unwrap_or(false) }
    }
    fn tick(&mut self) {
        if let Some(t0) = self.t0 {
            self.t = 100.0 + t0.elapsed().as_secs_f64();
        }
    }
    pub(crate) fn ask(&mut self, kind: &str) -> Value {
        if let Some(mut f) = self.live.take() {
            self.tick();
            let v = f(kind, &self.calls);
            self.live = Some(f);
            self.pos += 1;
            self.tick();
            return v;
        }
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
    pub(crate) fn call(&mut self, name: &str, args: Vec<Value>, kw: Value) {
        self.tick();
        self.calls.push(json!([name, args, kw]));
        // live mode: side effects of app-side calls (the native double-click, raising the results window) happen for real
        if let Some(mut f) = self.live.take() {
            f(&format!("call:{name}"), &self.calls);
            self.live = Some(f);
            self.tick();
        }
    }
    pub(crate) fn sleep(&mut self, s: f64) {
        self.sleeps.push(s);
        if self.live.is_some() {
            std::thread::sleep(std::time::Duration::from_secs_f64(s));
            self.tick();
            return;
        }
        self.t += s;
    }
    pub(crate) fn require(&mut self) -> Result<(), String> {
        if self.session_read() { Ok(()) } else { Err(if self.unified { "KaraFun launch cancelled: playback session ended" } else { "KaraFun playback session ended" }.to_string()) }
    }
    pub(crate) fn diag(&mut self, s: String) {
        self.log.push(s);
    }
    /// `(ok, error-or-None)` pairs the fake world answers as [bool, str]: an empty string is None.
    fn pair(&mut self, kind: &str) -> (bool, Option<String>) {
        let v = self.ask(kind);
        let ok = v[0].as_bool().unwrap_or(false);
        let e = v[1].as_str().unwrap_or("").to_string();
        (ok, if e.is_empty() { None } else { Some(e) })
    }
    fn press(&mut self) -> (bool, Option<String>) {
        let r = self.pair("press");
        self.call("press_play", vec![], json!({}));
        r
    }
    pub(crate) fn mouse_click(&mut self, x: i64, y: i64) -> bool {
        self.call("mouse_click", vec![json!(x), json!(y)], json!({"clicks": 1}));
        self.ask("mouse_click").as_bool().unwrap_or(false)
    }
    fn activate(&mut self, point: (i64, i64), reuse: bool) -> (bool, Option<String>) {
        self.call("activate_result", vec![json!([point.0, point.1])], json!({"reuse_point": reuse}));
        self.pair("activate")
    }
    fn menu_state(&mut self) -> String {
        self.call("menu_state", vec![], json!({}));
        self.ask("menu_state").as_str().unwrap_or("").to_string()
    }
    pub(crate) fn session_script(&mut self, script: &str, timeout: i64) -> Result<(bool, String, String), String> {
        self.session_script_json(script, json!(timeout))
    }
    pub(crate) fn session_script_f(&mut self, script: &str, timeout: f64) -> Result<(bool, String, String), String> {
        self.session_script_json(script, json!(timeout))
    }
    /// `_run_session_script`: in whole-method mode it checks the session before and after (the per-stretch harnesses stub it without the checks).
    fn session_script_json(&mut self, script: &str, timeout: Value) -> Result<(bool, String, String), String> {
        if self.unified {
            self.require()?;
        }
        self.call("session_script", vec![json!(script)], json!({"timeout": timeout}));
        let v = self.ask("session_script");
        if self.unified {
            self.require()?;
        }
        Ok((v[0].as_bool().unwrap_or(false), v[1].as_str().unwrap_or("").to_string(), v[2].as_str().unwrap_or("").to_string()))
    }
    fn in_progress(&mut self) -> bool {
        self.ask("in_progress").as_bool().unwrap_or(false)
    }
}

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

fn or_str(first: &str, second: &str) -> String {
    if first.is_empty() { second.to_string() } else { first.to_string() }
}

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
    pub capture: bool,
    pub manage: bool,
    pub fast_enabled: bool,
    pub parts: Vec<String>,
    pub queued: bool,
    pub needs_adjustment: bool,
    pub adjustment_signature: String,
    pub requested_key: i64,
    pub requested_tempo: i64,
    pub was_running: bool,
    pub selected_query: String,
    pub title: String,
    pub entry: Map<String, Value>,
    pub end_session_at: Option<i64>,
}

pub struct Outcome {
    pub result: Value,
    pub entry: Map<String, Value>,
    pub world: Value,
}

/// The worker's nested closures: stubbed (one recorded call) in the per-stretch harness, the real closures in whole-method mode.
fn closure(w: &mut World, entry: &mut Map<String, Value>, which: &str, reason: &str) -> Result<(), String> {
    if !w.unified {
        match which {
            "bgm" => w.call("schedule_bgm_fade", vec![json!(reason)], json!({})),
            "early" => w.call("schedule_early_handoff", vec![json!(reason)], json!({})),
            "watch" => w.call("watch_start", vec![json!(reason)], json!({})),
            _ => w.call("native_double_click", vec![], json!({})),
        }
        return Ok(());
    }
    {
        let mut h = crate::helpers::H { w: &mut *w, entry: &mut *entry };
        match which {
            "bgm" => h.bgm(reason),
            "early" => h.early(reason),
            "watch" => h.watch(),
            _ => h.dbl(),
        }
    }
    if w.declined.is_some() { Err("declined".to_string()) } else { Ok(()) }
}

fn closure_watch(w: &mut World, entry: &mut Map<String, Value>, point: (i64, i64)) -> Result<(), String> {
    if !w.unified {
        w.call("watch_start", vec![json!([point.0, point.1])], json!({}));
        return Ok(());
    }
    closure(w, entry, "watch", "")
}

/// Whole-method mode: a successful activation with the learned native double-click on leaves the two flags the real activation sets.
fn native_flags(w: &World, entry: &mut Map<String, Value>, ok: bool) {
    if w.unified && ok && w.native {
        entry.insert("karafun_native_retry_sent".into(), json!(true));
        entry.insert("karafun_native_first".into(), json!(true));
    }
}

pub(crate) fn flow(w: &mut World, sc: &Scenario, entry: &mut Map<String, Value>) -> Result<String, String> {
    w.require()?;
    w.diag(format!("[KARAFUN-AUTO] activating KaraFun result mode={} x={} y={}", sc.parts[0], sc.parts[1], sc.parts[2]));
    let fx: f64 = sc.parts[1].parse().map_err(|_| "declined".to_string()).map_err(|e| { w.declined = Some("a coordinate Python would reject with its own error text".into()); e })?;
    let fy: f64 = sc.parts[2].parse().map_err(|_| "declined".to_string()).map_err(|e| { w.declined = Some("a coordinate Python would reject with its own error text".into()); e })?;
    let point = (fx.trunc() as i64, fy.trunc() as i64);
    entry.insert("karafun_result_activation_point".into(), json!([point.0, point.1]));
    w.require()?;
    let capture = sc.capture;
    let managed = sc.manage && !capture;
    let mut managed_start_state = "UNKNOWN".to_string();
    let result_activated_at;
    if sc.queued {
        if !capture {
            closure(w, entry, "bgm", "before_adjusted_queue_play")?;
        }
        let (pressed, err) = w.press();
        if !pressed {
            return Err(err.unwrap_or_else(|| "KaraFun queued song could not be started".into()));
        }
        result_activated_at = w.t;
        entry.insert("karafun_result_activated_at".into(), json!(result_activated_at));
        entry.insert("karafun_playback_clock_started_at".into(), json!(result_activated_at));
        entry.insert("karafun_playback_assumed".into(), json!(true));
        entry.insert("karafun_explicit_play_sent".into(), json!(true));
        closure(w, entry, "early", "after_adjusted_queue_playback_started")?;
        if capture {
            closure_watch(w, entry, point)?;
        }
        w.sleep(0.8);
    } else if managed {
        if !w.mouse_click(point.0, point.1) {
            return Err("Could not select the KaraFun result".into());
        }
        closure(w, entry, "early", "after_result_activation")?;
        let ok = if w.unified {
            if w.event_flag {
                true
            } else {
                w.call("event_wait", vec![], json!({"timeout": 3.0}));
                w.t += 3.0;
                false
            }
        } else {
            let ok = w.ask("event_wait").as_bool().unwrap_or(false);
            w.call("event_wait", vec![], json!({"timeout": 3.0}));
            w.t += if ok { 0.2 } else { 3.0 };
            ok
        };
        if !ok {
            w.diag("[KARAFUN-AUTO] fullscreen handoff UI dispatch timed out".into());
        }
        let deadline = w.t + 35.0;
        while w.in_progress() && w.t < deadline {
            w.require()?;
            w.sleep(0.1);
        }
        let ready = w.ask("handoff_complete").as_bool().unwrap_or(false);
        entry.insert("karafun_handoff_timed_out_before_play".into(), json!(!ready));
        if ready {
            w.diag("[KARAFUN-AUTO] fullscreen audience handoff ready before play".into());
        } else {
            w.diag("[KARAFUN-AUTO] fullscreen audience handoff not verified before play; playback blocked to protect the show screen".into());
            return Err("KaraFun audience screen could not be verified. The song was selected but was not started.".into());
        }
        closure(w, entry, "bgm", "fullscreen_handoff_ready")?;
        let (activated, activation_error);
        if capture {
            w.call("renderer_ready_script", vec![], json!({}));
            let (raised, raise_result, raise_error) = w.session_script("ready-script", 5)?;
            if raised && py_strip(&raise_result) == "READY" {
                let r = w.activate(point, false);
                native_flags(w, entry, r.0);
                activated = r.0;
                activation_error = r.1.unwrap_or_else(|| "None".into());
            } else {
                activated = false;
                activation_error = or_str(&raise_error, &or_str(&raise_result, "KaraFun result window unavailable"));
            }
        } else {
            let r = w.activate(point, false);
            native_flags(w, entry, r.0);
            activated = r.0;
            activation_error = r.1.unwrap_or_else(|| "None".into());
        }
        if !activated {
            return Err(format!("Could not start selected KaraFun result: {activation_error}"));
        }
        if !capture {
            w.call("renderer_raise_script", vec![], json!({}));
            w.call("window_script", vec![json!(["raise-script"])], json!({"timeout": 5}));
        }
        result_activated_at = w.t;
        entry.insert("karafun_result_activated_at".into(), json!(result_activated_at));
        entry.insert("karafun_playback_clock_started_at".into(), json!(result_activated_at));
        entry.insert("karafun_playback_assumed".into(), json!(true));
        w.sleep(0.5);
        managed_start_state = w.menu_state();
        if managed_start_state == "IDLE" {
            w.sleep(0.7);
            managed_start_state = w.menu_state();
        }
        if managed_start_state == "IDLE" {
            let (pressed, err) = w.press();
            if !pressed {
                return Err(err.unwrap_or_else(|| "KaraFun play automation failed".into()));
            }
            entry.insert("karafun_playback_clock_started_at".into(), json!(w.t));
            managed_start_state = "PLAYING".into();
            entry.insert("karafun_explicit_play_sent".into(), json!(true));
            w.diag("[KARAFUN-AUTO] idle result loaded; explicit Play sent".into());
        } else {
            w.diag(format!("[KARAFUN-AUTO] post-activation playback state={managed_start_state}"));
        }
    } else {
        if !capture {
            closure(w, entry, "bgm", "before_result_activation")?;
        }
        let (activated, err) = w.activate(point, capture);
        native_flags(w, entry, activated);
        if !activated {
            return Err(format!("Could not start selected KaraFun result: {}", err.unwrap_or_else(|| "None".into())));
        }
        result_activated_at = w.t;
        entry.insert("karafun_result_activated_at".into(), json!(result_activated_at));
        entry.insert("karafun_playback_clock_started_at".into(), json!(result_activated_at));
        entry.insert("karafun_playback_assumed".into(), json!(true));
        if capture {
            closure(w, entry, "early", "after_capture_playback_started")?;
            closure_watch(w, entry, point)?;
        }
        w.sleep(0.8);
    }
    w.require()?;
    w.call("start_probe_script", vec![], json!({}));
    let fast_start = sc.fast_enabled && !capture;
    let mut ok;
    let mut initial_probe_state: String;
    if fast_start {
        ok = true;
        initial_probe_state = if managed { managed_start_state.clone() } else { "PLAYING".into() };
        entry.insert("karafun_playback_assumed".into(), json!(!truthy(entry.get("karafun_explicit_play_sent"))));
        w.diag(format!("[KARAFUN-AUTO] fast start continuing after result activation state={initial_probe_state}; monitor will verify"));
    } else {
        let (o, probe, probe_err) = w.session_script("probe-script", 5)?;
        ok = o;
        initial_probe_state = py_strip(&or_str(&probe, &probe_err)).to_string();
        if initial_probe_state.starts_with("ERROR|KaraFun control window not found") {
            initial_probe_state = w.menu_state();
            ok = initial_probe_state != "UNKNOWN";
        }
    }
    let r = repr!(w, &initial_probe_state);
    w.diag(format!("[KARAFUN-AUTO] playback pre-click state={r}"));
    if ok && initial_probe_state == "PLAYING" {
        closure(w, entry, "early", "pre_click_playing")?;
    }
    let managed_state_unknown = fast_start && managed && initial_probe_state == "UNKNOWN";
    let explicit = truthy(entry.get("karafun_explicit_play_sent"));
    let flow_action;
    if !(ok && initial_probe_state == "PLAYING") && !managed_state_unknown && !explicit {
        flow_action = "Press";
        let (pressed, err) = w.press();
        if !pressed {
            return Err(err.unwrap_or_else(|| "KaraFun play automation failed".into()));
        }
        entry.insert("karafun_playback_clock_started_at".into(), json!(w.t));
    } else if explicit {
        flow_action = "SkipExplicit";
        w.diag("[KARAFUN-AUTO] play click skipped; explicit Play already sent".into());
    } else if managed_state_unknown {
        flow_action = "DeferToMonitor";
        w.diag("[KARAFUN-AUTO] managed playback state unknown; deferring to monitor".into());
    } else {
        flow_action = "SkipAlreadyPlaying";
        entry.insert("karafun_playback_clock_started_at".into(), json!(result_activated_at));
        w.diag("[KARAFUN-AUTO] play click skipped already playing".into());
    }
    if capture {
        closure(w, entry, "early", "start_action_finished")?;
        w.call("ui_thread", vec![], json!({}));
        if w.unified {
            let session = match w.session_restored {
                Some(r) => json!({"id": 1, "host_focus_restored": r}),
                None => json!({"id": 1}),
            };
            w.call("activate_host", vec![session], json!({}));
        }
    }
    let mut verified_playing = ok && initial_probe_state == "PLAYING" && !fast_start;
    w.call(
        "shadow_startflow",
        vec![],
        json!({"fast_start": fast_start, "managed_handoff": managed, "managed_start_state": managed_start_state, "explicit_play_sent": truthy(entry.get("karafun_explicit_play_sent")),
               "probe_ok": ok, "probe_state": initial_probe_state, "was_running": sc.was_running, "python_action": flow_action, "python_initial": initial_probe_state,
               "python_verified": verified_playing}),
    );
    let mut last_playback_probe = String::new();
    let mut native_double_click_retried = false;
    if !verified_playing && !fast_start {
        let max_probe_attempts = if sc.was_running { 12 } else { 30 };
        for probe_attempt in 0..max_probe_attempts {
            w.sleep(1.0);
            let (o, probe, probe_err) = w.session_script("probe-script", 5)?;
            ok = o;
            last_playback_probe = py_strip(&or_str(&probe, &probe_err)).to_string();
            if last_playback_probe == "UNKNOWN" || last_playback_probe == "ERROR|KaraFun control window not found" {
                last_playback_probe = w.menu_state();
                ok = last_playback_probe != "UNKNOWN";
            }
            let r = repr!(w, &last_playback_probe);
            w.diag(format!("[KARAFUN-AUTO] playback verify attempt={} state={}", probe_attempt + 1, r));
            if probe_attempt == 0 && !native_double_click_retried && !(ok && last_playback_probe == "PLAYING") {
                native_double_click_retried = true;
                if w.menu_state() == "IDLE" {
                    closure(w, entry, "dbl", "")?;
                }
            }
            if ok && last_playback_probe == "PLAYING" {
                closure(w, entry, "early", "playback_verified")?;
                closure(w, entry, "bgm", "playback_verified")?;
                if !entry.contains_key("karafun_display_started_at") {
                    entry.insert("karafun_display_started_at".into(), json!(w.t));
                }
                verified_playing = true;
                break;
            }
        }
    }
    if !verified_playing && !fast_start {
        return Err(format!("KaraFun did not report active playback after Play ({})", if last_playback_probe.is_empty() { "unknown state".to_string() } else { last_playback_probe }));
    }
    if !truthy(entry.get("karafun_playback_clock_started_at")) {
        entry.insert("karafun_playback_clock_started_at".into(), json!(w.t));
    }
    entry.insert("karafun_play_started_at".into(), json!(1_700_000_000.0 + w.t));
    let tr = repr!(w, &sc.title);
    let qr = repr!(w, &sc.selected_query);
    w.diag(format!("[KARAFUN-AUTO] playback start event title={tr} query={qr}"));
    w.require()?;
    w.call("start_monitor", vec![], json!({}));
    let mut adjusted = "ADJUSTED|false|false".to_string();
    let already = entry.get("karafun_adjustment_applied").and_then(|v| v.as_str()).map_or(false, |s| s == sc.adjustment_signature);
    if sc.needs_adjustment && !already {
        let deadline = w.t + 15.0;
        while w.in_progress() && w.t < deadline {
            w.sleep(0.25);
        }
        w.sleep(1.5);
        w.call("set_key_tempo_script", vec![json!(sc.requested_key), json!(sc.requested_tempo)], json!({}));
        let script = format!("kt-script:{}:{}", sc.requested_key, sc.requested_tempo);
        let (ok, a, script_error) = w.session_script(&script, 8)?;
        adjusted = a;
        if !ok {
            let cut: String = script_error.chars().take(180).collect();
            let r = repr!(w, &cut);
            w.diag(format!("[KARAFUN-AUTO] adjustment skipped error={r}"));
            adjusted = String::new();
        } else {
            let lowered = adjusted.to_lowercase();
            let key_ok = sc.requested_key == 0 || lowered.contains("|true|");
            let tempo_ok = sc.requested_tempo == 100 || lowered.ends_with("|true");
            if key_ok && tempo_ok {
                entry.insert("karafun_adjustment_applied".into(), json!(sc.adjustment_signature));
                w.diag(format!("[KARAFUN-AUTO] adjustment applied {}", sc.adjustment_signature));
            }
        }
    } else if already {
        w.diag(format!("[KARAFUN-AUTO] adjustment skipped already applied {}", sc.adjustment_signature));
    } else {
        w.diag("[KARAFUN-AUTO] adjustment skipped default key/tempo".into());
    }
    Ok(if adjusted.is_empty() { "ADJUSTED|false|false".to_string() } else { adjusted })
}

pub fn run(sc: &Scenario, answers: &[Value]) -> Outcome {
    let mut w = World::new(answers, sc.end_session_at);
    let mut entry = sc.entry.clone();
    let r = flow(&mut w, sc, &mut entry);
    let result = match r {
        Ok(msg) => json!({"ok": true, "message": msg}),
        Err(msg) => json!({"ok": false, "message": msg}),
    };
    let world = json!({"calls": w.calls, "log": w.log, "sleeps": w.sleeps, "final_clock": w.t, "order_error": w.order_error, "declined": w.declined, "consumed": w.pos});
    Outcome { result, entry, world }
}
