//! The karaoke start/follow protocol of `mpv_karaoke_transport.py` (`MpvKaraokeTransport` with a Rust voice attached), as a pure state machine.
//!
//! mpv (the "picture") keeps the song's clock; the Rust karaoke voice plays the sound and is slaved to that clock. At the start of a song and after
//! a seek the voice plays first (its stretcher needs ~130 ms before sound comes out), and the picture plays as soon as the voice's clock moves.
//! Time is passed in (`now`, seconds) so the machine is tested against fakes; `play_demo.rs` wires the real bridge and engine.
//! Ported: `start`/`start_full`, `_voice_step`, `_voice_follow` (+ the `_measured_rate` of `rust_karaoke_voice.py`), `seek` (with the CDG dead-zone
//! clamp), `pause`, `resume`, `stop`, the fallbacks (`_drop_voice`), the started / ended / visual_stalled events of `_poll`, loop, key and tempo
//! (`set_modifiers`), volume, `fade_out`, the CDG visual offset, `is_paused`, `apply_audio_spec`, and the resync log.
//! The timers of the Python transport (10 ms start sequence, the visual-timer poll, fade steps) are modelled as explicit timers on an integer
//! microsecond clock so `tools/karaoke_transport_parity.py` can run the same scripted scenario through both and compare every command.
//! `parity_python()` switches the two places where the demo deliberately differs (follow cadence, rate measured here instead of in the voice).
//! Not ported: voice calls that raise (the Python `except` paths other than load), several fades at once, `query_times_ns`, `engine_at_end`.

/// What the machine needs from the picture (mpv, through the video bridge).
pub trait Picture {
    fn duration_ms(&self) -> i64;
    fn position_ms(&self) -> i64;
    fn is_playing(&self) -> bool;
    fn at_end(&self) -> bool;
    fn visuals_ready(&self) -> bool;
    fn play(&mut self);
    fn pause(&mut self);
    fn seek_ms(&mut self, ms: i64);
    fn stop(&mut self);
    fn set_shadow_audio(&mut self, on: bool);
    /// `loadSingWSMedia`: false = the picture failed to load.
    fn load(&mut self, _source: &str, _audio: Option<&str>, _autoplay: bool, _semitones: i32, _tempo_percent: i32) -> bool {
        true
    }
    fn set_volume(&mut self, _v: f64) {}
    fn set_tempo_ratio(&mut self, _r: f64) {}
    fn set_pitch_semitones(&mut self, _k: f64) {}
    /// `setVideoOffsetMs`; false = this backend cannot apply it.
    fn set_video_offset_ms(&mut self, _v: i32) -> bool {
        false
    }
    fn is_seek_holding(&self) -> bool {
        false
    }
}

/// What the machine needs from the karaoke voice (the Rust engine).
pub trait Voice {
    fn is_ready(&self) -> bool;
    fn position(&self) -> f64;
    fn play(&mut self);
    fn pause(&mut self);
    fn stop(&mut self);
    fn seek(&mut self, seconds: f64);
    fn slave_to(&mut self, position: f64, rate: f64);
    /// (hard resyncs, resyncs held back, voice position, target, master clock, kind: 0 resynced, 1 near the end, 2 clock jump)
    fn resync_info(&self) -> Option<(u32, u32, f32, f32, f32, i32)> {
        None
    }
    fn duration(&self) -> f64 {
        0.0
    }
    fn load(&mut self, _path: &str, _norm_gain: f64, _volume: f64) -> Result<(), String> {
        Ok(())
    }
    fn set_key_semitones(&mut self, _k: f64) {}
    fn set_tempo_percent(&mut self, _t: f64) {}
    fn set_volume(&mut self, _v: f64) {}
    fn slide_volume(&mut self, _v: f64, _ms: i64) {}
    fn set_norm_gain(&mut self, _g: f64) {}
    fn set_eq_gains(&mut self, _on: bool, _gains: &[f64]) {}
    fn set_master_params(&mut self, _on: bool, _has_params: bool) {}
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Idle,
    Starting,
    VoiceWait,
    Seeking,
    Running,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    Started,
    Ended,
    /// CDG: healthy audio but no picture at 5 s (video path, position ms); report-only.
    VisualStalled(String, i64),
    /// The `on_finished` of `fade_out` ran.
    FadeFinished,
}

/// The arguments of `MpvKaraokeTransport.__init__` + `start()` that the machine needs.
#[derive(Clone, Debug, Default)]
pub struct StartArgs {
    pub audio_path: String,
    pub video_path: String,
    /// "cdg" | "mp4" | anything else = audio only
    pub mode: String,
    pub start_seconds: f64,
    /// The CDG's dead zones (`cdg_seek_guard.load_gap`); only used in cdg mode.
    pub zones: Vec<(f64, f64)>,
}

/// `apply_audio_spec`'s dict.
#[derive(Clone, Debug, Default)]
pub struct AudioSpec {
    pub normalize_gain_db: f64,
    pub eq_enabled: bool,
    pub eq_gains_db: Vec<f64>,
    pub master_enabled: bool,
    pub has_master_params: bool,
}

/// The pace the picture's clock really runs at over the last ~0.6 s (mpv applies a speed change a little late); `rust_karaoke_voice._measured_rate`.
#[derive(Default)]
pub struct RateMeter {
    hist: Vec<(f64, f64)>,
}

impl RateMeter {
    pub fn measure(&mut self, now: f64, position: f64, commanded: f64) -> f64 {
        if commanded <= 0.0 {
            self.hist.clear();
            return 0.0;
        }
        if let Some(&(t, p)) = self.hist.last() {
            if now - t > 0.5 || position < p - 0.2 || position > p + 2.0 {
                self.hist.clear(); // a gap or a jump (a seek): the old samples say nothing about the pace
            }
        }
        self.hist.push((now, position));
        while self.hist.first().is_some_and(|f| now - f.0 > 0.6) {
            self.hist.remove(0);
        }
        let (first, last) = (self.hist[0], *self.hist.last().unwrap());
        if self.hist.len() < 8 || last.0 - first.0 < 0.3 {
            return commanded;
        }
        ((last.1 - first.1) / (last.0 - first.0)).clamp(0.2, 5.0)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TimerKind {
    Voice,
    Follow,
    Poll,
    FadeStep,
    FadeEnd,
}

struct Timer {
    kind: TimerKind,
    due: i64,
    interval: i64,
    repeat: bool,
    seq: u64,
}

struct Fade {
    start_volume: f64,
    step: i64,
    steps: i64,
    on_finished: bool,
}

fn us(now: f64) -> i64 {
    (now * 1e6).round() as i64
}

pub struct Protocol<P: Picture, V: Voice> {
    pub picture: P,
    pub voice: Option<V>,
    pub phase: Phase,
    pub log: Vec<String>,
    /// Called with every log line (and every `print:` line) as it happens, so a harness can interleave them with the calls.
    pub on_log: Option<Box<dyn FnMut(&str) + Send>>,
    /// Set when the voice was given up on (the song then plays with mpv's own audio): the reason.
    pub dropped: Option<String>,
    /// A CDG's graphics-free stretches (`singws_cdg::dead_zones`): a seek into it moves back to the last frame before it, for picture and voice alike.
    pub seek_gap: Vec<(f64, f64)>,
    /// Report the picture clock to the voice inside the poll, as the Python transport does (false: its own 30 ms timer, as the demo does).
    pub follow_in_poll: bool,
    /// Measure the pace of the picture clock here (the demo) instead of passing the commanded rate (the Python transport; the voice measures).
    pub measure_rate: bool,
    audio_path: String,
    video_path: String,
    cdg: bool,
    volume: f64,
    voice_norm: f64,
    pitch: f64,
    tempo_ratio: f64,
    looping: Option<(f64, f64)>,
    user_paused: bool,
    stopped: bool,
    started_emitted: bool,
    ended_emitted: bool,
    visual_stall_emitted: bool,
    video_offset_warned: bool,
    deadline: f64,
    hard_deadline: f64,
    loaded_at: Option<f64>,
    origin: f64,
    poll_interval_us: i64,
    timers: Vec<Timer>,
    seq: u64,
    fade: Option<Fade>,
    meter: RateMeter,
    resync_seen: Option<(u32, u32)>,
    playing_polls_without_visual: u32,
}

impl<P: Picture, V: Voice> Protocol<P, V> {
    pub fn new(picture: P, voice: Option<V>) -> Self {
        Protocol {
            picture,
            voice,
            phase: Phase::Idle,
            log: Vec::new(),
            on_log: None,
            dropped: None,
            seek_gap: Vec::new(),
            follow_in_poll: false,
            measure_rate: true,
            audio_path: String::new(),
            video_path: String::new(),
            cdg: false,
            volume: 1.0,
            voice_norm: 1.0,
            pitch: 0.0,
            tempo_ratio: 1.0,
            looping: None,
            user_paused: false,
            stopped: true,
            started_emitted: false,
            ended_emitted: false,
            visual_stall_emitted: false,
            video_offset_warned: false,
            deadline: 0.0,
            hard_deadline: 0.0,
            loaded_at: None,
            origin: 0.0,
            poll_interval_us: 50_000,
            timers: Vec::new(),
            seq: 0,
            fade: None,
            meter: RateMeter::default(),
            resync_seen: None,
            playing_polls_without_visual: 0,
        }
    }

    /// The configuration of `MpvKaraokeTransport` itself: follow inside the poll, commanded rate to the voice.
    pub fn parity_python(mut self) -> Self {
        self.follow_in_poll = true;
        self.measure_rate = false;
        self
    }

    pub fn user_paused(&self) -> bool {
        self.user_paused
    }

    fn say(&mut self, line: String) {
        if let Some(f) = self.on_log.as_mut() {
            f(&line);
        }
        self.log.push(line);
    }

    // ---- timers (QTimer) ------------------------------------------------------------------------------------------------------------

    fn timer_start(&mut self, kind: TimerKind, now: f64, interval: i64, repeat: bool) {
        self.timers.retain(|t| t.kind != kind);
        self.seq += 1;
        self.timers.push(Timer { kind, due: us(now) + interval, interval, repeat, seq: self.seq });
    }

    fn timer_stop(&mut self, kind: TimerKind) {
        self.timers.retain(|t| t.kind != kind);
    }

    fn timer_active(&self, kind: TimerKind) -> bool {
        self.timers.iter().any(|t| t.kind == kind)
    }

    /// When the next timer is due (microseconds on the caller's clock), if any is running.
    pub fn next_due_us(&self) -> Option<i64> {
        self.timers.iter().map(|t| t.due).min()
    }

    /// Fire the earliest timer that is due by `until_us` (ties: shorter interval first, then the older timer; Qt leaves that order undefined).
    /// Returns None when nothing is due. The handler sees the timer's own due time as `now`.
    pub fn fire_next(&mut self, until_us: i64) -> Option<Vec<Event>> {
        let idx = (0..self.timers.len()).filter(|&i| self.timers[i].due <= until_us).min_by_key(|&i| (self.timers[i].due, self.timers[i].interval, self.timers[i].seq))?;
        let (kind, due, interval, repeat) = {
            let t = &self.timers[idx];
            (t.kind, t.due, t.interval, t.repeat)
        };
        if repeat {
            self.timers[idx].due = due + interval;
        } else {
            self.timers.remove(idx);
        }
        let now = due as f64 / 1e6;
        let mut events = Vec::new();
        match kind {
            TimerKind::Voice => self.voice_step(now),
            TimerKind::Follow => self.follow(now),
            TimerKind::Poll => self.poll(now, &mut events),
            TimerKind::FadeStep => self.fade_step(&mut events),
            TimerKind::FadeEnd => events.push(Event::FadeFinished),
        }
        Some(events)
    }

    /// Call every ~10 ms: fires every timer that is due.
    pub fn tick(&mut self, now: f64) -> Vec<Event> {
        let mut events = Vec::new();
        let until = us(now);
        while let Some(ev) = self.fire_next(until) {
            events.extend(ev);
        }
        events
    }

    // ---- start ----------------------------------------------------------------------------------------------------------------------

    /// Both are loaded and paused (the voice from `Voice` construction, the picture from the bridge load). Mirrors the tail of `start()`.
    pub fn start(&mut self, now: f64, start_seconds: f64) {
        let start_seconds = singws_cdg::clamp_seek(&self.seek_gap, start_seconds);
        self.user_paused = false;
        if self.voice.is_some() {
            self.begin_voice_start(now);
        } else {
            self.picture.play(); // no voice: mpv plays the sound itself
        }
        self.finish_start(now, start_seconds);
    }

    fn begin_voice_start(&mut self, now: f64) {
        self.phase = Phase::Starting;
        self.deadline = now + 4.0;
        self.hard_deadline = now + 20.0;
        self.loaded_at = None;
        self.timer_start(TimerKind::Voice, now, 10_000, true);
    }

    fn finish_start(&mut self, now: f64, start_seconds: f64) {
        if start_seconds > 0.0 {
            self.picture.seek_ms((start_seconds * 1000.0) as i64);
        }
        self.picture.set_volume(self.volume);
        self.stopped = false;
        self.started_emitted = false;
        self.ended_emitted = false;
        self.playing_polls_without_visual = 0;
        self.visual_stall_emitted = false;
        if !self.follow_in_poll {
            self.timer_start(TimerKind::Follow, now, 30_000, true);
        }
        let iv = self.poll_interval_us;
        self.timer_start(TimerKind::Poll, now, iv, true);
    }

    /// `MpvKaraokeTransport.start`: loads the voice (paused) and the picture itself. Err = the Python `RuntimeError`.
    pub fn start_full(&mut self, now: f64, a: &StartArgs) -> Result<(), String> {
        self.audio_path = a.audio_path.clone();
        self.video_path = a.video_path.clone();
        let mode = a.mode.to_lowercase();
        self.cdg = mode == "cdg";
        let source = if mode == "cdg" || mode == "mp4" { a.video_path.as_str() } else { a.audio_path.as_str() };
        if source.is_empty() {
            return Err("mpv source path is empty".into());
        }
        let audio = if self.cdg { Some(a.audio_path.as_str()) } else { None };
        self.seek_gap = if self.cdg { a.zones.clone() } else { Vec::new() };
        let start_seconds = singws_cdg::clamp_seek(&self.seek_gap, a.start_seconds);
        let mut voice_on = false;
        if self.voice.is_some() {
            voice_on = self.voice_load(start_seconds);
        }
        self.user_paused = false;
        let ok = self.picture.load(source, audio, !voice_on, self.pitch.round_ties_even() as i32, (self.tempo_ratio * 100.0).round_ties_even() as i32);
        if !ok {
            if voice_on {
                self.drop_voice("the picture failed to load");
            }
            return Err("mpv media load failed".into());
        }
        if voice_on {
            self.begin_voice_start(now);
        }
        self.finish_start(now, start_seconds);
        Ok(())
    }

    fn voice_load(&mut self, start_seconds: f64) -> bool {
        let path = if self.audio_path.is_empty() { self.video_path.clone() } else { self.audio_path.clone() };
        let (norm, vol, key, tempo) = (self.voice_norm, self.volume, self.pitch, self.tempo_ratio * 100.0);
        let r = {
            let v = self.voice.as_mut().unwrap();
            match v.load(&path, norm, vol) {
                Ok(()) => {
                    v.set_key_semitones(key);
                    v.set_tempo_percent(tempo);
                    if start_seconds > 0.0 {
                        v.seek(start_seconds);
                    }
                    Ok(())
                }
                Err(e) => Err(e),
            }
        };
        match r {
            Ok(()) => true,
            Err(e) => {
                self.drop_voice(&format!("could not open '{path}': {e}"));
                false
            }
        }
    }

    /// Give the song back to mpv's own audio: never leave a show without sound.
    fn drop_voice(&mut self, reason: &str) {
        let voice = self.voice.take();
        self.phase = Phase::Idle;
        self.timer_stop(TimerKind::Voice);
        self.say(format!("[KARAOKE-RUST] falling back to mpv audio for this song: {reason}"));
        if let Some(mut v) = voice {
            v.stop();
        }
        self.dropped = Some(reason.to_string());
        self.picture.set_shadow_audio(false);
    }

    pub fn stop(&mut self) {
        self.stopped = true;
        self.timer_stop(TimerKind::Poll);
        self.timer_stop(TimerKind::Follow);
        self.timer_stop(TimerKind::Voice);
        self.phase = Phase::Idle;
        if let Some(v) = self.voice.as_mut() {
            v.stop();
        }
        self.picture.stop();
    }

    pub fn seek(&mut self, now: f64, seconds: f64) {
        let target = singws_cdg::clamp_seek(&self.seek_gap, seconds.max(0.0));
        if self.voice.is_some() && matches!(self.phase, Phase::Running | Phase::Seeking | Phase::VoiceWait) {
            // Pause both, move both, and start them together again once the voice's source is ready (it takes a moment to refill).
            if let Some(v) = self.voice.as_mut() {
                v.pause();
            }
            self.picture.pause();
            self.picture.seek_ms((target * 1000.0) as i64);
            if let Some(v) = self.voice.as_mut() {
                v.seek(target);
            }
            self.phase = Phase::Seeking;
            self.deadline = now + 3.0;
            self.hard_deadline = now + 10.0;
            // `loaded_at` is deliberately NOT reset: the Python transport keeps the value from the start of the song.
            self.timer_start(TimerKind::Voice, now, 10_000, true);
            return;
        }
        self.picture.seek_ms((target * 1000.0) as i64);
    }

    pub fn pause(&mut self) {
        self.user_paused = true;
        self.picture.pause();
        if let Some(v) = self.voice.as_mut() {
            v.pause();
        }
    }

    pub fn resume(&mut self) {
        self.user_paused = false;
        if let Some(v) = self.voice.as_mut() {
            if self.phase == Phase::Running {
                v.play();
            }
        }
        if self.voice.is_some() && matches!(self.phase, Phase::Starting | Phase::VoiceWait | Phase::Seeking) {
            return; // the start sequence is still running; it will play both when it is ready
        }
        self.picture.play();
    }

    /// A coordinated seek pauses the audible engine while the video followers catch up; the start sequence is not a user-visible pause either.
    pub fn is_paused(&self) -> bool {
        if self.picture.is_seek_holding() {
            return false;
        }
        if self.voice.is_some() && matches!(self.phase, Phase::Starting | Phase::VoiceWait | Phase::Seeking) && !self.user_paused {
            return false;
        }
        !self.picture.is_playing()
    }

    pub fn position_seconds(&self) -> f64 {
        (self.picture.position_ms() as f64 / 1000.0).max(0.0)
    }

    // ---- modifiers, level, offset -----------------------------------------------------------------------------------------------------

    /// `set_modifiers`: tempo goes to mpv (its clock paces the picture) and, with a voice, key and tempo go to the voice; without one mpv also takes the key.
    pub fn set_modifiers(&mut self, tempo_ratio: f64, semitones: f64) {
        let ratio = if tempo_ratio == 0.0 { 1.0 } else { tempo_ratio };
        self.tempo_ratio = ratio.clamp(0.5, 2.0);
        self.pitch = semitones;
        self.picture.set_tempo_ratio(self.tempo_ratio);
        let (k, t) = (self.pitch, self.tempo_ratio * 100.0);
        if let Some(v) = self.voice.as_mut() {
            v.set_key_semitones(k);
            v.set_tempo_percent(t);
        } else {
            self.picture.set_pitch_semitones(k);
        }
    }

    pub fn set_volume(&mut self, value: f64) {
        self.volume = value.clamp(0.0, 2.0);
        self.picture.set_volume(self.volume);
        let vol = self.volume;
        if let Some(v) = self.voice.as_mut() {
            v.set_volume(vol);
        }
    }

    /// The host's audio chain for the voice (normalization, karaoke EQ, master chain); only with a voice.
    pub fn apply_audio_spec(&mut self, spec: &AudioSpec) {
        let Some(v) = self.voice.as_mut() else { return };
        self.voice_norm = 10f64.powf(spec.normalize_gain_db / 20.0);
        v.set_norm_gain(self.voice_norm);
        v.set_eq_gains(spec.eq_enabled, &spec.eq_gains_db);
        v.set_master_params(spec.master_enabled, spec.has_master_params);
    }

    pub fn set_loop(&mut self, start_seconds: f64, end_seconds: f64) {
        let start = start_seconds.max(0.0);
        let end = end_seconds.max(start);
        self.looping = if end > start { Some((start, end)) } else { None };
    }

    pub fn set_visual_timer_interval_ms(&mut self, now: f64, value: i64) {
        let v = if value == 0 { 50 } else { value };
        self.poll_interval_us = v.clamp(16, 250) * 1000;
        if self.timer_active(TimerKind::Poll) {
            let iv = self.poll_interval_us;
            self.timer_start(TimerKind::Poll, now, iv, true); // Qt restarts a running timer when its interval changes
        }
    }

    /// The CDG visual-timing calibration: forwarded when the backend can apply it, otherwise said once.
    pub fn set_video_offset_ms(&mut self, value: i32) {
        if self.picture.set_video_offset_ms(value) {
            return;
        }
        if value != 0 && !self.video_offset_warned {
            self.video_offset_warned = true;
            self.say(format!(
                "print: [MPV] WARNING this backend cannot apply the {value:+}ms CDG visual offset; lyrics will run early/late and the Display tab fine tuning has no effect"
            ));
        }
    }

    /// `fade_out`: with a voice one slide inside the engine, otherwise 40 ms volume steps from here. `on_finished` shows up as `Event::FadeFinished`.
    pub fn fade_out(&mut self, now: f64, duration_ms: i64, on_finished: bool) -> bool {
        let ms = duration_ms.max(0);
        if ms > 0 {
            if let Some(v) = self.voice.as_mut() {
                v.slide_volume(0.0, ms);
                self.volume = 0.0;
                if on_finished {
                    self.timer_start(TimerKind::FadeEnd, now, ms * 1000, false);
                }
                return true;
            }
        }
        if ms <= 0 {
            self.set_volume(0.0);
            if on_finished {
                self.timer_start(TimerKind::FadeEnd, now, 0, false);
            }
            return true;
        }
        let steps = (ms / 40).max(1);
        self.fade = Some(Fade { start_volume: self.volume, step: 0, steps, on_finished });
        self.timer_start(TimerKind::FadeStep, now, (ms / steps).max(10) * 1000, true);
        true
    }

    fn fade_step(&mut self, events: &mut Vec<Event>) {
        let Some(f) = self.fade.as_mut() else { return };
        f.step += 1;
        let remaining = (1.0 - f.step as f64 / f.steps as f64).max(0.0);
        let (level, done, cb) = (f.start_volume * remaining, f.step >= f.steps, f.on_finished);
        self.set_volume(level);
        if done {
            self.timer_stop(TimerKind::FadeStep);
            self.fade = None;
            if cb {
                events.push(Event::FadeFinished);
            }
        }
    }

    // ---- start sequence, follow, poll -------------------------------------------------------------------------------------------------

    /// `_voice_step`: start the picture and the sound together (beginning of a song and after a seek).
    fn voice_step(&mut self, now: f64) {
        if self.voice.is_none() || self.stopped || matches!(self.phase, Phase::Idle | Phase::Running) {
            self.timer_stop(TimerKind::Voice);
            return;
        }
        if self.user_paused {
            return;
        }
        match self.phase {
            Phase::Starting | Phase::Seeking => {
                let loaded = self.picture.duration_ms() > 0;
                if loaded && self.loaded_at.is_none() {
                    self.loaded_at = Some(now);
                }
                let ready = self.voice.as_ref().is_some_and(|v| v.is_ready());
                if loaded && ready {
                    let pos = self.position_seconds();
                    let v = self.voice.as_mut().unwrap();
                    self.origin = v.position();
                    v.slave_to(pos, 0.0);
                    v.play();
                    self.phase = Phase::VoiceWait;
                    self.deadline = now + 0.7;
                } else if now > self.deadline && ((loaded && now > self.loaded_at.unwrap_or(now) + 3.0) || now > self.hard_deadline) {
                    // A slow disk can keep the PICTURE from loading for several seconds: the voice's 4 s only counts from the moment the picture is loaded.
                    self.drop_voice(if loaded { "the voice was not ready in time" } else { "the picture did not load in time" });
                    self.picture.play();
                }
            }
            Phase::VoiceWait => {
                let moved = self.voice.as_ref().is_some_and(|v| v.position() > self.origin + 0.002);
                if moved || now > self.deadline {
                    self.picture.play();
                    self.phase = Phase::Running;
                    self.timer_stop(TimerKind::Voice);
                }
            }
            _ => {}
        }
    }

    /// `_voice_follow`: report the picture's clock to the voice (which extrapolates between reports and trims its own tempo to stay on it).
    fn follow(&mut self, now: f64) {
        if self.phase != Phase::Running || self.voice.is_none() {
            return;
        }
        let playing = self.picture.is_playing();
        let pos = self.position_seconds();
        let commanded = if playing { self.tempo_ratio } else { 0.0 };
        let rate = if self.measure_rate { self.meter.measure(now, pos, commanded) } else { commanded };
        self.voice.as_mut().unwrap().slave_to(pos, rate);
        self.log_resyncs();
    }

    fn log_resyncs(&mut self) {
        let Some(v) = self.voice.as_ref() else { return };
        let Some((resyncs, guarded, here, target, master, kind)) = v.resync_info() else { return };
        let Some(seen) = self.resync_seen else {
            self.resync_seen = Some((resyncs, guarded)); // first look at this song: remember where the counters are, say nothing
            return;
        };
        let song = v.duration();
        if resyncs != seen.0 && kind == 0 {
            self.say(format!("[KARAOKE-RUST] voice re-synced to the picture clock: song {here:.2}s -> {target:.2}s (picture clock {master:.2}s, song length {song:.1}s)"));
        } else if guarded != seen.1 && (kind == 1 || kind == 2) {
            let why = if kind == 1 { "close to the end of the song" } else { "the picture clock jumped and did not stay" };
            self.say(format!("[KARAOKE-RUST] voice kept its place ({why}): song {here:.2}s, picture clock {master:.2}s, song length {song:.1}s"));
        }
        self.resync_seen = Some((resyncs, guarded));
    }

    /// `_poll`: follow, loop, started / visual_stalled / ended.
    fn poll(&mut self, now: f64, events: &mut Vec<Event>) {
        if self.stopped {
            return;
        }
        if self.follow_in_poll {
            self.follow(now);
        }
        if let Some((start, end)) = self.looping {
            if self.position_seconds() >= end {
                self.seek(now, start);
            }
        }
        let ready = self.picture.visuals_ready();
        let playing = self.picture.is_playing() && self.picture.position_ms() >= 40;
        if !self.started_emitted {
            if playing && !ready {
                self.playing_polls_without_visual += 1;
            } else {
                self.playing_polls_without_visual = 0;
            }
            // a late native drawable must not leave Play locked in "starting": commit after one second of sound without a picture
            let interval_ms = (self.poll_interval_us / 1000).max(1) as f64;
            let limit = ((1000.0 / interval_ms).round_ties_even() as u32).max(4);
            if playing && (ready || self.playing_polls_without_visual >= limit) {
                self.started_emitted = true;
                events.push(Event::Started);
            }
        }
        // healthy audio but a dead CDG picture: report only, never reload the shared core (that would interrupt the singer's audio)
        if self.cdg && playing && !ready && !self.visual_stall_emitted && self.picture.position_ms() >= 5000 {
            self.visual_stall_emitted = true;
            events.push(Event::VisualStalled(self.video_path.clone(), self.picture.position_ms()));
        }
        if !self.ended_emitted && self.picture.at_end() {
            self.ended_emitted = true;
            self.timer_stop(TimerKind::Poll);
            self.timer_stop(TimerKind::Voice);
            if let Some(v) = self.voice.as_mut() {
                v.stop();
            }
            events.push(Event::Ended);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::rc::Rc;

    #[derive(Default)]
    struct PState {
        duration: i64,
        position: i64,
        playing: bool,
        at_end: bool,
        visuals: bool,
        shadow: Option<bool>,
        calls: Vec<String>,
    }
    #[derive(Clone, Default)]
    struct FakePicture(Rc<RefCell<PState>>);
    impl Picture for FakePicture {
        fn duration_ms(&self) -> i64 { self.0.borrow().duration }
        fn position_ms(&self) -> i64 { self.0.borrow().position }
        fn is_playing(&self) -> bool { self.0.borrow().playing }
        fn at_end(&self) -> bool { self.0.borrow().at_end }
        fn visuals_ready(&self) -> bool { self.0.borrow().visuals }
        fn play(&mut self) { let mut s = self.0.borrow_mut(); s.playing = true; s.calls.push("p.play".into()) }
        fn pause(&mut self) { let mut s = self.0.borrow_mut(); s.playing = false; s.calls.push("p.pause".into()) }
        fn seek_ms(&mut self, ms: i64) { let mut s = self.0.borrow_mut(); s.position = ms; s.calls.push(format!("p.seek {ms}")) }
        fn stop(&mut self) { let mut s = self.0.borrow_mut(); s.playing = false; s.calls.push("p.stop".into()) }
        fn set_shadow_audio(&mut self, on: bool) { let mut s = self.0.borrow_mut(); s.shadow = Some(on); s.calls.push(format!("p.shadow {on}")) }
    }

    #[derive(Default)]
    struct VState {
        ready: bool,
        position: f64,
        slaved: Vec<(f64, f64)>,
        calls: Vec<String>,
        resync: Option<(u32, u32, f32, f32, f32, i32)>,
    }
    #[derive(Clone, Default)]
    struct FakeVoice(Rc<RefCell<VState>>);
    impl Voice for FakeVoice {
        fn is_ready(&self) -> bool { self.0.borrow().ready }
        fn position(&self) -> f64 { self.0.borrow().position }
        fn play(&mut self) { self.0.borrow_mut().calls.push("v.play".into()) }
        fn pause(&mut self) { self.0.borrow_mut().calls.push("v.pause".into()) }
        fn stop(&mut self) { self.0.borrow_mut().calls.push("v.stop".into()) }
        fn seek(&mut self, s: f64) { self.0.borrow_mut().calls.push(format!("v.seek {s}")) }
        fn slave_to(&mut self, p: f64, r: f64) { self.0.borrow_mut().slaved.push((p, r)) }
        fn resync_info(&self) -> Option<(u32, u32, f32, f32, f32, i32)> { self.0.borrow().resync }
        fn duration(&self) -> f64 { 200.0 }
    }

    fn rig() -> (Protocol<FakePicture, FakeVoice>, FakePicture, FakeVoice) {
        let (p, v) = (FakePicture::default(), FakeVoice::default());
        p.0.borrow_mut().duration = 200_000;
        p.0.borrow_mut().visuals = true;
        (Protocol::new(p.clone(), Some(v.clone())), p, v)
    }
    fn run(proto: &mut Protocol<FakePicture, FakeVoice>, from: f64, to: f64) -> Vec<Event> {
        let mut out = Vec::new();
        let mut t = from;
        while t < to {
            out.extend(proto.tick(t));
            t += 0.01;
        }
        out
    }

    #[test]
    fn the_voice_plays_first_and_the_picture_follows_when_the_voice_clock_moves() {
        let (mut proto, p, v) = rig();
        proto.start(0.0, 0.0);
        assert_eq!(proto.phase, Phase::Starting);
        run(&mut proto, 0.0, 0.5);
        assert!(!p.0.borrow().playing, "the picture waits while the voice is not ready");
        v.0.borrow_mut().ready = true;
        run(&mut proto, 0.5, 0.6);
        assert_eq!(proto.phase, Phase::VoiceWait);
        assert!(v.0.borrow().calls.contains(&"v.play".to_string()));
        assert!(!p.0.borrow().playing, "the voice plays first: its clock has not moved yet");
        v.0.borrow_mut().position = 0.05;
        run(&mut proto, 0.6, 0.7);
        assert_eq!(proto.phase, Phase::Running);
        assert!(p.0.borrow().playing);
    }

    #[test]
    fn the_picture_plays_after_the_voice_wait_even_if_the_voice_clock_never_moves() {
        let (mut proto, p, v) = rig();
        v.0.borrow_mut().ready = true;
        proto.start(0.0, 0.0);
        run(&mut proto, 0.0, 0.5);
        assert!(!p.0.borrow().playing);
        run(&mut proto, 0.5, 1.5); // 0.7 s after the voice started
        assert_eq!(proto.phase, Phase::Running);
        assert!(p.0.borrow().playing);
    }

    #[test]
    fn a_voice_that_is_never_ready_is_dropped_four_seconds_after_the_picture_loaded_and_mpv_takes_over() {
        let (mut proto, p, v) = rig();
        proto.start(0.0, 0.0);
        run(&mut proto, 0.0, 3.9);
        assert_eq!(proto.phase, Phase::Starting);
        run(&mut proto, 3.9, 4.2);
        assert_eq!(proto.phase, Phase::Idle);
        assert!(proto.voice.is_none());
        assert_eq!(p.0.borrow().shadow, Some(false), "mpv gets its own audio back");
        assert!(p.0.borrow().playing);
        assert!(v.0.borrow().calls.contains(&"v.stop".to_string()));
        assert!(proto.log[0].contains("the voice was not ready in time"));
        assert!(proto.dropped.is_some());
    }

    #[test]
    fn a_picture_that_never_loads_is_given_up_at_the_hard_deadline() {
        let (mut proto, p, _v) = rig();
        p.0.borrow_mut().duration = 0;
        proto.start(0.0, 0.0);
        run(&mut proto, 0.0, 19.9);
        assert_eq!(proto.phase, Phase::Starting, "the voice's 4 s only count once the picture is loaded");
        run(&mut proto, 19.9, 20.2);
        assert_eq!(proto.phase, Phase::Idle);
        assert!(proto.log[0].contains("the picture did not load in time"));
    }

    #[test]
    fn a_seek_pauses_both_moves_both_and_restarts_them_together() {
        let (mut proto, p, v) = rig();
        v.0.borrow_mut().ready = true;
        proto.start(0.0, 0.0);
        v.0.borrow_mut().position = 0.1;
        run(&mut proto, 0.0, 2.0);
        assert_eq!(proto.phase, Phase::Running);
        v.0.borrow_mut().calls.clear();
        p.0.borrow_mut().calls.clear();
        proto.seek(2.0, 60.0);
        assert_eq!(proto.phase, Phase::Seeking);
        assert_eq!(v.0.borrow().calls, vec!["v.pause", "v.seek 60"]);
        assert_eq!(p.0.borrow().calls, vec!["p.pause", "p.seek 60000"]);
        assert!(!p.0.borrow().playing);
        v.0.borrow_mut().ready = false; // refilling
        run(&mut proto, 2.0, 2.5);
        assert_eq!(proto.phase, Phase::Seeking);
        v.0.borrow_mut().ready = true;
        v.0.borrow_mut().position = 60.0;
        run(&mut proto, 2.5, 2.6);
        assert_eq!(proto.phase, Phase::VoiceWait);
        v.0.borrow_mut().position = 60.1;
        run(&mut proto, 2.6, 2.7);
        assert_eq!(proto.phase, Phase::Running);
        assert!(p.0.borrow().playing);
    }

    #[test]
    fn pause_and_resume_stop_both_and_resume_only_plays_the_voice_while_running() {
        let (mut proto, p, v) = rig();
        v.0.borrow_mut().ready = true;
        proto.start(0.0, 0.0);
        v.0.borrow_mut().position = 0.1;
        run(&mut proto, 0.0, 2.0);
        proto.pause();
        assert!(!p.0.borrow().playing);
        assert!(v.0.borrow().calls.contains(&"v.pause".to_string()));
        v.0.borrow_mut().calls.clear();
        proto.resume();
        assert_eq!(v.0.borrow().calls, vec!["v.play"]);
        assert!(p.0.borrow().playing);
    }

    #[test]
    fn resume_during_the_start_sequence_waits_for_the_sequence() {
        let (mut proto, p, _v) = rig();
        proto.start(0.0, 0.0);
        proto.pause();
        proto.resume();
        assert!(!p.0.borrow().playing, "the start sequence will play both when it is ready");
    }

    #[test]
    fn started_is_emitted_once_the_picture_plays_and_ended_stops_the_voice() {
        let (mut proto, p, v) = rig();
        v.0.borrow_mut().ready = true;
        proto.start(0.0, 0.0);
        v.0.borrow_mut().position = 0.1;
        let mut ev = Vec::new();
        let mut t = 0.0;
        while t < 1.5 {
            p.0.borrow_mut().position = if p.0.borrow().playing { (p.0.borrow().position + 10).max(100) } else { 0 };
            ev.extend(proto.tick(t));
            t += 0.01;
        }
        assert_eq!(ev, vec![Event::Started]);
        p.0.borrow_mut().at_end = true;
        let ev = run(&mut proto, 1.5, 1.7);
        assert_eq!(ev, vec![Event::Ended]);
        assert!(v.0.borrow().calls.last().unwrap() == "v.stop");
        assert!(run(&mut proto, 1.7, 2.5).is_empty(), "ended is emitted once and the machine goes quiet");
    }

    #[test]
    fn the_picture_clock_reaches_the_voice_about_every_30_ms_with_the_measured_rate() {
        let (mut proto, p, v) = rig();
        v.0.borrow_mut().ready = true;
        proto.start(0.0, 0.0);
        v.0.borrow_mut().position = 0.1;
        run(&mut proto, 0.0, 1.0);
        v.0.borrow_mut().slaved.clear();
        let mut t = 1.0;
        while t < 3.0 {
            p.0.borrow_mut().position = (t * 1000.0) as i64;
            proto.tick(t);
            t += 0.01;
        }
        let slaved = v.0.borrow().slaved.clone();
        assert!((60..=70).contains(&slaved.len()), "{} reports in 2 s", slaved.len());
        let last = slaved.last().unwrap();
        assert!((last.1 - 1.0).abs() < 0.05, "measured rate {}", last.1);
    }

    #[test]
    fn a_paused_picture_reports_rate_zero() {
        let (mut proto, p, v) = rig();
        v.0.borrow_mut().ready = true;
        proto.start(0.0, 0.0);
        v.0.borrow_mut().position = 0.1;
        run(&mut proto, 0.0, 1.0);
        p.0.borrow_mut().playing = false; // e.g. mpv paused by itself
        v.0.borrow_mut().slaved.clear();
        run(&mut proto, 1.0, 1.2);
        assert!(v.0.borrow().slaved.iter().all(|s| s.1 == 0.0));
    }

    #[test]
    fn rate_meter_measures_pace_and_forgets_after_a_jump() {
        let mut m = RateMeter::default();
        let mut r = 0.0;
        for i in 0..40 {
            let t = i as f64 * 0.03;
            r = m.measure(t, 10.0 + t * 1.25, 1.0);
        }
        assert!((r - 1.25).abs() < 0.01, "{r}");
        assert_eq!(m.measure(2.0, 99.0, 1.0), 1.0, "after a seek the commanded rate is used until samples accumulate");
        assert_eq!(m.measure(2.03, 99.04, 0.0), 0.0);
    }

    #[test]
    fn resyncs_and_held_back_resyncs_are_logged_but_the_first_look_is_silent() {
        let (mut proto, _p, v) = rig();
        v.0.borrow_mut().ready = true;
        proto.start(0.0, 0.0);
        v.0.borrow_mut().position = 0.1;
        v.0.borrow_mut().resync = Some((0, 0, 0.0, 0.0, 0.0, 0));
        run(&mut proto, 0.0, 1.0);
        assert!(proto.log.is_empty());
        v.0.borrow_mut().resync = Some((1, 0, 5.0, 5.4, 5.4, 0));
        run(&mut proto, 1.0, 1.1);
        assert!(proto.log.iter().any(|l| l.contains("voice re-synced to the picture clock: song 5.00s -> 5.40s")));
        v.0.borrow_mut().resync = Some((1, 1, 199.0, 199.9, 199.9, 1));
        run(&mut proto, 1.1, 1.2);
        assert!(proto.log.iter().any(|l| l.contains("voice kept its place (close to the end of the song)")));
    }

    #[test]
    fn a_seek_into_the_cdg_tail_moves_both_to_the_last_frame_before_it() {
        let (mut proto, p, v) = rig();
        proto.seek_gap = vec![(100.0, 130.0)];
        v.0.borrow_mut().ready = true;
        proto.start(0.0, 0.0);
        run(&mut proto, 0.0, 2.0);
        assert_eq!(proto.phase, Phase::Running);
        proto.seek(2.0, 115.0);
        assert!(p.0.borrow().calls.contains(&"p.seek 100000".to_string()), "{:?}", p.0.borrow().calls);
        assert!(v.0.borrow().calls.contains(&"v.seek 100".to_string()), "{:?}", v.0.borrow().calls);
        proto.seek(2.5, 90.0);
        assert!(p.0.borrow().calls.contains(&"p.seek 90000".to_string()));
    }

    #[test]
    fn without_a_voice_the_picture_plays_at_once_with_its_own_audio() {
        let p = FakePicture::default();
        let mut proto: Protocol<FakePicture, FakeVoice> = Protocol::new(p.clone(), None);
        proto.start(0.0, 12.0);
        assert!(p.0.borrow().playing);
        assert_eq!(p.0.borrow().position, 12_000);
    }
}
