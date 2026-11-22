//! W2: the song lifecycle of `karafun_web_host.KaraFunWebHost` (start gating, "heard" start, display clock, pause, end rules, never-started watchdog, recovery,
//! seek clamp, controls, finish), transcribed from the Python (the oracle). Pure: the caller passes the monotonic time and a `World` snapshot of the things
//! the host reads from the app and the service; the machine answers with the ordered effects to perform (JSON arrays, see `action` names below).
//! Cases whose Python spelling is not modelled (string-valued numbers, non-ASCII text in a repr) are DECLINED with `Err`, never guessed.
use serde_json::{json, Value};

/// A fresh song's play is sent this long after the start (`COUNTDOWN_HOLD_S`, 1.0.3.9): the countdown animation and the background music run first.
pub const COUNTDOWN_HOLD_S: f64 = 3.5;
pub const NEVER_STARTED_AFTER_S: f64 = 60.0;
/// A song asked for while the player is still starting / signing in is held this long before it is failed (`SIGN_IN_HOLD_S`, 1.0.3.8).
pub const SIGN_IN_HOLD_S: f64 = 240.0;
pub const RECOVER_START_WITHIN_S: f64 = 150.0;
pub const MAX_RECOVERIES: i64 = 3;
pub const SAT_AT_END_S: f64 = 2.5;
pub const END_MARGIN_S: f64 = 1.5;
pub const SEEK_END_GUARD_S: f64 = 8.0;
/// A song the page calls playing whose position is still 0 this long after it started is stuck (the 2026-11-21 Intel show: six starts, the clock never left 0): restart the player.
pub const STUCK_CLOCK_S: f64 = 8.0;
/// A player that still plays after this many stops within `STRAY_WINDOW_S` ignores them: close it (the next song starts a fresh one).
pub const STRAY_STOPS_BEFORE_CLOSE: u32 = 3;
pub const STRAY_WINDOW_S: f64 = 60.0;

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
    /// when play was sent to the page (the 60 s start limit counts from here once it was sent)
    sent_at: Option<f64>,
    at: f64,
    /// the earliest moment the play may be sent (`asked["play_not_before"]`)
    play_not_before: f64,
    /// a hold timer is already running for this song (`asked["_hold_armed"]`)
    hold_armed: bool,
    limit: f64,
    key: i64,
    tempo: i64,
    recoveries: i64,
    last_position: Option<f64>,
    resume_at: Option<f64>,
    gen: u64,
    /// when the song was heard starting, and whether its position has moved since
    started_at: f64,
    moved: bool,
}

#[derive(Debug, Clone)]
pub struct Host {
    asked: Option<Asked>,
    song_open: bool,
    sat_since: Option<f64>,
    stray_stopped_at: f64,
    stray_count: u32,
    stray_since: f64,
    pub state: (String, String),
    status: Value,
    gens: u64,
    watchdogs: Vec<u64>,
    /// generations of the running hold timers, oldest first
    hold_timers: Vec<u64>,
    /// seconds the play is held after a start (`countdown_hold_s`; 0 = no hold)
    pub hold_s: f64,
    paused_at: Option<f64>,
    key_current: Option<i64>,
    tempo_current: Option<i64>,
}

pub enum Event<'a> {
    Start { song_id: Option<i64>, enabled: bool, key: Option<i64>, tempo: Option<&'a Value>, title: &'a str },
    /// The first half of `Start`, up to and including the `ensure_service` effect: the caller carries that effect out (the set-up runs and the service reports states), then sends
    /// `StartBranch` with a `World` that shows the service's state AFTER the set-up, as the Python reads it. `Start` is both halves in one step (the replay tools).
    StartPrefix { song_id: Option<i64>, enabled: bool, key: Option<i64>, tempo: Option<&'a Value>, title: &'a str },
    /// The second half of `Start` (nothing happens when no song was asked for).
    StartBranch,
    Service { state: &'a str, detail: &'a str },
    Status(&'a Value),
    PageError(&'a Value),
    Watchdog,
    /// the hold timer fired (the oldest one)
    HoldOver,
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
            stray_count: 0,
            stray_since: 0.0,
            state: ("idle".into(), String::new()),
            status: json!({}),
            gens: 0,
            watchdogs: vec![],
            hold_timers: vec![],
            hold_s: COUNTDOWN_HOLD_S,
            paused_at: None,
            key_current: None,
            tempo_current: None,
        }
    }

    /// What follows the set-up in `start`: play (after the hold) when the player is ready, fail when it could not start, otherwise wait; then the watchdog.
    fn start_branch(&mut self, w: &World, now: f64, o: &mut Vec<Value>) -> R<()> {
        if w.service_state == "ready" {
            self.play_after_hold(w, now, o);
        } else if w.service_state == "error" {
            let why = if self.state.1.is_empty() { "KaraFun's web player could not start".to_string() } else { self.state.1.clone() };
            self.fail(&why, w, o)?;
        } else {
            o.push(json!(["entry_status", "launching", "Waiting for KaraFun to sign in...", true]));
        }
        self.arm_watchdog(o);
        o.push(json!(["returns", true]));
        Ok(())
    }

    pub fn step(&mut self, ev: Event, w: &World, now: f64) -> R<Vec<Value>> {
        let mut o = Vec::new();
        let prefix_only = matches!(ev, Event::StartPrefix { .. });
        match ev {
            Event::Start { song_id, enabled, key, tempo, title } | Event::StartPrefix { song_id, enabled, key, tempo, title } => {
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
                self.asked = Some(Asked { id, title: title.to_string(), heard: false, sent: false, sent_at: None, at: now, play_not_before: now + self.hold_s.max(0.0), hold_armed: false, limit: NEVER_STARTED_AFTER_S, key: key_i, tempo: tempo_v,
                                          recoveries: 0, last_position: None, resume_at: None, gen: self.gens, started_at: 0.0, moved: false });
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
                if !prefix_only {
                    self.start_branch(w, now, &mut o)?;
                }
            }
            Event::StartBranch => {
                if self.asked.is_some() {
                    self.start_branch(w, now, &mut o)?;
                } else {
                    // the song already failed while the player was being set up (the failure was told then): `start` still answers True, as the Python does
                    o.push(json!(["returns", true]));
                }
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
            Event::HoldOver => {
                if self.hold_timers.is_empty() {
                    return Ok(o);
                }
                let gen = self.hold_timers.remove(0);
                let live = matches!(&self.asked, Some(a) if a.gen == gen && !a.sent && !a.heard);
                if live {
                    if let Some(a) = self.asked.as_mut() {
                        a.hold_armed = false;
                    }
                    if w.service_present && w.service_state == "ready" {
                        self.state_changed("ready", "", w, now, &mut o)?;
                    }
                }
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

    /// `_hold_left`: seconds still to wait before the play is sent.
    fn hold_left(&self, now: f64) -> f64 {
        match &self.asked {
            Some(a) if !a.sent => (a.play_not_before - now).max(0.0),
            _ => 0.0,
        }
    }

    /// `_play_after_hold`: send the play now when the hold is over, else (once) start the hold timer.
    fn play_after_hold(&mut self, w: &World, now: f64, o: &mut Vec<Value>) {
        if self.asked.is_none() {
            return;
        }
        let left = self.hold_left(now);
        if left <= 0.02 {
            self.send_play(w, now, o);
            return;
        }
        let gen = {
            let a = self.asked.as_mut().unwrap();
            if a.hold_armed {
                return;
            }
            a.hold_armed = true;
            a.gen
        };
        o.push(json!(["log", format!("holding the play for {}s: the countdown animation and the background music go first", format!("{left:.1}"))]));
        o.push(json!(["hold_timer", (left * 1000.0) as i64 + 25]));
        self.hold_timers.push(gen);
    }

    fn send_play(&mut self, w: &World, now: f64, o: &mut Vec<Value>) {
        if !(w.service_present && w.has_web) {
            return;
        }
        if let Some(a) = self.asked.as_mut() {
            a.sent = true;
            a.sent_at = Some(now);
            // frames flow now, but the picture goes over the windows only when the song starts (see `started`)
            o.push(json!(["start_frames_hidden"]));
            o.push(json!(["web_play", a.id]));
        }
    }

    fn stop_stray(&mut self, w: &World, now: f64, o: &mut Vec<Value>) {
        if now - self.stray_stopped_at < 4.0 {
            return;
        }
        self.stray_stopped_at = now;
        if now - self.stray_since > STRAY_WINDOW_S {
            self.stray_since = now;
            self.stray_count = 0;
        }
        self.stray_count += 1;
        o.push(json!(["log", "the player was playing a song SingWS did not ask for; stopped it"]));
        if self.stray_count >= STRAY_STOPS_BEFORE_CLOSE {
            // the page ignores the stop (the 2026-11-21 show: 620 stops in 45 minutes): close the player; the next song starts a new one
            self.stray_count = 0;
            o.push(json!(["log", format!("the player ignored {STRAY_STOPS_BEFORE_CLOSE} stops in a row; closing it (the next song starts a fresh one)")]));
            if w.service_present {
                o.push(json!(["stop_frames"]));
                o.push(json!(["service_stop"]));
            }
            return;
        }
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
            self.play_after_hold(w, now, o);
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
            // the real `service.stop()` forgets its web object BEFORE it closes it, so a stop that raised leaves no player to stop in the failure path
            let mut after_stop = w.clone();
            after_stop.has_web = false;
            return self.fail(&format!("KaraFun's player could not be restarted: {err}"), &after_stop, o);
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
        let (id, waited, limit) = match &self.asked {
            Some(a) if a.gen == gen && !a.heard => match (a.sent, a.sent_at) {
                (true, Some(sent_at)) => (a.id, now - sent_at, a.limit),            // play was sent: the song has this long to start
                _ => (a.id, now - a.at, a.limit.max(SIGN_IN_HOLD_S)),             // not sent yet: the player is still coming up; the song is held, not failed
            },
            _ => return Ok(()),
        };
        if waited >= limit {
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
        let stuck = {
            let a = self.asked.as_mut().unwrap();
            if position > 0.5 {
                a.moved = true;
            }
            same && playing && !a.moved && now - a.started_at > STUCK_CLOCK_S
        };
        if stuck {
            let state = self.status.get("state").map(|v| v.to_string()).unwrap_or_default();
            o.push(json!(["log", format!("the player says it is playing but the song's clock has not moved for {}s (state={state}, position={}); restarting the player", f0(STUCK_CLOCK_S), f0(position))]));
            return self.recover("the song's clock never moved", w, now, o);
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
            a.started_at = now;
            a.moved = position > 0.5;
            (a.title.clone(), a.at, a.key, a.tempo, a.id)
        };
        let _ = id;
        o.push(json!(["start_frames"])); // the song is really playing: now the picture goes over the windows
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

/// One host event (the replay format: `{"ev": ..., "t": monotonic seconds, "world": {...}, ...}`) -> the ordered effects as JSON. `Err(reason)` when not modelled.
pub fn host_event(h: &mut Host, e: &Value) -> R<Vec<Value>> {
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
        "hold_over" => Event::HoldOver,
        "toggle_pause" => Event::TogglePause,
        "seek" => Event::SeekTo(e.get("target").and_then(|x| x.as_f64()).unwrap_or(0.0)),
        "key_tempo" => Event::ApplyKeyTempo { key: int(e.get("key")).unwrap_or(0), tempo: int(e.get("tempo")).unwrap_or(100) },
        "finish" => Event::OnFinish,
        other => return Err(format!("unknown event {other}")),
    };
    h.step(ev, &w, now)
}

/// Replay request: `{"events": [{"ev": ..., "t": monotonic seconds, "world": {...}, ...}]}` -> `{"trace": [[action...] per event]}` or `{"declined": reason}`.
pub fn handle(req: &Value) -> Value {
    let mut h = Host::new();
    h.hold_s = req.get("hold_s").and_then(|x| x.as_f64()).unwrap_or(COUNTDOWN_HOLD_S);
    let mut trace = Vec::new();
    let empty = vec![];
    for e in req.get("events").and_then(|x| x.as_array()).unwrap_or(&empty) {
        match host_event(&mut h, e) {
            Ok(a) => trace.push(Value::Array(a)),
            Err(r) => return json!({"declined": r}),
        }
    }
    json!({"trace": trace})
}

#[cfg(test)]
mod hold_tests {
    use super::*;

    fn world(state: &str) -> World {
        World { service_present: true, service_state: state.into(), has_web: true, entry_current: true, active_same: true, restart_error: None }
    }
    fn start(h: &mut Host, state: &str, now: f64) -> Vec<Value> {
        h.step(Event::Start { song_id: Some(7), enabled: true, key: None, tempo: None, title: "T" }, &world(state), now).unwrap()
    }
    fn has(o: &[Value], name: &str) -> bool {
        o.iter().any(|a| a[0] == name)
    }

    #[test]
    fn a_ready_player_waits_for_the_hold_then_plays_once() {
        let mut h = Host::new();
        let o = start(&mut h, "ready", 100.0);
        assert!(!has(&o, "web_play"));
        assert!(has(&o, "hold_timer"));
        // the timer fires after the hold: the play goes out
        let o = h.step(Event::HoldOver, &world("ready"), 103.53).unwrap();
        assert!(has(&o, "web_play"));
        // a second timer (none left) does nothing
        assert!(h.step(Event::HoldOver, &world("ready"), 104.0).unwrap().is_empty());
    }

    #[test]
    fn a_player_that_becomes_ready_late_plays_at_once() {
        let mut h = Host::new();
        start(&mut h, "starting", 100.0);
        let o = h.step(Event::Service { state: "ready", detail: "" }, &world("ready"), 110.0).unwrap();
        assert!(has(&o, "web_play") && !has(&o, "hold_timer"));
    }

    #[test]
    fn a_replaced_song_ignores_the_old_timer() {
        let mut h = Host::new();
        start(&mut h, "ready", 100.0);
        start(&mut h, "starting", 101.0);
        let o = h.step(Event::HoldOver, &world("ready"), 103.6).unwrap();
        assert!(!has(&o, "web_play"));
    }

    #[test]
    fn no_hold_sends_at_once() {
        let mut h = Host::new();
        h.hold_s = 0.0;
        assert!(has(&start(&mut h, "ready", 100.0), "web_play"));
    }

    fn playing(pos: f64) -> Value {
        json!({"playing": true, "moving": pos > 0.0, "position": pos, "duration": 200.0, "state": 3, "song": {"id": 7}})
    }
    fn begin_playing(h: &mut Host, now: f64) {
        start(h, "ready", now);
        h.step(Event::HoldOver, &world("ready"), now + 3.53).unwrap();
        h.step(Event::Status(&playing(0.0)), &world("ready"), now + 4.0).unwrap();
    }

    #[test]
    fn a_clock_that_never_leaves_zero_restarts_the_player() {
        let mut h = Host::new();
        begin_playing(&mut h, 100.0);
        // 7 s after it started: still waiting
        let o = h.step(Event::Status(&playing(0.0)), &world("ready"), 111.0).unwrap();
        assert!(!has(&o, "service_stop"));
        // past STUCK_CLOCK_S: the player is restarted and the song resumes
        let o = h.step(Event::Status(&playing(0.0)), &world("ready"), 112.5).unwrap();
        assert!(has(&o, "service_stop") && has(&o, "service_start"), "{o:?}");
    }

    #[test]
    fn a_clock_that_moves_is_never_restarted() {
        let mut h = Host::new();
        begin_playing(&mut h, 100.0);
        h.step(Event::Status(&playing(2.0)), &world("ready"), 106.0).unwrap();
        // even if the page later reports the position 0 again (a seek back), nothing is restarted
        let o = h.step(Event::Status(&playing(0.0)), &world("ready"), 130.0).unwrap();
        assert!(!has(&o, "service_stop"));
    }

    #[test]
    fn a_paused_player_is_not_called_stuck() {
        let mut h = Host::new();
        begin_playing(&mut h, 100.0);
        let paused = json!({"playing": false, "moving": false, "position": 0.0, "duration": 200.0, "state": 2, "song": {"id": 7}});
        let o = h.step(Event::Status(&paused), &world("ready"), 130.0).unwrap();
        assert!(!has(&o, "service_stop"));
    }

    #[test]
    fn a_stuck_clock_that_keeps_happening_ends_with_a_failed_song_after_three_restarts() {
        let mut h = Host::new();
        begin_playing(&mut h, 100.0);
        let mut t = 112.5;
        for attempt in 0..3 {
            let o = h.step(Event::Status(&playing(0.0)), &world("ready"), t).unwrap();
            assert!(has(&o, "service_stop"), "attempt {attempt}");
            // the restarted player is ready again, plays, and is stuck again
            h.step(Event::Service { state: "ready", detail: "" }, &world("ready"), t + 5.0).unwrap();
            h.step(Event::Status(&playing(0.0)), &world("ready"), t + 6.0).unwrap();
            t += 20.0;
        }
        let o = h.step(Event::Status(&playing(0.0)), &world("ready"), t).unwrap();
        assert!(has(&o, "entry_status") && o.iter().any(|a| a[0] == "log" && a[1].as_str().unwrap_or("").contains("giving up")), "{o:?}");
    }

    #[test]
    fn a_player_that_ignores_three_stops_is_closed() {
        let mut h = Host::new();
        let w = world("ready");
        let mut closed = false;
        for k in 0..3 {
            let o = h.step(Event::Status(&playing(5.0)), &w, 200.0 + 4.5 * k as f64).unwrap();
            assert!(has(&o, "log"));
            closed = has(&o, "service_stop");
            if k < 2 {
                assert!(has(&o, "web_stop") && !closed);
            }
        }
        assert!(closed);
        // a new run of strays much later starts counting again
        let o = h.step(Event::Status(&playing(5.0)), &w, 400.0).unwrap();
        assert!(has(&o, "web_stop") && !has(&o, "service_stop"));
    }
}
