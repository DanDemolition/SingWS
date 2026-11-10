//! W2: the song lifecycle of `karafun_web_host.KaraFunWebHost` (start gating, "heard" start, display clock, pause, end rules, never-started watchdog, recovery,
//! seek clamp, controls, finish), transcribed from the Python (the oracle). Pure: the caller passes the monotonic time and a `World` snapshot of the things
//! the host reads from the app and the service; the machine answers with the ordered effects to perform (JSON arrays, see `action` names below).
//! Cases whose Python spelling is not modelled (string-valued numbers, non-ASCII text in a repr) are DECLINED with `Err`, never guessed.
use serde_json::{json, Value};

pub const NEVER_STARTED_AFTER_S: f64 = 60.0;
pub const RECOVER_START_WITHIN_S: f64 = 150.0;
pub const MAX_RECOVERIES: i64 = 3;
pub const SAT_AT_END_S: f64 = 2.5;
pub const END_MARGIN_S: f64 = 1.5;
pub const SEEK_END_GUARD_S: f64 = 8.0;

type R<T> = Result<T, String>;

/// What the host reads from outside at the moment of an event.
#[derive(Debug, Clone, Default)]
pub struct World {
    pub service_present: bool,
    pub service_state: String,
    pub has_web: bool,
    /// `app._karafun_active_entry() is asked["entry"]`
    pub entry_current: bool,
    /// `app._active_external_karafun is asked["active"]`
    pub active_same: bool,
    /// `service.stop()/start()` raises with this text during a recovery
    pub restart_error: Option<String>,
}

#[derive(Debug, Clone)]
struct Asked {
    id: i64,
    title: String,
    heard: bool,
    sent: bool,
    at: f64,
    limit: f64,
    key: i64,
    tempo: i64,
    recoveries: i64,
    last_position: Option<f64>,
    resume_at: Option<f64>,
    gen: u64,
}

#[derive(Debug, Clone)]
pub struct Host {
    asked: Option<Asked>,
    song_open: bool,
    sat_since: Option<f64>,
    stray_stopped_at: f64,
    pub state: (String, String),
    status: Value,
    gens: u64,
    watchdogs: Vec<u64>,
    paused_at: Option<f64>,
    key_current: Option<i64>,
    tempo_current: Option<i64>,
}

pub enum Event<'a> {
    Start { song_id: Option<i64>, enabled: bool, key: Option<i64>, tempo: Option<&'a Value>, title: &'a str },
    Service { state: &'a str, detail: &'a str },
    Status(&'a Value),
    PageError(&'a Value),
    Watchdog,
    TogglePause,
    SeekTo(f64),
    ApplyKeyTempo { key: i64, tempo: i64 },
    OnFinish,
}

fn truthy(v: &Value) -> bool {
    crate::truthy(v)
}

enum Fl {
    Type,
    Decline(String),
}

/// Python `float(x or 0.0)`.
fn pyfloat_or0(v: Option<&Value>) -> Result<f64, Fl> {
    match v {
        None | Some(Value::Null) => Ok(0.0),
        Some(Value::Bool(b)) => Ok(if *b { 1.0 } else { 0.0 }),
        Some(Value::Number(n)) => Ok(n.as_f64().unwrap_or(0.0)),
        Some(Value::String(s)) if s.is_empty() => Ok(0.0),
        Some(Value::String(_)) => Err(Fl::Decline("string-valued number".into())),
        Some(Value::Array(a)) if a.is_empty() => Ok(0.0),
        Some(Value::Object(o)) if o.is_empty() => Ok(0.0),
        Some(_) => Err(Fl::Type),
    }
}

/// Python `repr()` of a str, for printable ASCII only.
fn repr(s: &str) -> R<String> {
    if !s.chars().all(|c| (' '..='~').contains(&c)) {
        return Err("repr of non-ASCII or control text".into());
    }
    let q = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::new();
    out.push(q);
    for c in s.chars() {
        if c == '\\' || c == q {
            out.push('\\');
        }
        out.push(c);
    }
    out.push(q);
    Ok(out)
}

fn chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn f0(x: f64) -> String {
    format!("{x:.0}")
}

impl Default for Host {
    fn default() -> Self {
        Self::new()
    }
}

impl Host {
    pub fn new() -> Self {
        Host {
            asked: None,
            song_open: false,
            sat_since: None,
            stray_stopped_at: 0.0,
            state: ("idle".into(), String::new()),
            status: json!({}),
            gens: 0,
            watchdogs: vec![],
            paused_at: None,
            key_current: None,
            tempo_current: None,
        }
    }

    pub fn step(&mut self, ev: Event, w: &World, now: f64) -> R<Vec<Value>> {
        let mut o = Vec::new();
        match ev {
            Event::Start { song_id, enabled, key, tempo, title } => {
                let id = match song_id {
                    Some(i) if enabled => i,
                    _ => {
                        o.push(json!(["returns", false]));
                        return Ok(o);
                    }
                };
                let tempo_v = match tempo {
                    None | Some(Value::Null) => 100,
                    Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)).ok_or("tempo")?,
                    Some(Value::Bool(b)) => *b as i64,
                    Some(_) => return Err("tempo type not modelled".into()),
                };
                let key_i = key.unwrap_or(0);
                self.gens += 1;
                self.asked = Some(Asked { id, title: title.to_string(), heard: false, sent: false, at: now, limit: NEVER_STARTED_AFTER_S, key: key_i, tempo: tempo_v,
                                          recoveries: 0, last_position: None, resume_at: None, gen: self.gens });
                self.sat_since = None;
                self.song_open = true;
                o.push(json!(["entry_wall", "karafun_pending_at"]));
                self.paused_at = None;
                self.key_current = Some(0);
                self.tempo_current = Some(100);
                o.push(json!(["entry_set", "karafun_key_current", 0]));
                o.push(json!(["entry_set", "karafun_tempo_current", 100]));
                o.push(json!(["entry_status", "launching", "Starting KaraFun...", true]));
                let keytxt = match key {
                    Some(k) => k.to_string(),
                    None => "None".into(),
                };
                o.push(json!(["log", format!("start id={id} title={} key={keytxt} tempo={tempo_v}", repr(title)?)]));
                o.push(json!(["ensure_service"]));
                if w.service_state == "ready" {
                    self.send_play(w, now, &mut o);
                } else if w.service_state == "error" {
                    let why = if self.state.1.is_empty() { "KaraFun's web player could not start".to_string() } else { self.state.1.clone() };
                    self.fail(&why, w, &mut o)?;
                }
                self.arm_watchdog(&mut o);
                o.push(json!(["returns", true]));
            }
            Event::Service { state, detail } => {
                self.state = (state.to_string(), detail.to_string());
                o.push(json!(["log", format!("state={state} {detail}").trim_end().to_string()]));
                self.state_changed(state, detail, w, now, &mut o)?;
            }
            Event::Status(m) => {
                self.status = m.clone();
                let playing = m.get("playing").map(truthy).unwrap_or(false) || m.get("moving").map(truthy).unwrap_or(false);
                if self.asked.is_none() && !self.song_open && playing {
                    self.stop_stray(w, now, &mut o);
                }
                self.follow(w, now, &mut o)?;
            }
            Event::PageError(m) => {
                // kind == "error": text = str(message.get("message") or "")
                let text = match m {
                    Value::Null => String::new(),
                    Value::String(s) => s.clone(),
                    Value::Bool(false) => String::new(),
                    Value::Number(n) if n.as_f64() == Some(0.0) => String::new(),
                    Value::Array(a) if a.is_empty() => String::new(),
                    Value::Object(ob) if ob.is_empty() => String::new(),
                    _ => return Err("page error text not a string".into()),
                };
                o.push(json!(["log", format!("page error: {text}")]));
                if let Some(a) = &self.asked {
                    if !a.heard && text.starts_with("Play") {
                        self.fail(&text, w, &mut o)?;
                    }
                }
            }
            Event::Watchdog => {
                if self.watchdogs.is_empty() {
                    return Ok(o);
                }
                let gen = self.watchdogs.remove(0);
                self.check(gen, w, now, &mut o)?;
            }
            Event::TogglePause => {
                let ok = self.active(w) && self.asked.as_ref().map(|a| a.heard).unwrap_or(false);
                if ok {
                    let playing = self.status.get("playing").map(truthy).unwrap_or(false);
                    o.push(json!([if playing { "web_pause" } else { "web_resume" }]));
                }
                o.push(json!(["returns", ok]));
            }
            Event::SeekTo(target_sec) => {
                let ok = self.active(w) && self.asked.as_ref().map(|a| a.heard).unwrap_or(false);
                if !ok {
                    o.push(json!(["returns", false]));
                    return Ok(o);
                }
                let duration = match pyfloat_or0(self.status.get("duration")) {
                    Ok(d) => d,
                    Err(Fl::Type) => {
                        o.push(json!(["returns", false]));
                        return Ok(o);
                    }
                    Err(Fl::Decline(r)) => return Err(r),
                };
                if duration <= 0.0 {
                    o.push(json!(["returns", false]));
                    return Ok(o);
                }
                let target = (0.0f64).max(target_sec.min(duration - SEEK_END_GUARD_S));
                o.push(json!(["web_seek", target]));
                let base = match self.paused_at {
                    Some(p) if p != 0.0 => p,
                    _ => now,
                };
                o.push(json!(["entry_set", "karafun_display_started_at", base - target]));
                o.push(json!(["log", format!("seek to {}s", f0(target))]));
                o.push(json!(["returns", true]));
            }
            Event::ApplyKeyTempo { key, tempo } => {
                if w.service_present && w.has_web {
                    o.push(json!(["web_set_key", key]));
                    o.push(json!(["web_set_tempo", tempo]));
                }
                self.key_current = Some(key);
                self.tempo_current = Some(tempo);
                o.push(json!(["entry_set", "karafun_key_current", key]));
                o.push(json!(["entry_set", "karafun_tempo_current", tempo]));
                o.push(json!(["log", format!("live key/tempo {key:+}/{tempo}%")]));
            }
            Event::OnFinish => {
                let was_open = self.song_open || self.asked.is_some();
                self.song_open = false;
                self.asked = None;
                self.sat_since = None;
                if !was_open {
                    o.push(json!(["returns", false]));
                    return Ok(o);
                }
                o.push(json!(["stop_frames"]));
                if w.service_present && w.has_web {
                    o.push(json!(["web_stop"]));
                }
                o.push(json!(["returns", true]));
            }
        }
        Ok(o)
    }

    fn active(&self, w: &World) -> bool {
        self.asked.is_some() && w.service_present && w.has_web && w.entry_current
    }

    fn arm_watchdog(&mut self, o: &mut Vec<Value>) {
        if let Some(a) = &self.asked {
            self.watchdogs.push(a.gen);
            o.push(json!(["arm_watchdog"]));
        }
    }

    fn send_play(&mut self, w: &World, now: f64, o: &mut Vec<Value>) {
        if !(w.service_present && w.has_web) {
            return;
        }
        if let Some(a) = self.asked.as_mut() {
            a.sent = true;
            let _ = now;
            o.push(json!(["web_play", a.id]));
        }
    }

    fn stop_stray(&mut self, w: &World, now: f64, o: &mut Vec<Value>) {
        if now - self.stray_stopped_at < 4.0 {
            return;
        }
        self.stray_stopped_at = now;
        o.push(json!(["log", "the player was playing a song SingWS did not ask for; stopped it"]));
        if w.service_present && w.has_web {
            o.push(json!(["web_stop"]));
        }
    }

    fn state_changed(&mut self, state: &str, detail: &str, w: &World, now: f64, o: &mut Vec<Value>) -> R<()> {
        let (heard, sent) = match &self.asked {
            None => return Ok(()),
            Some(a) => (a.heard, a.sent),
        };
        if state == "error" && heard && self.song_open {
            return self.recover(detail, w, now, o);
        }
        if heard {
            return Ok(());
        }
        if state == "ready" && !sent {
            self.send_play(w, now, o);
        } else if state == "error" {
            self.fail(detail, w, o)?;
        }
        Ok(())
    }

    fn recover(&mut self, why: &str, w: &World, now: f64, o: &mut Vec<Value>) -> R<()> {
        let (tries, last, akey, atempo) = match &self.asked {
            None => return Ok(()),
            Some(a) => (a.recoveries, a.last_position, a.key, a.tempo),
        };
        if !w.entry_current || tries >= MAX_RECOVERIES || !w.service_present {
            return self.fail(&format!("KaraFun's player stopped and could not be restarted ({why})"), w, o);
        }
        let resume = last.unwrap_or(0.0);
        let key = match self.key_current { Some(k) => k, None => akey };
        let tempo = match self.tempo_current { Some(t) => t, None => atempo };
        let (key, tempo) = (key, if tempo == 0 { 100 } else { tempo });
        {
            let a = self.asked.as_mut().unwrap();
            a.recoveries = tries + 1;
            a.heard = false;
            a.sent = false;
            a.resume_at = Some(resume);
            a.at = now;
            a.limit = RECOVER_START_WITHIN_S;
            a.key = key;
            a.tempo = tempo;
        }
        self.sat_since = None;
        o.push(json!(["log", format!("the player stopped during the song ({why}); restarting it and resuming at {}s (attempt {}/{})", f0(resume), tries + 1, MAX_RECOVERIES)]));
        o.push(json!(["stop_frames"]));
        o.push(json!(["entry_status", "launching", "Restarting KaraFun's player...", true]));
        o.push(json!(["notify", "KaraFun's player stopped: restarting it and resuming the song.", "warning"]));
        o.push(json!(["service_stop"]));
        if let Some(err) = &w.restart_error {
            return self.fail(&format!("KaraFun's player could not be restarted: {err}"), w, o);
        }
        o.push(json!(["service_start"]));
        self.arm_watchdog(o);
        Ok(())
    }

    fn fail(&mut self, why: &str, w: &World, o: &mut Vec<Value>) -> R<()> {
        let asked = match self.asked.take() {
            None => return Ok(()),
            Some(a) => a,
        };
        o.push(json!(["log", format!("giving up on the song: {}", chars(why, 120))]));
        o.push(json!(["stop_frames"]));
        if w.service_present && w.has_web {
            o.push(json!(["web_stop"]));
        }
        if !w.entry_current {
            return Ok(());
        }
        o.push(json!(["entry_set", "karafun_submission_state", "karafun_failed"]));
        o.push(json!(["entry_pop", "karafun_pending_at"]));
        o.push(json!(["entry_set", "karafun_submission_error", chars(why, 240)]));
        o.push(json!(["entry_status", "failed", format!("KaraFun automation failed: {why}"), false]));
        o.push(json!(["log", format!("failed title={} error={}", repr(&asked.title)?, repr(&chars(why, 240))?)]));
        o.push(json!(["notify", format!("KaraFun automation failed: {why} Press Stop in SingWS to return the song to the queue."), "error"]));
        o.push(json!(["save"]));
        o.push(json!(["update_queue"]));
        Ok(())
    }

    fn song_id(&self) -> R<Option<f64>> {
        let song = self.status.get("song");
        let song = match song {
            None => return Ok(None),
            Some(s) if !truthy(s) => return Ok(None),
            Some(s) => s,
        };
        let song = song.as_object().ok_or("song not an object")?;
        match song.get("id") {
            None | Some(Value::Null) => Ok(None),
            Some(Value::Number(n)) => Ok(n.as_f64()),
            Some(_) => Err("song id not a number".into()),
        }
    }

    fn check(&mut self, gen: u64, w: &World, now: f64, o: &mut Vec<Value>) -> R<()> {
        let (id, at, limit) = match &self.asked {
            Some(a) if a.gen == gen && !a.heard => (a.id, a.at, a.limit),
            _ => return Ok(()),
        };
        if now - at >= limit {
            let cur = self.song_id()?;
            let st = &self.status;
            let playing = st.get("playing").map(truthy).unwrap_or(false) || st.get("moving").map(truthy).unwrap_or(false);
            if cur == Some(id as f64) && playing {
                if w.entry_current {
                    let pos = pyfloat_or0(st.get("position")).map_err(|e| match e { Fl::Type => "float raises".to_string(), Fl::Decline(r) => r })?;
                    let dur = pyfloat_or0(st.get("duration")).map_err(|e| match e { Fl::Type => "float raises".to_string(), Fl::Decline(r) => r })?;
                    o.push(json!(["log", "the song was playing but its start was not noticed; treating it as started"]));
                    self.started(pos, dur, w, now, o)?;
                    return Ok(());
                }
            }
            if w.service_present && w.service_state == "sign_in" {
                self.fail("KaraFun's web player is not signed in: sign in to KaraFun in your browser (Settings > KaraFun)", w, o)?;
            } else {
                self.fail("KaraFun's web player never started the song", w, o)?;
            }
        } else {
            self.watchdogs.push(gen);
            o.push(json!(["arm_watchdog"]));
        }
        Ok(())
    }

    fn follow(&mut self, w: &World, now: f64, o: &mut Vec<Value>) -> R<()> {
        if self.asked.is_none() || !w.entry_current {
            return Ok(());
        }
        let current = self.song_id()?;
        let playing = self.status.get("playing").map(truthy).unwrap_or(false) || self.status.get("moving").map(truthy).unwrap_or(false);
        let pd = match (pyfloat_or0(self.status.get("position")), pyfloat_or0(self.status.get("duration"))) {
            (Ok(p), Ok(d)) => (p, d),
            (Err(Fl::Decline(r)), _) | (_, Err(Fl::Decline(r))) => return Err(r),
            _ => (0.0, 0.0),
        };
        let (position, duration) = pd;
        let (id, heard) = {
            let a = self.asked.as_ref().unwrap();
            (a.id, a.heard)
        };
        let same = current == Some(id as f64);
        if !heard {
            if playing && same {
                self.started(position, duration, w, now, o)?;
            }
            return Ok(());
        }
        if position > 0.0 {
            self.asked.as_mut().unwrap().last_position = Some(position);
        }
        if duration > 0.0 {
            o.push(json!(["entry_set", "karafun_display_duration", duration]));
        }
        if playing {
            self.paused_at = None;
            o.push(json!(["entry_pop", "karafun_paused_at"]));
            o.push(json!(["entry_set", "karafun_display_started_at", now - position]));
        } else if same && self.paused_at.map(|p| p == 0.0).unwrap_or(true) {
            self.paused_at = Some(now);
            o.push(json!(["entry_set", "karafun_paused_at", now]));
            o.push(json!(["entry_set", "karafun_display_started_at", now - position]));
        }
        let at_end = duration > 0.0 && position >= duration - END_MARGIN_S;
        if !at_end {
            self.sat_since = None;
        } else if self.sat_since.is_none() {
            self.sat_since = Some(now);
        }
        let why = if current.is_none() {
            "gone from the player"
        } else if !same {
            "the player went on to another song"
        } else if at_end && !playing {
            "stopped at the end"
        } else if self.sat_since.map(|s| now - s > SAT_AT_END_S).unwrap_or(false) {
            "at the end"
        } else {
            return Ok(());
        };
        self.complete(why, w, o);
        Ok(())
    }

    fn started(&mut self, position: f64, duration: f64, w: &World, now: f64, o: &mut Vec<Value>) -> R<()> {
        let (title, at, key, tempo, id) = {
            let a = self.asked.as_mut().unwrap();
            a.heard = true;
            (a.title.clone(), a.at, a.key, a.tempo, a.id)
        };
        let _ = id;
        o.push(json!(["entry_set", "karafun_playback_clock_started_at", now - position]));
        o.push(json!(["entry_set", "karafun_display_started_at", now - position]));
        o.push(json!(["entry_wall", "karafun_play_started_at"]));
        if duration > 0.0 {
            o.push(json!(["entry_set", "karafun_display_duration", duration]));
            o.push(json!(["verified_duration", duration.round_ties_even() as i64]));
        }
        o.push(json!(["entry_set", "karafun_submission_state", "karafun_queued"]));
        o.push(json!(["entry_wall", "karafun_submitted_at"]));
        o.push(json!(["entry_pop", "karafun_pending_at"]));
        o.push(json!(["entry_status", "playing", "Playing in KaraFun", true]));
        o.push(json!(["log", format!("started after {:.1}s title={} duration={}s", now - at, repr(&title)?, f0(duration))]));
        o.push(json!(["fade_bg"]));
        let web = w.service_present && w.has_web;
        if web {
            if key != 0 {
                o.push(json!(["web_set_key", key]));
            }
            if tempo != 100 {
                o.push(json!(["web_set_tempo", tempo]));
            }
        }
        self.key_current = Some(key);
        self.tempo_current = Some(tempo);
        o.push(json!(["entry_set", "karafun_key_current", key]));
        o.push(json!(["entry_set", "karafun_tempo_current", tempo]));
        o.push(json!(["show_key_tempo", key, tempo]));
        let resume = self.asked.as_mut().unwrap().resume_at.take().unwrap_or(0.0);
        if resume > 3.0 && web {
            let target = (0.0f64).max(if duration > 0.0 { resume.min(duration - SEEK_END_GUARD_S) } else { resume });
            o.push(json!(["web_seek", target]));
            o.push(json!(["log", format!("resumed the song at {}s after restarting the player", f0(target))]));
        }
        o.push(json!(["start_frames"]));
        o.push(json!(["notify", format!("KaraFun queued and started: {title}"), "success"]));
        Ok(())
    }

    fn complete(&mut self, why: &str, w: &World, o: &mut Vec<Value>) {
        if !w.active_same {
            self.asked = None;
            return;
        }
        o.push(json!(["log", format!("song ended ({why})")]));
        self.asked = None;
        o.push(json!(["diag", format!("[KARAFUN] completion event received reason=web_{} remaining=0 idle=1", why.replace(' ', "_"))]));
        o.push(json!(["finish_complete"]));
    }
}

fn int(v: Option<&Value>) -> Option<i64> {
    v.and_then(|x| x.as_i64())
}

/// Replay request: `{"events": [{"ev": ..., "t": monotonic seconds, "world": {...}, ...}]}` -> `{"trace": [[action...] per event]}` or `{"declined": reason}`.
pub fn handle(req: &Value) -> Value {
    let mut h = Host::new();
    let mut trace = Vec::new();
    let empty = vec![];
    for e in req.get("events").and_then(|x| x.as_array()).unwrap_or(&empty) {
        let now = e.get("t").and_then(|x| x.as_f64()).unwrap_or(0.0);
        let wj = e.get("world").cloned().unwrap_or(json!({}));
        let w = World {
            service_present: wj.get("service_present").and_then(|x| x.as_bool()).unwrap_or(true),
            service_state: wj.get("service_state").and_then(|x| x.as_str()).unwrap_or("idle").to_string(),
            has_web: wj.get("has_web").and_then(|x| x.as_bool()).unwrap_or(true),
            entry_current: wj.get("entry_current").and_then(|x| x.as_bool()).unwrap_or(true),
            active_same: wj.get("active_same").and_then(|x| x.as_bool()).unwrap_or(true),
            restart_error: wj.get("restart_error").and_then(|x| x.as_str()).map(|s| s.to_string()),
        };
        let kind = e.get("ev").and_then(|x| x.as_str()).unwrap_or("");
        let null = Value::Null;
        let ev = match kind {
            "start" => Event::Start {
                song_id: int(e.get("song_id")),
                enabled: e.get("enabled").and_then(|x| x.as_bool()).unwrap_or(true),
                key: int(e.get("key")),
                tempo: e.get("tempo"),
                title: e.get("title").and_then(|x| x.as_str()).unwrap_or(""),
            },
            "service" => Event::Service { state: e.get("state").and_then(|x| x.as_str()).unwrap_or(""), detail: e.get("detail").and_then(|x| x.as_str()).unwrap_or("") },
            "status" => Event::Status(e.get("message").unwrap_or(&null)),
            "page_error" => Event::PageError(e.get("message").unwrap_or(&null)),
            "watchdog" => Event::Watchdog,
            "toggle_pause" => Event::TogglePause,
            "seek" => Event::SeekTo(e.get("target").and_then(|x| x.as_f64()).unwrap_or(0.0)),
            "key_tempo" => Event::ApplyKeyTempo { key: int(e.get("key")).unwrap_or(0), tempo: int(e.get("tempo")).unwrap_or(100) },
            "finish" => Event::OnFinish,
            other => return json!({"declined": format!("unknown event {other}")}),
        };
        match h.step(ev, &w, now) {
            Ok(a) => trace.push(Value::Array(a)),
            Err(r) => return json!({"declined": r}),
        }
    }
    json!({"trace": trace})
}
