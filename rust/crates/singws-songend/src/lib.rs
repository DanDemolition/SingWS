//! Offline port of the host-side decisions that start the background music at the end of a karaoke song (0.2.18.1.py).
//!
//! Pure: no I/O, no clock (the caller passes the event time), no engine. `Machine::event` takes one event of the replay produced by
//! tools/songend_scenarios.py (the REAL Python methods run there) plus a snapshot of everything those methods could read, and returns
//! the commands the Python code issued (bg/host calls, single-shot timers, QTimer start/stop, log lines) in order. Proof only.
//! Design, rules and surprises: docs/design/stage-e-songend-handoff.md.
use serde_json::{json, Value};
use std::collections::HashMap;

const NS: f64 = 1e9;
const MAX_CREDITED_SILENCE_S: f64 = 8.0;
// KaraokeApp.__init__ values (0.2.18.1.py ~23116)
const DB_THRESHOLD: f64 = -38.0;
const MIN_S: f64 = 2.5;
const NEAR_END_WINDOW_S: f64 = 20.0;
const MIN_ELAPSED_S: f64 = 20.0;
const CONFIDENT_REMAIN_S: f64 = 6.0;
const CONFIDENT_MIN_S: f64 = 0.7;
const CDG_SECTOR_THRESHOLD: f64 = 120.0;
const END_OVERLAP_MS: i64 = 3200;

enum Deferred {
    Audio { path: String, duration: f64, trailing: f64, source: String, runs_to_end: bool },
    Visual { path: String, safe: bool, visual_end: Option<f64>, conf: f64, reason: String },
}

pub struct Machine {
    audio_end: Option<f64>,
    audio_dur: Option<f64>,
    audio_end_path: String,
    visual_end: Option<f64>,
    visual_conf: f64,
    visual_path: String,
    visual_reason: String,
    prefired: bool,
    tail_started: bool,
    triggered: bool,
    accum: f64,
    last_tick: f64,
    last_reason: String,
    mode: String,
    auto_advance_next: bool,
    eos_guard: i64,
    last_pos: Option<i64>,
    stalled: i64,
    handoff_active: bool,
    cleanup_gen: i64,
    sched_gen: i64,
    cleanup_est: bool,
    cleanup_sbr: bool,
    overlay_eligible: bool,
    resume_reason: String,
    verify_gen: i64,
    verify_retried: bool,
    verify_recovered: bool,
    floor_logged: bool,
    unknown_logged: bool,
    no_level_logged: bool,
    cdg_gen: Option<i64>,
    cdg_gen_ts: f64,
    hold_log_ts: f64,
    debug_ts: f64,
    visual_unknown_ts: f64,
    suppress_ts: f64,
    pending_intro: bool,
    library_path: String,
    // per event
    out: Vec<Value>,
    now: f64,
    settings: Value,
    snap: Value,
    bg_playing: bool,
    deliver_seq: i64,
    deferred: HashMap<i64, Deferred>,
}

fn pyb(b: bool) -> &'static str {
    if b {
        "True"
    } else {
        "False"
    }
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|x| x != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn base(p: &str) -> &str {
    p.rsplit('/').next().unwrap_or(p)
}

fn upper_or(mode: &str, dflt: &str) -> String {
    if mode.is_empty() {
        dflt.to_string()
    } else {
        mode.to_uppercase()
    }
}

impl Default for Machine {
    fn default() -> Self {
        Self::new()
    }
}

impl Machine {
    pub fn new() -> Self {
        Machine {
            audio_end: None,
            audio_dur: None,
            audio_end_path: String::new(),
            visual_end: None,
            visual_conf: 0.0,
            visual_path: String::new(),
            visual_reason: String::new(),
            prefired: false,
            tail_started: false,
            triggered: false,
            accum: 0.0,
            last_tick: 0.0,
            last_reason: String::new(),
            mode: String::new(),
            auto_advance_next: false,
            eos_guard: 0,
            last_pos: None,
            stalled: 0,
            handoff_active: false,
            cleanup_gen: 0,
            sched_gen: 0,
            cleanup_est: false,
            cleanup_sbr: false,
            overlay_eligible: false,
            resume_reason: "default".into(),
            verify_gen: 0,
            verify_retried: false,
            verify_recovered: false,
            floor_logged: false,
            unknown_logged: false,
            no_level_logged: false,
            cdg_gen: None,
            cdg_gen_ts: 0.0,
            hold_log_ts: 0.0,
            debug_ts: 0.0,
            visual_unknown_ts: 0.0,
            suppress_ts: 0.0,
            pending_intro: false,
            library_path: String::new(),
            out: Vec::new(),
            now: 0.0,
            settings: Value::Null,
            snap: Value::Null,
            bg_playing: false,
            deliver_seq: 0,
            deferred: HashMap::new(),
        }
    }

    pub fn state(&self) -> Value {
        json!({
            "audio_end_s": self.audio_end, "audio_duration_s": self.audio_dur, "audio_end_path": self.audio_end_path,
            "visual_end_s": self.visual_end, "visual_conf": self.visual_conf, "visual_path": self.visual_path, "visual_reason": self.visual_reason,
            "prefired": self.prefired, "tail_started": self.tail_started, "triggered": self.triggered, "accum_s": self.accum,
            "last_tick": self.last_tick, "last_reason": self.last_reason, "mode": self.mode, "auto_advance_next": self.auto_advance_next,
            "eos_guard": self.eos_guard, "last_pos_ns": self.last_pos, "stalled_ticks": self.stalled,
            "handoff_active": self.handoff_active, "cleanup_gen": self.cleanup_gen, "scheduled_cleanup_gen": self.sched_gen,
            "cleanup_end_silence": self.cleanup_est, "cleanup_schedule_resume": self.cleanup_sbr, "overlay_eligible": self.overlay_eligible,
            "resume_reason": self.resume_reason, "verify_gen": self.verify_gen, "verify_retried": self.verify_retried,
            "verify_recovered": self.verify_recovered, "soundboard_pending": "",
            "duration_floor_logged": self.floor_logged, "unknown_logged": self.unknown_logged, "no_level_logged": self.no_level_logged,
            "cdg_gen": self.cdg_gen, "cdg_gen_ts": self.cdg_gen_ts, "hold_log_ts": self.hold_log_ts, "debug_ts": self.debug_ts,
            "visual_unknown_ts": self.visual_unknown_ts, "suppress_ts": self.suppress_ts,
            "bg_playing": self.bg_playing, "pending_intro": self.pending_intro,
        })
    }

    // ------------------------------------------------------------------------------------------------ helpers
    fn log(&mut self, s: String) {
        self.out.push(json!({"c": "log", "a": [s]}));
    }
    fn cmd(&mut self, name: &str, a: Vec<Value>) {
        self.out.push(json!({"c": name, "a": a}));
    }
    fn timer(&mut self, ms: i64, kind: &str, args: Vec<Value>) {
        self.out.push(json!({"c": "timer", "a": [ms, kind, args]}));
    }
    fn qstart(&mut self, name: &str, ms: i64) {
        self.out.push(json!({"c": "timer_start", "a": [name, ms]}));
    }
    fn qstop(&mut self, name: &str) {
        self.out.push(json!({"c": "timer_stop", "a": [name]}));
    }
    fn sb(&self, key: &str, dflt: bool) -> bool {
        match self.settings.get(key) {
            None => dflt,
            Some(v) => truthy(v),
        }
    }
    /// float(settings.get(key, dflt)): None when Python would raise (null / non-number)
    fn sf(&self, key: &str, dflt: f64) -> Option<f64> {
        match self.settings.get(key) {
            None => Some(dflt),
            Some(Value::Number(n)) => n.as_f64(),
            Some(Value::Bool(b)) => Some(if *b { 1.0 } else { 0.0 }),
            _ => None,
        }
    }
    fn bg_has_playlist(&self) -> bool {
        self.snap["bg"]["playlist"].as_bool().unwrap_or(false)
    }
    fn bg_enabled_setting(&self) -> bool {
        self.sb("bg_enabled", true)
    }
    fn bg_autoplay_setting(&self) -> bool {
        self.sb("bg_autoplay_on_idle", true)
    }
    fn bg_effective(&self) -> bool {
        self.bg_playing && self.snap["bg"]["audible"].as_bool().unwrap_or(true)
    }
    /// bg_music.fade_in(target, ms, allow_during_karaoke): the harness stand-in records the call; the snapshot says whether it starts.
    fn bg_fade_in(&mut self, target: Value, ms: i64, allow: bool) {
        self.cmd("bg:fade_in", vec![target, json!(ms), json!(allow)]);
        let starts = self.snap["bg"][if allow { "starts_allow" } else { "starts_normal" }].as_bool().unwrap_or(false);
        if starts {
            self.bg_playing = true;
        }
    }
    fn crossfade_enabled(&self) -> bool {
        self.sb("seamless_transitions_enabled", true) && self.sb("karaoke_bgm_crossfade_enabled", false)
    }
    fn early_trim_enabled(&self) -> bool {
        if self.sb("karaoke_trim_verified_tail", true) {
            return true;
        }
        self.sb("seamless_transitions_enabled", true) && self.sb("karaoke_allow_early_silence_trim", false) && self.sb("end_silence_trim_enabled", false)
    }
    fn end_trim_threshold(&self) -> f64 {
        let v = self.sf("end_silence_trim_threshold_sec", MIN_S).unwrap_or(MIN_S);
        0.5_f64.max(60.0_f64.min(v))
    }

    // ------------------------------------------------------------------------------------------------ dispatch
    pub fn event(&mut self, t_ms: i64, ev: &str, a: &Value, snap: &Value) -> (Vec<Value>, Value) {
        self.out = Vec::new();
        self.now = t_ms as f64 / 1000.0;
        self.snap = snap.clone();
        self.settings = snap["settings"].clone();
        self.bg_playing = snap["bg"]["is_playing"].as_bool().unwrap_or(false);
        let mut ret = Value::Null;
        match ev {
            "tick" => self.tick(),
            "song_start" => self.song_start(a),
            "set" => {}
            "karafun_complete" => self.schedule_bg_resume(120, "external_karafun_complete"),
            "karafun_return" => self.schedule_bg_resume(120, "external_karafun_return"),
            "manual_stop" => {
                self.cancel_pending("manual_stop", true);
                self.reset_end_silence_state();
            }
            "eos" => self.handle_media_end(a["trigger"].as_str().unwrap_or("eos")),
            "eos_during_stop" => self.handle_media_end(a["trigger"].as_str().unwrap_or("eos")),
            "timer" => ret = self.fire_timer(a),
            "qtimer" => match a["name"].as_str().unwrap_or("") {
                "bg_resume" => {
                    self.start_bg_with_fade("resume_timer");
                }
                "handoff_guard" => self.handoff_active = false,
                "media_end_cleanup" => self.run_media_end_cleanup(),
                _ => {}
            },
            "deliver" => self.deliver(a["id"].as_i64().unwrap_or(-1)),
            _ => {}
        }
        (std::mem::take(&mut self.out), ret)
    }

    fn fire_timer(&mut self, a: &Value) -> Value {
        let kind = a["kind"].as_str().unwrap_or("");
        let args = a["args"].as_array().cloned().unwrap_or_default();
        match kind {
            "_handle_media_end_safe" => {
                let trig = args.first().and_then(|v| v.as_str()).unwrap_or("eos").to_string();
                self.handle_media_end(&trig);
                Value::Null
            }
            "_verify_bg_resume_started" => {
                self.verify_bg_resume(args.first().and_then(|v| v.as_i64()).unwrap_or(0));
                Value::Null
            }
            "_auto_play_next_if_generation" => {
                let gen = args.first().and_then(|v| v.as_i64()).unwrap_or(0);
                let reason = args.get(1).and_then(|v| v.as_str()).unwrap_or("").to_string();
                let reason = if reason.is_empty() { "early_media_end".to_string() } else { reason };
                json!(self.auto_play_next(gen, &reason))
            }
            "update_bg_button_state" => {
                self.cmd("host:update_bg_button_state", vec![]);
                Value::Null
            }
            _ => Value::Null,
        }
    }

    fn deliver(&mut self, id: i64) {
        match self.deferred.remove(&id) {
            Some(Deferred::Audio { path, duration, trailing, source, runs_to_end }) => self.apply_audio_end_floor(&path, duration, trailing, &source, runs_to_end),
            Some(Deferred::Visual { path, safe, visual_end, conf, reason }) => self.apply_visual_end_floor(&path, safe, visual_end, conf, &reason),
            None => {}
        }
    }

    fn run_on_ui_thread(&mut self, d: Deferred) {
        self.deliver_seq += 1;
        let id = self.deliver_seq;
        self.cmd("deliver_later", vec![json!(id)]);
        self.deferred.insert(id, d);
    }

    // ------------------------------------------------------------------------------------------------ per-song state
    fn reset_end_silence_state(&mut self) {
        self.mode = String::new();
        self.triggered = false;
        self.accum = 0.0;
        self.last_tick = 0.0;
        self.debug_ts = 0.0;
        self.no_level_logged = false;
        self.suppress_ts = 0.0;
        self.cdg_gen = None;
        self.cdg_gen_ts = 0.0;
        self.auto_advance_next = false;
        self.last_reason = String::new();
        self.tail_started = false;
        self.audio_end = None;
        self.audio_end_path = String::new();
        self.visual_end = None;
        self.visual_path = String::new();
        self.visual_conf = 0.0;
        self.visual_reason = "analysis_unavailable".into();
        self.visual_unknown_ts = 0.0;
        self.hold_log_ts = 0.0;
        self.unknown_logged = false;
        self.prefired = false;
    }

    fn setup_end_silence_state(&mut self, mode: &str) {
        self.mode = mode.to_string();
        self.triggered = false;
        self.accum = 0.0;
        self.last_tick = 0.0;
        self.debug_ts = 0.0;
        self.no_level_logged = false;
        self.suppress_ts = 0.0;
        self.cdg_gen = None;
        self.cdg_gen_ts = self.now;
        self.auto_advance_next = false;
        self.last_reason = String::new();
        self.tail_started = false;
        self.hold_log_ts = 0.0;
        if mode == "cdg" || mode == "mp4" {
            self.log(format!("[END-SILENCE] {} detector armed", mode.to_uppercase()));
        }
    }

    fn song_start(&mut self, a: &Value) {
        let mode = a["mode"].as_str().unwrap_or("").to_string();
        self.library_path = a["library_path"].as_str().unwrap_or("").to_string();
        let audio_path = a["audio_path"].as_str().unwrap_or("/t/x.mp3").to_string();
        let visual_path = a["visual_path"].as_str().unwrap_or("/t/x.cdg").to_string();
        let hint = a["duration_hint"].as_f64();
        self.reset_end_silence_state();
        self.setup_end_silence_state(&mode);
        self.arm_audio_end_floor(&audio_path, hint);
        self.arm_visual_end_floor(&visual_path, &mode);
    }

    fn arm_audio_end_floor(&mut self, audio_path: &str, hint: Option<f64>) {
        self.audio_end = None;
        self.audio_dur = None;
        self.floor_logged = false;
        self.audio_end_path = audio_path.to_string();
        if audio_path.is_empty() {
            return;
        }
        if !self.early_trim_enabled() {
            return;
        }
        let mut duration = 0.0_f64.max(hint.unwrap_or(0.0));
        if duration <= 0.0 {
            duration = match &self.snap["db_duration"] {
                Value::Number(n) => n.as_f64().unwrap_or(0.0),
                _ => 0.0, // None, or the "raise" marker: Python falls back to 0.0 either way
            };
        }
        let lib = self.library_path.clone();
        if !lib.is_empty() {
            let rec = &self.snap["arm"]["cached"][lib.as_str()];
            if rec.is_object() {
                let rec_dur = rec["duration"].as_f64().unwrap_or(0.0);
                if let Some(rec_end) = rec["audio_end"].as_f64() {
                    if rec_dur > 0.0 {
                        let tail = 0.0_f64.max(rec_dur - rec_end);
                        if tail >= 0.3 {
                            self.apply_audio_end_floor(audio_path, rec_dur, tail, "library-cache", false);
                            return;
                        }
                    }
                }
            }
        }
        let cached = self.snap["arm"]["trailing_cached"][audio_path].as_f64();
        let true_dur = self.snap["audio_true_duration"][audio_path].as_f64().unwrap_or(0.0);
        if let Some(c) = cached {
            // a cached 0.0 is ambiguous (see audible_through_end in phrase_detect.py), so it is re-scanned, never trusted
            if c > 0.0 && true_dur > 0.0 {
                self.apply_audio_end_floor(audio_path, true_dur, c, "cache", false);
                return;
            }
        }
        // worker thread (runs at once in the harness; its answer is delivered later)
        self.cmd("spawn", vec![json!("singws-audio-end-scan")]);
        let mut scan_duration = duration;
        let probe = match &self.snap["arm"]["scan_probe"] {
            Value::Number(n) => n.as_f64().unwrap_or(0.0),
            _ => 0.0,
        };
        if probe > 0.0 {
            scan_duration = probe;
        }
        if scan_duration <= 0.0 {
            self.log(format!("[END-AUDIO] duration unknown; end-of-song floor unavailable file={}", base(audio_path)));
            return;
        }
        let trailing = match &self.snap["arm"]["scan_trailing"] {
            Value::Number(n) => n.as_f64().unwrap_or(0.0),
            _ => {
                self.log(format!("[END-AUDIO] scan failed; retaining normal end-of-file file={} error=RuntimeError('scan failed')", base(audio_path)));
                return;
            }
        };
        // trailing == 0.0 is ambiguous: only a clearly audible last half second proves the audio runs to the end of the file
        let runs_to_end = trailing <= 0.0 && self.snap["arm"]["scan_audible_end"].as_bool().unwrap_or(false);
        self.run_on_ui_thread(Deferred::Audio { path: audio_path.to_string(), duration: scan_duration, trailing, source: "scan".into(), runs_to_end });
    }

    fn apply_audio_end_floor(&mut self, path: &str, duration: f64, trailing: f64, source: &str, runs_to_end: bool) {
        if path != self.audio_end_path {
            return;
        }
        let mut trailing = 0.0_f64.max(trailing);
        if duration > 0.0 {
            self.audio_dur = Some(duration);
        }
        if trailing <= 0.0 && runs_to_end && duration > 0.0 {
            self.audio_end = Some(duration);
            self.log(format!("[END-AUDIO] audio runs to the end of the file; end verified at {:.2}s source={} file={}", duration, source, base(path)));
            return;
        }
        if trailing <= 0.0 {
            self.audio_end = None;
            self.log(format!("[END-AUDIO] no trailing silence found; full duration will play source={} duration={:.2}s file={}", source, duration, base(path)));
            return;
        }
        if trailing > MAX_CREDITED_SILENCE_S {
            self.log(format!("[END-AUDIO] trailing silence {:.2}s capped to {:.1}s source={} file={}", trailing, MAX_CREDITED_SILENCE_S, source, base(path)));
            trailing = MAX_CREDITED_SILENCE_S;
        }
        let end = 0.0_f64.max(duration - trailing);
        self.audio_end = Some(end);
        self.log(format!(
            "[END-AUDIO] last audible audio at {:.2}s (trailing_silence={:.2}s duration={:.2}s source={}) file={}",
            end, trailing, duration, source, base(path)
        ));
    }

    fn arm_visual_end_floor(&mut self, visual_path: &str, mode: &str) {
        self.visual_end = None;
        self.visual_path = visual_path.to_string();
        self.visual_conf = 0.0;
        self.visual_reason = "analysis_unavailable".into();
        let mode = mode.to_lowercase();
        if visual_path.is_empty() {
            let m = if mode.is_empty() { "unknown".to_string() } else { mode.clone() };
            self.log(format!("[END-VISUAL] early completion unavailable mode={}; using normal EOS", m));
            return;
        }
        if !self.early_trim_enabled() {
            return;
        }
        let rec = self.snap["arm"]["cached"][visual_path].clone();
        if rec.is_object() {
            let cached_end = rec["visual_end"].as_f64();
            let conf = rec["visual_confidence"].as_f64().unwrap_or(0.0);
            if let Some(ce) = cached_end {
                if conf >= 0.85 {
                    let reason = rec["safety_reason"].as_str().filter(|s| !s.is_empty()).unwrap_or("cached_visual").to_string();
                    self.apply_visual_end_values(visual_path, &mode, ce, conf, &reason, "cache");
                    return;
                }
            }
            self.log(format!(
                "[TRANSITION] {} cached visual metadata is uncertain; using physical-duration fallback file={}",
                mode.to_uppercase(),
                base(visual_path)
            ));
        }
        if mode != "cdg" {
            self.log(format!(
                "[TRANSITION] {} visual analysis unavailable; using physical-duration fallback file={}",
                upper_or(&mode, "VIDEO"),
                base(visual_path)
            ));
            return;
        }
        self.cmd("spawn", vec![json!("singws-cdg-visual-end-scan")]);
        let r = self.snap["arm"]["cdg_scan"].clone();
        let safe = r["safe"].as_bool().unwrap_or(false);
        let visual_end = r["visual_end"].as_f64();
        let conf = r["confidence"].as_f64().unwrap_or(0.0);
        let reason = r["reason"].as_str().filter(|s| !s.is_empty()).unwrap_or("analysis_unavailable").to_string();
        self.run_on_ui_thread(Deferred::Visual { path: visual_path.to_string(), safe, visual_end, conf, reason });
    }

    fn apply_visual_end_floor(&mut self, path: &str, safe: bool, visual_end: Option<f64>, conf: f64, reason: &str) {
        if path != self.visual_path {
            return;
        }
        self.visual_reason = reason.to_string();
        if !safe || visual_end.is_none() || conf < 0.85 {
            self.visual_end = None;
            self.visual_conf = 0.0;
            self.log(format!("[END-VISUAL] CDG early completion suppressed reason={} file={}", reason, base(path)));
            return;
        }
        self.apply_visual_end_values(path, "cdg", visual_end.unwrap(), conf, reason, "scan");
    }

    fn apply_visual_end_values(&mut self, path: &str, mode: &str, visual_end: f64, conf: f64, reason: &str, source: &str) {
        if path != self.visual_path {
            self.log(format!("[TRANSITION] stale visual endpoint ignored source={}", source));
            return;
        }
        if visual_end < 0.0 || conf < 0.85 {
            return;
        }
        self.visual_end = Some(0.0_f64.max(visual_end));
        self.visual_conf = conf;
        self.visual_reason = if reason.is_empty() { "verified_visual_end".into() } else { reason.to_string() };
        self.log(format!(
            "[TRANSITION] verified {} visual end={:.2}s confidence={:.2} reason={} source={} file={}",
            upper_or(mode, "VISUAL"),
            0.0_f64.max(visual_end),
            conf,
            reason,
            source,
            base(path)
        ));
    }

    fn effective_dur(&mut self, dur: Option<i64>) -> Option<i64> {
        let d = dur?;
        let audio_s = self.audio_dur.unwrap_or(0.0);
        if audio_s <= 0.0 {
            return dur;
        }
        let audio_ns = (audio_s * NS) as i64;
        if audio_ns <= d {
            return dur;
        }
        if !self.floor_logged {
            self.floor_logged = true;
            self.log(format!(
                "[END-AUDIO] engine duration {:.2}s is short of the scanned audio {:.2}s; using the audio length (MP3+G graphics stream ends early)",
                d as f64 / NS,
                audio_s
            ));
        }
        Some(audio_ns)
    }

    // ------------------------------------------------------------------------------------------------ the 500 ms tick
    fn tick(&mut self) {
        let (dur, pos) = match self.snap["times"].as_array() {
            Some(a) if a.len() == 2 => (a[0].as_i64(), a[1].as_i64()),
            _ => (None, None),
        };
        let dur = self.effective_dur(dur);
        if let (Some(dur), Some(pos)) = (dur, pos) {
            if dur > 0 {
                self.tick_playing(dur, pos);
                return;
            }
        }
        self.last_pos = None;
        self.stalled = 0;
        self.eos_guard = 0;
    }

    fn tick_playing(&mut self, dur: i64, pos: i64) {
        let crossfade = self.crossfade_enabled();
        let remaining = dur - pos;
        let lead = self.sf("bgm_fallback_prestart_sec", 5.0).unwrap_or(5.0);
        let window = (2.0_f64.max(8.0_f64.min(lead)) * 1_000_000_000.0) as i64;
        if crossfade
            && remaining <= window
            && remaining > 150_000_000
            && !self.prefired
            && self.bg_has_playlist()
            && !self.bg_playing
            && self.bg_enabled_setting()
            && self.bg_autoplay_setting()
        {
            self.prefired = true;
            self.resume_reason = "karaoke_end_overlap".into();
            self.bg_fade_in(Value::Null, 3000, true);
            self.log(format!("[CROSSFADE] BG pre-start fired ({:.1}s remaining)", remaining as f64 / 1e9));
        }
        if self.maybe_trim_end_silence(dur, pos) {
            return;
        }
        let audio_db = self.snap["level_db"].as_f64();
        let mut audio_is_live = audio_db.map(|d| d > -45.0).unwrap_or(false);
        if self.snap["engine_at_end"].as_bool() == Some(false) {
            audio_is_live = true;
        }
        let near_end = (dur - pos) <= 200_000_000;
        if near_end && !audio_is_live {
            self.eos_guard += 1;
            if self.eos_guard >= 2 {
                self.eos_guard = 0;
                self.timer(0, "_handle_media_end_safe", vec![]);
                return;
            }
        } else {
            self.eos_guard = 0;
        }
        let advanced = match self.last_pos {
            None => true,
            Some(l) => (pos - l) >= 20_000_000,
        };
        let paused = self.snap["paused"].as_bool().unwrap_or(false);
        if advanced || paused || audio_is_live {
            self.stalled = 0;
        } else if self.snap["karaoke_playing"].as_bool().unwrap_or(false) && pos > 0 && !near_end {
            self.stalled += 1;
        } else {
            self.stalled = 0;
        }
        self.last_pos = Some(pos);
        if self.stalled >= 20 {
            self.stalled = 0;
            self.eos_guard = 0;
            self.log("[TIME] Position stalled ~10s with silence; forcing end-safe handler".into());
            self.timer(0, "_handle_media_end_safe", vec![]);
        }
    }

    fn prefire_bgm_at_verified_audio_end(&mut self) -> bool {
        if self.sb("karaoke_auto_advance", false) {
            return false;
        }
        if !self.crossfade_enabled() {
            return false;
        }
        if self.prefired {
            return false;
        }
        if !self.bg_has_playlist() || self.bg_playing {
            return false;
        }
        if !self.bg_enabled_setting() || !self.bg_autoplay_setting() {
            return false;
        }
        self.prefired = true;
        self.resume_reason = "karaoke_verified_audio_tail".into();
        self.bg_fade_in(Value::Null, 3000, true);
        self.log("[CROSSFADE] BG fade started at verified audio end; karaoke visuals retained until their safe endpoint".into());
        true
    }

    fn db_text(db: Option<f64>) -> String {
        match db {
            None => "n/a".to_string(),
            Some(d) => format!("{:.1}", d),
        }
    }

    fn maybe_trim_end_silence(&mut self, dur_ns: i64, pos_ns: i64) -> bool {
        if !self.early_trim_enabled() {
            return false;
        }
        if self.mode != "cdg" && self.mode != "mp4" {
            return false;
        }
        if self.tail_started {
            return false;
        }
        if dur_ns <= 0 || pos_ns <= 0 {
            return false;
        }
        let elapsed = pos_ns as f64 / NS;
        let remain = (dur_ns - pos_ns) as f64 / NS;
        if elapsed < MIN_ELAPSED_S {
            self.accum = 0.0;
            self.last_tick = self.now;
            return false;
        }
        let db = self.snap["level_db"].as_f64();
        let meterless = db.is_none();
        if meterless {
            if !self.no_level_logged {
                self.log("[END-SILENCE] no level tap on this engine; using the file scan's verified audio endpoint alone".into());
                self.no_level_logged = true;
            }
        } else {
            self.no_level_logged = false;
        }
        let now = self.now;
        let last = self.last_tick;
        let dt = if last <= 0.0 { 0.5 } else { 0.0_f64.max(1.0_f64.min(now - last)) };
        self.last_tick = now;
        let mut threshold = self.end_trim_threshold();
        let dbt = Self::db_text(db);

        let audio_end = match self.audio_end {
            None => {
                self.accum = 0.0;
                if !self.unknown_logged {
                    self.unknown_logged = true;
                    self.log(format!("[END-AUDIO] trim suppressed; verified audio endpoint unavailable (pos={:.2}s remain={:.2}s db={})", elapsed, remain, dbt));
                }
                return false;
            }
            Some(e) => e,
        };
        let overlap = self.sf("bgm_overlap_before_audio_end_sec", 2.0).unwrap_or(0.0);
        if overlap > 0.0 && self.sb("karaoke_trim_verified_tail", true) && audio_end - overlap <= elapsed && elapsed < audio_end {
            self.prefire_bgm_at_verified_audio_end();
        }
        if elapsed < audio_end {
            self.accum = 0.0;
            if (now - self.hold_log_ts) >= 5.0 {
                self.hold_log_ts = now;
                self.log(format!("[END-AUDIO] holding; audio continues to {:.2}s (pos={:.2}s db={})", audio_end, elapsed, dbt));
            }
            return false;
        }
        let gate = self.snap["gate"].clone(); // true / false / "raise" / "missing"
        if self.sb("karaoke_trim_verified_tail", true) {
            if elapsed < audio_end + threshold {
                return false;
            }
            if !meterless && db.unwrap() > DB_THRESHOLD {
                return false;
            }
            self.prefire_bgm_at_verified_audio_end();
            let vend = match self.visual_end {
                Some(v) if self.visual_conf >= 0.85 => v,
                _ => return false,
            };
            if elapsed < vend {
                return false;
            }
            if self.mode == "cdg" && gate.as_bool() != Some(true) {
                return false; // missing, raising or not finished
            }
            self.tail_started = true;
            self.triggered = true;
            self.last_reason = "verified_audio_tail".into();
            self.auto_advance_next = self.sb("karaoke_auto_advance", false);
            self.log(format!("[END-SILENCE] skipping scanned silent tail at {:.2}s audio_end={:.2}s remaining={:.2}s", elapsed, audio_end, remain));
            self.timer(0, "_handle_media_end_safe", vec![json!("verified_silent_tail")]);
            return true;
        }

        // legacy (non-verified-tail) path
        self.prefire_bgm_at_verified_audio_end();
        let vend = match self.visual_end {
            Some(v) if self.visual_conf >= 0.85 => v,
            _ => {
                self.accum = 0.0;
                if (now - self.visual_unknown_ts) >= 10.0 {
                    self.visual_unknown_ts = now;
                    self.log(format!("[END-VISUAL] handoff suppressed; visual endpoint unverified mode={} reason={}", self.mode, self.visual_reason));
                }
                return false;
            }
        };
        if elapsed < vend {
            self.accum = 0.0;
            return false;
        }
        if meterless || db.unwrap() <= DB_THRESHOLD {
            self.accum += dt;
        } else {
            self.accum = 0.0;
        }
        let mut cdg_stale_for = 0.0_f64;
        let mut rem_sectors: Option<f64> = None;
        let mut cdg_done = false;
        if self.mode == "cdg" {
            if let Some(g) = self.snap["generation"].as_i64() {
                if self.cdg_gen != Some(g) {
                    self.cdg_gen = Some(g);
                    self.cdg_gen_ts = now;
                }
                let ts = if self.cdg_gen_ts == 0.0 { now } else { self.cdg_gen_ts };
                cdg_stale_for = now - ts;
            }
            rem_sectors = self.snap["sectors"].as_f64();
            if let Some(r) = rem_sectors {
                cdg_done = r <= CDG_SECTOR_THRESHOLD;
            }
        }
        let confident_end = remain <= CONFIDENT_REMAIN_S && elapsed >= vend;
        if confident_end {
            threshold = threshold.min(0.3_f64.max(CONFIDENT_MIN_S));
        }
        if (now - self.debug_ts) >= 30.0 {
            self.debug_ts = now;
            self.log(format!(
                "[END-SILENCE] db={} remain={:.2}s silent={:.2}/{:.2}s confident_end={} cdg_done={} cdg_stale={:.2}s",
                dbt,
                remain,
                self.accum,
                threshold,
                confident_end as i32,
                pyb(cdg_done),
                cdg_stale_for
            ));
        }
        if self.accum >= threshold {
            let near_end = remain <= NEAR_END_WINDOW_S;
            let mut reason = "";
            if near_end && elapsed >= vend {
                reason = "verified_audio_and_visual_tail";
            }
            if self.mode == "cdg" && reason.is_empty() {
                if let Some(r) = rem_sectors {
                    if (now - self.suppress_ts) >= 1.5 {
                        self.suppress_ts = now;
                        self.log(format!("[END-SILENCE] suppressed; CDG still active (sectors={:.0}, stale={:.2}s, remain={:.2}s)", r, cdg_stale_for, remain));
                    }
                }
                self.accum = self.accum.min(threshold);
                return false;
            }
            if !reason.is_empty() && self.mode == "cdg" && gate.as_str() != Some("missing") {
                let finished = match gate.as_bool() {
                    Some(b) => b,
                    None => true, // the gate raised: treated as finished
                };
                if !finished {
                    if (now - self.suppress_ts) >= 1.5 {
                        self.suppress_ts = now;
                        self.log("[END-SILENCE] suppressed; lyrics still ahead (CDG final-frame gate)".into());
                    }
                    self.accum = self.accum.min(threshold);
                    return false;
                }
            }
            if reason.is_empty() {
                return false;
            }
            self.tail_started = true;
            self.triggered = true;
            self.last_reason = reason.into();
            self.auto_advance_next = self.sb("karaoke_auto_advance", false);
            self.log(format!(
                "[END-SILENCE] verified tail complete reason={}; ending karaoke before EOS (db={}, silent={:.2}s, remain={:.2}s, cdg_done={}, cdg_stale={:.2}s)",
                reason,
                dbt,
                self.accum,
                remain,
                pyb(cdg_done),
                cdg_stale_for
            ));
            self.timer(0, "_handle_media_end_safe", vec![json!("verified_silent_tail")]);
            return true;
        }
        false
    }

    // ------------------------------------------------------------------------------------------------ media end hand-off
    fn handle_media_end(&mut self, trigger: &str) {
        let stop_active = self.snap["stop_in_progress"].as_bool().unwrap_or(false);
        let handoff = self.handoff_active;
        if stop_active || handoff {
            if !stop_active && handoff {
                self.log("[END] duplicate media-end ignored: handoff already active".into());
            }
            return;
        }
        let trigger = {
            let t = trigger.trim().to_lowercase();
            if t.is_empty() { "eos".to_string() } else { t }
        };
        self.overlay_eligible = trigger != "error";
        self.handoff_active = true;
        self.qstart("handoff_guard", 6000);
        self.cmd("host:_schedule_idle_full_gc", vec![]);
        let est = self.triggered;
        let early_reason = self.last_reason.clone();
        let mut early_auto = self.auto_advance_next;
        self.reset_end_silence_state();
        if !early_auto && self.snap["intro_loop"].as_bool().unwrap_or(false) {
            early_auto = true;
            self.pending_intro = true;
            self.log("[INTRO-LOOP] media-end → auto-advance into next song's intro loop".into());
        }
        if !early_reason.is_empty() {
            self.log(format!("[END] early-end handoff reason={} auto_advance={}", early_reason, pyb(early_auto)));
        }
        let mut can_overlap = self.crossfade_enabled()
            && !early_auto
            && !est
            && early_reason.is_empty()
            && self.bg_has_playlist()
            && !self.bg_playing
            && self.bg_enabled_setting()
            && self.bg_autoplay_setting();
        if can_overlap {
            self.resume_reason = "karaoke_end_overlap".into();
            self.log("Karaoke track ended, starting BG overlap fade...".into());
            can_overlap = self.start_bg_with_fade("media_end_overlap");
            if can_overlap {
                let overlap_ms = END_OVERLAP_MS.max(0);
                self.log(format!("Finishing karaoke teardown after {}ms overlap", overlap_ms));
                self.schedule_media_end_cleanup(overlap_ms, est, false);
                return;
            }
        }
        self.finish_media_end_cleanup(est, !early_auto);
        if early_auto {
            self.log(format!("[END] scheduling next queued karaoke after early end ({})", early_reason));
            let gen = self.snap["gen_current"].as_i64().unwrap_or(0);
            self.timer(450, "_auto_play_next_if_generation", vec![json!(gen), json!(early_reason)]);
        }
    }

    fn auto_play_next(&mut self, ended: i64, reason: &str) -> bool {
        let current = self.snap["gen_current"].as_i64().unwrap_or(0);
        if current != ended {
            self.log(format!("[PLAY-COMMAND] stale auto-advance ignored reason={} ended_generation={} current_generation={}", reason, ended, current));
            return false;
        }
        if self.snap["pending_token"].as_bool().unwrap_or(false) || self.snap["next_in_progress"].as_bool().unwrap_or(false) {
            self.log(format!("[PLAY-COMMAND] auto-advance yielded to active command reason={}", reason));
            return false;
        }
        self.cmd("host:play_next_file", vec![]);
        true
    }

    fn schedule_media_end_cleanup(&mut self, delay_ms: i64, est: bool, sbr: bool) {
        self.cleanup_gen += 1;
        self.sched_gen = self.cleanup_gen;
        self.cleanup_est = est;
        self.cleanup_sbr = sbr;
        self.qstop("media_end_cleanup");
        self.qstart("media_end_cleanup", delay_ms.max(0));
    }

    fn run_media_end_cleanup(&mut self) {
        if self.sched_gen != self.cleanup_gen {
            self.log("[END] stale media-end cleanup ignored".into());
            return;
        }
        if self.snap["stop_in_progress"].as_bool().unwrap_or(false) {
            self.log("[END] media-end cleanup skipped: teardown already in progress".into());
            return;
        }
        let (est, sbr) = (self.cleanup_est, self.cleanup_sbr);
        self.finish_media_end_cleanup(est, sbr);
    }

    fn cancel_pending(&mut self, reason: &str, clear_handoff: bool) {
        self.cleanup_gen += 1;
        self.qstop("media_end_cleanup");
        self.cleanup_est = false;
        self.cleanup_sbr = false;
        if clear_handoff {
            self.qstop("handoff_guard");
            self.handoff_active = false;
        }
        self.log(format!("[END] pending media-end cleanup cancelled reason={}", reason));
    }

    /// The harness stand-in for KaraokeApp.stop_playback: the parts that touch song-end state, in the real order.
    fn stop_playback(&mut self) {
        self.cmd("host:stop_playback", vec![]);
        self.cancel_pending("stop_playback", true);
        self.reset_end_silence_state();
    }

    fn finish_media_end_cleanup(&mut self, est: bool, sbr: bool) {
        self.cmd("host:_commit_pending_performance", vec![json!("song_completed")]);
        self.stop_playback();
        self.cmd("host:_flush_deferred_remote_adds", vec![]);
        if self.overlay_eligible {
            self.cmd("host:_mark_next_up_overlay_pending_after_completion", vec![json!("media_end")]);
        } else {
            self.cmd("host:_clear_next_up_overlay_pending", vec![]);
        }
        self.cmd("host:clear_now_singing", vec![]);
        self.cmd("host:_detach_video_sinks_now", vec![]);
        self.cmd("host:_recreate_video_surfaces", vec![]);
        self.cmd("host:_show_idle_background_after_karaoke", vec![json!("media_end_cleanup")]);
        if sbr {
            self.resume_after_karaoke(est);
        }
        // finally:
        self.qstop("media_end_cleanup");
        self.qstop("handoff_guard");
        self.handoff_active = false;
    }

    fn resume_after_karaoke(&mut self, est: bool) {
        self.log("Karaoke track ended, attempting to start background music...".into());
        let has_bg = true;
        let has_playlist = self.bg_has_playlist();
        let raw_playing = self.bg_playing;
        let effective = self.bg_effective();
        if has_bg && has_playlist && !effective {
            if raw_playing {
                self.log("[BG] stale is_playing detected at karaoke end; forcing BG reset".into());
                self.cmd("bg:stop", vec![]);
                self.bg_playing = false;
                self.cmd("bg:preload_current_track_paused", vec![]);
            }
            let delay = if est { 60 } else { 120 };
            self.log(format!("Starting background music with {}ms delay (end_silence={})...", delay, pyb(est)));
            self.schedule_bg_resume(delay, "karaoke_end");
        } else {
            self.log(format!(
                "Cannot start background music: has_bg_music={}, has_playlist={}, is_playing={}, effective_playing={}",
                pyb(has_bg),
                pyb(has_playlist),
                pyb(raw_playing),
                pyb(effective)
            ));
            if has_bg && has_playlist && effective {
                self.verify_gen += 1;
                let gen = self.verify_gen;
                self.verify_retried = false;
                self.verify_recovered = false;
                self.log(format!("[BG-HANDOFF] overlap already active at karaoke end; verification armed generation={}", gen));
                self.timer(1500, "_verify_bg_resume_started", vec![json!(gen)]);
            }
        }
    }

    // ------------------------------------------------------------------------------------------------ background music start
    fn schedule_bg_resume(&mut self, delay_ms: i64, reason: &str) {
        self.qstop("bg_resume");
        self.resume_reason = reason.to_string();
        self.qstart("bg_resume", delay_ms.max(0));
    }

    fn start_bg_with_fade(&mut self, _source: &str) -> bool {
        let reason = self.resume_reason.clone();
        self.cmd("bg:ensure_audible", vec![json!("bg_resume")]);
        self.resume_reason = "default".into();
        self.cmd("host:_clear_bg_transition_timers", vec![]);
        if !self.bg_enabled_setting() {
            self.cmd("bg:stop", vec![]);
            self.bg_playing = false;
            return false;
        }
        if !self.bg_autoplay_setting() {
            self.cmd("bg:stop", vec![]);
            self.bg_playing = false;
            return false;
        }
        if !self.snap["session_started"].as_bool().unwrap_or(false) && !self.snap["karaoke_played"].as_bool().unwrap_or(false) {
            self.log("[BG-HANDOFF] resume blocked: no BGM play pressed and no song played yet this session".into());
            return false;
        }
        if !self.bg_has_playlist() {
            return false;
        }
        let target = self.sf("bg_volume", 0.8).unwrap_or(0.8);
        let allow = reason == "karaoke_end_overlap" && self.crossfade_enabled();
        let fade_ms: i64 = if reason == "karaoke_end_overlap" || reason == "manual_stop" { 3000 } else { 1000 };
        self.bg_fade_in(json!(target), fade_ms, allow);
        let started = self.bg_playing;
        if !started {
            self.log(format!("[BG-HANDOFF] fade_in did not start reason={}", reason));
            self.verify_gen += 1;
            let gen = self.verify_gen;
            if reason != "verified_retry" && reason != "verified_recovery" {
                self.verify_retried = false;
                self.verify_recovered = false;
            }
            self.timer(250, "_verify_bg_resume_started", vec![json!(gen)]);
            return false;
        }
        self.timer(100, "update_bg_button_state", vec![]);
        self.timer(fade_ms + 100, "update_bg_button_state", vec![]);
        self.log(format!("[BG-HANDOFF] fade_in started reason={} duration_ms={}", reason, fade_ms));
        self.verify_gen += 1;
        let gen = self.verify_gen;
        if reason != "verified_retry" && reason != "verified_recovery" {
            self.verify_retried = false;
            self.verify_recovered = false;
        }
        self.timer(1500, "_verify_bg_resume_started", vec![json!(gen)]);
        true
    }

    fn verify_bg_resume(&mut self, generation: i64) {
        if generation != self.verify_gen {
            return;
        }
        if self.snap["karaoke_playing"].as_bool().unwrap_or(false) {
            return;
        }
        if !self.bg_has_playlist() || !self.bg_enabled_setting() || !self.bg_autoplay_setting() {
            return;
        }
        if self.bg_effective() {
            self.log(format!("[BG-HANDOFF] playback verified generation={}", generation));
            return;
        }
        if self.verify_retried {
            if self.verify_recovered {
                self.log(format!("[BG-HANDOFF] playback still inactive after bounded recovery generation={}", generation));
                return;
            }
            self.verify_recovered = true;
            self.log(format!("[BG-HANDOFF] playback still inactive after verified retry; advancing or rebuilding deck generation={}", generation));
            self.cmd("bg:recover_failed_resume", vec![]);
            if !self.snap["bg"]["recover_ok"].as_bool().unwrap_or(true) {
                self.log(format!("[BG-HANDOFF] bounded deck recovery failed generation={}", generation));
                return;
            }
            self.resume_reason = "verified_recovery".into();
            self.start_bg_with_fade("verified_recovery");
            return;
        }
        self.verify_retried = true;
        self.log(format!("[BG-HANDOFF] playback verification failed; resetting deck and retrying generation={}", generation));
        self.cmd("bg:stop", vec![]);
        self.bg_playing = false;
        self.cmd("bg:preload_current_track_paused", vec![]);
        self.resume_reason = "verified_retry".into();
        self.start_bg_with_fade("verified_retry");
    }
}
