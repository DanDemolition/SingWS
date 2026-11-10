//! (From SingWS 2's src/karafun_chrome.rs, by its author, with printing moved to stderr: stdout is this helper's protocol.)
//! KaraFun on the Mac (experiment): Google Chrome, run without a window
//! ("headless"), driven over its DevTools pipe. Apple's own web view can't
//! run KaraFun's player: KaraFun's song database needs the page to be
//! "cross-origin isolated", which KaraFun asks for in a way WebKit doesn't
//! support yet (Cross-Origin-Embedder-Policy: credentialless). Chrome does.
//!
//! Chrome reads DevTools commands from its file 3 and writes replies and
//! events to its file 4, each a JSON message ending in a NUL byte. This
//! attaches to Chrome's one page, sets it up (the page script's messages
//! come back through a binding, `singwsIpc`), and hands on the page's
//! messages and the picture frames Chrome streams while asked to.
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    fs::File,
    io::{BufRead, BufReader, Write},
    os::unix::{io::FromRawFd, process::CommandExt},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{atomic::{AtomicU64, Ordering}, Arc, Mutex},
    time::{Duration, Instant},
};

/// Where Google Chrome is, if it's installed.
pub fn find() -> Option<PathBuf> {
    let app = "Google Chrome.app/Contents/MacOS/Google Chrome";
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    [PathBuf::from("/Applications").join(app), home.join("Applications").join(app)].into_iter().find(|path| path.is_file())
}

pub struct Chrome {
    child: Child,
    link: Arc<Link>,
}

/// The way in to Chrome, shared with the thread reading what it says.
struct Link {
    to_chrome: Mutex<File>,
    next_id: AtomicU64,
    /// The page's DevTools session (once attached), and commands for the
    /// page sent before then, sent once it is.
    session: Mutex<(Option<String>, Vec<(String, Value)>)>,
}

/// What the page set-up asks for, in order, once attached: the commands
/// given to `launch`, after these.
const GET_TARGETS: u64 = 1;
const ATTACH: u64 = 2;
const VERSION: u64 = 3;

impl Chrome {
    /// Start Chrome (`binary`) with its own profile folder (`profile`, which
    /// keeps the sign-in), open `url` once set up with `scripts` run first on
    /// every page, and hand on the page's messages (`on_message`: what its
    /// script posts) and picture frames (`on_frame`: a JPEG, base64).
    pub fn launch(binary: &Path, profile: &Path, url: &str, scripts: Vec<String>,
        on_message: impl Fn(Value) + Send + 'static, on_frame: impl Fn(String) + Send + 'static) -> std::io::Result<Self> {
        // Two pipes: commands to Chrome's file 3, its replies from its file 4.
        let (to_read, to_write) = pipe()?;
        let (from_read, from_write) = pipe()?;
        let mut command = Command::new(binary);
        command.args([
            "--headless=new", "--remote-debugging-pipe", "--no-first-run", "--no-default-browser-check",
            "--autoplay-policy=no-user-gesture-required", "--hide-scrollbars", "--window-size=1920,1080",
            // Never slowed down, though it has no window anyone sees.
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
        ]).arg(format!("--user-data-dir={}", profile.display())).arg("about:blank")
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        // SAFETY: between fork and exec, only dup2/fcntl (async-signal-safe):
        // the pipe ends become files 3 and 4 (moved high first, so neither
        // overwrites the other).
        unsafe {
            command.pre_exec(move || {
                let high_in = libc::fcntl(to_read, libc::F_DUPFD, 10);
                let high_out = libc::fcntl(from_write, libc::F_DUPFD, 10);
                if high_in < 0 || high_out < 0 || libc::dup2(high_in, 3) < 0 || libc::dup2(high_out, 4) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let spawned = command.spawn();
        // SAFETY: closes this process's copies of the ends Chrome keeps.
        unsafe { libc::close(to_read); libc::close(from_write); }
        let child = spawned?;
        // SAFETY: each fd is this process's own pipe end, owned by its File from here.
        let (to_chrome, from_chrome) = unsafe { (File::from_raw_fd(to_write), File::from_raw_fd(from_read)) };
        let link = Arc::new(Link { to_chrome: Mutex::new(to_chrome), next_id: AtomicU64::new(100), session: Mutex::new((None, Vec::new())) });
        link.write(GET_TARGETS, "Target.getTargets", json!({}), None);
        link.write(4, "Target.setDiscoverTargets", json!({"discover": true}), None);
        let reader = link.clone();
        let url = url.to_owned();
        std::thread::Builder::new().name("karafun-chrome".into())
            .spawn(move || reader.read(from_chrome, url, scripts, on_message, on_frame))?;
        Ok(Self { child, link })
    }

    /// A command for the page (sent once the page is set up, if it isn't yet).
    pub fn send(&self, method: &str, params: Value) { self.link.send(method, params); }

    /// Run `script` in the page (no answer).
    pub fn eval(&self, script: &str) { self.send("Runtime.evaluate", json!({"expression": script})); }
}

impl Drop for Chrome {
    fn drop(&mut self) {
        self.link.write(5, "Browser.close", json!({}), None);
        // Let Chrome quit by itself first: it saves the sign-in cookies on the way out.
        let t0 = Instant::now();
        while t0.elapsed() < Duration::from_secs(5) {
            if matches!(self.child.try_wait(), Ok(Some(_))) { return; }
            std::thread::sleep(Duration::from_millis(100));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Link {
    fn write(&self, id: u64, method: &str, params: Value, session: Option<&str>) {
        let mut message = json!({"id": id, "method": method, "params": params});
        if let Some(session) = session { message["sessionId"] = session.into(); }
        let mut bytes = message.to_string().into_bytes();
        bytes.push(0);
        let _ = self.to_chrome.lock().unwrap().write_all(&bytes);
    }

    fn send(&self, method: &str, params: Value) {
        let mut session = self.session.lock().unwrap();
        match &session.0 {
            Some(id) => { let id = id.clone(); drop(session); self.write(self.next_id.fetch_add(1, Ordering::Relaxed), method, params, Some(&id)); }
            None => session.1.push((method.to_owned(), params)),
        }
    }

    /// Read what Chrome says, until it quits: set the page up, then hand on
    /// its messages and frames.
    fn read(&self, from_chrome: File, url: String, scripts: Vec<String>, on_message: impl Fn(Value), on_frame: impl Fn(String)) {
        let mut reader = BufReader::new(from_chrome);
        let mut page: Option<String> = None;
        // The page's session, once attached (commands go to it once it's set up).
        let mut attached: Option<String> = None;
        // Addresses opened in the browser, and when (a pop-up can be told of
        // more than once: opened once each).
        let mut opened: HashMap<String, Instant> = HashMap::new();
        let mut open = |address: &str| {
            opened.retain(|_, at| at.elapsed() < Duration::from_secs(10));
            if address.starts_with("https://") && !opened.contains_key(address) {
                opened.insert(address.to_owned(), Instant::now());
                eprintln!("KaraFun: opening in the browser: {address}");
                let _ = Command::new("/usr/bin/open").arg(address).status();
            }
        };
        let mut line = Vec::new();
        loop {
            line.clear();
            match reader.read_until(0, &mut line) { Ok(0) | Err(_) => break, Ok(_) => {} }
            if line.last() == Some(&0) { line.pop(); }
            let Ok(message) = serde_json::from_slice::<Value>(&line) else { continue };
            match (message["id"].as_u64(), message["method"].as_str()) {
                (Some(GET_TARGETS), _) => {
                    page = message["result"]["targetInfos"].as_array().into_iter().flatten()
                        .find(|t| t["type"] == "page").and_then(|t| t["targetId"].as_str()).map(str::to_owned);
                    match &page {
                        Some(page) => self.write(ATTACH, "Target.attachToTarget", json!({"targetId": page, "flatten": true}), None),
                        None => eprintln!("KaraFun: Chrome has no page"),
                    }
                }
                (Some(ATTACH), _) => {
                    let Some(session) = message["result"]["sessionId"].as_str() else { eprintln!("KaraFun: Chrome's page couldn't be reached"); continue };
                    attached = Some(session.to_owned());
                    // Chrome's own name, without "Headless" (some sites turn that away).
                    self.write(VERSION, "Browser.getVersion", json!({}), None);
                }
                (Some(VERSION), _) => {
                    let agent = message["result"]["userAgent"].as_str().unwrap_or("").replace("HeadlessChrome", "Chrome");
                    let Some(session) = attached.clone() else { continue };
                    let mut setup = vec![
                        ("Emulation.setUserAgentOverride", json!({"userAgent": agent})),
                        ("Runtime.enable", json!({})), ("Page.enable", json!({})),
                        ("Runtime.addBinding", json!({"name": "singwsIpc"})),
                        // The page script posts with window.ipc.postMessage (as in wry's web views).
                        ("Page.addScriptToEvaluateOnNewDocument", json!({"source": "window.ipc = { postMessage: m => window.singwsIpc(m) };"})),
                    ];
                    for script in &scripts { setup.push(("Page.addScriptToEvaluateOnNewDocument", json!({"source": script}))); }
                    setup.push(("Page.navigate", json!({"url": url})));
                    for (method, params) in setup { self.write(self.next_id.fetch_add(1, Ordering::Relaxed), method, params, Some(&session)); }
                    // Then whatever was asked for meanwhile, and from now on, straight to the page.
                    let queued = {
                        let mut state = self.session.lock().unwrap();
                        state.0 = Some(session.clone());
                        std::mem::take(&mut state.1)
                    };
                    for (method, params) in queued { self.write(self.next_id.fetch_add(1, Ordering::Relaxed), &method, params, Some(&session)); }
                }
                (_, Some("Runtime.bindingCalled")) => {
                    if let Ok(payload) = serde_json::from_str::<Value>(message["params"]["payload"].as_str().unwrap_or("")) { on_message(payload); }
                }
                (_, Some("Page.screencastFrame")) => {
                    let params = &message["params"];
                    // Acknowledged at once, so Chrome sends the next.
                    if let Some(session) = message["sessionId"].as_str() {
                        self.write(self.next_id.fetch_add(1, Ordering::Relaxed), "Page.screencastFrameAck", json!({"sessionId": params["sessionId"]}), Some(session));
                    }
                    if let Some(data) = params["data"].as_str() { on_frame(data.to_owned()); }
                }
                // A pop-up from the page (KaraFun's sign-in: its "connect" page,
                // which links this player to the account from any browser): it
                // opens in the owner's own browser instead. Chrome blocks it (no
                // click started it) but says where it was going.
                (_, Some("Page.windowOpen")) => open(message["params"]["url"].as_str().unwrap_or("")),
                // One that did open: in the browser too, and closed here.
                (_, Some("Target.targetCreated" | "Target.targetInfoChanged")) => {
                    let info = &message["params"]["targetInfo"];
                    let from_page = page.is_some() && info["openerId"].as_str() == page.as_deref() && info["type"] == "page";
                    let address = info["url"].as_str().unwrap_or("");
                    if from_page && address.starts_with("https://") {
                        open(address);
                        self.write(self.next_id.fetch_add(1, Ordering::Relaxed), "Target.closeTarget", json!({"targetId": info["targetId"]}), None);
                    }
                }
                _ => {}
            }
        }
        eprintln!("KaraFun: Chrome stopped");
        on_message(json!({"type": "error", "message": "Chrome stopped"}));
    }
}

/// A pipe whose ends aren't passed on to other programs SingWS starts (mpv).
fn pipe() -> std::io::Result<(i32, i32)> {
    let mut fds = [0; 2];
    // SAFETY: fills two new file descriptors, marked close-on-exec.
    unsafe {
        if libc::pipe(fds.as_mut_ptr()) != 0 { return Err(std::io::Error::last_os_error()); }
        for fd in fds { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC); }
    }
    Ok((fds[0], fds[1]))
}
