//! The five nested helpers of `KaraokeApp._automate_karafun_search_and_play`'s worker (0.2.18.1.py): `_schedule_bgm_fade` and `_schedule_early_handoff` (each acts once per song),
//! `_native_double_click_result`, `_return_to_singws`, and `_watch_karafun_start` (the loop that waits for KaraFun's menu to read Pause, resends a native double-click and LEARNS that
//! this Mac needs the native click). Sequential code against the same scripted `World` as flow.rs; `_run_on_ui_thread` runs its function inline. tools/karafun_helpers_parity.py executes
//! the REAL Python closures (read by AST) against the same scripted calls and answers.
use crate::flow::World;
use crate::ladder::py_repr;
use serde_json::{json, Map, Value};

pub struct Scenario {
    pub native_double_click: bool,
    pub entry: Map<String, Value>,
    pub ops: Vec<Vec<Value>>,
}

pub struct Outcome {
    pub entry: Map<String, Value>,
    pub native_double_click: bool,
    pub session: Value,
    pub world: Value,
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

struct H<'w, 'a> {
    w: &'w mut World<'a>,
    entry: Map<String, Value>,
    native: bool,
    bgm_scheduled: bool,
    handoff_scheduled: bool,
    host_focus_restored: bool,
}

macro_rules! repr {
    ($w:expr, $s:expr) => {
        match py_repr($s) {
            Some(r) => r,
            None => {
                $w.declined = Some("a character whose Python repr is not modelled".to_string());
                return;
            }
        }
    };
}

impl<'w, 'a> H<'w, 'a> {
    fn session_current(&mut self) -> bool {
        self.w.ask("session_current").as_bool().unwrap_or(false)
    }

    fn bgm(&mut self, reason: &str) {
        if self.bgm_scheduled {
            return;
        }
        self.bgm_scheduled = true;
        self.w.diag(format!("[KARAFUN] delayed BGM fade scheduled reason={reason}"));
        self.w.call("ui_thread", vec![], json!({}));
        if self.session_current() {
            self.w.call("fade_bg", vec![], json!({}));
        }
    }

    fn point_ints(&self) -> Option<(i64, i64)> {
        let p = self.entry.get("karafun_result_activation_point")?;
        let a = p.as_array()?;
        Some((a[0].as_f64()?.trunc() as i64, a[1].as_f64()?.trunc() as i64))
    }

    fn native_click_with_context(&mut self, x: i64, y: i64) {
        self.w.call("raise_results", vec![], json!({}));
        self.w.sleep(0.4);
        self.w.call("frontmost", vec![], json!({}));
        let name = self.w.ask("frontmost").as_str().unwrap_or("").to_string();
        let r = repr!(self.w, &name);
        self.w.diag(format!("[KARAFUN-AUTO] click context frontmost={r}"));
        self.w.call("native_double_click", vec![json!(x), json!(y)], json!({}));
    }

    fn dbl(&mut self) {
        if truthy(self.entry.get("karafun_native_retry_sent")) || truthy(self.entry.get("karafun_queued_for_adjustment")) {
            return;
        }
        self.entry.insert("karafun_native_retry_sent".into(), json!(true));
        if !truthy(self.entry.get("karafun_result_activation_point")) {
            return;
        }
        self.w.diag("[KARAFUN-AUTO] KaraFun still idle after accessibility double-click; sending native double-click".into());
        let (x, y) = self.point_ints().unwrap_or((0, 0));
        self.native_click_with_context(x, y);
    }

    fn return_to_singws(&mut self) {
        if !self.session_current() {
            return;
        }
        self.host_focus_restored = false;
        self.w.call("activate_host", vec![json!({"id": 1, "host_focus_restored": false})], json!({}));
    }

    fn early(&mut self, reason: &str) {
        if self.handoff_scheduled {
            return;
        }
        self.handoff_scheduled = true;
        self.w.diag(format!("[KARAFUN] early show-screen handoff scheduled reason={reason}"));
        self.w.call("ui_thread", vec![], json!({}));
        if self.session_current() {
            self.w.call("handoff_show_screen", vec![], json!({}));
        }
        self.w.call("handoff_dispatched_set", vec![], json!({}));
    }

    fn watch(&mut self) {
        self.w.call("thread", vec![], json!({"thread_name": "karafun-start-watch"}));
        let started = self.w.t;
        loop {
            if !(self.w.t - started < 60.0) {
                break;
            }
            if !self.session_current() {
                break;
            }
            self.w.call("menu_state", vec![], json!({}));
            let state = self.w.ask("menu_state").as_str().unwrap_or("").to_string();
            if state == "PLAYING" {
                if truthy(self.entry.get("karafun_native_retry_sent")) && !self.native {
                    self.native = true;
                    self.w.call("ui_thread", vec![], json!({}));
                    self.w.call("save_settings", vec![], json!({}));
                    self.w.diag("[KARAFUN-AUTO] learned: native double-click needed on this Mac".into());
                }
                self.bgm("karafun_menu_playing");
                if !self.entry.contains_key("karafun_display_started_at") {
                    self.entry.insert("karafun_display_started_at".into(), json!(self.w.t));
                }
                self.w.call("ui_thread", vec![], json!({}));
                self.return_to_singws();
                return;
            }
            if self.w.t - started > 4.0 && state == "IDLE" {
                self.dbl();
            }
            if self.w.t - started > 3.5 && state == "IDLE" && truthy(self.entry.get("karafun_native_first")) && !truthy(self.entry.get("karafun_native_reclick")) {
                self.entry.insert("karafun_native_reclick".into(), json!(true));
                if truthy(self.entry.get("karafun_result_activation_point")) {
                    self.w.diag("[KARAFUN-AUTO] learned native click did not start playback; re-raising and clicking again".into());
                    let (x, y) = self.point_ints().unwrap_or((0, 0));
                    self.native_click_with_context(x, y);
                    if self.w.declined.is_some() {
                        return;
                    }
                }
            }
            if self.w.t - started > 8.0 && state == "IDLE" && truthy(self.entry.get("karafun_native_first")) && !truthy(self.entry.get("karafun_ax_retry_sent")) {
                self.entry.insert("karafun_ax_retry_sent".into(), json!(true));
                if truthy(self.entry.get("karafun_result_activation_point")) {
                    self.w.diag("[KARAFUN-AUTO] learned native click did not start playback; trying accessibility double-click".into());
                    let p = self.entry.get("karafun_result_activation_point").cloned().unwrap_or(Value::Null);
                    self.w.call("click_point_script", vec![p], json!({}));
                    self.w.call("session_script", vec![json!("click-script")], json!({"timeout": 5}));
                }
            }
            self.w.sleep(0.5);
        }
    }
}

pub fn run(sc: &Scenario, answers: &[Value]) -> Outcome {
    let mut w = World { answers, pos: 0, calls: vec![], log: vec![], sleeps: vec![], t: 100.0, requires: 0, end_session_at: None, order_error: None, declined: None };
    let (entry, native, restored) = {
        let mut h = H { w: &mut w, entry: sc.entry.clone(), native: sc.native_double_click, bgm_scheduled: false, handoff_scheduled: false, host_focus_restored: true };
        for op in &sc.ops {
            match op[0].as_str().unwrap_or("") {
                "bgm" => h.bgm(op[1].as_str().unwrap_or("")),
                "early" => h.early(op[1].as_str().unwrap_or("")),
                "dbl" => h.dbl(),
                "return" => h.return_to_singws(),
                "watch" => h.watch(),
                _ => {}
            }
            if h.w.declined.is_some() {
                break;
            }
        }
        (h.entry, h.native, h.host_focus_restored)
    };
    let world = json!({"calls": w.calls, "log": w.log, "sleeps": w.sleeps, "final_clock": w.t, "order_error": w.order_error, "declined": w.declined, "consumed": w.pos});
    Outcome { entry, native_double_click: native, session: json!({"id": 1, "host_focus_restored": restored}), world }
}
