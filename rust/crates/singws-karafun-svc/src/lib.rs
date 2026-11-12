//! W1: the set-up/sign-in state machine of `karafun_web.KaraFunWebService`, transcribed from the Python (the oracle).
//!
//! Pure: `Machine::step(event, now)` returns the ordered `Action`s the caller must perform (set a state, log, send the player a command, forward the page
//! message). The machine owns no clock (the caller passes `now`, seconds from a monotonic clock), no thread and no file. It never sees the saved
//! password: when the login should be tried it asks (`Action::ReadSavedLogin`) and is told only whether a login was found.
use serde_json::{json, Value};

pub mod browser;
pub mod credentials;
pub mod host;

/// Seconds after the post-login reload during which the old page's statuses are ignored (`RELOAD_SETTLE_S`).
pub const RELOAD_SETTLE_S: f64 = 8.0;
/// Statuses (twice a second) before the saved login / browser sign-in is acted on (`SIGN_IN_AFTER_STATUSES`).
pub const SIGN_IN_AFTER_STATUSES: u64 = 60;

#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    /// `_set(state, detail)`
    Set(String, String),
    Log(String),
    /// `web.command("reload")`
    WebReload,
    /// `web.login(email, password)` with the saved login the caller just read
    WebLogin,
    /// `web.sign_in()`: the browser "Add a TV" page
    WebSignIn,
    /// `web.close()` (from `stop()`)
    WebClose,
    /// the caller must read the saved login and answer with `Event::SavedLogin`
    ReadSavedLogin,
    /// `on_message(message)`: pass the page's message on
    Forward,
}

#[derive(Debug, Clone, PartialEq)]
pub enum SavedLogin {
    Found,
    None,
    /// the credentials callable raised; the exception's type name is logged
    Error(String),
}

pub enum Event<'a> {
    /// `start()`; `alive` = the set-up thread is still running (then nothing happens)
    Start { alive: bool },
    /// the set-up thread begins (`_run`)
    RunBegin,
    Progress { done: i64, total: i64 },
    BrowserFound,
    /// `self.web = web` (the helper object exists; `web.start()` comes next)
    WebMade,
    SetupFailed { error: &'a str },
    Stop,
    Message(&'a Value),
    SavedLogin(SavedLogin),
}

#[derive(Debug, Clone)]
pub struct Machine {
    pub has_credentials: bool,
    pub state: String,
    pub has_web: bool,
    login_tried: bool,
    reload_after_login: bool,
    reload_at: f64,
    signed_out_seen: u64,
    sign_in_opened: bool,
    ready_told: bool,
    awaiting_login: bool,
}

/// Python truthiness of a JSON value.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Python `str(v)` for the plan name; only the shapes whose spelling is certain (the rest is declined).
fn py_str(v: &Value) -> Option<String> {
    match v {
        Value::String(s) => Some(s.clone()),
        Value::Bool(b) => Some(if *b { "True" } else { "False" }.to_string()),
        Value::Number(n) if n.is_i64() || n.is_u64() => Some(n.to_string()),
        _ => None,
    }
}

fn set(s: &str, d: &str) -> Action {
    Action::Set(s.to_string(), d.to_string())
}

impl Machine {
    pub fn new(has_credentials: bool) -> Self {
        Machine {
            has_credentials,
            state: "idle".into(),
            has_web: false,
            login_tried: false,
            reload_after_login: false,
            reload_at: 0.0,
            signed_out_seen: 0,
            sign_in_opened: false,
            ready_told: false,
            awaiting_login: false,
        }
    }

    /// `Err` = a case the machine does not model (a plan name whose Python spelling is uncertain); the caller must then leave the decision to Python.
    pub fn step(&mut self, ev: Event, now: f64) -> Result<Vec<Action>, String> {
        let mut out = Vec::new();
        match ev {
            Event::Start { alive } => {
                if !alive {
                    self.signed_out_seen = 0;
                    self.sign_in_opened = false;
                    self.login_tried = false;
                    self.reload_after_login = false;
                    self.ready_told = false;
                }
            }
            Event::RunBegin => self.put(&mut out, "preparing", "Looking for a browser"),
            Event::Progress { done, total } => {
                if done < 0 || total < 0 {
                    return Err("negative progress".into());
                }
                let percent = if total != 0 { format!(" ({}%)", done.saturating_mul(100) / total) } else { String::new() };
                self.put(&mut out, "preparing", &format!("Downloading the browser{percent}"));
            }
            Event::BrowserFound => self.put(&mut out, "starting", "Starting KaraFun's player"),
            Event::WebMade => self.has_web = true,
            Event::SetupFailed { error } => {
                out.push(Action::Log(format!("set-up failed: {error}")));
                self.put(&mut out, "error", error);
            }
            Event::Stop => {
                if self.has_web {
                    out.push(Action::WebClose);
                }
                self.has_web = false;
                self.state = "idle".into();
            }
            Event::Message(m) => self.message(m, now, &mut out)?,
            Event::SavedLogin(r) => {
                if self.awaiting_login {
                    self.awaiting_login = false;
                    match r {
                        SavedLogin::Error(name) => {
                            out.push(Action::Log(format!("saved login unreadable: {name}")));
                            self.open_sign_in_page(&mut out);
                        }
                        SavedLogin::None => self.open_sign_in_page(&mut out),
                        SavedLogin::Found => {
                            out.push(Action::Log("signing in with the saved KaraFun login".into()));
                            out.push(Action::WebLogin);
                        }
                    }
                    self.finish_signed_out(&mut out);
                }
            }
        }
        Ok(out)
    }

    fn put(&mut self, out: &mut Vec<Action>, s: &str, d: &str) {
        self.state = s.to_string();
        out.push(set(s, d));
    }

    fn open_sign_in_page(&mut self, out: &mut Vec<Action>) {
        if self.sign_in_opened || !self.has_web {
            return;
        }
        self.sign_in_opened = true;
        self.put(out, "sign_in", "Sign in to KaraFun in your browser, then come back");
        out.push(Action::WebSignIn);
    }

    /// What follows the saved-login decision inside `_message`: only the forward (the `elif` after it cannot run once the `if` was entered).
    fn finish_signed_out(&mut self, out: &mut Vec<Action>) {
        out.push(Action::Forward);
    }

    fn message(&mut self, m: &Value, now: f64, out: &mut Vec<Action>) -> Result<(), String> {
        let kind = m.get("type");
        let is = |v: Option<&Value>, s: &str| v.and_then(|x| x.as_str()) == Some(s);
        if is(kind, "status") {
            let signed_in = m.get("signedIn").map(truthy).unwrap_or(false);
            if signed_in && self.login_tried && !self.reload_after_login && self.has_web {
                self.reload_after_login = true;
                self.reload_at = now;
                self.ready_told = false;
                out.push(Action::Log("signed in with the saved login; reloading the player once".into()));
                self.put(out, "starting", "Starting KaraFun's player");
                out.push(Action::WebReload);
                self.signed_out_seen = 0;
            } else if self.reload_after_login && now - self.reload_at < RELOAD_SETTLE_S {
                // statuses from the page that is being reloaded say nothing yet
            } else if signed_in {
                self.signed_out_seen = 0;
                if !self.ready_told || self.state != "ready" {
                    self.ready_told = true;
                    let detail = match m.get("plan") {
                        Some(p) if truthy(p) => format!("Signed in ({})", py_str(p).ok_or("plan spelling not modelled")?),
                        _ => "Signed in".to_string(),
                    };
                    self.put(out, "ready", &detail);
                }
            } else {
                self.signed_out_seen += 1;
                if self.signed_out_seen == 4 && self.has_web {
                    self.ready_told = false;
                    self.put(out, "sign_in", "Signing in to KaraFun");
                }
                if self.signed_out_seen == SIGN_IN_AFTER_STATUSES && self.has_web && !self.login_tried {
                    self.login_tried = true;
                    if self.has_credentials {
                        self.awaiting_login = true;
                        out.push(Action::ReadSavedLogin);
                        return Ok(()); // the Forward follows the answer
                    }
                    self.open_sign_in_page(out);
                } else if self.signed_out_seen == SIGN_IN_AFTER_STATUSES + 60 && self.has_web {
                    self.open_sign_in_page(out);
                }
            }
        } else if is(kind, "error") && is(m.get("message"), "KaraFun's web player stopped") {
            self.put(out, "error", "KaraFun's player stopped");
        } else if is(kind, "error") && is(m.get("message"), "Chrome stopped") && self.has_web && self.state != "idle" && self.state != "error" {
            self.put(out, "error", "The player's browser stopped");
        }
        out.push(Action::Forward);
        Ok(())
    }
}

fn action_json(a: &Action) -> Value {
    match a {
        Action::Set(s, d) => json!(["set", s, d]),
        Action::Log(t) => json!(["log", t]),
        Action::WebReload => json!(["reload"]),
        Action::WebLogin => json!(["login"]),
        Action::WebSignIn => json!(["sign_in"]),
        Action::WebClose => json!(["close"]),
        Action::ReadSavedLogin => json!(["read_saved_login"]),
        Action::Forward => json!(["forward"]),
    }
}

fn saved_from(v: &Value) -> SavedLogin {
    match v.get("result").and_then(|x| x.as_str()) {
        Some("found") => SavedLogin::Found,
        Some("error") => SavedLogin::Error(v.get("kind").and_then(|x| x.as_str()).unwrap_or("").to_string()),
        _ => SavedLogin::None,
    }
}

/// Replay request: `{"has_credentials": bool, "events": [{"ev": "...", "t": seconds, ...}]}` -> `{"trace": [[action...] per event], "state": ...}`.
/// A `message` event may carry `"saved": {"result": "found"|"none"|"error", "kind": TypeName}` which answers a `read_saved_login` request.
/// Answer is `{"declined": reason}` when a case is not modelled.
pub fn handle(req: &Value) -> Value {
    let mut m = Machine::new(req.get("has_credentials").and_then(|x| x.as_bool()).unwrap_or(false));
    let empty = vec![];
    let mut trace = Vec::new();
    for e in req.get("events").and_then(|x| x.as_array()).unwrap_or(&empty) {
        let now = e.get("t").and_then(|x| x.as_f64()).unwrap_or(0.0);
        let kind = e.get("ev").and_then(|x| x.as_str()).unwrap_or("");
        let ev = match kind {
            "start" => Event::Start { alive: e.get("alive").and_then(|x| x.as_bool()).unwrap_or(false) },
            "run_begin" => Event::RunBegin,
            "progress" => Event::Progress { done: e.get("done").and_then(|x| x.as_i64()).unwrap_or(0), total: e.get("total").and_then(|x| x.as_i64()).unwrap_or(0) },
            "browser_found" => Event::BrowserFound,
            "web_made" => Event::WebMade,
            "setup_failed" => Event::SetupFailed { error: e.get("error").and_then(|x| x.as_str()).unwrap_or("") },
            "stop" => Event::Stop,
            "message" => match e.get("message") {
                Some(v) => Event::Message(v),
                None => return json!({"declined": "message missing"}),
            },
            other => return json!({"declined": format!("unknown event {other}")}),
        };
        let mut acts = match m.step(ev, now) {
            Ok(a) => a,
            Err(r) => return json!({"declined": r}),
        };
        if acts.last() == Some(&Action::ReadSavedLogin) {
            let saved = e.get("saved").map(saved_from).unwrap_or(SavedLogin::None);
            match m.step(Event::SavedLogin(saved), now) {
                Ok(more) => acts.extend(more),
                Err(r) => return json!({"declined": r}),
            }
        }
        trace.push(Value::Array(acts.iter().map(action_json).collect()));
    }
    json!({"trace": trace, "state": m.state})
}
