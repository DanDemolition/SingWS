//! `--play-demo VIDEO.cdg AUDIO.mp3`: the SECOND no-Python host spike (docs/design/stage-e-no-python-host-spike-2.md). One Rust process plays a karaoke song the
//! way the app does: the Rust video bridge shows the CDG picture and keeps the song's clock (mpv, shadow audio ON = null output), the Rust karaoke voice plays
//! the MP3 and is slaved to that clock by `karaoke_proto` (a port of `mpv_karaoke_transport.py`). The BGM engine runs HEADLESS (no sound card: nothing is
//! audible); a thread pulls blocks at real-time pace and records level and song position per block. Throwaway: nothing here is connected to the app.
//! Scenario (default): play 8 s, seek to 60 s, play 4 s, pause 1.5 s, resume 3 s, seek to the last 30 s and play to the natural end, then 3 s more.
//! `--play-demo-full` plays the whole song once instead (no seeks). Output: `--play-demo-report FILE` (JSON), `--play-demo-frame FILE` (raw RGBA of the picture).

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, status)]
        #[qproperty(bool, done)]
        type PlayDemo = super::PlayDemoRust;

        /// True once the output window's NSView exists and its window is shown (the bridge does nothing with a hidden view: trap 1 of spike 1).
        #[qinvokable]
        fn prepare(self: Pin<&mut PlayDemo>) -> bool;
        /// Create the engine, the voice and the bridge, load everything paused and start the protocol.
        #[qinvokable]
        fn start(self: Pin<&mut PlayDemo>) -> bool;
        /// Every ~10 ms: the protocol and the scenario.
        #[qinvokable]
        fn tick(self: Pin<&mut PlayDemo>);
        /// Tear down (bridge, voice, render thread) and write the report.
        #[qinvokable]
        fn finish(self: Pin<&mut PlayDemo>, ok: bool) -> bool;
    }
}

use crate::karaoke_proto::{Event, Phase, Picture, Protocol, Voice};
use core::pin::Pin;
use cxx_qt_lib::QString;
use serde_json::{json, Value};
use std::ffi::{c_char, c_void, CStr, CString};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

#[derive(Default, Clone, Debug, PartialEq)]
pub struct Options {
    pub video: String,
    pub audio: String,
    pub report: String,
    pub frame: String,
    pub full: bool,
    /// how many seconds before the song's end the scenario seeks to (default 30; within ~20 s of the end mpv's CDG seek jumps the picture to the end, see the design doc)
    pub seek_end: f64,
}

pub fn parse_args<I: Iterator<Item = String>>(args: I) -> Option<Options> {
    let mut o = Options { seek_end: 30.0, ..Options::default() };
    let mut on = false;
    let mut it = args;
    while let Some(a) = it.next() {
        match a.as_str() {
            "--play-demo" => {
                on = true;
                o.video = it.next().unwrap_or_default();
                o.audio = it.next().unwrap_or_default();
            }
            "--play-demo-report" => o.report = it.next().unwrap_or_default(),
            "--play-demo-frame" => o.frame = it.next().unwrap_or_default(),
            "--play-demo-full" => o.full = true,
            "--play-demo-seek-end" => o.seek_end = it.next().and_then(|v| v.parse().ok()).unwrap_or(30.0),
            _ => {}
        }
    }
    on.then_some(o)
}

static OPTIONS: Mutex<Option<Options>> = Mutex::new(None);
pub fn configure<I: Iterator<Item = String>>(args: I) -> bool {
    match parse_args(args) {
        Some(o) => {
            *OPTIONS.lock().unwrap() = Some(o);
            true
        }
        None => false,
    }
}
fn options() -> Options {
    OPTIONS.lock().unwrap().clone().unwrap_or_default()
}

// ---- pure helpers (tested) -------------------------------------------------------------------------------------------------------

pub fn median(v: &[f64]) -> f64 {
    if v.is_empty() {
        return 0.0;
    }
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = s.len();
    if n % 2 == 1 { s[n / 2] } else { (s[n / 2 - 1] + s[n / 2]) / 2.0 }
}

pub fn percentile(v: &[f64], p: f64) -> f64 {
    if v.is_empty() {
        return 0.0;
    }
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    s[(((s.len() - 1) as f64) * p).round() as usize]
}

/// One block pulled from the engine: wall time (s since the demo's epoch), the voice's audible song position, RMS, and the voice's flags.
#[derive(Clone, Copy, Debug)]
pub struct Block {
    pub t: f64,
    pub pos: f32,
    pub rms: f32,
    pub flags: u32,
}

pub const AUDIBLE: f32 = 1e-4; // -80 dBFS
pub const KFLAG_FINISHED: u32 = 8;

#[derive(Debug, Default, PartialEq)]
pub struct BlockAnalysis {
    pub blocks: usize,
    pub audible_blocks: usize,
    /// song position of the last audible block
    pub last_audible_pos: f32,
    /// wall time of the last audible block
    pub last_audible_t: f64,
    /// audible blocks while the voice reported FINISHED
    pub audible_after_finished: usize,
    /// audible blocks later than `stopped_at + grace` (the voice was told to stop / the song ended)
    pub audible_after_stop: usize,
    /// audible blocks whose song position went BACKWARD by > 50 ms against the previous audible block, outside the 1.5 s after each seek (earlier audio replayed)
    pub backward_jumps: usize,
}

pub fn analyze_blocks(blocks: &[Block], seeks_t: &[f64], stopped_at: Option<f64>, grace: f64) -> BlockAnalysis {
    let mut a = BlockAnalysis { blocks: blocks.len(), ..Default::default() };
    let mut prev: Option<f32> = None;
    for b in blocks {
        if b.rms <= AUDIBLE {
            continue;
        }
        a.audible_blocks += 1;
        a.last_audible_pos = b.pos;
        a.last_audible_t = b.t;
        if b.flags & KFLAG_FINISHED != 0 {
            a.audible_after_finished += 1;
        }
        if stopped_at.is_some_and(|s| b.t > s + grace) {
            a.audible_after_stop += 1;
        }
        let after_seek = seeks_t.iter().any(|&s| b.t >= s && b.t < s + 1.5);
        if let Some(p) = prev {
            if !after_seek && b.pos < p - 0.05 {
                a.backward_jumps += 1;
            }
        }
        prev = if after_seek { None } else { Some(b.pos) };
    }
    a
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Step {
    Wait(f64),   // seconds of RUNNING time (the protocol in phase Running and not user-paused)
    Seek(f64),   // seconds, absolute; -N = N seconds before the song's end
    Pause,
    PauseWait(f64),
    Resume,
    UntilEnded,
    Tail(f64),   // seconds after the end
}

fn scenario(full: bool, seek_end: f64) -> Vec<Step> {
    if full {
        vec![Step::UntilEnded, Step::Tail(3.0)]
    } else {
        vec![Step::Wait(8.0), Step::Seek(60.0), Step::Wait(4.0), Step::Pause, Step::PauseWait(1.5), Step::Resume, Step::Wait(3.0), Step::Seek(-seek_end), Step::UntilEnded, Step::Tail(3.0)]
    }
}

// ---- the real picture and voice ----------------------------------------------------------------------------------------------------

struct BridgePicture(usize);
impl BridgePicture {
    fn h(&self) -> *mut c_void {
        self.0 as *mut c_void
    }
}
impl Picture for BridgePicture {
    fn duration_ms(&self) -> i64 { singws_video::singws_bridge_duration(self.h()) }
    fn position_ms(&self) -> i64 { singws_video::singws_bridge_position(self.h()) }
    fn is_playing(&self) -> bool { singws_video::singws_bridge_is_playing(self.h()) != 0 }
    fn at_end(&self) -> bool { singws_video::singws_bridge_at_end(self.h()) != 0 }
    fn visuals_ready(&self) -> bool { singws_video::singws_bridge_visual_ready(self.h()) != 0 }
    fn play(&mut self) { singws_video::singws_bridge_play(self.h()) }
    fn pause(&mut self) { singws_video::singws_bridge_pause(self.h()) }
    fn seek_ms(&mut self, ms: i64) { singws_video::singws_bridge_seek(self.h(), ms) }
    fn stop(&mut self) { singws_video::singws_bridge_stop(self.h()) }
    fn set_shadow_audio(&mut self, on: bool) { singws_video::singws_bridge_set_shadow_audio(self.h(), on as i32) }
}

const KFLAG_LOADED: u32 = 1;
const KFLAG_PENDING: u32 = 16;

struct EngineVoice(usize);
impl EngineVoice {
    fn s(&self) -> *mut singws_bgm_ffi::Sys {
        self.0 as *mut singws_bgm_ffi::Sys
    }
    fn flags(&self) -> u32 {
        unsafe { singws_bgm_ffi::singws_bgm_karaoke_flags(self.s()) }
    }
    fn times(&self) -> (f32, f32) {
        let (mut p, mut d) = (0f32, 0f32);
        unsafe { singws_bgm_ffi::singws_bgm_karaoke_times(self.s(), &mut p, &mut d) };
        (p, d)
    }
}
impl Voice for EngineVoice {
    fn is_ready(&self) -> bool { let f = self.flags(); f & KFLAG_LOADED != 0 && f & KFLAG_PENDING == 0 }
    fn position(&self) -> f64 { self.times().0 as f64 }
    fn play(&mut self) { unsafe { singws_bgm_ffi::singws_bgm_karaoke_play(self.s()) }; }
    fn pause(&mut self) { unsafe { singws_bgm_ffi::singws_bgm_karaoke_pause(self.s()) }; }
    fn stop(&mut self) { unsafe { singws_bgm_ffi::singws_bgm_karaoke_stop(self.s()) }; }
    fn seek(&mut self, s: f64) { unsafe { singws_bgm_ffi::singws_bgm_karaoke_seek(self.s(), s as f32) }; }
    fn slave_to(&mut self, p: f64, r: f64) { unsafe { singws_bgm_ffi::singws_bgm_karaoke_slave_clock(self.s(), p, r) }; }
    fn duration(&self) -> f64 { self.times().1 as f64 }
    fn resync_info(&self) -> Option<(u32, u32, f32, f32, f32, i32)> {
        let mut counts = [0u32; 2];
        let mut last = [0f32; 4];
        unsafe { singws_bgm_ffi::singws_bgm_karaoke_resync_info(self.s(), counts.as_mut_ptr(), last.as_mut_ptr()) };
        Some((counts[0], counts[1], last[0], last[1], last[2], last[3].round() as i32))
    }
}

// ---- demo state ----------------------------------------------------------------------------------------------------------------------

struct Render {
    stop: Arc<AtomicBool>,
    blocks: Arc<Mutex<Vec<Block>>>,
    thread: Option<JoinHandle<()>>,
}

struct Demo {
    proto: Protocol<BridgePicture, EngineVoice>,
    epoch: Instant,
    render: Render,
    steps: Vec<Step>,
    step: usize,
    step_started: f64,
    run_time: f64,        // running seconds accumulated in the current Wait step
    last_tick: f64,
    last_transition: f64, // time of the last start / seek / resume (steady samples start 1 s later)
    events: Vec<Value>,
    errors_steady_ms: Vec<f64>,
    errors_all_ms: Vec<f64>,
    seeks_t: Vec<f64>,
    seek_marks: Vec<(f64, f64)>, // (seek call time, time the protocol was Running again)
    pending_seek: Option<f64>,
    pause_probe: Option<(f64, f64, f64)>, // (picture ms, voice s, wall t) at the start of the pause
    pause_result: Value,
    stopped_at: Option<f64>,
    ended_at: Option<f64>,
    presented_checks: Vec<Value>,
    next_present_check: f64,
    frame_dumped: bool,
    started_at: f64,
    voice_duration: f64,
    song_ms: i64,
    timeline: Vec<Value>,
    next_sample: f64,
    large_errors: Vec<Value>,
}

static DEMO: Mutex<Option<Demo>> = Mutex::new(None);
static BRIDGE_LOG: Mutex<Vec<String>> = Mutex::new(Vec::new());
static CHECKS: Mutex<Vec<Value>> = Mutex::new(Vec::new());

fn record(name: &str, ok: bool, detail: Value) {
    CHECKS.lock().unwrap().push(json!({"name": name, "ok": ok, "detail": detail}));
}

unsafe extern "C" fn bridge_log_cb(line: *const c_char) {
    if !line.is_null() {
        let s = unsafe { CStr::from_ptr(line) }.to_string_lossy().into_owned();
        let mut g = BRIDGE_LOG.lock().unwrap();
        if g.len() < 400 {
            g.push(s);
        }
    }
}

unsafe extern "C" {
    fn singws_window_native_id(title: *const c_char) -> u64;
}
const OUT_TITLE: &CStr = c"SingWS play-demo output";
const PREV_TITLE: &CStr = c"SingWS play-demo preview";

/// The view of the output window, and why it cannot be handed to the bridge yet (None = fine).
fn view_problem(title: &CStr) -> (u64, Option<String>) {
    let id = unsafe { singws_window_native_id(title.as_ptr()) };
    if id == 0 {
        return (0, Some("the output window has no native view yet".into()));
    }
    let st = crate::selfcheck::ns_view_state(id as usize);
    if st["has_window"] != json!(true) {
        return (id, Some(format!("the view has no window: {st}")));
    }
    if st["window_visible"] != json!(true) {
        return (id, Some(format!("the view's window is not shown (the bridge would silently draw nothing): {st}")));
    }
    if st["isHiddenOrHasHiddenAncestor"] == json!(true) {
        return (id, Some(format!("the view or an ancestor is hidden: {st}")));
    }
    (id, None)
}

/// Real-time pulls from the headless engine, recording level and song position per block.
fn spawn_render(sys: usize, epoch: Instant) -> Render {
    let stop = Arc::new(AtomicBool::new(false));
    let blocks = Arc::new(Mutex::new(Vec::with_capacity(40_000)));
    let (st, bl) = (stop.clone(), blocks.clone());
    let thread = std::thread::spawn(move || {
        let s = sys as *mut singws_bgm_ffi::Sys;
        let (rate, block) = (48_000f64, 512u32);
        let mut buf = vec![0f32; 2 * block as usize];
        let t0 = Instant::now();
        let mut n: u64 = 0;
        while !st.load(Ordering::Relaxed) {
            // pace like a sound-card callback: block n is due at n * block / rate
            let due = Duration::from_secs_f64(n as f64 * block as f64 / rate);
            if let Some(wait) = due.checked_sub(t0.elapsed()) {
                std::thread::sleep(wait);
            }
            if unsafe { singws_bgm_ffi::singws_bgm_render(s, buf.as_mut_ptr(), block) } != 1 {
                break;
            }
            let rms = crate::selfcheck::rms(&buf) as f32;
            let (mut pos, mut dur) = (0f32, 0f32);
            unsafe { singws_bgm_ffi::singws_bgm_karaoke_times(s, &mut pos, &mut dur) };
            let flags = unsafe { singws_bgm_ffi::singws_bgm_karaoke_flags(s) };
            bl.lock().unwrap().push(Block { t: epoch.elapsed().as_secs_f64(), pos, rms, flags });
            n += 1;
        }
    });
    Render { stop, blocks, thread: Some(thread) }
}

impl Demo {
    fn now(&self) -> f64 {
        self.epoch.elapsed().as_secs_f64()
    }
}

#[derive(Default)]
pub struct PlayDemoRust {
    status: QString,
    done: bool,
}

impl qobject::PlayDemo {
    fn prepare(mut self: Pin<&mut Self>) -> bool {
        let (_, p1) = view_problem(OUT_TITLE);
        let (_, p2) = view_problem(PREV_TITLE);
        match p1.or(p2) {
            None => true,
            Some(p) => {
                self.as_mut().set_status(QString::from(p.as_str()));
                false
            }
        }
    }

    fn start(mut self: Pin<&mut Self>) -> bool {
        let o = options();
        let epoch = Instant::now();
        singws_video::singws_bridge_set_log_callback(Some(bridge_log_cb));
        // A host window that is covered gets App Nap: timers slow to ~1 s and the song stalls mid-play (seen in this spike). The Python app holds the same assertion.
        let awake = singws_mac::keep_awake_begin("SingWS play-demo");
        record("keep_awake", awake, json!({"active": singws_mac::keep_awake_active()}));
        let (view, problem) = view_problem(OUT_TITLE);
        let (pview, pproblem) = view_problem(PREV_TITLE);
        record("output_view", problem.is_none() && pproblem.is_none(), json!({"id": view, "preview_id": pview, "problem": problem, "preview_problem": pproblem, "state": crate::selfcheck::ns_view_state(view as usize)}));
        if problem.is_some() || pproblem.is_some() {
            self.as_mut().set_status(QString::from("output view not shown"));
            return false;
        }
        match singws_video::mpvapi::api() {
            Ok(_) => record("libmpv", true, json!({"env": std::env::var("SINGWS_LIBMPV").unwrap_or_default()})),
            Err(e) => {
                record("libmpv", false, json!({"error": e}));
                return false;
            }
        }
        // the engine: headless, one karaoke voice; the MP3 is opened PAUSED (the protocol starts it)
        let sys = singws_bgm_ffi::singws_bgm_create_headless(48_000);
        if sys.is_null() {
            record("engine", false, json!({"error": "singws_bgm_create_headless returned null"}));
            return false;
        }
        let mut err = [0 as c_char; 256];
        let Ok(path) = CString::new(o.audio.clone()) else { return false };
        let deck = unsafe { singws_bgm_ffi::singws_bgm_deck_open(sys, path.as_ptr(), err.as_mut_ptr(), err.len()) };
        if deck.is_null() {
            let msg = unsafe { CStr::from_ptr(err.as_ptr()) }.to_string_lossy().into_owned();
            record("engine", false, json!({"error": format!("deck_open: {msg}"), "file": o.audio}));
            return false;
        }
        unsafe {
            singws_bgm_ffi::singws_bgm_karaoke_load(sys, deck, 1.0, 1, 1.0, 1);
            singws_bgm_ffi::singws_bgm_karaoke_set_key(sys, 0.0);
            singws_bgm_ffi::singws_bgm_karaoke_set_tempo(sys, 100.0);
        }
        let render = spawn_render(sys as usize, epoch);

        // the picture: shadow audio is read when the core is created, so set it first
        unsafe { std::env::set_var("SINGWS_BRIDGE_SHADOW_AUDIO", "1") };
        let (v, a) = (CString::new(o.video.clone()).unwrap(), CString::new(o.audio.clone()).unwrap());
        let h = singws_video::singws_bridge_create(view as usize, pview as usize, v.as_ptr(), a.as_ptr());
        if h.is_null() {
            record("bridge_create", false, json!({"error": "singws_bridge_create returned null"}));
            return false;
        }
        let mut picture = BridgePicture(h as usize);
        picture.set_shadow_audio(true);
        singws_video::singws_bridge_pause(h); // loaded PAUSED, like loadSingWSMedia(autoplay=False)
        record("bridge_create", true, json!({"video": o.video, "audio": o.audio, "render_thread": singws_video::singws_bridge_render_thread_active(h) != 0}));

        let mut proto = Protocol::new(picture, Some(EngineVoice(sys as usize)));
        proto.seek_gap = std::fs::read(&o.video).ok().and_then(|d| singws_cdg::terminal_gap(&d));
        record("seek_gap", true, json!({"gap": proto.seek_gap}));
        let now = epoch.elapsed().as_secs_f64();
        proto.start(now, 0.0);
        let steps = scenario(o.full, o.seek_end);
        *DEMO.lock().unwrap() = Some(Demo {
            proto,
            epoch,
            render,
            steps,
            step: 0,
            step_started: now,
            run_time: 0.0,
            last_tick: now,
            last_transition: now,
            events: Vec::new(),
            errors_steady_ms: Vec::new(),
            errors_all_ms: Vec::new(),
            seeks_t: Vec::new(),
            seek_marks: Vec::new(),
            pending_seek: None,
            pause_probe: None,
            pause_result: Value::Null,
            stopped_at: None,
            ended_at: None,
            presented_checks: Vec::new(),
            next_present_check: now + 1.5,
            frame_dumped: false,
            started_at: now,
            voice_duration: 0.0,
            song_ms: 0,
            timeline: Vec::new(),
            next_sample: 0.0,
            large_errors: Vec::new(),
        });
        true
    }

    fn tick(mut self: Pin<&mut Self>) {
        let mut g = DEMO.lock().unwrap();
        let Some(d) = g.as_mut() else { return };
        let now = d.now();
        let dt = now - d.last_tick;
        d.last_tick = now;
        for ev in d.proto.tick(now) {
            d.events.push(json!({"t": now, "event": format!("{ev:?}"), "picture_ms": d.proto.picture.position_ms()}));
            if ev == Event::Ended {
                d.ended_at = Some(now);
                d.stopped_at = Some(now);
            }
        }
        let running = d.proto.phase == Phase::Running && !d.proto.user_paused();
        if let Some(t) = d.pending_seek {
            if d.proto.phase == Phase::Running {
                d.seek_marks.push((t, now));
                d.pending_seek = None;
                d.last_transition = now;
            }
        }
        if d.voice_duration == 0.0 {
            d.voice_duration = d.proto.voice.as_ref().map(|v| v.duration()).unwrap_or(0.0);
            d.song_ms = d.proto.picture.duration_ms();
        }
        if now >= d.next_sample && d.timeline.len() < 6000 {
            d.next_sample = now + 0.1;
            let vpos = d.proto.voice.as_ref().map(|v| v.position());
            d.timeline.push(json!({"t": (now * 1000.0).round() / 1000.0, "phase": format!("{:?}", d.proto.phase), "picture_ms": d.proto.picture.position_ms(), "voice_s": vpos,
                                   "playing": d.proto.picture.is_playing(), "at_end": d.proto.picture.at_end()}));
        }
        // sound-vs-picture error while both run
        if running && d.proto.picture.is_playing() && d.ended_at.is_none() {
            if let Some(v) = d.proto.voice.as_ref() {
                let err = (v.position() - d.proto.position_seconds()) * 1000.0;
                d.errors_all_ms.push(err);
                if err.abs() > 200.0 && d.large_errors.len() < 30 {
                    d.large_errors.push(json!({"t": now, "err_ms": err, "voice_s": v.position(), "picture_ms": d.proto.picture.position_ms(), "since_transition_s": now - d.last_transition}));
                }
                if now - d.last_transition > 1.0 {
                    d.errors_steady_ms.push(err);
                }
            }
        }
        // presentation: the bridge says why when it does not draw (trap 1)
        if now >= d.next_present_check && d.presented_checks.len() < 8 && d.ended_at.is_none() {
            d.next_present_check = now + 3.0;
            let h = d.proto.picture.h();
            let n = singws_video::singws_bridge_present_count(h);
            d.presented_checks.push(json!({"t": now, "present_count": n, "visual_ready": d.proto.picture.visuals_ready(), "picture_ms": d.proto.picture.position_ms()}));
            if d.presented_checks.len() == 2 && !d.frame_dumped && !options().frame.is_empty() {
                d.frame_dumped = true;
                dump_frame(h, &options().frame);
            }
        }
        // the scenario
        let step = d.steps.get(d.step).copied();
        let mut advance = false;
        match step {
            None => {
                drop(g);
                self.as_mut().set_done(true);
                return;
            }
            Some(Step::Wait(s)) => {
                if running {
                    d.run_time += dt;
                }
                advance = d.run_time >= s && now - d.last_transition > 0.0 && d.proto.phase == Phase::Running;
            }
            Some(Step::Seek(s)) => {
                let target = if s < 0.0 { (d.song_ms as f64 / 1000.0 + s).max(0.0) } else { s };
                d.proto.seek(now, target);
                d.seeks_t.push(now);
                d.pending_seek = Some(now);
                d.events.push(json!({"t": now, "event": format!("seek {target:.1}")}));
                advance = true;
            }
            Some(Step::Pause) => {
                d.pause_probe = Some((d.proto.picture.position_ms() as f64, d.proto.voice.as_ref().map(|v| v.position()).unwrap_or(0.0), now));
                d.proto.pause();
                d.events.push(json!({"t": now, "event": "pause"}));
                advance = true;
            }
            Some(Step::PauseWait(s)) => {
                if now - d.step_started >= s {
                    let (p0, v0, t0) = d.pause_probe.unwrap_or((0.0, 0.0, now));
                    let p1 = d.proto.picture.position_ms() as f64;
                    let v1 = d.proto.voice.as_ref().map(|v| v.position()).unwrap_or(0.0);
                    d.pause_result = json!({"seconds": now - t0, "picture_moved_ms": p1 - p0, "voice_moved_ms": (v1 - v0) * 1000.0, "picture_playing": d.proto.picture.is_playing()});
                    advance = true;
                }
            }
            Some(Step::Resume) => {
                d.proto.resume();
                d.last_transition = now;
                d.events.push(json!({"t": now, "event": "resume"}));
                advance = true;
            }
            Some(Step::UntilEnded) => {
                if d.ended_at.is_some() {
                    advance = true;
                } else if now - d.started_at > 900.0 {
                    advance = true; // never hang
                }
            }
            Some(Step::Tail(s)) => {
                advance = d.ended_at.is_none() || now - d.ended_at.unwrap() >= s;
            }
        }
        if advance {
            d.step += 1;
            d.step_started = now;
            d.run_time = 0.0;
        }
    }

    fn finish(mut self: Pin<&mut Self>, ok: bool) -> bool {
        let o = options();
        let mut g = DEMO.lock().unwrap();
        let mut summary = json!({});
        if let Some(mut d) = g.take() {
            let now = d.now();
            d.proto.stop();
            let h = d.proto.picture.h();
            let present_final = singws_video::singws_bridge_present_count(h);
            let why: Vec<String> = BRIDGE_LOG.lock().unwrap().iter().filter(|l| l.contains("NO PICTURE") || l.contains("view-hidden") || l.contains("window-not-visible") || l.contains("no-window")).cloned().collect();
            // a bridge that never presented a frame is a failure with a reason, not a quiet black picture
            record("presentation", present_final > 30, json!({"present_count": present_final, "bridge_says": why}));
            let flags = d.proto.voice.as_ref().map(|v| v.flags());
            // flush the render thread, then analyse
            std::thread::sleep(Duration::from_millis(150));
            d.render.stop.store(true, Ordering::Relaxed);
            if let Some(t) = d.render.thread.take() {
                let _ = t.join();
            }
            let blocks = d.render.blocks.lock().unwrap().clone();
            let analysis = analyze_blocks(&blocks, &d.seeks_t, d.stopped_at, 0.25);
            let seek_ms: Vec<f64> = d.seek_marks.iter().map(|(a, b)| (b - a) * 1000.0).collect();
            let sys = d.proto.voice.as_ref().map(|v| v.0).unwrap_or(0) as *mut singws_bgm_ffi::Sys;
            let mut stats = [0u64; 16];
            if !sys.is_null() {
                unsafe { singws_bgm_ffi::singws_bgm_stats(sys, stats.as_mut_ptr(), 16) };
            }
            let st = |v: &[f64]| json!({"n": v.len(), "median_ms": median(&v.iter().map(|x| x.abs()).collect::<Vec<_>>()), "p95_ms": percentile(&v.iter().map(|x| x.abs()).collect::<Vec<_>>(), 0.95),
                                       "worst_abs_ms": v.iter().fold(0.0f64, |m, x| m.max(x.abs())), "mean_signed_ms": if v.is_empty() { 0.0 } else { v.iter().sum::<f64>() / v.len() as f64 }});
            summary = json!({
                "song_ms": d.song_ms, "voice_duration_s": d.voice_duration,
                "scenario_steps": d.step, "scenario_total": d.steps.len(), "elapsed_s": now,
                "events": d.events, "timeline": d.timeline, "large_errors": d.large_errors, "protocol_log": d.proto.log, "voice_dropped": d.proto.dropped,
                "sound_vs_picture_steady": st(&d.errors_steady_ms), "sound_vs_picture_all_running": st(&d.errors_all_ms),
                "seek_to_running_ms": seek_ms, "pause": d.pause_result,
                "presented": d.presented_checks, "present_count_final": present_final,
                "ended_at": d.ended_at, "voice_flags_at_finish": flags,
                "audio": {"blocks": analysis.blocks, "audible_blocks": analysis.audible_blocks, "last_audible_song_pos_s": analysis.last_audible_pos, "last_audible_t": analysis.last_audible_t,
                          "audible_after_finished_flag": analysis.audible_after_finished, "audible_after_stop_250ms": analysis.audible_after_stop,
                          "backward_position_jumps": analysis.backward_jumps},
                "engine_stats": {"underruns": stats[11], "gap_frames": stats[12], "dropped_commands": stats[10]},
            });
            singws_video::singws_bridge_destroy(h);
            record("teardown_bridge", true, json!({}));
            if !sys.is_null() {
                unsafe { singws_bgm_ffi::singws_bgm_destroy(sys) };
                record("teardown_engine", true, json!({}));
            }
        }
        singws_mac::keep_awake_end();
        let checks = CHECKS.lock().unwrap().clone();
        let doc = json!({"playdemo": 1, "ok": ok, "arch": std::env::consts::ARCH, "pid": std::process::id(), "checks": checks, "bridge_log": *BRIDGE_LOG.lock().unwrap(), "summary": summary});
        let wrote = !o.report.is_empty() && std::fs::write(&o.report, serde_json::to_string_pretty(&doc).unwrap()).is_ok();
        self.as_mut().set_done(true);
        wrote
    }
}

fn dump_frame(h: *mut c_void, path: &str) {
    let (mut w, mut hh, mut stride) = (0i32, 0i32, 0i32);
    let buf = singws_video::singws_bridge_grab_frame(h, &mut w, &mut hh, &mut stride);
    if buf.is_null() || w <= 0 || hh <= 0 {
        record("grab_frame", false, json!({"note": "no frame"}));
        return;
    }
    let bytes = unsafe { std::slice::from_raw_parts(buf as *const u8, (stride * hh) as usize) };
    let lit = crate::selfcheck::lit_fraction(bytes, 24);
    // tightly packed RGBA for `ffmpeg -f rawvideo -pix_fmt rgba -s WxH`
    let mut packed = Vec::with_capacity((w * hh * 4) as usize);
    for row in 0..hh as usize {
        packed.extend_from_slice(&bytes[row * stride as usize..row * stride as usize + (w * 4) as usize]);
    }
    let _ = std::fs::write(path, &packed);
    singws_video::singws_bridge_free_frame(buf);
    record("grab_frame", true, json!({"w": w, "h": hh, "lit_fraction": lit, "file": path}));
}

#[cfg(test)]
mod tests {
    use super::*;
    fn a(v: &[&str]) -> std::vec::IntoIter<String> {
        v.iter().map(|s| s.to_string()).collect::<Vec<_>>().into_iter()
    }
    fn blk(t: f64, pos: f32, rms: f32, flags: u32) -> Block {
        Block { t, pos, rms, flags }
    }

    #[test]
    fn parses_the_flags() {
        let o = parse_args(a(&["--play-demo", "s.cdg", "s.mp3", "--play-demo-report", "/r.json", "--play-demo-frame", "/f.rgba", "--play-demo-full"])).unwrap();
        assert_eq!(o, Options { video: "s.cdg".into(), audio: "s.mp3".into(), report: "/r.json".into(), frame: "/f.rgba".into(), full: true, seek_end: 30.0 });
        assert!(parse_args(a(&["--selfcheck"])).is_none());
    }

    #[test]
    fn median_and_percentile() {
        assert_eq!(median(&[]), 0.0);
        assert_eq!(median(&[3.0, 1.0, 2.0]), 2.0);
        assert_eq!(median(&[4.0, 1.0, 2.0, 3.0]), 2.5);
        assert_eq!(percentile(&[1.0, 2.0, 3.0, 4.0, 100.0], 0.95), 100.0);
        assert_eq!(percentile(&[1.0, 2.0, 3.0, 4.0, 100.0], 0.5), 3.0);
    }

    #[test]
    fn a_clean_end_has_nothing_audible_after_the_stop_and_no_backward_jumps() {
        let mut bs = Vec::new();
        for i in 0..100 {
            bs.push(blk(i as f64 * 0.01, i as f32 * 0.01, 0.1, 0));
        }
        for i in 100..200 {
            bs.push(blk(i as f64 * 0.01, 1.0, 0.0, KFLAG_FINISHED));
        }
        let a = analyze_blocks(&bs, &[], Some(1.0), 0.25);
        assert_eq!(a.audible_blocks, 100);
        assert_eq!(a.audible_after_stop, 0);
        assert_eq!(a.audible_after_finished, 0);
        assert_eq!(a.backward_jumps, 0);
        assert!((a.last_audible_pos - 0.99).abs() < 1e-6);
    }

    #[test]
    fn a_quarter_second_clip_of_earlier_audio_at_the_end_is_caught() {
        // the real-show symptom: after the song's last audible moment, a short burst from EARLIER in the song plays
        let mut bs = Vec::new();
        for i in 0..100 {
            bs.push(blk(i as f64 * 0.01, 10.0 + i as f32 * 0.01, 0.1, 0));
        }
        for i in 100..125 {
            bs.push(blk(i as f64 * 0.01, 9.0 + (i - 100) as f32 * 0.01, 0.1, KFLAG_FINISHED)); // position jumped back 2 s, audible, flagged finished
        }
        let a = analyze_blocks(&bs, &[], Some(1.0), 0.0);
        assert_eq!(a.backward_jumps, 1);
        assert_eq!(a.audible_after_finished, 25);
        assert!(a.audible_after_stop > 0);
    }

    #[test]
    fn a_position_jump_right_after_a_seek_is_not_a_fault() {
        let bs = vec![blk(0.0, 50.0, 0.1, 0), blk(0.01, 50.01, 0.1, 0), blk(1.0, 10.0, 0.1, 0), blk(1.01, 10.01, 0.1, 0)];
        assert_eq!(analyze_blocks(&bs, &[0.9], None, 0.25).backward_jumps, 0);
        assert_eq!(analyze_blocks(&bs, &[], None, 0.25).backward_jumps, 1);
    }

    #[test]
    fn the_default_scenario_covers_seek_pause_resume_and_the_natural_end() {
        let s = scenario(false, 12.0);
        assert!(s.contains(&Step::Pause) && s.contains(&Step::Resume) && s.contains(&Step::UntilEnded));
        assert_eq!(s.iter().filter(|x| matches!(x, Step::Seek(_))).count(), 2);
        assert_eq!(scenario(true, 12.0), vec![Step::UntilEnded, Step::Tail(3.0)]);
    }
}
