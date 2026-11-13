//! KaraFun on the Mac: KaraFun's web player runs in Chromium inside SingWS
//! itself (CEF, the Chromium Embedded Framework), drawn off-screen on the
//! graphics chip. Each frame comes as a GPU picture (an IOSurface) that the
//! cover, the preview and KaraFun's own window all show as it is: the same
//! picture in each, with no copy and no JPEG in between. Apple's own web view
//! can't run KaraFun's player: its song database needs the page to be
//! "cross-origin isolated", which KaraFun asks for in a way WebKit doesn't
//! support yet (Cross-Origin-Embedder-Policy: credentialless). Chromium does.
//!
//! The page is driven as before (karafun_mac.rs), over Chromium's DevTools
//! protocol: the page script's messages come back through a binding
//! (`singwsIpc`). A few commands the screencast used are taken here instead:
//! the page size, and starting and stopping the frames.
//!
//! Chromium starts helper programs (for the page, the graphics chip, the
//! network): those are SingWS itself, started with Chromium's `--type=`
//! (see `run_helper`). Chromium's framework (about 135 MB to download, 340 MB
//! unpacked) is got once, the first time, into SingWS's own folder.
//!
//! Everything here runs on the main thread: Chromium's work is done there
//! (`pump`), between the app's own.
use cef::{rc::Rc as _, *};
use serde_json::{json, Value};
use std::{
    cell::{Cell, RefCell},
    ffi::c_void,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

/// The largest page Chromium is asked to draw: 720p, so older Macs keep up (the window scales the picture up to the TV). One pixel per point (see
/// `screen_info`), so a Retina screen does not double it.
pub const MAX_PAGE: (i32, i32) = (1280, 720);

/// The most frames a second Chromium is asked to draw: 60, even on a 120 Hz screen, so older Macs keep up.
pub const MAX_FPS: i32 = 60;

/// A requested page size, kept inside `MAX_PAGE` with its shape (never below 1x1).
pub fn limit_page(width: i64, height: i64) -> (i32, i32) {
    let (w, h) = (width.max(1) as f64, height.max(1) as f64);
    let scale = (f64::from(MAX_PAGE.0) / w).min(f64::from(MAX_PAGE.1) / h).min(1.0);
    (((w * scale).round() as i32).max(1), ((h * scale).round() as i32).max(1))
}

/// The Chromium build these bindings were made for (the framework got must match it).
fn version() -> &'static str {
    std::str::from_utf8(cef::sys::CEF_VERSION).unwrap_or("").trim_end_matches('\0')
}

const PLATFORM: &str = if cfg!(target_arch = "x86_64") { "macosx64" } else { "macosarm64" };
const FRAMEWORK: &str = "Chromium Embedded Framework.framework";
/// The switch that tells the helpers where the framework is.
const FRAMEWORK_SWITCH: &str = "singws-cef-framework";

/// SingWS's Chromium folder: the framework (one folder per version) and the
/// profile (KaraFun's sign-in, its song database).
pub fn folder() -> PathBuf {
    let home = std::env::var_os("SINGWS_HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join("SingWS"));
    home.join("KaraFun-Chromium")
}

fn framework_dir() -> PathBuf { folder().join(version()) }

/// Whether Chromium's framework is here (got before).
pub fn installed() -> bool { framework_dir().join(FRAMEWORK).join("Chromium Embedded Framework").is_file() }

/// Get Chromium's framework for this Mac's chip from CEF's own builds,
/// checked against its published checksum, saying how it's going
/// (`progress`). Unpacked beside, then moved in place, so a half-done
/// download is never taken for it. Older versions are removed.
pub fn get(progress: impl Fn(String)) -> Result<(), String> {
    let folder = folder();
    std::fs::create_dir_all(&folder).map_err(|e| format!("couldn't make {} ({e})", folder.display()))?;
    let name = format!("cef_binary_{}_{PLATFORM}_minimal", version());
    let url = format!("https://cef-builds.spotifycdn.com/{}.tar.bz2", name.replace('+', "%2B"));
    progress("Getting KaraFun's player (Chromium, once)…".into());
    let expected = ureq::get(&format!("{url}.sha1")).call().map_err(|e| format!("its checksum didn't come ({e})"))?
        .body_mut().read_to_string().map_err(|e| format!("its checksum was unreadable ({e})"))?.trim().to_owned();
    if expected.len() != 40 { return Err("its checksum was unreadable".into()); }

    let archive = folder.join("download.tar.bz2");
    let mut response = ureq::get(&url).call().map_err(|e| format!("the download didn't start ({e})"))?;
    let total: u64 = response.headers().get("content-length").and_then(|v| v.to_str().ok()?.parse().ok()).unwrap_or(0);
    let mut reader = response.body_mut().with_config().limit(2 * 1024 * 1024 * 1024).reader();
    let mut file = std::fs::File::create(&archive).map_err(|e| format!("couldn't save the download ({e})"))?;
    let (mut got, mut said, mut buffer) = (0u64, u64::MAX, vec![0u8; 256 * 1024]);
    loop {
        let read = std::io::Read::read(&mut reader, &mut buffer).map_err(|e| format!("the download stopped ({e})"))?;
        if read == 0 { break; }
        std::io::Write::write_all(&mut file, &buffer[..read]).map_err(|e| format!("couldn't save the download ({e})"))?;
        got += read as u64;
        let mb = got / (1024 * 1024);
        if mb != said && mb % 5 == 0 {
            said = mb;
            progress(if total > 0 { format!("Getting KaraFun's player (Chromium, once): {mb} of {} MB…", total / (1024 * 1024)) }
                else { format!("Getting KaraFun's player (Chromium, once): {mb} MB…") });
        }
    }
    drop(file);
    let fail = |why: String| { let _ = std::fs::remove_file(&archive); Err(why) };
    if total > 0 && got != total { return fail("the download was cut short".into()); }

    progress("Getting KaraFun's player: checking it…".into());
    let sum = std::process::Command::new("/usr/bin/shasum").args(["-a", "1"]).arg(&archive).output()
        .map_err(|e| format!("couldn't check it ({e})"))?;
    let sum = String::from_utf8_lossy(&sum.stdout).split_whitespace().next().unwrap_or("").to_owned();
    if !sum.eq_ignore_ascii_case(&expected) { return fail("the download didn't match its checksum".into()); }

    progress("Getting KaraFun's player: unpacking…".into());
    let unpacking = folder.join("unpacking");
    let _ = std::fs::remove_dir_all(&unpacking);
    std::fs::create_dir_all(&unpacking).map_err(|e| format!("couldn't unpack it ({e})"))?;
    let inside = format!("{name}/Release/{FRAMEWORK}");
    let unpacked = std::process::Command::new("/usr/bin/tar").arg("-xjf").arg(&archive).arg("-C").arg(&unpacking).arg(&inside).status();
    let _ = std::fs::remove_file(&archive);
    if !unpacked.is_ok_and(|status| status.success()) { let _ = std::fs::remove_dir_all(&unpacking); return Err("couldn't unpack it".into()); }
    let place = framework_dir();
    let _ = std::fs::remove_dir_all(&place);
    std::fs::create_dir_all(&place).map_err(|e| format!("couldn't put it in place ({e})"))?;
    std::fs::rename(unpacking.join(&inside), place.join(FRAMEWORK)).map_err(|e| format!("couldn't put it in place ({e})"))?;
    let _ = std::fs::remove_dir_all(&unpacking);
    // Frameworks of other versions (from before an update) aren't used again.
    if let Ok(entries) = std::fs::read_dir(&folder) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.join(FRAMEWORK).is_dir() && path != place { let _ = std::fs::remove_dir_all(&path); }
        }
    }
    if !installed() { return Err("the download had no Chromium in it".into()); }
    eprintln!("KaraFun: got Chromium {}", version());
    Ok(())
}

/// Load Chromium's framework now, if it's here: call first thing in `main`,
/// before SingWS starts any other thread. Loading it puts Chromium's own
/// memory manager in place for the whole app, which crashes if another
/// thread is getting memory at that moment.
pub fn preload() {
    if installed() && !load(&framework_dir()) { eprintln!("KaraFun: Chromium's framework couldn't be loaded"); }
}

static LOADED: std::sync::OnceLock<bool> = std::sync::OnceLock::new();

/// Load Chromium's framework from `dir` (once per process).
fn load(dir: &Path) -> bool {
    *LOADED.get_or_init(|| load_now(dir))
}

fn load_now(dir: &Path) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let Ok(path) = std::ffi::CString::new(dir.join(FRAMEWORK).join("Chromium Embedded Framework").as_os_str().as_bytes()) else { return false };
    // SAFETY: `path` is a NUL-terminated path, alive for the call.
    if cef::load_library(Some(unsafe { &*path.as_ptr() })) != 1 { return false; }
    // Says which version of Chromium's interface these bindings speak.
    let _ = cef::api_hash(cef::sys::CEF_API_VERSION_LAST, 0);
    true
}

/// If SingWS was started as one of Chromium's helpers, run as that and
/// exit (call first thing in `main`).
pub fn run_helper() {
    let args: Vec<String> = std::env::args().collect();
    if !args.iter().any(|arg| arg.starts_with("--type=")) { return; }
    // A helper is SingWS's own program, so macOS would give it a Dock icon
    // of its own; say first thing it's a background process.
    if let Some(mtm) = objc2::MainThreadMarker::new() {
        use objc2_app_kit::{NSApplication, NSApplicationActivationPolicy};
        NSApplication::sharedApplication(mtm).setActivationPolicy(NSApplicationActivationPolicy::Prohibited);
    }
    let prefix = format!("--{FRAMEWORK_SWITCH}=");
    let Some(dir) = args.iter().find_map(|arg| arg.strip_prefix(&prefix)) else { std::process::exit(1) };
    if !load(Path::new(dir)) { std::process::exit(1); }
    let main_args = cef::args::Args::new();
    let code = cef::execute_process(Some(main_args.as_main_args()), None, std::ptr::null_mut());
    std::process::exit(code.max(0));
}

/// What the page's messages and frames go to, and the page itself once it's made.
struct State {
    on_message: Box<dyn Fn(Value)>,
    on_frame: Box<dyn Fn(&Picture)>,
    url: String,
    scripts: Vec<String>,
    browser: Option<Browser>,
    /// Watches the page's DevTools events (kept for as long as the page).
    _observer: Option<cef::Registration>,
    /// Commands for the page sent before it was made, sent once it is.
    queued: Vec<(String, Value)>,
    next_id: i64,
    /// The page's size, in points.
    size: (i32, i32),
    /// Whether frames are wanted (the cover or KaraFun's window is up).
    streaming: bool,
    /// Addresses opened in the browser, and when (a pop-up can be told of
    /// more than once: opened once each).
    opened: Vec<(String, Instant)>,
    closed: bool,
    /// The command whose answer opens KaraFun's page.
    navigate_after: Option<i64>,
}

thread_local! {
    static STATE: RefCell<Option<State>> = const { RefCell::new(None) };
    /// Chromium is started (once per run: it can't be started again).
    static STARTED: Cell<bool> = const { Cell::new(false) };
    /// Chromium's work is being done now (it isn't asked to do it again inside).
    static PUMPING: Cell<bool> = const { Cell::new(false) };
    /// KaraFun's page load was tried again (once) after being cut off.
    static RETRIED: Cell<bool> = const { Cell::new(false) };
}

fn with_state<T>(work: impl FnOnce(&mut State) -> T) -> Option<T> {
    STATE.with(|state| state.borrow_mut().as_mut().map(work))
}

/// How evenly frames come while they're shown: for the log, to compare ways
/// of timing them.
#[derive(Default)]
struct Pacing {
    last: Option<Instant>, gaps: Vec<f64>,
    /// A song is playing: a long gap then is a freeze, and goes in the log.
    watching: bool,
    /// Since the last frame, the longest SingWS's main thread was away (busy
    /// with its own work) between Chromium's turns on it, and the longest
    /// one of Chromium's turns took (ms); when its last turn ended.
    away: f64, turn: f64, turn_ended: Option<Instant>,
}
static PACING: std::sync::Mutex<Pacing> = std::sync::Mutex::new(Pacing { last: None, gaps: Vec::new(), watching: false, away: 0.0, turn: 0.0, turn_ended: None });

fn frame_came() {
    let now = Instant::now();
    let mut pacing = PACING.lock().unwrap();
    if let Some(last) = pacing.last {
        let gap = now.duration_since(last).as_secs_f64() * 1000.0;
        // A pause (no song, nothing changing) isn't a late frame.
        if gap < 250.0 { pacing.gaps.push(gap); }
        // While a song plays, it's a freeze: which side held it up.
        else if pacing.watching && gap < 30_000.0 {
            eprintln!("KaraFun: picture froze for {gap:.0} ms; SingWS's main thread was busy elsewhere for up to {:.0} ms at a time, Chromium's own work on it took up to {:.0} ms",
                pacing.away, pacing.turn);
        }
    }
    pacing.last = Some(now);
    pacing.away = 0.0;
    pacing.turn = 0.0;
}

/// A song started (true) or ended: freezes are written in the log while one
/// plays, and the frames for each song when it ends.
pub fn watch(on: bool) {
    let mut state = PACING.lock().unwrap();
    if state.watching == on { return; }
    state.watching = on;
    state.last = None;
    drop(state);
    if !on { let summary = pacing(); if !summary.is_empty() { eprintln!("KaraFun: frames this song {summary}"); } }
}

/// Frames since last asked: how many a second, the usual gap between them,
/// how much the gaps wander, and how many were late (over 1.5 times the
/// usual gap). Empty if none came.
pub fn pacing() -> String {
    let gaps = std::mem::take(&mut PACING.lock().unwrap().gaps);
    if gaps.len() < 10 { return String::new(); }
    let total: f64 = gaps.iter().sum();
    let mut sorted = gaps.clone();
    sorted.sort_by(f64::total_cmp);
    let usual = sorted[sorted.len() / 2];
    let mean = total / gaps.len() as f64;
    let wander = (gaps.iter().map(|g| (g - mean).powi(2)).sum::<f64>() / gaps.len() as f64).sqrt();
    let late = gaps.iter().filter(|g| **g > usual * 1.5).count();
    format!("{:.1} fps, usual gap {usual:.1} ms, wander {wander:.1} ms, late {late} of {} ({:.1}%), worst {:.0} ms",
        gaps.len() as f64 * 1000.0 / total, gaps.len(), late as f64 * 100.0 / gaps.len() as f64, sorted[sorted.len() - 1])
}

/// A frame from Chromium: its picture on the graphics chip (an IOSurface,
/// good only while being handed on), and the part of it that's the page
/// (x, y, width, height, as fractions of the whole).
pub struct Picture { pub surface: *mut c_void, pub visible: (f64, f64, f64, f64) }

pub struct Chrome;

impl Chrome {
    /// Start Chromium (once got, see `get`) with its own profile folder
    /// (`profile`, which keeps the sign-in), open `url` once set up with
    /// `scripts` run first on every page, and hand on the page's messages
    /// (`on_message`: what its script posts) and frames (`on_frame`, while
    /// asked for with Page.startScreencast). On the main thread.
    pub fn launch(profile: &Path, url: &str, scripts: Vec<String>,
        on_message: impl Fn(Value) + 'static, on_frame: impl Fn(&Picture) + 'static) -> Result<Self, String> {
        if STARTED.get() { return Err("Chromium was already started".into()); }
        let dir = framework_dir();
        // Loaded at start (`preload`) only: it's not safe once other threads run.
        match LOADED.get() {
            Some(true) => {}
            Some(false) => return Err("Chromium's framework couldn't be loaded".into()),
            None => return Err("KaraFun's player is downloaded: restart SingWS 2 to start it".into()),
        }
        STARTED.set(true);
        STATE.with(|state| *state.borrow_mut() = Some(State {
            on_message: Box::new(on_message), on_frame: Box::new(on_frame), url: url.to_owned(), scripts, browser: None,
            _observer: None, queued: Vec::new(), next_id: 100, size: MAX_PAGE, streaming: false, opened: Vec::new(), closed: false, navigate_after: None,
        }));
        install_app_protocol();
        let _ = std::fs::create_dir_all(profile);
        let exe = std::env::current_exe().map_err(|e| format!("couldn't find SingWS itself ({e})"))?;
        // The app's hidden copy of SingWS for Chromium's helpers (made by
        // release.command, marked LSUIElement so macOS gives it no Dock icon),
        // or SingWS itself when run from a build folder.
        let helper = exe.parent().map(|macos| macos.join("../Frameworks/SingWS KaraFun Helper.app/Contents/MacOS/SingWS KaraFun Helper"));
        let exe = helper.filter(|path| path.is_file()).unwrap_or(exe);
        let settings = Settings {
            no_sandbox: 1,
            external_message_pump: 1,
            windowless_rendering_enabled: 1,
            framework_dir_path: dir.join(FRAMEWORK).to_string_lossy().as_ref().into(),
            browser_subprocess_path: exe.to_string_lossy().as_ref().into(),
            root_cache_path: profile.to_string_lossy().as_ref().into(),
            cache_path: profile.to_string_lossy().as_ref().into(),
            persist_session_cookies: 1,
            log_file: folder().join("chromium.log").to_string_lossy().as_ref().into(),
            log_severity: if trace() { cef::sys::cef_log_severity_t::LOGSEVERITY_INFO } else { cef::sys::cef_log_severity_t::LOGSEVERITY_ERROR }.into(),
            // SingWS's own Ctrl-C and crash handling stay its own.
            disable_signal_handlers: 1,
            background_color: 0xff00_0000,
            ..Default::default()
        };
        let main_args = cef::args::Args::new();
        if cef::initialize(Some(main_args.as_main_args()), Some(&settings), Some(&mut KaraFunApp::new()), std::ptr::null_mut()) != 1 {
            return Err(format!("Chromium didn't start (code {})", cef::get_exit_code()));
        }
        keep_pumping();
        Ok(Self)
    }

    /// A command for the page (sent once the page is made, if it isn't yet).
    pub fn send(&self, method: &str, params: Value) { send(method, params); }

    /// Run `script` in the page (no answer).
    pub fn eval(&self, script: &str) { self.send("Runtime.evaluate", json!({"expression": script})); }

    /// A way to send the page commands from elsewhere (AppKit's event handlers).
    pub fn commands(&self) -> Commands { Commands }
}

/// Sends the page commands (see `Chrome::send`), on the main thread.
#[derive(Clone)]
pub struct Commands;

impl Commands {
    pub fn send(&self, method: &str, params: Value) { send(method, params); }
}

impl Drop for Chrome {
    /// The cookies (KaraFun's sign-in) are saved, and the page closed, with
    /// a moment for Chromium to finish: the process ends soon after.
    fn drop(&mut self) {
        let Some(browser) = with_state(|state| state.browser.clone()).flatten() else { return };
        if let Some(host) = browser.host() {
            if let Some(manager) = host.request_context().and_then(|context| context.cookie_manager(None)) { manager.flush_store(None); }
            host.close_browser(1);
        }
        let until = Instant::now() + Duration::from_millis(1500);
        while Instant::now() < until && with_state(|state| !state.closed).unwrap_or(false) {
            pump();
            std::thread::sleep(Duration::from_millis(10));
        }
    }
}

/// A command for the page, on the main thread (queued until the page is made).
pub fn send(method: &str, params: Value) {
    /// What to do once STATE is let go (Chromium may call back into it at once).
    enum Then { Nothing, Resize(Browser), Frames(Browser, bool), Send(Browser, i64) }
    let then = with_state(|state| {
        let browser = state.browser.clone();
        // The commands the screencast used: done here.
        match method {
            "Emulation.setDeviceMetricsOverride" => {
                let size = limit_page(params["width"].as_i64().unwrap_or(i64::from(MAX_PAGE.0)), params["height"].as_i64().unwrap_or(i64::from(MAX_PAGE.1)));
                if state.size == size { return Then::Nothing; }
                state.size = size;
                browser.map_or(Then::Nothing, Then::Resize)
            }
            "Page.startScreencast" | "Page.stopScreencast" => {
                // Only a change counts (on again while on would restart the pace).
                let on = method == "Page.startScreencast";
                if state.streaming == on { Then::Nothing } else {
                    state.streaming = on;
                    browser.map_or(Then::Nothing, |b| Then::Frames(b, on))
                }
            }
            _ => match browser {
                None => { state.queued.push((method.to_owned(), params.clone())); Then::Nothing }
                Some(browser) => { state.next_id += 1; Then::Send(browser, state.next_id) }
            },
        }
    }).unwrap_or(Then::Nothing);
    match then {
        Then::Nothing => {}
        Then::Resize(browser) => if let Some(host) = browser.host() { host.was_resized(); },
        Then::Frames(browser, on) => if let Some(host) = browser.host() {
            // Drawn at the TV's pace while it's seen; hardly at all otherwise.
            host.set_windowless_frame_rate(if on { frame_rate() } else { 1 });
            if on { host.invalidate(cef::sys::cef_paint_element_type_t::PET_VIEW.into()); }
        },
        Then::Send(browser, id) => {
            let message = json!({"id": id, "method": method, "params": params}).to_string();
            if let Some(host) = browser.host() { host.send_dev_tools_message(Some(message.as_bytes())); }
        }
    }
}

/// The page is made: watch its DevTools events, set it up (its scripts,
/// the binding), then open the address and send what was asked for meanwhile.
fn page_made(browser: &Browser) {
    let Some(host) = browser.host() else { return };
    let registration = host.add_dev_tools_message_observer(Some(&mut KaraFunObserver::new()));
    host.set_focus(1);
    eprintln!("KaraFun: Chromium's page made");
    let streaming = with_state(|state| {
        state.browser = Some(browser.clone());
        state._observer = registration;
        state.streaming
    }).unwrap_or(false);
    host.set_windowless_frame_rate(if streaming { frame_rate() } else { 1 });
    host.was_resized();
    let setup = with_state(|state| {
        let mut setup = vec![
            ("Runtime.enable".to_owned(), json!({})), ("Page.enable".to_owned(), json!({})),
            ("Runtime.addBinding".to_owned(), json!({"name": "singwsIpc"})),
            // The page script posts with window.ipc.postMessage (as in wry's web views).
            ("Page.addScriptToEvaluateOnNewDocument".to_owned(), json!({"source": "window.ipc = { postMessage: m => window.singwsIpc(m) };"})),
        ];
        for script in &state.scripts { setup.push(("Page.addScriptToEvaluateOnNewDocument".to_owned(), json!({"source": script}))); }
        setup.extend(std::mem::take(&mut state.queued));
        setup
    }).unwrap_or_default();
    for (method, params) in setup { send(&method, params); }
    // KaraFun's page is opened once Chromium has all of that in place (its
    // answer to the last), so the scripts run on it from the start.
    with_state(|state| state.navigate_after = Some(state.next_id));
}

/// A DevTools event from the page: the page script's messages, and pop-ups
/// Chromium blocked (opened in the browser instead).
fn page_event(method: &str, params: &[u8]) {
    let Ok(params) = serde_json::from_slice::<Value>(params) else { return };
    match method {
        "Runtime.bindingCalled" => {
            if let Ok(payload) = serde_json::from_str::<Value>(params["payload"].as_str().unwrap_or("")) { tell(payload); }
        }
        "Page.windowOpen" => open(params["url"].as_str().unwrap_or("")),
        _ => {}
    }
}

/// Every DevTools message in the log (SINGWS_CEF_TRACE=1), to see what Chromium does.
fn trace() -> bool { std::env::var("SINGWS_CEF_TRACE").is_ok_and(|v| v == "1") }

/// Hand a message to the app (as the page's script would).
fn tell(message: Value) {
    // Taken out while it's called, so the app can send commands from it.
    let on_message = STATE.with(|state| state.borrow_mut().as_mut().map(|s| std::mem::replace(&mut s.on_message, Box::new(|_| {}))));
    let Some(on_message) = on_message else { return };
    on_message(message);
    with_state(|state| state.on_message = on_message);
}

/// A pop-up from the page (KaraFun's sign-in: its "connect" page, which
/// links this player to the account from any browser) opens in the owner's
/// own browser instead.
fn open(address: &str) {
    let fresh = with_state(|state| {
        state.opened.retain(|(_, at)| at.elapsed() < Duration::from_secs(10));
        let fresh = address.starts_with("https://") && !state.opened.iter().any(|(seen, _)| seen == address);
        if fresh { state.opened.push((address.to_owned(), Instant::now())); }
        fresh
    }).unwrap_or(false);
    if fresh {
        // Through macOS itself (NSWorkspace): with Chromium in SingWS, the
        // `open` program SingWS used before didn't bring the browser up.
        let opened = open_url(address);
        eprintln!("KaraFun: opening in the browser: {address}{}", if opened { "" } else { " (macOS turned it down; trying another way)" });
    }
}

/// Open `address` in the owner's default browser, through macOS.
fn open_url(address: &str) -> bool {
    use objc2::{msg_send, runtime::{AnyClass, AnyObject, Bool}};
    let (Some(urls), Some(workspace)) = (AnyClass::get(c"NSURL"), AnyClass::get(c"NSWorkspace")) else { return false };
    let text = objc2_foundation::NSString::from_str(address);
    // SAFETY: AppKit's own classes, as documented; on the main thread.
    unsafe {
        let url: *mut AnyObject = msg_send![urls, URLWithString: &*text];
        if url.is_null() { return false; }
        let shared: *mut AnyObject = msg_send![workspace, sharedWorkspace];
        if shared.is_null() { return false; }
        let opened: Bool = msg_send![shared, openURL: url];
        opened.as_bool()
    }
}

wrap_app! {
    struct KaraFunApp;
    impl App {
        fn on_before_command_line_processing(&self, _process_type: Option<&CefString>, command_line: Option<&mut CommandLine>) {
            let Some(command_line) = command_line else { return };
            for switch in [
                // Never slowed down, though no one sees it while no song is on.
                "disable-background-timer-throttling", "disable-renderer-backgrounding", "disable-backgrounding-occluded-windows",
                // Cookies kept without asking for the Mac's keychain (no "SingWS wants to use your confidential information").
                "use-mock-keychain",
                // The page may read output-device names (to play on the device SingWS is set to) without a permission prompt; no microphone is opened.
                "use-fake-ui-for-media-stream",
            ] { command_line.append_switch(Some(&switch.into())); }
            command_line.append_switch_with_value(Some(&"autoplay-policy".into()), Some(&"no-user-gesture-required".into()));
            // The Mac's media controls (the play/pause key, headphones or
            // AirPods taken out, Now Playing) never reach KaraFun's page: one
            // paused a song mid-show. SingWS alone pauses it.
            let name: CefString = "disable-features".into();
            let mut off = CefString::from(&command_line.switch_value(Some(&name))).to_string();
            if !off.is_empty() { off.push(','); }
            off.push_str("HardwareMediaKeyHandling");
            command_line.append_switch_with_value(Some(&name), Some(&off.as_str().into()));
            // Intel Macs with two graphics chips (Intel and AMD): drawn on the
            // AMD one, which runs the external ports (the TV). On the Intel
            // chip, each frame had to be copied across to the TV's chip.
            #[cfg(target_arch = "x86_64")]
            command_line.append_switch(Some(&"force_high_performance_gpu".into()));
        }
        fn browser_process_handler(&self) -> Option<BrowserProcessHandler> { Some(KaraFunProcess::new()) }
    }
}

wrap_browser_process_handler! {
    struct KaraFunProcess;
    impl BrowserProcessHandler {
        fn on_context_initialized(&self) {
            // Chromium times the page's frames itself (at `windowless_frame_rate`):
            // timed by SingWS instead, KaraFun's player never started.
            let window = WindowInfo { windowless_rendering_enabled: 1, shared_texture_enabled: 1, ..Default::default() };
            let settings = BrowserSettings { windowless_frame_rate: 60, background_color: 0xff00_0000, ..Default::default() };
            if cef::browser_host_create_browser(Some(&window), Some(&mut KaraFunClient::new()), Some(&"about:blank".into()), Some(&settings), None, None) != 1 {
                eprintln!("KaraFun: Chromium couldn't make the page");
            }
        }
        fn on_before_child_process_launch(&self, command_line: Option<&mut CommandLine>) {
            let Some(command_line) = command_line else { return };
            command_line.append_switch_with_value(Some(&FRAMEWORK_SWITCH.into()), Some(&framework_dir().to_string_lossy().as_ref().into()));
        }
        fn on_schedule_message_pump_work(&self, delay_ms: i64) { schedule(delay_ms); }
    }
}

wrap_client! {
    struct KaraFunClient;
    impl Client {
        fn render_handler(&self) -> Option<RenderHandler> { Some(KaraFunPainter::new()) }
        fn life_span_handler(&self) -> Option<LifeSpanHandler> { Some(KaraFunLife::new()) }
        fn request_handler(&self) -> Option<RequestHandler> { Some(KaraFunRequests::new()) }
    }
}

wrap_render_handler! {
    struct KaraFunPainter;
    impl RenderHandler {
        fn view_rect(&self, _browser: Option<&mut Browser>, rect: Option<&mut cef::Rect>) {
            let Some(rect) = rect else { return };
            let (width, height) = with_state(|state| state.size).unwrap_or(MAX_PAGE);
            if trace() { eprintln!("view rect {width}x{height}"); }
            rect.x = 0;
            rect.y = 0;
            rect.width = width;
            rect.height = height;
        }
        fn screen_info(&self, _browser: Option<&mut Browser>, info: Option<&mut cef::ScreenInfo>) -> ::std::os::raw::c_int {
            let Some(info) = info else { return 0 };
            // One pixel per point, as the TV is (and the screencast was).
            info.device_scale_factor = 1.0;
            info.depth = 24;
            info.depth_per_component = 8;
            1
        }
        fn on_paint(&self, _browser: Option<&mut Browser>, _type_: PaintElementType, _dirty_rects: Option<&[cef::Rect]>,
            _buffer: *const u8, width: ::std::os::raw::c_int, height: ::std::os::raw::c_int) {
            if trace() { eprintln!("processor paint {width}x{height}"); }
        }
        fn on_accelerated_paint(&self, _browser: Option<&mut Browser>, type_: PaintElementType, _dirty_rects: Option<&[cef::Rect]>, info: Option<&AcceleratedPaintInfo>) {
            if trace() { eprintln!("accelerated paint"); }
            if !matches!(type_.as_ref(), cef::sys::cef_paint_element_type_t::PET_VIEW) { return; }
            let Some(info) = info else { return };
            if info.shared_texture_io_surface.is_null() { return; }
            let (coded, visible) = (info.extra.coded_size.clone(), info.extra.visible_rect.clone());
            if coded.width <= 0 || coded.height <= 0 { return; }
            let (w, h) = (f64::from(coded.width), f64::from(coded.height));
            let visible = if visible.width > 0 && visible.height > 0 {
                (f64::from(visible.x) / w, f64::from(visible.y) / h, f64::from(visible.width) / w, f64::from(visible.height) / h)
            } else { (0.0, 0.0, 1.0, 1.0) };
            let picture = Picture { surface: info.shared_texture_io_surface, visible };
            if with_state(|state| state.streaming).unwrap_or(false) { frame_came(); }
            // Taken out while it's called (it may ask for more).
            let on_frame = STATE.with(|state| state.borrow_mut().as_mut().filter(|s| s.streaming)
                .map(|s| std::mem::replace(&mut s.on_frame, Box::new(|_| {}))));
            let Some(on_frame) = on_frame else { return };
            on_frame(&picture);
            with_state(|state| state.on_frame = on_frame);
        }
    }
}

wrap_life_span_handler! {
    struct KaraFunLife;
    impl LifeSpanHandler {
        fn on_before_popup(&self, _browser: Option<&mut Browser>, _frame: Option<&mut Frame>, _popup_id: ::std::os::raw::c_int,
            target_url: Option<&CefString>, _target_frame_name: Option<&CefString>, _target_disposition: WindowOpenDisposition,
            _user_gesture: ::std::os::raw::c_int, _popup_features: Option<&PopupFeatures>, _window_info: Option<&mut WindowInfo>,
            _client: Option<&mut Option<Client>>, _settings: Option<&mut BrowserSettings>, _extra_info: Option<&mut Option<cef::DictionaryValue>>,
            _no_javascript_access: Option<&mut ::std::os::raw::c_int>) -> ::std::os::raw::c_int {
            if let Some(url) = target_url { open(&url.to_string()); }
            // Never opened here.
            1
        }
        fn on_after_created(&self, browser: Option<&mut Browser>) {
            if let Some(browser) = browser { let browser = browser.clone(); later(move || page_made(&browser)); }
        }
        fn on_before_close(&self, _browser: Option<&mut Browser>) {
            with_state(|state| { state.closed = true; state.browser = None; state._observer = None; });
        }
    }
}

wrap_request_handler! {
    struct KaraFunRequests;
    impl RequestHandler {
        /// The page's own process stopped (it ran out of memory, say): loaded
        /// again, and the app told (as when Chrome stopped).
        fn on_render_process_terminated(&self, browser: Option<&mut Browser>, _status: TerminationStatus,
            error_code: ::std::os::raw::c_int, _error_string: Option<&CefString>) {
            eprintln!("KaraFun: Chromium's page stopped (code {error_code}); loading it again");
            if let Some(browser) = browser { browser.reload(); }
            tell(json!({"type": "error", "message": "Chromium's page stopped; loading it again"}));
        }
    }
}

wrap_dev_tools_message_observer! {
    struct KaraFunObserver;
    impl DevToolsMessageObserver {
        fn on_dev_tools_method_result(&self, _browser: Option<&mut Browser>, message_id: ::std::os::raw::c_int, success: ::std::os::raw::c_int, result: Option<&[u8]>) {
            if trace() { eprintln!("result {message_id} ok={success}: {}", String::from_utf8_lossy(result.unwrap_or_default()).chars().take(160).collect::<String>()); }
            let ready = with_state(|state| {
                let ready = state.navigate_after == Some(i64::from(message_id));
                if ready { state.navigate_after = None; }
                ready.then(|| state.url.clone())
            }).flatten();
            if let Some(url) = ready { later(move || send("Page.navigate", json!({"url": url}))); }
            let text = String::from_utf8_lossy(result.unwrap_or_default());
            if text.contains("\"errorText\":\"net::ERR_ABORTED\"") && RETRIED.with(|r| !r.replace(true)) {
                eprintln!("KaraFun: KaraFun's page load was cut off; loading it again");
                if let Some(url) = with_state(|state| state.url.clone()) { later(move || send("Page.navigate", json!({"url": url}))); }
            }
            if success == 0 {
                let text = String::from_utf8_lossy(result.unwrap_or_default());
                eprintln!("KaraFun: Chromium turned down command {message_id}: {}", text.chars().take(200).collect::<String>());
            }
        }
        fn on_dev_tools_event(&self, _browser: Option<&mut Browser>, method: Option<&CefString>, params: Option<&[u8]>) {
            if let (Some(method), Some(params)) = (method, params) {
                if trace() && method.to_string() == "Debugger.paused" {
                    if let Ok(paused) = serde_json::from_slice::<Value>(params) {
                        for frame in paused["callFrames"].as_array().into_iter().flatten().take(15) {
                            eprintln!("  at {} {}:{}:{}", frame["functionName"], frame["url"].as_str().unwrap_or("").rsplit('/').next().unwrap_or(""),
                                frame["location"]["lineNumber"], frame["location"]["columnNumber"]);
                        }
                    }
                } else if trace() && !method.to_string().starts_with("Debugger.scriptParsed") { eprintln!("event {method}: {}", String::from_utf8_lossy(params).chars().take(160).collect::<String>()); }
                page_event(&method.to_string(), params);
            }
        }
    }
}

// Chromium's work on the main thread ("external message pump"): done when
// Chromium asks (`schedule`, from any thread), and at least 30 times a
// second besides, as CEF recommends.

unsafe extern "C" {
    static _dispatch_main_q: c_void;
    fn dispatch_async_f(queue: *const c_void, context: *mut c_void, work: extern "C" fn(*mut c_void));
    fn dispatch_after_f(when: u64, queue: *const c_void, context: *mut c_void, work: extern "C" fn(*mut c_void));
    fn dispatch_time(when: u64, delta: i64) -> u64;
}

/// Do `work` on the main thread once the current work is done (Chromium
/// doesn't take commands from inside its own callbacks).
pub fn later(work: impl FnOnce() + 'static) {
    extern "C" fn run(context: *mut c_void) {
        // SAFETY: `context` is the box made below, run once.
        let work = unsafe { Box::from_raw(context.cast::<Box<dyn FnOnce()>>()) };
        work();
    }
    let work: Box<Box<dyn FnOnce()>> = Box::new(Box::new(work));
    // SAFETY: the main queue lives for the whole process; `run` takes the box back.
    unsafe { dispatch_async_f(&raw const _dispatch_main_q, Box::into_raw(work).cast(), run) }
}

fn pump() {
    if PUMPING.replace(true) { return; }
    let began = Instant::now();
    if let Ok(mut pacing) = PACING.lock() {
        if let Some(ended) = pacing.turn_ended { pacing.away = pacing.away.max(began.duration_since(ended).as_secs_f64() * 1000.0); }
    }
    cef::do_message_loop_work();
    if let Ok(mut pacing) = PACING.lock() {
        let ended = Instant::now();
        pacing.turn = pacing.turn.max(ended.duration_since(began).as_secs_f64() * 1000.0);
        pacing.turn_ended = Some(ended);
    }
    PUMPING.set(false);
}

extern "C" fn pump_now(_: *mut c_void) { pump(); }

fn schedule(delay_ms: i64) {
    let main = &raw const _dispatch_main_q;
    // SAFETY: the main queue lives for the whole process; `pump_now` takes no context.
    unsafe {
        if delay_ms <= 0 { dispatch_async_f(main, std::ptr::null_mut(), pump_now); }
        else { dispatch_after_f(dispatch_time(0, delay_ms.min(1000) * 1_000_000), main, std::ptr::null_mut(), pump_now); }
    }
}

/// The audience screen's refresh rate (frames a second), once known.
static REFRESH: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(60);

/// Frames a second while the page is seen: the audience screen's refresh
/// rate, so each of its refreshes gets one new frame (144 on a 144 Hz
/// screen, 60 on a 60 Hz TV). SINGWS_CEF_FPS sets it by hand.
fn frame_rate() -> i32 {
    std::env::var("SINGWS_CEF_FPS").ok().and_then(|v| v.parse().ok())
        .unwrap_or_else(|| REFRESH.load(std::sync::atomic::Ordering::Relaxed)).clamp(24, MAX_FPS)
}

/// The audience screen refreshes `rate` times a second: the page follows
/// (at once, if it's being shown).
pub fn set_refresh(rate: i32) {
    if rate <= 0 || REFRESH.swap(rate, std::sync::atomic::Ordering::Relaxed) == rate { return; }
    eprintln!("KaraFun: drawing at {} frames a second (the audience screen's refresh)", frame_rate());
    let host = with_state(|state| state.streaming.then(|| state.browser.as_ref().and_then(|b| b.host())).flatten()).flatten();
    if let Some(host) = host { host.set_windowless_frame_rate(frame_rate()); }
}

fn keep_pumping() {
    extern "C" fn tick(_: *mut c_void) {
        pump();
        // SAFETY: as in `schedule`.
        unsafe { dispatch_after_f(dispatch_time(0, 33_000_000), &raw const _dispatch_main_q, std::ptr::null_mut(), tick) }
    }
    tick(std::ptr::null_mut());
}

/// Chromium asks the app (NSApp) whether it's handing on an event now
/// (`isHandlingSendEvent`, Chromium's own app protocol): the app's class
/// gets the two methods, answering from a flag.
fn install_app_protocol() {
    use objc2::{runtime::{AnyClass, AnyObject, Bool, Sel}, sel};
    thread_local! { static HANDLING: Cell<bool> = const { Cell::new(false) }; }
    extern "C" fn is_handling(_: *mut AnyObject, _: Sel) -> Bool { Bool::new(HANDLING.get()) }
    extern "C" fn set_handling(_: *mut AnyObject, _: Sel, on: Bool) { HANDLING.set(on.as_bool()); }
    let (getter, setter) = if cfg!(target_arch = "aarch64") { (c"B@:", c"v@:B") } else { (c"c@:", c"v@:c") };
    let Some(class) = AnyClass::get(c"NSApplication") else { return };
    // SAFETY: the functions match the methods' types (encoded above); added
    // only if missing, before Chromium starts, on the main thread.
    unsafe {
        let class = class as *const AnyClass as *mut AnyClass;
        objc2::ffi::class_addMethod(class, sel!(isHandlingSendEvent), std::mem::transmute::<extern "C" fn(*mut AnyObject, Sel) -> Bool, objc2::runtime::Imp>(is_handling), getter.as_ptr());
        objc2::ffi::class_addMethod(class, sel!(setHandlingSendEvent:), std::mem::transmute::<extern "C" fn(*mut AnyObject, Sel, Bool), objc2::runtime::Imp>(set_handling), setter.as_ptr());
    }
}

/// Run `script` in the page (no answer), on the main thread.
pub fn eval(script: &str) { send("Runtime.evaluate", json!({"expression": script})); }

/// Run `work` on the main thread, from any thread (the stdin reader uses it).
pub fn on_main(work: impl FnOnce() + Send + 'static) { later(work) }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn drawing_never_goes_above_60_fps() {
        set_refresh(120);
        assert_eq!(frame_rate(), 60);
        set_refresh(144);
        assert_eq!(frame_rate(), 60);
        set_refresh(30);
        assert_eq!(frame_rate(), 30);
        set_refresh(60);
        assert_eq!(frame_rate(), 60);
    }

    #[test]
    fn pages_never_exceed_720p_and_keep_their_shape() {
        assert_eq!(limit_page(1920, 1080), (1280, 720));
        assert_eq!(limit_page(3840, 2160), (1280, 720));
        assert_eq!(limit_page(2560, 1440), (1280, 720));
        assert_eq!(limit_page(1280, 720), (1280, 720));
        assert_eq!(limit_page(640, 360), (640, 360));
        assert_eq!(limit_page(1920, 1200), (1152, 720));       // 16:10 keeps its shape
        assert_eq!(limit_page(0, -5), (1, 1));
    }
}
