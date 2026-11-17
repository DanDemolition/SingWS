//! W11: the REAL `Io` for the driver, and the loop that runs it: `singws-karafun-svc --drive --display PATH [--profile DIR] [--email EMAIL]`.
//!
//! The app talks to this process in JSON lines. stdin (from the app):
//!   {"op":"start_song","seq":n,"song_id":58970,"enabled":true,"key":0,"tempo":100,"title":"..","play":{"query":..,"queries":[..],"artist":..,"title":..}}
//!   {"op":"toggle_pause"|"finish","seq":n}   {"op":"seek","seq":n,"target":12.5}   {"op":"key_tempo","seq":n,"key":2,"tempo":105}
//!   {"op":"world","entry_current":bool,"active_same":bool}       what the app says about the current entry
//!   {"op":"display","command":{...}}                             anything else for the display program (window, output, audience, refresh, volume, signout, top, ...)
//!   {"op":"shutdown"}
//! stdout (to the app): {"ev":"effect","effect":[name, ...]} for what only the app can do, {"ev":"done","seq":n,"ok":bool} answering an op with a seq,
//! {"ev":"state","service":..,"host":[..,..]} when the sign-in state changes. Logs go to stderr. The saved login is read here (the Keychain, `/usr/bin/security`)
//! and goes straight to the display program: it never appears on either pipe to the app.
use crate::credentials;
use crate::driver::{Driver, Io, StartRequest};
use crate::SavedLogin;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, RecvTimeoutError, Sender};
use std::time::{Duration, Instant};

enum Msg {
    App(Value),
    AppClosed,
    Display(Value),
    DisplayGone(u64),
}

struct Real {
    display_path: String,
    profile: Option<String>,
    email: String,
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    generation: u64,
    tx: Sender<Msg>,
}

fn out(v: Value) {
    let mut o = std::io::stdout().lock();
    let _ = writeln!(o, "{v}");
    let _ = o.flush();
}

impl Real {
    fn spawn(&mut self) -> Result<(), String> {
        let mut cmd = Command::new(&self.display_path);
        if let Some(p) = &self.profile {
            cmd.args(["--profile", p]);
        }
        let mut child = cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().map_err(|e| format!("could not start the display program: {e}"))?;
        let stdout = child.stdout.take().ok_or("no display output")?;
        self.stdin = child.stdin.take();
        self.child = Some(child);
        self.generation += 1;
        let (tx, generation) = (self.tx.clone(), self.generation);
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Ok(v) = serde_json::from_str::<Value>(&line) {
                    if v.is_object() && tx.send(Msg::Display(v)).is_err() {
                        return;
                    }
                }
            }
            let _ = tx.send(Msg::DisplayGone(generation));
        });
        Ok(())
    }

    fn password(&self) -> Option<String> {
        credentials::tool::load(credentials::SERVICE, &self.email)
    }
}

impl Io for Real {
    fn display(&mut self, command: Value) {
        if let Some(stdin) = self.stdin.as_mut() {
            let _ = writeln!(stdin, "{command}");
            let _ = stdin.flush();
        }
    }
    fn start_display(&mut self) -> Result<bool, String> {
        let installed = Command::new(&self.display_path).arg("installed").status().map(|s| s.code() == Some(0)).unwrap_or(false);
        if !installed {
            return Ok(false);
        }
        if self.child.is_none() {
            self.spawn()?;
        }
        Ok(true)
    }
    fn stop_display(&mut self) {
        self.stdin = None;
        if let Some(mut c) = self.child.take() {
            self.generation += 1; // its reader's "gone" is then not an error
            let _ = c.kill();
            let _ = c.wait();
        }
    }
    fn reset_profile(&mut self) {
        // as the Python does: only one `<profile>.engine-crash-*` copy is ever kept, the saved profile is moved beside it
        let Some(p) = &self.profile else { return };
        let path = std::path::Path::new(p);
        let (Some(parent), Some(name)) = (path.parent(), path.file_name().and_then(|n| n.to_str())) else { return };
        if let Ok(entries) = std::fs::read_dir(parent) {
            for e in entries.flatten() {
                if e.file_name().to_string_lossy().starts_with(&format!("{name}.engine-crash-")) {
                    let _ = std::fs::remove_dir_all(e.path());
                }
            }
        }
        if path.exists() {
            let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
            let _ = std::fs::rename(path, parent.join(format!("{name}.engine-crash-{stamp}")));
        }
    }
    fn saved_login(&mut self) -> SavedLogin {
        match credentials::clean_email(&self.email) {
            None => SavedLogin::None,
            Some(_) => match self.password() {
                Some(_) => SavedLogin::Found,
                None => SavedLogin::None,
            },
        }
    }
    fn login(&mut self) {
        if let (Some(email), Some(password)) = (credentials::clean_email(&self.email), self.password()) {
            self.display(json!({"cmd": "login", "email": email, "password": password}));
        }
    }
    fn app(&mut self, effect: &Value) {
        out(json!({"ev": "effect", "effect": effect}));
    }
}

fn arg(name: &str) -> Option<String> {
    let args: Vec<String> = std::env::args().collect();
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned()
}

fn num(v: &Value, k: &str) -> Option<i64> {
    v.get(k).and_then(|x| x.as_i64())
}

/// Apply one request from the app; returns false to end the loop.
fn op(d: &mut Driver<Real>, v: &Value, now: f64) -> bool {
    let name = v.get("op").and_then(|x| x.as_str()).unwrap_or("");
    let seq = v.get("seq").cloned();
    let ok = match name {
        "start_song" => {
            let req = StartRequest {
                song_id: num(v, "song_id"),
                enabled: v.get("enabled").and_then(|x| x.as_bool()).unwrap_or(true),
                key: num(v, "key"),
                tempo: v.get("tempo").filter(|t| !t.is_null()).cloned(),
                title: v.get("title").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                play: v.get("play").cloned().unwrap_or(Value::Null),
            };
            Some(d.start_song(&req, now))
        }
        "toggle_pause" => Some(d.toggle_pause(now)),
        "finish" => Some(d.on_finish(now)),
        "seek" => Some(d.seek_to(v.get("target").and_then(|x| x.as_f64()).unwrap_or(0.0), now)),
        "key_tempo" => Some(d.apply_key_tempo(num(v, "key").unwrap_or(0), num(v, "tempo").unwrap_or(100), now)),
        "world" => {
            d.entry_current = v.get("entry_current").and_then(|x| x.as_bool()).unwrap_or(true);
            d.active_same = v.get("active_same").and_then(|x| x.as_bool()).unwrap_or(true);
            None
        }
        "display" => {
            if let Some(c) = v.get("command") {
                d.io.display(c.clone());
            }
            None
        }
        "retry_service" => {
            d.ensure_service(now);
            None
        }
        "shutdown" => {
            d.shutdown(now);
            return false;
        }
        _ => Some(false),
    };
    if let (Some(seq), Some(ok)) = (seq, ok) {
        out(json!({"ev": "done", "seq": seq, "ok": ok}));
    }
    true
}

pub fn run() -> i32 {
    let Some(display_path) = arg("--display") else {
        eprintln!("--drive needs --display PATH");
        return 2;
    };
    let (tx, rx) = channel::<Msg>();
    let io = Real { display_path, profile: arg("--profile"), email: arg("--email").unwrap_or_default(), child: None, stdin: None, generation: 0, tx: tx.clone() };
    let has_credentials = credentials::clean_email(&io.email).is_some();
    let mut d = Driver::new(io, has_credentials);
    let start = Instant::now();
    // the app's monotonic clock: every op carries its `t`, and the driver's time is that plus what has passed since (entry times the app stores are in its clock)
    let offset = std::cell::Cell::new(0.0f64);
    let now = || offset.get() + start.elapsed().as_secs_f64();
    let app_tx = tx.clone();
    std::thread::spawn(move || {
        for line in std::io::stdin().lock().lines().map_while(Result::ok) {
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                if app_tx.send(Msg::App(v)).is_err() {
                    return;
                }
            }
        }
        let _ = app_tx.send(Msg::AppClosed);
    });
    let mut last_state = (String::new(), String::new(), String::new());
    loop {
        let wait = d.next_deadline().map(|t| Duration::from_secs_f64((t - now()).clamp(0.0, 3600.0))).unwrap_or(Duration::from_secs(3600));
        match rx.recv_timeout(wait) {
            Ok(Msg::App(v)) => {
                if let Some(t) = v.get("t").and_then(|x| x.as_f64()) {
                    offset.set(t - start.elapsed().as_secs_f64());
                }
                if !op(&mut d, &v, now()) {
                    return 0;
                }
            }
            Ok(Msg::AppClosed) => {
                d.shutdown(now());
                return 0;
            }
            Ok(Msg::Display(v)) => {
                if v.get("type").and_then(|t| t.as_str()) == Some("status") {
                    out(json!({"ev": "status", "message": v})); // the app keeps the latest status and feeds its own log-only shadows from it
                }
                d.on_display_message(&v, now());
            }
            Ok(Msg::DisplayGone(g)) => {
                if g == d.io.generation {
                    d.io.child = None;
                    d.io.stdin = None;
                    d.on_display_message(&json!({"type": "error", "message": "KaraFun's web player stopped"}), now());
                }
            }
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => return 0,
        }
        d.tick(now());
        let (h0, h1) = d.host_state();
        let st = (d.service_state().to_string(), h0.to_string(), h1.to_string());
        if st != last_state {
            out(json!({"ev": "state", "service": st.0, "host": [st.1, st.2]}));
            last_state = st;
        }
    }
}
