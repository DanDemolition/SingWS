//! W10: the DRIVER, the first runtime that acts on the two machines instead of only replaying them. It owns what the pure machines must not: the display program
//! (through [`Io`]), the saved login, the timers (the 1 s start watchdog and the countdown hold), and the routing of the player's messages between the service
//! machine (sign-in) and the host machine (the song). Python never has to decide anything for a song: it asks (`start_song`, `toggle_pause`, `seek_to`,
//! `apply_key_tempo`, `on_finish`), feeds in what the display program says (`on_display_message`) and calls `tick(now)` when `next_deadline()` passes. What only
//! the app can do (the queue entry, notifications, background music fade, the picture windows, the log) comes back through `Io::app` as the same effect arrays the
//! host machine produces, so the app side is a thin interpreter.
//!
//! Nothing here reads a clock or a device: time is passed in (monotonic seconds) and every outside effect is the `Io` trait, so the whole driver is tested
//! offline with a scripted fake (see `tests`). Nothing in the app calls it yet; `karafun_web_engine=rust` is where it will be wired.
use crate::host::{Event as HostEvent, Host, World};
use crate::{Action, Event as SvcEvent, Machine, SavedLogin};
use serde_json::{json, Value};

/// Everything outside the driver.
pub trait Io {
    /// One JSON-line command for the display program (`play`, `stop`, `seek`, `key`, `tempo`, `reload`, `signin`, `quit`, ...).
    fn display(&mut self, command: Value);
    /// Make sure Chromium is here and the display program runs. `Err(reason)` is the plain reason the set-up failed. `Ok(false)` = Chromium is missing and the user
    /// must be asked first (the service then waits in `needs_browser`).
    fn start_display(&mut self) -> Result<bool, String>;
    /// Stop the display program.
    fn stop_display(&mut self);
    /// Move the player's profile folder aside (after KaraFun's engine crashed on it).
    fn reset_profile(&mut self);
    /// Read the saved login; only whether one exists comes back, the secret stays in the Io.
    fn saved_login(&mut self) -> SavedLogin;
    /// Send the saved login to the page (`login` command with the email and password the Io holds).
    fn login(&mut self);
    /// Whether stopping and restarting the player right now would FAIL, and why (Python: `service.stop()` raising during a recovery). The song machine then gives up on
    /// the song with "could not be restarted" instead of waiting for a player that is not coming back. Read before every step; most Ios never fail.
    fn restart_error(&mut self) -> Option<String> {
        None
    }
    /// An effect only the app can perform: `entry_set`, `entry_status`, `entry_wall`, `entry_pop`, `notify`, `fade_bg`, `update_queue`, `save`,
    /// `start_frames`, `start_frames_hidden`, `stop_frames`, `show_key_tempo`, `verified_duration`, `finish_complete`, `diag`, `log`, ...
    fn app(&mut self, effect: &Value);
}

/// How a song is asked for.
#[derive(Debug, Clone, Default)]
pub struct StartRequest {
    pub song_id: Option<i64>,
    pub enabled: bool,
    pub key: Option<i64>,
    pub tempo: Option<Value>,
    pub title: String,
    /// what the page needs to find the song (`query`, `queries`, `artist`, `title`)
    pub play: Value,
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Timer {
    Watchdog,
    Hold,
}

pub struct Driver<I: Io> {
    pub io: I,
    svc: Machine,
    host: Host,
    timers: Vec<(f64, Timer)>,
    play: Value,
    returns: Option<bool>,
    last_message: Option<Value>,
    /// `app._karafun_active_entry() is asked["entry"]` and `app._active_external_karafun is asked["active"]`: the app says when it changed them
    pub entry_current: bool,
    pub active_same: bool,
}

impl<I: Io> Driver<I> {
    pub fn new(io: I, has_credentials: bool) -> Self {
        Driver { io, svc: Machine::new(has_credentials), host: Host::new(), timers: Vec::new(), play: Value::Null, returns: None, last_message: None, entry_current: true, active_same: true }
    }

    /// Whether the display program is up (messages can only come from it then).
    pub fn service_has_web(&self) -> bool {
        self.svc.has_web
    }

    pub fn service_state(&self) -> &str {
        &self.svc.state
    }

    pub fn host_state(&self) -> (&str, &str) {
        (&self.host.state.0, &self.host.state.1)
    }

    /// The next moment `tick` has work, if any.
    pub fn next_deadline(&self) -> Option<f64> {
        self.timers.iter().map(|t| t.0).fold(None, |m, t| Some(m.map_or(t, |x: f64| x.min(t))))
    }

    fn world(&mut self) -> World {
        let restart_error = self.io.restart_error();
        World { service_present: true, service_state: self.svc.state.clone(), has_web: self.svc.has_web, entry_current: self.entry_current, active_same: self.active_same, restart_error }
    }

    // ---- what the app asks for

    /// A song is asked for. Returns whether it was accepted (`false`: not a KaraFun song / disabled).
    pub fn start_song(&mut self, req: &StartRequest, now: f64) -> bool {
        self.play = req.play.clone();
        self.returns = None;
        // two halves, because the Python reads the sign-in state AFTER the set-up has started: the first half ends with `ensure_service` (carried out here, in order, with
        // the state changes it causes), the second half decides on what the service says by then
        let w = self.world();
        let ev = HostEvent::StartPrefix { song_id: req.song_id, enabled: req.enabled, key: req.key, tempo: req.tempo.as_ref(), title: &req.title };
        self.host_step(ev, w, now);
        if self.returns == Some(false) {
            return false;
        }
        let w = self.world();
        self.host_step(HostEvent::StartBranch, w, now);
        self.returns.unwrap_or(false)
    }

    pub fn toggle_pause(&mut self, now: f64) -> bool {
        self.simple(HostEvent::TogglePause, now)
    }

    pub fn seek_to(&mut self, target: f64, now: f64) -> bool {
        self.simple(HostEvent::SeekTo(target), now)
    }

    pub fn apply_key_tempo(&mut self, key: i64, tempo: i64, now: f64) -> bool {
        self.simple(HostEvent::ApplyKeyTempo { key, tempo }, now)
    }

    pub fn on_finish(&mut self, now: f64) -> bool {
        self.simple(HostEvent::OnFinish, now)
    }

    fn simple(&mut self, ev: HostEvent, now: f64) -> bool {
        self.returns = None;
        let w = self.world();
        self.host_step(ev, w, now);
        self.returns.unwrap_or(false)
    }

    /// Stop everything (the app is closing or the show ended).
    pub fn shutdown(&mut self, now: f64) {
        self.timers.clear();
        self.svc_step(SvcEvent::Stop, now);
    }

    // ---- what the display program says

    /// One JSON line from the display program. The service machine sees every message first (it decides the sign-in), then the song machine.
    pub fn on_display_message(&mut self, message: &Value, now: f64) {
        self.last_message = Some(message.clone());
        self.svc_step(SvcEvent::Message(message), now);
        self.last_message = None;
    }

    /// Run the timers that are due.
    pub fn tick(&mut self, now: f64) {
        loop {
            // the earliest due timer first (ties: the one armed first), as real timers fire
            let mut pos = None;
            for (i, t) in self.timers.iter().enumerate() {
                if t.0 <= now && pos.map_or(true, |p: usize| t.0 < self.timers[p].0) {
                    pos = Some(i);
                }
            }
            let Some(pos) = pos else { return };
            let (_, kind) = self.timers.remove(pos);
            let w = self.world();
            let ev = match kind {
                Timer::Watchdog => HostEvent::Watchdog,
                Timer::Hold => HostEvent::HoldOver,
            };
            self.host_step(ev, w, now);
        }
    }

    // ---- the service machine

    fn svc_step(&mut self, ev: SvcEvent, now: f64) {
        match self.svc.step(ev, now) {
            Ok(actions) => {
                for action in actions {
                    self.svc_action(action, now);
                }
            }
            Err(why) => self.io.app(&json!(["log", format!("service machine declined: {why}")])),
        }
    }

    fn svc_action(&mut self, action: Action, now: f64) {
        match action {
            Action::Set(state, detail) => {
                let w = self.world();
                self.host_step(HostEvent::Service { state: &state, detail: &detail }, w, now);
            }
            Action::Log(text) => self.io.app(&json!(["log", text])),
            Action::WebReload => self.io.display(json!({"cmd": "reload"})),
            Action::WebLogin => self.io.login(),
            Action::WebSignIn => self.io.display(json!({"cmd": "signin"})),
            Action::WebClose => {
                self.io.display(json!({"cmd": "quit"}));
                self.io.stop_display();
            }
            Action::ReadSavedLogin => {
                let saved = self.io.saved_login();
                self.svc_step(SvcEvent::SavedLogin(saved), now);
            }
            Action::Restart => {
                self.io.reset_profile();
                self.ensure_service(now);
            }
            Action::Forward => {
                // a forwarded message is the page's message again: the song machine's turn (the caller of `svc_step` still holds it, so it is rebuilt from the last one)
                if let Some(m) = self.last_message.take() {
                    self.route_to_host(&m, now);
                }
            }
        }
    }

    /// `ensure_service`: start the set-up once (idle / error / needs_browser), then run the set-up the way the Python thread does.
    pub fn ensure_service(&mut self, now: f64) {
        if !matches!(self.svc.state.as_str(), "idle" | "error" | "needs_browser") {
            return;
        }
        self.svc_step(SvcEvent::Start { alive: false }, now);
        self.svc_step(SvcEvent::RunBegin, now);
        match self.io.start_display() {
            Ok(true) => {
                self.svc_step(SvcEvent::BrowserFound, now);
                self.svc_step(SvcEvent::WebMade, now);
            }
            Ok(false) => {
                let w = self.world();
                self.svc.state = "needs_browser".into();
                self.host_step(HostEvent::Service { state: "needs_browser", detail: "KaraFun's player needs Chromium, which is not on this Mac yet" }, w, now);
            }
            Err(why) => self.svc_step(SvcEvent::SetupFailed { error: &why }, now),
        }
    }

    // ---- the song machine

    fn route_to_host(&mut self, m: &Value, now: f64) {
        let kind = m.get("type").and_then(|k| k.as_str()).unwrap_or("");
        let w = self.world();
        match kind {
            "status" => self.host_step(HostEvent::Status(m), w, now),
            "error" => {
                // the song machine takes the error's TEXT (`str(message.get("message") or "")`)
                let text = Value::String(m.get("message").map(|t| t.as_str().map(|s| s.to_string()).unwrap_or_else(|| t.to_string())).filter(|t| t != "null").unwrap_or_default());
                self.host_step(HostEvent::PageError(&text), w, now)
            }
            // `log` / `ready` / `helper` lines carry nothing for the song machine: the app logs them and places the picture windows
            _ => self.io.app(&json!(["page_message", m])),
        }
    }

    fn host_step(&mut self, ev: HostEvent, w: World, now: f64) {
        match self.host.step(ev, &w, now) {
            Ok(effects) => {
                for effect in effects {
                    self.host_effect(effect, now);
                }
            }
            Err(why) => self.io.app(&json!(["log", format!("song machine declined: {why}")])),
        }
    }

    fn host_effect(&mut self, effect: Value, now: f64) {
        let name = effect.get(0).and_then(|n| n.as_str()).unwrap_or("").to_string();
        let arg = |i: usize| effect.get(i).cloned().unwrap_or(Value::Null);
        match name.as_str() {
            "returns" => self.returns = arg(1).as_bool(),
            "web_play" => {
                let mut c = json!({"cmd": "play", "id": arg(1)});
                for key in ["query", "queries", "artist", "title"] {
                    if let Some(v) = self.play.get(key) {
                        c[key] = v.clone();
                    }
                }
                self.io.display(c);
            }
            "web_stop" => self.io.display(json!({"cmd": "stop"})),
            "web_pause" => self.io.display(json!({"cmd": "pause"})),
            "web_resume" => self.io.display(json!({"cmd": "resume"})),
            "web_seek" => self.io.display(json!({"cmd": "seek", "seconds": arg(1)})),
            "web_set_key" => self.io.display(json!({"cmd": "key", "semitones": arg(1)})),
            "web_set_tempo" => self.io.display(json!({"cmd": "tempo", "percent": arg(1)})),
            "arm_watchdog" => self.timers.push((now + 1.0, Timer::Watchdog)),
            "hold_timer" => {
                let ms = arg(1).as_f64().unwrap_or(0.0);
                self.timers.push((now + ms / 1000.0, Timer::Hold));
            }
            "ensure_service" => self.ensure_service(now),
            "service_stop" => self.svc_step(SvcEvent::Stop, now),
            "service_start" => self.ensure_service(now),
            _ => self.io.app(&effect),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Default)]
    struct Fake {
        display: Vec<Value>,
        app: Vec<Value>,
        started: u32,
        stopped: u32,
        logins: u32,
        saved: Option<SavedLogin>,
        chromium_here: bool,
        restart_error: Option<String>,
    }

    impl Io for Fake {
        fn display(&mut self, c: Value) { self.display.push(c); }
        fn start_display(&mut self) -> Result<bool, String> { self.started += 1; Ok(self.chromium_here) }
        fn stop_display(&mut self) { self.stopped += 1; }
        fn reset_profile(&mut self) {}
        fn restart_error(&mut self) -> Option<String> { self.restart_error.clone() }
        fn saved_login(&mut self) -> SavedLogin { self.saved.clone().unwrap_or(SavedLogin::None) }
        fn login(&mut self) { self.logins += 1; }
        fn app(&mut self, e: &Value) { self.app.push(e.clone()); }
    }

    fn driver() -> Driver<Fake> {
        Driver::new(Fake { chromium_here: true, ..Default::default() }, true)
    }

    fn req() -> StartRequest {
        StartRequest { song_id: Some(58970), enabled: true, key: Some(0), tempo: None, title: "Linoleum".into(),
                       play: json!({"query": "NOFX Linoleum", "queries": ["NOFX Linoleum"], "artist": "NOFX", "title": "Linoleum"}) }
    }

    fn status(signed_in: bool, playing: bool, pos: f64, dur: f64) -> Value {
        json!({"type": "status", "signedIn": signed_in, "plan": "Plus", "playing": playing, "state": if playing { "playing" } else { "idle" }, "position": pos, "duration": dur, "song": {"id": 58970}})
    }

    fn names(v: &[Value]) -> Vec<String> {
        v.iter().filter_map(|e| e.get(0).and_then(|n| n.as_str()).map(|s| s.to_string())).collect()
    }

    #[test]
    fn a_song_asked_for_before_the_player_is_signed_in_waits_then_plays_after_the_hold() {
        let mut d = driver();
        assert!(d.start_song(&req(), 0.0));
        assert_eq!(d.io.started, 1, "the display program is started once");
        assert_eq!(d.service_state(), "starting");
        assert!(d.io.display.is_empty(), "nothing is played while the player is not ready");
        assert_eq!(d.next_deadline(), Some(1.0), "the 1 s start watchdog is running");
        // the player says it is signed in
        d.on_display_message(&status(true, false, 0.0, 0.0), 2.0);
        assert_eq!(d.service_state(), "ready");
        assert!(d.io.display.is_empty(), "ready, but the countdown hold has not passed");
        let due = d.next_deadline().unwrap();
        assert!((due - 1.0).abs() < 1e-9 || due > 2.0);
        d.tick(2.0 + 3.6);
        let plays: Vec<_> = d.io.display.iter().filter(|c| c["cmd"] == "play").collect();
        assert_eq!(plays.len(), 1, "exactly one play: {:?}", d.io.display);
        assert_eq!(plays[0]["id"], 58970);
        assert_eq!(plays[0]["artist"], "NOFX");
        // the page says the song is playing
        d.on_display_message(&status(true, true, 1.0, 120.0), 6.8);
        assert!(names(&d.io.app).iter().any(|n| n == "start_frames"), "the picture goes over the windows once the song really plays: {:?}", names(&d.io.app));
    }

    #[test]
    fn a_ready_player_plays_after_the_hold_and_the_song_ends_by_itself() {
        let mut d = driver();
        d.start_song(&req(), 0.0);
        d.on_display_message(&status(true, false, 0.0, 0.0), 0.5);
        d.tick(5.0);
        assert_eq!(d.io.display.iter().filter(|c| c["cmd"] == "play").count(), 1);
        d.on_display_message(&status(true, true, 1.0, 10.0), 5.5);
        d.on_display_message(&status(true, true, 9.9, 10.0), 14.5);
        assert_eq!(names(&d.io.app).iter().filter(|n| *n == "finish_complete").count(), 0, "not finished while it still plays");
        // stopped at the end: the song is complete, exactly once, and the picture leaves
        d.on_display_message(&status(true, false, 10.0, 10.0), 15.0);
        let n = names(&d.io.app);
        assert_eq!(n.iter().filter(|x| *x == "finish_complete").count(), 1, "{n:?}");
        // the player keeps saying the same thing: the song is not completed again
        d.on_display_message(&status(true, false, 10.0, 10.0), 15.5);
        d.tick(20.0);
        assert_eq!(names(&d.io.app).iter().filter(|x| *x == "finish_complete").count(), 1);
        // the app's own finish then stops the player and clears the picture
        assert!(d.on_finish(16.0), "a web song was open, so the app may bring its slideshow back");
        assert_eq!(d.io.display.last().map(|c| c["cmd"].clone()), Some(json!("stop")), "{:?}", d.io.display);
        assert_eq!(names(&d.io.app).last().map(|s| s.as_str()), Some("stop_frames"), "the picture leaves after the stop: {:?}", names(&d.io.app));
    }

    #[test]
    fn a_player_that_cannot_be_restarted_fails_the_song_instead_of_waiting() {
        let mut d = driver();
        d.start_song(&req(), 0.0);
        d.on_display_message(&status(true, false, 0.0, 0.0), 0.5);
        d.tick(5.0);
        d.on_display_message(&status(true, true, 1.0, 120.0), 5.5);
        d.io.restart_error = Some("the player would not close".into());
        d.on_display_message(&json!({"type": "error", "message": "KaraFun's web player stopped"}), 8.0);
        let text: Vec<String> = d.io.app.iter().map(|e| e.to_string()).collect();
        assert!(text.iter().any(|t| t.contains("could not be restarted")), "{text:?}");
        assert!(names(&d.io.app).iter().any(|n| n == "update_queue"), "the failed song is shown as failed");
    }

    #[test]
    fn a_song_that_never_starts_is_failed_by_the_watchdog() {
        let mut d = driver();
        d.start_song(&req(), 0.0);
        d.on_display_message(&status(true, false, 0.0, 0.0), 0.5);
        let mut t = 0.5;
        while t < 90.0 {
            t += 1.0;
            d.tick(t);
        }
        let n: Vec<String> = d.io.app.iter().map(|e| e.to_string()).collect();
        assert!(n.iter().any(|x| x.contains("never started") || x.contains("failed") || x.contains("did not start")), "{n:?}");
    }

    #[test]
    fn pause_and_resume_reach_the_player() {
        let mut d = driver();
        d.start_song(&req(), 0.0);
        d.on_display_message(&status(true, false, 0.0, 0.0), 0.5);
        d.tick(5.0);
        d.on_display_message(&status(true, true, 1.0, 120.0), 5.5);
        assert!(d.toggle_pause(6.0));
        d.on_display_message(&status(true, false, 2.0, 120.0), 6.5);
        assert!(d.toggle_pause(7.0));
        let cmds: Vec<String> = d.io.display.iter().filter_map(|c| c["cmd"].as_str().map(|s| s.to_string())).collect();
        assert_eq!(cmds, ["play", "pause", "resume"], "{cmds:?}");
    }

    #[test]
    fn chromium_missing_asks_the_user_instead_of_failing() {
        let mut d = Driver::new(Fake { chromium_here: false, ..Default::default() }, false);
        d.start_song(&req(), 0.0);
        assert_eq!(d.service_state(), "needs_browser");
    }

    #[test]
    fn a_signed_out_player_uses_the_saved_login_once() {
        let mut d = driver();
        d.io.saved = Some(SavedLogin::Found);
        d.start_song(&req(), 0.0);
        for i in 0..20 {
            d.on_display_message(&status(false, false, 0.0, 0.0), 1.0 + i as f64 * 0.5);
        }
        assert_eq!(d.io.logins, 1, "the saved login is sent once");
    }

    #[test]
    fn a_disabled_song_is_refused_without_starting_anything() {
        let mut d = driver();
        let mut r = req();
        r.enabled = false;
        assert!(!d.start_song(&r, 0.0), "a disabled KaraFun is not started");
        assert_eq!(d.io.started, 0, "the display program is not started for a refused song");
        assert!(d.io.display.is_empty());
        assert_eq!(d.next_deadline(), None, "no timers for a refused song");
    }

    #[test]
    fn the_app_is_told_no_when_nothing_is_open() {
        let mut d = driver();
        assert!(!d.toggle_pause(1.0));
        assert!(!d.seek_to(5.0, 1.0));
        assert!(!d.on_finish(1.0));
        assert!(!d.apply_key_tempo(1, 100, 1.0));
    }

    #[test]
    fn the_hold_timer_is_the_asked_number_of_milliseconds() {
        let mut d = driver();
        d.start_song(&req(), 0.0);
        d.on_display_message(&status(true, false, 0.0, 0.0), 2.0);
        // the hold armed at 2.0 s: its due time is 2.0 + whole milliseconds / 1000
        let due = d.timers.iter().map(|t| t.0).filter(|t| *t > 2.0).fold(f64::MAX, f64::min);
        let ms = (due - 2.0) * 1000.0;
        assert!((ms - ms.round()).abs() < 1e-6, "{ms} ms is not a whole number of milliseconds");
        assert!(ms > 0.0 && ms <= 3500.0, "{ms} ms");
    }

    #[test]
    fn two_timers_due_together_fire_in_the_order_they_were_armed() {
        let build = |order: [Timer; 2]| {
            let mut d = driver();
            d.start_song(&req(), 0.0);
            d.on_display_message(&status(true, false, 0.0, 0.0), 0.5);
            d.timers.clear();
            for k in order {
                d.timers.push((1.0, k));
            }
            d.tick(1.0);
            d.io.app.iter().map(|e| e.to_string()).collect::<Vec<_>>()
        };
        let sequential = |order: [Timer; 2]| {
            let mut d = driver();
            d.start_song(&req(), 0.0);
            d.on_display_message(&status(true, false, 0.0, 0.0), 0.5);
            d.timers.clear();
            for k in order {
                d.timers.push((1.0, k));
                d.tick(1.0);
            }
            d.io.app.iter().map(|e| e.to_string()).collect::<Vec<_>>()
        };
        assert_eq!(build([Timer::Watchdog, Timer::Hold]), sequential([Timer::Watchdog, Timer::Hold]));
        assert_eq!(build([Timer::Hold, Timer::Watchdog]), sequential([Timer::Hold, Timer::Watchdog]));
    }

    #[test]
    fn shutdown_stops_the_player_and_the_timers() {
        let mut d = driver();
        d.start_song(&req(), 0.0);
        d.on_display_message(&status(true, false, 0.0, 0.0), 0.5);
        d.shutdown(1.0);
        assert_eq!(d.next_deadline(), None);
        assert_eq!(d.io.stopped, 1);
        assert_eq!(d.service_state(), "idle");
    }
}

/// Replay of a whole session for the parity tool (`{"kind":"driver", ...}`): a scripted clock, a fake display program and a fake saved login. Events (each with `t`):
/// start {song_id, key, tempo, title}, msg {message}, tick, toggle_pause, seek {target}, key_tempo {key, tempo}, finish. Timers due before an event fire first, each at its
/// own due time. The trace per event is the ordered list of what happened outside the driver, in the same spelling the Python oracle records.
pub mod replay {
    use super::*;

    #[derive(Default)]
    struct Rec {
        log: Vec<Value>,
        chromium: bool,
        saved: String,
        restart: Option<String>,
    }

    impl Io for Rec {
        fn display(&mut self, c: Value) {
            let name = c["cmd"].as_str().unwrap_or("").to_string();
            let entry = match name.as_str() {
                "play" => json!(["web_play", c["id"]]),
                "pause" => json!(["web_pause"]),
                "resume" => json!(["web_resume"]),
                "stop" => json!(["web_stop"]),
                "seek" => json!(["web_seek", c["seconds"]]),
                "key" => json!(["web_set_key", c["semitones"]]),
                "tempo" => json!(["web_set_tempo", c["percent"]]),
                "reload" => json!(["reload"]),
                "signin" => json!(["sign_in"]),
                "quit" => json!(["close"]),
                other => json!(["display", other]),
            };
            self.log.push(entry);
        }
        fn start_display(&mut self) -> Result<bool, String> {
            self.log.push(json!(["display_start"]));
            Ok(self.chromium)
        }
        fn stop_display(&mut self) {}
        fn restart_error(&mut self) -> Option<String> {
            self.restart.clone()
        }
        fn reset_profile(&mut self) {
            self.log.push(json!(["reset_profile"]));
        }
        fn saved_login(&mut self) -> SavedLogin {
            self.log.push(json!(["read_saved_login"]));
            match self.saved.as_str() {
                "found" => SavedLogin::Found,
                "error" => SavedLogin::Error("RuntimeError".into()),
                _ => SavedLogin::None,
            }
        }
        fn login(&mut self) {
            self.log.push(json!(["login"]));
        }
        fn app(&mut self, e: &Value) {
            match e.get(0).and_then(|n| n.as_str()) {
                Some("finish_complete") => self.log.push(json!(["finish_complete"])),
                Some("page_message") => {
                    let m = &e[1];
                    let kind = m.get("type").and_then(|k| k.as_str()).unwrap_or("");
                    if kind == "helper" && m.get("state").and_then(|x| x.as_str()) == Some("no_frames") {
                        self.log.push(json!(["log", "NO PICTURE FRAMES from the graphics chip 8 s after the picture was shown"]));
                        self.log.push(json!(["notify", "KaraFun: the graphics chip is giving no picture. Send the log (see the KaraFun GPU test).", "warning"]));
                    }
                    if kind == "log" || kind == "ready" {
                        let text = m.get("message").and_then(|t| t.as_str()).filter(|t| !t.is_empty()).unwrap_or(kind);
                        self.log.push(json!(["log", format!("page: {text}")]));
                    }
                }
                _ => self.log.push(e.clone()),
            }
        }
    }

    pub fn handle(req: &Value) -> Value {
        let io = Rec { log: Vec::new(), chromium: req.get("chromium").and_then(|x| x.as_bool()).unwrap_or(true), saved: req.get("saved").and_then(|x| x.as_str()).unwrap_or("none").to_string(), restart: None };
        let has_credentials = req.get("has_credentials").and_then(|x| x.as_bool()).unwrap_or(true);
        let mut d = Driver::new(io, has_credentials);
        let mut trace = Vec::new();
        for e in req.get("events").and_then(|x| x.as_array()).unwrap_or(&vec![]) {
            let t = e.get("t").and_then(|x| x.as_f64()).unwrap_or(0.0);
            let mark = d.io.log.len();
            // timers that fell due before this event fire at their own due times (their effects belong to this event's trace)
            while let Some(due) = d.next_deadline() {
                if due > t {
                    break;
                }
                d.tick(due);
            }
            d.entry_current = e.get("entry_current").and_then(|x| x.as_bool()).unwrap_or(true);
            d.active_same = e.get("active_same").and_then(|x| x.as_bool()).unwrap_or(true);
            d.io.restart = e.get("restart_error").and_then(|x| x.as_str()).map(|x| x.to_string());
            let mut ret = None;
            match e.get("ev").and_then(|x| x.as_str()).unwrap_or("") {
                "start" => {
                    let req = StartRequest {
                        song_id: e.get("song_id").and_then(|x| x.as_i64()),
                        enabled: e.get("enabled").and_then(|x| x.as_bool()).unwrap_or(true),
                        key: e.get("key").and_then(|x| x.as_i64()),
                        tempo: e.get("tempo").filter(|x| !x.is_null()).cloned(),
                        title: e.get("title").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                        play: json!({}),
                    };
                    ret = Some(d.start_song(&req, t));
                }
                "msg" => {
                    if d.service_has_web() {
                        d.on_display_message(e.get("message").unwrap_or(&Value::Null), t)
                    }
                }
                "tick" => {}
                "toggle_pause" => ret = Some(d.toggle_pause(t)),
                "seek" => ret = Some(d.seek_to(e.get("target").and_then(|x| x.as_f64()).unwrap_or(0.0), t)),
                "key_tempo" => {
                    d.apply_key_tempo(e.get("key").and_then(|x| x.as_i64()).unwrap_or(0), e.get("tempo").and_then(|x| x.as_i64()).unwrap_or(100), t); // the Python answers (key, tempo) always
                }
                "finish" => ret = Some(d.on_finish(t)),
                other => return json!({"declined": format!("unknown event {other}")}),
            }
            if let Some(r) = ret {
                d.io.log.push(json!(["returns", r]));
            }
            trace.push(Value::Array(d.io.log[mark..].to_vec()));
        }
        json!({"trace": trace, "service": d.service_state()})
    }
}
