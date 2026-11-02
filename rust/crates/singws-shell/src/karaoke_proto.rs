//! The karaoke start/follow protocol of `mpv_karaoke_transport.py` (`MpvKaraokeTransport` with a Rust voice attached), as a pure state machine.
//!
//! mpv (the "picture") keeps the song's clock; the Rust karaoke voice plays the sound and is slaved to that clock. At the start of a song and after
//! a seek the voice plays first (its stretcher needs ~130 ms before sound comes out), and the picture plays as soon as the voice's clock moves.
//! Time is passed in (`now`, seconds) so the machine is tested against fakes; `play_demo.rs` wires the real bridge and engine.
//! Ported: `start`, `_voice_step`, `_voice_follow` (+ the `_measured_rate` of `rust_karaoke_voice.py`), `seek`, `pause`, `resume`, the fallbacks
//! (`_drop_voice`), the started/ended events of `_poll`, and the resync log. Not ported: loop, fade_out, volume/EQ/master chain, key/tempo changes.

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
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Idle,
    Starting,
    VoiceWait,
    Seeking,
    Running,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Event {
    Started,
    Ended,
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

pub struct Protocol<P: Picture, V: Voice> {
    pub picture: P,
    pub voice: Option<V>,
    pub phase: Phase,
    pub log: Vec<String>,
    /// Set when the voice was given up on (the song then plays with mpv's own audio): the reason.
    pub dropped: Option<String>,
    /// A CDG's graphics-free tail (`singws_cdg::terminal_gap`): a seek into it moves back to the last frame before it, for picture and voice alike.
    pub seek_gap: Option<(f64, f64)>,
    tempo_ratio: f64,
    user_paused: bool,
    stopped: bool,
    started_emitted: bool,
    ended_emitted: bool,
    deadline: f64,
    hard_deadline: f64,
    loaded_at: Option<f64>,
    origin: f64,
    last_follow: f64,
    follow_every: f64,
    last_poll: f64,
    poll_every: f64,
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
            dropped: None,
            seek_gap: None,
            tempo_ratio: 1.0,
            user_paused: false,
            stopped: true,
            started_emitted: false,
            ended_emitted: false,
            deadline: 0.0,
            hard_deadline: 0.0,
            loaded_at: None,
            origin: 0.0,
            last_follow: f64::NEG_INFINITY,
            follow_every: 0.030,
            last_poll: f64::NEG_INFINITY,
            poll_every: 0.050,
            meter: RateMeter::default(),
            resync_seen: None,
            playing_polls_without_visual: 0,
        }
    }

    pub fn user_paused(&self) -> bool {
        self.user_paused
    }

    /// Both are loaded and paused (the voice from `Voice` construction, the picture from the bridge load). Mirrors the tail of `start()`.
    pub fn start(&mut self, now: f64, start_seconds: f64) {
        let start_seconds = singws_cdg::clamp_seek(self.seek_gap, start_seconds);
        self.user_paused = false;
        if self.voice.is_some() {
            self.phase = Phase::Starting;
            self.deadline = now + 4.0;
            self.hard_deadline = now + 20.0;
            self.loaded_at = None;
        } else {
            self.picture.play(); // no voice: mpv plays the sound itself
        }
        if start_seconds > 0.0 {
            self.picture.seek_ms((start_seconds * 1000.0) as i64);
        }
        self.stopped = false;
        self.started_emitted = false;
        self.ended_emitted = false;
        self.playing_polls_without_visual = 0;
    }

    /// Give the song back to mpv's own audio: never leave a show without sound.
    fn drop_voice(&mut self, reason: &str) {
        self.log.push(format!("[KARAOKE-RUST] falling back to mpv audio for this song: {reason}"));
        if let Some(v) = self.voice.as_mut() {
            v.stop();
        }
        self.voice = None;
        self.phase = Phase::Idle;
        self.dropped = Some(reason.to_string());
        self.picture.set_shadow_audio(false);
    }

    pub fn stop(&mut self) {
        self.stopped = true;
        self.phase = Phase::Idle;
        if let Some(v) = self.voice.as_mut() {
            v.stop();
        }
        self.picture.stop();
    }

    pub fn seek(&mut self, now: f64, seconds: f64) {
        let target = singws_cdg::clamp_seek(self.seek_gap, seconds.max(0.0));
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
            self.loaded_at = None;
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

    pub fn position_seconds(&self) -> f64 {
        (self.picture.position_ms() as f64 / 1000.0).max(0.0)
    }

    /// `_voice_step`: start the picture and the sound together (beginning of a song and after a seek).
    fn voice_step(&mut self, now: f64) {
        if self.stopped || self.user_paused || !matches!(self.phase, Phase::Starting | Phase::Seeking | Phase::VoiceWait) {
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
        let rate = self.meter.measure(now, pos, commanded);
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
            self.log.push(format!("[KARAOKE-RUST] voice re-synced to the picture clock: song {here:.2}s -> {target:.2}s (picture clock {master:.2}s, song length {song:.1}s)"));
        } else if guarded != seen.1 && (kind == 1 || kind == 2) {
            let why = if kind == 1 { "close to the end of the song" } else { "the picture clock jumped and did not stay" };
            self.log.push(format!("[KARAOKE-RUST] voice kept its place ({why}): song {here:.2}s, picture clock {master:.2}s, song length {song:.1}s"));
        }
        self.resync_seen = Some((resyncs, guarded));
    }

    /// Call every ~10 ms. Runs the start sequence each call, the follow at ~30 ms and the poll (started / ended) at ~50 ms.
    pub fn tick(&mut self, now: f64) -> Vec<Event> {
        let mut events = Vec::new();
        if self.stopped {
            return events;
        }
        self.voice_step(now);
        if now - self.last_follow >= self.follow_every - 1e-6 {
            self.last_follow = now;
            self.follow(now);
        }
        if now - self.last_poll < self.poll_every - 1e-6 {
            return events;
        }
        self.last_poll = now;
        let ready = self.picture.visuals_ready();
        let playing = self.picture.is_playing() && self.picture.position_ms() >= 40;
        if !self.started_emitted {
            if playing && !ready {
                self.playing_polls_without_visual += 1;
            } else {
                self.playing_polls_without_visual = 0;
            }
            // a late native drawable must not leave Play locked in "starting": commit after one second of sound without a picture
            if playing && (ready || self.playing_polls_without_visual >= 20) {
                self.started_emitted = true;
                events.push(Event::Started);
            }
        }
        if !self.ended_emitted && self.picture.at_end() {
            self.ended_emitted = true;
            self.stopped = true;
            if let Some(v) = self.voice.as_mut() {
                v.stop();
            }
            self.phase = Phase::Idle;
            events.push(Event::Ended);
        }
        events
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
        proto.seek_gap = Some((100.0, 130.0));
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
