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

    fn world(&self) -> World {
        World { service_present: true, service_state: self.svc.state.clone(), has_web: self.svc.has_web, entry_current: self.entry_current, active_same: self.active_same, restart_error: None }
    }

    // ---- what the app asks for

    /// A song is asked for. Returns whether it was accepted (`false`: not a KaraFun song / disabled).
    pub fn start_song(&mut self, req: &StartRequest, now: f64) -> bool {
        self.play = req.play.clone();
        self.returns = None;
        let w = self.world();
        let ev = HostEvent::Start { song_id: req.song_id, enabled: req.enabled, key: req.key, tempo: req.tempo.as_ref(), title: &req.title };
        self.host_step(ev, w, now);
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
            let Some(pos) = self.timers.iter().position(|t| t.0 <= now) else { return };
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
    }

    impl Io for Fake {
        fn display(&mut self, c: Value) { self.display.push(c); }
        fn start_display(&mut self) -> Result<bool, String> { self.started += 1; Ok(self.chromium_here) }
        fn stop_display(&mut self) { self.stopped += 1; }
        fn reset_profile(&mut self) {}
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
        d.on_display_message(&status(true, false, 10.0, 10.0), 15.0);
        d.tick(20.0);
        let n = names(&d.io.app);
        assert!(n.iter().any(|x| x == "finish_complete") || n.iter().any(|x| x == "entry_status"), "the song is finished for the app: {n:?}");
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
