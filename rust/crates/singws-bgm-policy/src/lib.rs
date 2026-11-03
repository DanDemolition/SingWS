//! Offline port of the DECISIONS of `BackgroundMusicPlayer` (0.2.18.1.py ~5073-6690) as a pure state machine.
//!
//! No clock, no engine, no I/O: every call is `event(t_ms, name, args, snapshot)` and returns the commands the Python player
//! would have issued (engine calls, host calls, single-shot timers, worker spawns) plus its resulting state. The snapshot is
//! everything the player could read from the outside world at that instant (engine position, settings, file stats, cached
//! analysis); commands whose RESULT the player consults come back in `snapshot.responses`.
//!
//! Proof only: nothing in the app uses this. Rules and surprises: docs/design/stage-e-bgm-policy.md. The replay protocol is
//! the output of tools/bgm_policy_scenarios.py; tools/bgm_policy_parity.py compares every command and the state after
//! every event. Keep the statement order of each function identical to the Python: a command's position in the list is part
//! of the contract (Python evaluates `still_next` with short-circuiting, `or 1.0` defaults, banker's rounding, and so on).

use serde_json::{json, Map, Value};
use singws_plan::Record;
use std::collections::BTreeMap;

pub const BG_SILENCE_LEVEL: f64 = 0.02;
pub const BG_SILENCE_MIN_S: f64 = 1.2;
pub const BG_SILENCE_TAIL_WINDOW: f64 = 45.0;
pub const UNKNOWN_ANALYSIS_PREGAIN: f64 = 1.0;
pub const STOP_FADE_MS: i64 = 1000;

type Sig = (String, Option<i64>, Option<i64>);

fn clamp01(x: f64) -> f64 {
    x.min(1.0).max(0.0)
}

fn fv(x: f64) -> Value {
    Value::from(x)
}

fn iv(x: i64) -> Value {
    Value::from(x)
}

/// Python `int(x)` for a finite float: truncate toward zero.
fn py_int(x: f64) -> i64 {
    x as i64
}

#[derive(Clone, Debug)]
enum Resume {
    Play,
    Fade(Option<f64>, i64, bool),
}

#[derive(Clone, Debug)]
enum Pending {
    Next { next_index: usize, path: String, primary: Option<i64> },
    Startup { gen: i64, path: String, target: f64 },
}

#[derive(Clone, Debug, Default)]
struct Snap {
    primary: Option<i64>,
    pos: f64,
    dur: f64,
    source_ended: bool,
    xf_finished: bool,
    lead: f64,
    meter: f64,
    engine_playing: bool,
    engine_paused: bool,
    master: f64,
    settle_ms: i64,
    settings: Map<String, Value>,
    live_volume: Option<f64>,
    karaoke: bool,
    files: BTreeMap<String, (bool, Option<i64>, Option<i64>)>,
    records: BTreeMap<String, Option<Record>>,
    loud: BTreeMap<String, Option<Map<String, Value>>>,
    responses: Map<String, Value>,
}

impl Snap {
    fn parse(v: &Value) -> Snap {
        let mut s = Snap::default();
        s.primary = v["primary"].as_i64();
        s.pos = v["pos"].as_f64().unwrap_or(0.0);
        s.dur = v["dur"].as_f64().unwrap_or(0.0);
        s.source_ended = v["source_ended"].as_bool().unwrap_or(false);
        s.xf_finished = v["xf_finished"].as_bool().unwrap_or(false);
        s.lead = v["lead"].as_f64().unwrap_or(0.0);
        s.meter = v["meter"].as_f64().unwrap_or(0.0);
        s.engine_playing = v["engine_playing"].as_bool().unwrap_or(false);
        s.engine_paused = v["engine_paused"].as_bool().unwrap_or(false);
        s.master = v["master"].as_f64().unwrap_or(0.0);
        s.settle_ms = v["settle_ms"].as_i64().unwrap_or(0);
        if let Some(m) = v["settings"].as_object() {
            s.settings = m.clone();
        }
        s.live_volume = v["live_volume"].as_f64();
        s.karaoke = v["karaoke"].as_bool().unwrap_or(false);
        if let Some(m) = v["files"].as_object() {
            for (k, e) in m {
                s.files.insert(k.clone(), (e[0].as_bool().unwrap_or(false), e[1].as_i64(), e[2].as_i64()));
            }
        }
        if let Some(m) = v["records"].as_object() {
            for (k, e) in m {
                s.records.insert(k.clone(), if e.is_null() { None } else { serde_json::from_value(e.clone()).ok() });
            }
        }
        if let Some(m) = v["loud"].as_object() {
            for (k, e) in m {
                s.loud.insert(k.clone(), e.as_object().cloned());
            }
        }
        if let Some(m) = v["responses"].as_object() {
            s.responses = m.clone();
        }
        s
    }

    fn setting_bool(&self, key: &str, default: bool) -> bool {
        match self.settings.get(key) {
            None => default,
            Some(Value::Bool(b)) => *b,
            Some(Value::Null) => false,
            Some(Value::Number(n)) => n.as_f64().map(|x| x != 0.0).unwrap_or(true),
            Some(Value::String(s)) => !s.is_empty(),
            Some(_) => true,
        }
    }

    fn setting_f64(&self, key: &str, default: f64) -> f64 {
        self.settings.get(key).and_then(|v| v.as_f64()).unwrap_or(default)
    }
}

/// What a fake-engine call answered.
enum Resp {
    Ok(bool),
    Raise,
}

pub struct Policy {
    playlist: Vec<String>,
    current_index: i64,
    is_playing: bool,
    volume: f64,
    enabled: bool,
    stop_after_current: bool,
    realign: bool,
    crossfade_active: bool,
    crossfade_target: Option<i64>,
    crossfade_duration_ms: i64,
    fade_gen: i64,
    xf_gen: i64,
    startup_gen: i64,
    startup_in_progress: bool,
    startup_resume: Option<Resume>,
    next_preload_in_progress: bool,
    next_preload_attempt: Option<(Option<i64>, String)>,
    unplayable: BTreeMap<String, Sig>,
    meter: f64,
    silence_accum: f64,
    silence_last_ts: f64,
    spawns: i64,
    pending: BTreeMap<i64, Pending>,
    // per event
    snap: Snap,
    now_s: f64,
    cmds: Vec<Value>,
    primary_present: bool,
}

impl Policy {
    /// `init` is the replay header: {paths, start_index, crossfade_ms, volume, init: snapshot}.
    pub fn new(init: &Value) -> Policy {
        let playlist = init["paths"].as_array().map(|a| a.iter().filter_map(|p| p.as_str().map(String::from)).collect()).unwrap_or_default();
        Policy {
            playlist,
            current_index: init["start_index"].as_i64().unwrap_or(0),
            is_playing: false,
            volume: init["volume"].as_f64().unwrap_or(0.8),
            enabled: true,
            stop_after_current: false,
            realign: false,
            crossfade_active: false,
            crossfade_target: None,
            crossfade_duration_ms: init["crossfade_ms"].as_i64().unwrap_or(5000),
            fade_gen: 0,
            xf_gen: 0,
            startup_gen: 0,
            startup_in_progress: false,
            startup_resume: None,
            next_preload_in_progress: false,
            next_preload_attempt: None,
            unplayable: BTreeMap::new(),
            meter: 0.0,
            silence_accum: 0.0,
            silence_last_ts: 0.0,
            spawns: 0,
            pending: BTreeMap::new(),
            snap: Snap::default(),
            now_s: 0.0,
            cmds: Vec::new(),
            primary_present: false,
        }
    }

    // ------------------------------------------------------------------------------------------------------------ plumbing
    fn cmd(&mut self, name: &str, args: Vec<Value>) {
        self.cmds.push(json!({"c": name, "a": args}));
    }

    fn timer(&mut self, ms: i64, kind: &str, args: Vec<Value>) {
        self.cmd("timer", vec![iv(ms), json!(kind), Value::Array(args)]);
    }

    fn resp(&self, name: &str) -> Resp {
        match self.snap.responses.get(name) {
            None => Resp::Ok(true),
            Some(Value::Object(m)) if m.contains_key("raise") => Resp::Raise,
            Some(Value::Bool(b)) => Resp::Ok(*b),
            Some(Value::Null) => Resp::Ok(false),
            Some(Value::Number(n)) => Resp::Ok(n.as_f64().map(|x| x != 0.0).unwrap_or(true)),
            Some(_) => Resp::Ok(true),
        }
    }

    fn resp_error_text(&self, name: &str) -> String {
        match self.snap.responses.get(name) {
            Some(Value::Object(m)) => m.get("raise").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            _ => String::new(),
        }
    }

    fn len(&self) -> i64 {
        self.playlist.len() as i64
    }

    fn file_at(&self, idx: i64) -> String {
        if idx >= 0 && (idx as usize) < self.playlist.len() {
            self.playlist[idx as usize].clone()
        } else {
            String::new()
        }
    }

    fn cur_file(&self) -> String {
        self.file_at(self.current_index)
    }

    fn get_times(&self) -> (f64, f64) {
        if self.primary_present {
            (self.snap.pos, self.snap.dur)
        } else {
            (0.0, 0.0)
        }
    }

    // --------------------------------------------------------------------------------------------------- volume + normalisation
    /// `_target_volume_from_ui`: the live slider value (clamped), else the saved `bg_volume` (NOT clamped), else 0.8.
    fn target_volume_from_ui(&self) -> f64 {
        if let Some(v) = self.snap.live_volume {
            return clamp01(v);
        }
        self.snap.setting_f64("bg_volume", 0.8)
    }

    fn sync_volume(&mut self) -> f64 {
        let t = clamp01(self.target_volume_from_ui());
        self.volume = t;
        self.cmd("set_master_volume", vec![fv(t)]);
        t
    }

    fn normalize_active(&self) -> bool {
        if self.snap.setting_bool("simple_audio_mode", true) {
            return false;
        }
        self.snap.setting_bool("bg_normalize_enabled", true)
    }

    /// `_bg_loudness_info_for_path`: cache lookup, starting the analysis on a miss.
    fn loudness_info(&mut self, path: &str) -> Option<Map<String, Value>> {
        if path.is_empty() || !self.normalize_active() {
            return None;
        }
        let info = self.snap.loud.get(path).cloned().flatten();
        if info.is_none() {
            self.cmd("analyze_loudness_async", vec![json!(path)]);
        }
        info
    }

    fn gain_linear(info: &Map<String, Value>) -> f64 {
        match info.get("gain_linear") {
            None => 1.0,
            Some(v) => {
                let f = v.as_f64().unwrap_or(1.0);
                if f == 0.0 { 1.0 } else { f }
            }
        }
    }

    /// `_bg_norm_factor_for_path`: the gain handed to prepare/crossfade (clamped 0.05..4.0).
    fn norm_factor(&mut self, path: &str) -> f64 {
        if !self.normalize_active() || path.is_empty() {
            return 1.0;
        }
        match self.loudness_info(path) {
            None => UNKNOWN_ANALYSIS_PREGAIN,
            Some(info) => Self::gain_linear(&info).min(4.0).max(0.05),
        }
    }

    fn set_norm_gain(&mut self, deck: &str, v: f64) {
        let name = if deck == "secondary" { "set_secondary_normalize_gain" } else { "set_primary_normalize_gain" };
        self.cmd(name, vec![fv(v)]);
    }

    /// `_refresh_bg_normalize`: the deck's gain is the UNCLAMPED cached gain (unlike `norm_factor`).
    fn refresh_normalize(&mut self, path: Option<String>, deck: &str) {
        if !self.normalize_active() {
            self.set_norm_gain(deck, 1.0);
            return;
        }
        let mut path = path;
        if path.is_none() && !self.playlist.is_empty() && self.current_index >= 0 && self.current_index < self.len() {
            path = Some(self.cur_file());
        }
        let Some(path) = path.filter(|p| !p.is_empty()) else {
            self.set_norm_gain(deck, 1.0);
            return;
        };
        match self.loudness_info(&path) {
            None => self.set_norm_gain(deck, UNKNOWN_ANALYSIS_PREGAIN),
            Some(info) => {
                let f = Self::gain_linear(&info);
                self.set_norm_gain(deck, f);
            }
        }
        // warm the next track's analysis so its gain is ready before the crossfade
        if !self.playlist.is_empty() {
            let nxt = self.file_at((self.current_index + 1).rem_euclid(self.len()));
            if !nxt.is_empty() {
                let _ = self.loudness_info(&nxt);
            }
        }
    }

    // ------------------------------------------------------------------------------------------------------------ unplayable files
    fn sig(&self, path: &str) -> Sig {
        match self.snap.files.get(path) {
            Some((true, s, m)) => (path.to_string(), *s, *m),
            _ => (path.to_string(), None, None),
        }
    }

    fn load_failure_is_permanent(&self, path: &str, error: &str) -> bool {
        let exists = self.snap.files.get(path).map(|f| f.0).unwrap_or(false);
        if !exists {
            return true;
        }
        let m = error.to_lowercase();
        m.contains("bass error 2") || m.contains("bass_error_fileopen")
    }

    fn mark_unplayable(&mut self, path: &str, error: &str) -> bool {
        if !self.load_failure_is_permanent(path, error) {
            return false;
        }
        let sig = self.sig(path);
        if self.unplayable.get(path) != Some(&sig) {
            self.unplayable.insert(path.to_string(), sig);
        }
        true
    }

    /// `_bg_track_is_unplayable`: a changed file is eligible again (and the entry is forgotten).
    fn is_unplayable(&mut self, path: &str) -> bool {
        let Some(saved) = self.unplayable.get(path).cloned() else { return false };
        let sig = self.sig(path);
        if saved != sig {
            self.unplayable.remove(path);
            return false;
        }
        true
    }

    fn next_playable_index(&mut self, step: i64) -> Option<i64> {
        if self.playlist.is_empty() {
            return None;
        }
        let dir = if step >= 0 { 1 } else { -1 };
        let n = self.len();
        for offset in 1..=n {
            let index = (self.current_index + dir * offset).rem_euclid(n);
            let p = self.file_at(index);
            if !self.is_unplayable(&p) {
                return Some(index);
            }
        }
        None
    }

    // ---------------------------------------------------------------------------------------------------------- analysis rules
    fn record(&self, path: &str) -> Option<&Record> {
        self.snap.records.get(path).and_then(|r| r.as_ref())
    }

    /// `_bgm_content_window`
    fn content_window(&self, path: &str, duration: Option<f64>) -> (f64, f64) {
        if !self.snap.setting_bool("seamless_transitions_enabled", true) {
            return (0.0, duration.unwrap_or(0.0));
        }
        let rec = self.record(path);
        let wd = match duration {
            Some(d) => d,
            None => rec.map(|r| r.duration).unwrap_or(0.0),
        };
        singws_plan::bgm_audio_window(rec, wd)
    }

    /// `_adaptive_crossfade_duration_ms` -> milliseconds (the reason string is log-only)
    fn adaptive_ms(&self) -> i64 {
        let configured = 2000.max(8000.min(self.crossfade_duration_ms));
        if !self.snap.setting_bool("seamless_transitions_enabled", true) {
            return configured;
        }
        if self.playlist.is_empty() {
            return configured;
        }
        if self.current_index < 0 || self.current_index >= self.len() {
            return configured; // Python: IndexError -> "safe_fallback"
        }
        let file = self.cur_file();
        let (seconds, _reason) = singws_plan::select_crossfade(self.record(&file), configured as f64 / 1000.0);
        (seconds * 1000.0).round_ties_even() as i64
    }

    // -------------------------------------------------------------------------------------------------------------- preload
    fn prepare_next(&mut self) {
        if self.playlist.is_empty() || self.crossfade_active || self.next_preload_in_progress {
            return;
        }
        let Some(next_index) = self.next_playable_index(1) else { return };
        let path = self.file_at(next_index);
        let primary = self.snap.primary;
        let attempt = (primary, path.clone());
        if self.next_preload_attempt.as_ref() == Some(&attempt) {
            return;
        }
        self.next_preload_attempt = Some(attempt);
        self.next_preload_in_progress = true;
        self.cmd("invalidate_secondary_preload", vec![]);
        let norm_gain = self.norm_factor(&path);
        self.spawns += 1;
        let n = self.spawns;
        self.cmd("spawn", vec![json!("next"), iv(n)]);
        self.cmd("prepare_primary", vec![json!(path), fv(norm_gain)]);
        self.pending.insert(n, Pending::Next { next_index: next_index as usize, path, primary });
    }

    fn finish_next(&mut self, next_index: usize, path: String, primary: Option<i64>, error: &str) {
        self.next_preload_in_progress = false;
        let prepared = error.is_empty();
        // short-circuit order matters: next_playable_index() can forget a changed file
        let mut still_next = primary == self.snap.primary && !self.crossfade_active && !self.playlist.is_empty();
        if still_next {
            still_next = Some(next_index as i64) == self.next_playable_index(1);
        }
        if still_next {
            still_next = path == self.file_at(next_index as i64);
        }
        if !still_next || !prepared {
            if prepared {
                self.cmd("discard_prepared_primary", vec![json!(path)]);
            }
            if !error.is_empty() && self.mark_unplayable(&path, error) {
                self.next_preload_attempt = None;
                self.timer(0, "_prepare_next_background_track", vec![]);
            }
            return;
        }
        self.cmd("install_prepared_secondary", vec![json!(path)]);
        // a refused/raising attach only logs
    }

    // ------------------------------------------------------------------------------------------------------------ crossfade
    fn check_silence(&mut self, time_left: f64) -> bool {
        let enabled = self.snap.setting_bool("bg_end_silence_trim_enabled", false);
        if !enabled || self.snap.dur <= 0.0 {
            self.silence_accum = 0.0;
            self.silence_last_ts = 0.0;
            return false;
        }
        let now = self.now_s;
        let last = self.silence_last_ts;
        self.silence_last_ts = now;
        let mut dt = if last > 0.0 { now - last } else { 0.0 };
        if dt <= 0.0 || dt > 2.0 {
            dt = 0.0;
        }
        if time_left <= BG_SILENCE_TAIL_WINDOW && self.meter <= BG_SILENCE_LEVEL {
            self.silence_accum += dt;
        } else {
            self.silence_accum = 0.0;
        }
        if self.silence_accum >= BG_SILENCE_MIN_S {
            self.silence_accum = 0.0;
            return true;
        }
        false
    }

    fn start_crossfade(&mut self, target_index: Option<i64>, duration_ms: Option<i64>) -> bool {
        self.silence_accum = 0.0;
        self.silence_last_ts = 0.0;
        if self.crossfade_active || self.playlist.is_empty() {
            return false;
        }
        self.sync_volume();
        let next_index = match target_index {
            Some(t) => Some(t.rem_euclid(self.len())),
            None => self.next_playable_index(1),
        };
        let Some(next_index) = next_index else { return false };
        let next_file = self.file_at(next_index);
        let mut selected = 100.max(8000.min(duration_ms.unwrap_or(self.crossfade_duration_ms)));
        let (position, duration) = self.get_times();
        if duration > 0.0 {
            let cur = self.cur_file();
            let (_s, end) = self.content_window(&cur, Some(duration));
            let lead = self.snap.lead;
            selected = selected.min(100.max(py_int((end - position - lead) * 1000.0)));
        }
        let norm_gain = self.norm_factor(&next_file);
        let (cs, ce) = self.content_window(&next_file, None);
        if ce > cs {
            selected = selected.min(100.max(py_int((ce - cs) * 1000.0)));
        }
        self.cmd("start_crossfade", vec![json!(next_file), iv(selected), fv(norm_gain), fv(cs)]);
        match self.resp("start_crossfade") {
            Resp::Raise => {
                let text = self.resp_error_text("start_crossfade");
                self.mark_unplayable(&next_file, &text);
                return false;
            }
            Resp::Ok(false) => return false,
            Resp::Ok(true) => {}
        }
        self.crossfade_active = true;
        self.crossfade_target = Some(next_index);
        self.xf_gen += 1;
        true
    }

    fn complete_crossfade(&mut self, gen: Option<i64>) {
        if let Some(g) = gen {
            if g != self.xf_gen {
                return;
            }
        }
        if !self.crossfade_active {
            return;
        }
        self.cmd("complete_crossfade", vec![]);
        match self.resp("complete_crossfade") {
            Resp::Ok(true) => {}
            _ => return,
        }
        self.current_index = match self.crossfade_target {
            Some(t) => t.rem_euclid(self.len()),
            None => (self.current_index + 1).rem_euclid(self.len()),
        };
        self.crossfade_target = None;
        self.crossfade_active = false;
        self.cmd("host:update_bg_track_display", vec![]);
        self.timer(200, "_start_artwork_precache", vec![]);
    }

    // ------------------------------------------------------------------------------------------------------------------ tick
    fn tick(&mut self) {
        // _bgm_update_meter
        self.meter = self.meter * 0.70 + self.snap.meter * 0.30;
        if !self.is_playing || !self.primary_present {
            return;
        }
        if self.crossfade_active {
            if self.snap.xf_finished {
                let g = self.xf_gen;
                self.complete_crossfade(Some(g));
            }
            return;
        }
        if self.snap.source_ended {
            if self.stop_after_current {
                self.stop_after_current = false;
                self.timer(0, "stop", vec![]);
            } else if !self.start_crossfade(None, Some(100)) {
                self.timer(0, "next_track", vec![]);
            }
            self.timer(100, "update_bg_button_state", vec![]);
            return;
        }
        if self.stop_after_current {
            return;
        }
        let (pos, dur) = self.get_times();
        self.prepare_next();
        let cur = self.cur_file();
        let (_start, audio_end) = self.content_window(&cur, Some(dur));
        let lead = self.snap.lead;
        let time_left = audio_end - pos - lead;
        let adaptive_ms = self.adaptive_ms();
        let crossfade_start_time = adaptive_ms as f64 / 1000.0;
        if self.check_silence(dur - pos) {
            self.start_crossfade(None, Some(100));
            return;
        }
        if dur > 0.0 && time_left <= crossfade_start_time {
            let ms = adaptive_ms.min(100.max(py_int(time_left * 1000.0)));
            self.start_crossfade(None, Some(ms));
        }
    }

    // ------------------------------------------------------------------------------------------------------------ transport
    fn stop(&mut self) {
        self.startup_gen += 1;
        self.startup_in_progress = false;
        self.startup_resume = None;
        self.fade_gen += 1;
        self.xf_gen += 1;
        self.cmd("stop", vec![]);
        self.primary_present = false;
        self.crossfade_active = false;
        self.crossfade_target = None;
        self.is_playing = false;
        self.meter = 0.0;
    }

    fn pause(&mut self) {
        self.fade_gen += 1;
        self.cmd("pause", vec![]);
        self.is_playing = false;
        self.meter = 0.0;
    }

    fn preload_current_track_paused(&mut self) -> bool {
        if self.playlist.is_empty() {
            return false;
        }
        if self.primary_present {
            self.sync_volume();
            return true;
        }
        let current_file = self.cur_file();
        let target = self.sync_volume();
        self.cmd("set_master_volume", vec![fv(0.0)]);
        self.cmd("load", vec![json!(current_file), json!(true)]);
        if let Resp::Raise = self.resp("load") {
            return false; // the engine volume is left at 0 until the next Play sets it again
        }
        self.primary_present = true;
        self.cmd("set_master_volume", vec![fv(target)]);
        self.refresh_normalize(Some(current_file), "primary");
        self.is_playing = false;
        true
    }

    fn play(&mut self) {
        if self.playlist.is_empty() {
            return;
        }
        if self.snap.karaoke {
            return;
        }
        if !self.enabled {
            return;
        }
        if self.startup_in_progress {
            self.startup_resume = Some(Resume::Play);
            return;
        }
        if self.realign && self.primary_present && !self.is_playing {
            self.cmd("stop", vec![]);
            self.primary_present = false;
        }
        self.realign = false;
        let target = self.sync_volume();
        if self.volume <= 0.001 {
            self.volume = target;
        }
        if !self.primary_present && !self.preload_current_track_paused() {
            return;
        }
        self.refresh_normalize(None, "primary");
        self.cmd("set_master_volume", vec![fv(target)]);
        self.cmd("play", vec![]);
        match self.resp("play") {
            Resp::Ok(true) => {}
            _ => return,
        }
        self.cmd("set_master_volume", vec![fv(target)]);
        self.is_playing = true;
    }

    fn next_track(&mut self) {
        if self.playlist.is_empty() {
            return;
        }
        let was_playing = self.is_playing;
        if was_playing {
            if self.crossfade_active {
                return;
            }
            let t = self.next_playable_index(1);
            if let Some(t) = t {
                if self.start_crossfade(Some(t), None) {
                    return;
                }
            }
        }
        let Some(t) = self.next_playable_index(1) else {
            self.stop();
            return;
        };
        self.current_index = t;
        self.cmd("host:update_bg_track_display", vec![]);
        self.stop();
        self.timer(200, "_start_artwork_precache", vec![]);
        if was_playing {
            self.play();
        }
    }

    fn previous_track(&mut self) {
        if self.playlist.is_empty() {
            return;
        }
        let was_playing = self.is_playing;
        if was_playing {
            if self.crossfade_active {
                return;
            }
            let t = (self.current_index - 1).rem_euclid(self.len());
            if self.start_crossfade(Some(t), None) {
                return;
            }
        }
        self.current_index = (self.current_index - 1).rem_euclid(self.len());
        self.cmd("host:update_bg_track_display", vec![]);
        self.stop();
        self.timer(200, "_start_artwork_precache", vec![]);
        if was_playing {
            self.play();
        }
    }

    // -------------------------------------------------------------------------------------------------------------- fades
    fn stop_with_fade(&mut self, ms: i64) {
        if !self.primary_present || !self.is_playing || ms <= 0 {
            self.stop();
            return;
        }
        self.fade_gen += 1;
        self.xf_gen += 1;
        self.crossfade_active = false;
        self.crossfade_target = None;
        self.cmd("cancel_crossfade", vec![]);
        self.next_preload_attempt = None;
        let gen = self.fade_gen;
        self.cmd("slide_master_volume", vec![fv(0.0), iv(ms)]);
        self.timer(ms + 30, "_stop_after_stop_fade", vec![iv(gen)]);
    }

    fn stop_after_stop_fade(&mut self, gen: i64) {
        if gen != self.fade_gen {
            return;
        }
        self.stop();
        self.volume = self.target_volume_from_ui();
        let v = self.volume;
        self.cmd("set_master_volume", vec![fv(v)]);
    }

    fn stop_after_fade(&mut self, gen: i64) {
        if gen != self.fade_gen {
            return;
        }
        self.pause();
        self.volume = self.target_volume_from_ui();
    }

    fn fade_out(&mut self, ms: i64) {
        if !self.primary_present || !self.is_playing {
            return;
        }
        self.fade_gen += 1;
        self.xf_gen += 1;
        self.crossfade_active = false;
        self.crossfade_target = None;
        self.cmd("cancel_crossfade", vec![]);
        self.next_preload_attempt = None;
        let gen = self.fade_gen;
        self.cmd("slide_master_volume", vec![fv(0.0), iv(ms)]);
        let settle = 0.max(self.snap.settle_ms);
        self.timer(1.max(ms + settle), "_bgm_stop_after_fade", vec![iv(gen)]);
    }

    fn fade_in(&mut self, target: Option<f64>, ms: i64, allow: bool) {
        if self.snap.karaoke && !allow {
            return;
        }
        if !self.enabled {
            return;
        }
        let target = match target {
            Some(t) => t,
            None => self.target_volume_from_ui(),
        };
        if self.startup_in_progress {
            self.startup_resume = Some(Resume::Fade(Some(target), ms, allow));
            return;
        }
        let target = clamp01(target);
        self.volume = target;
        if !self.primary_present && !self.preload_current_track_paused() {
            return;
        }
        self.fade_gen += 1;
        self.refresh_normalize(None, "primary");
        self.cmd("set_master_volume", vec![fv(0.0)]);
        self.cmd("play", vec![]);
        match self.resp("play") {
            Resp::Ok(true) => {}
            _ => return,
        }
        self.is_playing = true;
        self.cmd("slide_master_volume", vec![fv(target), iv(ms)]);
    }

    fn ensure_audible(&mut self) {
        if !self.is_playing {
            return;
        }
        let paused = self.snap.engine_paused;
        let master = self.snap.master;
        let really_playing = self.snap.engine_playing;
        if really_playing && !paused && master > 0.05 {
            return;
        }
        let target = self.target_volume_from_ui();
        self.fade_gen += 1;
        if paused || !really_playing {
            self.cmd("set_master_volume", vec![fv(0.0)]);
            self.cmd("play", vec![]);
        }
        self.cmd("slide_master_volume", vec![fv(target), iv(500)]);
    }

    fn cancel_pending_fades(&mut self, restore: bool) {
        self.fade_gen += 1;
        self.xf_gen += 1;
        self.crossfade_active = false;
        self.crossfade_target = None;
        self.cmd("cancel_crossfade", vec![]);
        self.next_preload_attempt = None;
        if !restore {
            return;
        }
        let t = clamp01(self.target_volume_from_ui());
        self.volume = t;
        self.cmd("set_master_volume", vec![fv(t)]);
    }

    fn set_volume(&mut self, v: f64) {
        self.volume = clamp01(v);
        let x = self.volume;
        self.cmd("set_master_volume", vec![fv(x)]);
    }

    fn set_enabled(&mut self, flag: bool) {
        self.enabled = flag;
        if self.enabled {
            self.volume = self.target_volume_from_ui();
        } else if self.is_playing {
            self.fade_out(500);
        }
    }

    fn init_volume(&mut self) {
        let t = self.sync_volume();
        self.cmd("set_master_volume", vec![fv(t)]);
    }

    fn attach(&mut self) {
        let floor = self.snap.setting_f64("bgm_fade_in_floor_db", -60.0);
        self.cmd("set_fade_in_floor_db", vec![fv(floor)]);
    }

    // ------------------------------------------------------------------------------------------------------ startup preload
    fn preload_async(&mut self) -> bool {
        if self.playlist.is_empty() {
            return false;
        }
        if self.primary_present {
            return true;
        }
        if self.startup_in_progress {
            return true;
        }
        let current_file = self.cur_file();
        let target = self.sync_volume();
        let norm_gain = self.norm_factor(&current_file);
        self.startup_gen += 1;
        let gen = self.startup_gen;
        self.startup_in_progress = true;
        self.spawns += 1;
        let n = self.spawns;
        self.cmd("spawn", vec![json!("startup"), iv(n)]);
        self.cmd("prepare_primary", vec![json!(current_file), fv(norm_gain)]);
        self.pending.insert(n, Pending::Startup { gen, path: current_file, target });
        true
    }

    fn run_resume(&mut self, r: Resume) {
        match r {
            Resume::Play => self.play(),
            Resume::Fade(t, ms, allow) => self.fade_in(t, ms, allow),
        }
    }

    fn finish_startup(&mut self, gen: i64, path: String, target: f64, error: &str) {
        let resume = self.startup_resume.clone();
        let prepared = error.is_empty();
        let stale = gen != self.startup_gen
            || self.playlist.is_empty()
            || self.cur_file() != path;
        if stale || !prepared {
            if prepared {
                self.cmd("discard_prepared_primary", vec![json!(path)]);
            }
            if gen == self.startup_gen {
                self.startup_in_progress = false;
                self.startup_resume = None;
            }
            return;
        }
        self.cmd("install_prepared_primary", vec![json!(path), fv(target)]);
        let installed = matches!(self.resp("install_prepared_primary"), Resp::Ok(true));
        self.startup_in_progress = false;
        self.startup_resume = None;
        if !installed {
            if let Some(r) = resume {
                self.run_resume(r);
            }
            return;
        }
        self.primary_present = true;
        self.is_playing = false;
        if let Some(r) = resume {
            self.run_resume(r);
        }
    }

    // ------------------------------------------------------------------------------------------------------------ dispatch
    pub fn state(&self) -> Value {
        json!({
            "current_index": self.current_index,
            "is_playing": self.is_playing,
            "crossfade_active": self.crossfade_active,
            "crossfade_target": self.crossfade_target,
            "stop_after_current": self.stop_after_current,
            "volume": self.volume,
            "enabled": self.enabled,
            "meter": self.meter,
            "silence_accum": self.silence_accum,
            "fade_gen": self.fade_gen,
            "xf_gen": self.xf_gen,
            "startup_gen": self.startup_gen,
            "startup_in_progress": self.startup_in_progress,
            "next_preload_in_progress": self.next_preload_in_progress,
            "realign": self.realign,
            "unplayable": self.unplayable.keys().cloned().collect::<Vec<_>>(),
        })
    }

    /// One event in; (commands, return value) out.
    pub fn event(&mut self, t_ms: i64, ev: &str, a: &Value, snap: &Value) -> (Vec<Value>, Value) {
        self.snap = Snap::parse(snap);
        self.now_s = t_ms as f64 / 1000.0;
        self.cmds = Vec::new();
        self.primary_present = self.snap.primary.is_some();
        let mut ret = Value::Null;
        match ev {
            "tick" => self.tick(),
            "play" => self.play(),
            "pause" => self.pause(),
            "stop" => self.stop(),
            "stop_with_fade" => self.stop_with_fade(a["ms"].as_i64().unwrap_or(STOP_FADE_MS)),
            "next" => self.next_track(),
            "prev" => self.previous_track(),
            "fade_out" => self.fade_out(a["ms"].as_i64().unwrap_or(1000)),
            "fade_in" => self.fade_in(a["target"].as_f64(), a["ms"].as_i64().unwrap_or(1000), a["allow"].as_bool().unwrap_or(false)),
            "ensure_audible" => self.ensure_audible(),
            "cancel_pending" => self.cancel_pending_fades(a["restore"].as_bool().unwrap_or(true)),
            "set_volume" => self.set_volume(a["v"].as_f64().unwrap_or(0.0)),
            "stop_after" => self.stop_after_current = a["v"].as_bool().unwrap_or(false),
            "enabled" => self.set_enabled(a["v"].as_bool().unwrap_or(false)),
            "preload" => ret = Value::Bool(self.preload_current_track_paused()),
            "preload_async" => ret = Value::Bool(self.preload_async()),
            "init_volume" => self.init_volume(),
            "attach" => self.attach(),
            "playlist" => {
                self.playlist = a["paths"].as_array().map(|x| x.iter().filter_map(|p| p.as_str().map(String::from)).collect()).unwrap_or_default();
                self.current_index = a["index"].as_i64().unwrap_or(0);
                self.realign = a["realign"].as_bool().unwrap_or(false);
            }
            "set" => {} // world mutation only: the next snapshot carries it
            "timer" => {
                let kind = a["kind"].as_str().unwrap_or("");
                let gen = a["args"].get(0).and_then(|v| v.as_i64());
                match kind {
                    "stop" => self.stop(),
                    "next_track" => self.next_track(),
                    "update_bg_button_state" => self.cmd("host:update_bg_button_state", vec![]),
                    "_start_artwork_precache" => self.cmd("host:artwork_precache", vec![]),
                    "_prepare_next_background_track" => self.prepare_next(),
                    "_complete_crossfade" => self.complete_crossfade(gen),
                    "_stop_after_stop_fade" => self.stop_after_stop_fade(gen.unwrap_or(-1)),
                    "_bgm_stop_after_fade" => self.stop_after_fade(gen.unwrap_or(-1)),
                    _ => {}
                }
            }
            "finish" => {
                let n = a["spawn"].as_i64().unwrap_or(0);
                let error = a["error"].as_str().unwrap_or("").to_string();
                match self.pending.remove(&n) {
                    Some(Pending::Next { next_index, path, primary, .. }) => self.finish_next(next_index, path, primary, &error),
                    Some(Pending::Startup { gen, path, target, .. }) => self.finish_startup(gen, path, target, &error),
                    None => {}
                }
            }
            _ => {}
        }
        (std::mem::take(&mut self.cmds), ret)
    }
}
