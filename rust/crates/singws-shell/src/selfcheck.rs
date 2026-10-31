//! `--selfcheck`: the "no-Python host" spike (docs/design/stage-e-no-python-gap.md, Step 3). One Rust process hosts the Qt Quick shell, the Rust
//! video bridge (`singws-video`) and the Rust BGM engine (`singws-bgm-ffi`), links them in-process, hands two Qt Quick windows' `winId`s to the bridge
//! as its output/preview views and plays ~5 s through the BGM engine headless. It writes `selfcheck.json` and changes nothing else.
//! Item (e) calls `singws-mac` (ax_is_trusted, frontmost_app_name, run_applescript("return 1+1")) in-process, read-only.

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
        type SelfCheck = super::SelfCheckRust;

        /// Create the bridge on the two windows' NSViews and start the BGM thread.
        #[qinvokable]
        fn start(self: Pin<&mut SelfCheck>) -> bool;
        /// Re-hand the windows' views to the bridge (what the Python host does after a window change).
        #[qinvokable]
        fn refresh(self: Pin<&mut SelfCheck>);
        /// Sample the bridge: position, picture state, a grabbed frame.
        #[qinvokable]
        fn probe(self: Pin<&mut SelfCheck>);
        /// Tear down, join the BGM thread, write the report.
        #[qinvokable]
        fn finish(self: Pin<&mut SelfCheck>) -> bool;
    }
}

use core::pin::Pin;
use cxx_qt_lib::QString;
use serde_json::{json, Value};
use std::ffi::{c_void, CString};
use std::sync::Mutex;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

#[derive(Default, Clone, Debug, PartialEq)]
pub struct Options {
    pub report: String,
    pub video: String,
    pub audio: String,
}

unsafe extern "C" {
    fn singws_window_native_id(title: *const std::os::raw::c_char) -> u64;
}
fn native_id(title: &std::ffi::CStr) -> u64 {
    unsafe { singws_window_native_id(title.as_ptr()) }
}

/// What AppKit says about a native view handed to the bridge: read-only questions through the Objective-C runtime (no shim code of our own).
fn ns_view_state(view: usize) -> Value {
    use std::ffi::{c_char, c_void, CStr};
    unsafe extern "C" {
        fn sel_registerName(n: *const c_char) -> *mut c_void;
        fn objc_msgSend();
        fn object_getClassName(o: *mut c_void) -> *const c_char;
    }
    if view == 0 {
        return json!({"error": "no view"});
    }
    let v = view as *mut c_void;
    unsafe {
        let b: unsafe extern "C" fn(*mut c_void, *mut c_void) -> bool = std::mem::transmute(objc_msgSend as unsafe extern "C" fn());
        let o: unsafe extern "C" fn(*mut c_void, *mut c_void) -> *mut c_void = std::mem::transmute(objc_msgSend as unsafe extern "C" fn());
        let sel = |n: &CStr| sel_registerName(n.as_ptr());
        let win = o(v, sel(c"window"));
        let supers = o(v, sel(c"superview"));
        json!({
            "class": CStr::from_ptr(object_getClassName(v)).to_string_lossy(),
            "isHidden": b(v, sel(c"isHidden")),
            "isHiddenOrHasHiddenAncestor": b(v, sel(c"isHiddenOrHasHiddenAncestor")),
            "has_window": !win.is_null(),
            "window_visible": if win.is_null() { Value::Null } else { json!(b(win, sel(c"isVisible"))) },
            "has_superview": !supers.is_null(),
            "superview_class": if supers.is_null() { Value::Null } else { json!(CStr::from_ptr(object_getClassName(supers)).to_string_lossy()) },
        })
    }
}

static OPTIONS: Mutex<Option<Options>> = Mutex::new(None);
static CHECKS: Mutex<Vec<Value>> = Mutex::new(Vec::new());
static BRIDGE: Mutex<usize> = Mutex::new(0);
static BGM: Mutex<Option<JoinHandle<()>>> = Mutex::new(None);

/// Parse the `--selfcheck` arguments. `None` = not a selfcheck run.
pub fn parse_args<I: Iterator<Item = String>>(args: I) -> Option<Options> {
    let mut on = false;
    let mut o = Options::default();
    let mut it = args;
    while let Some(a) = it.next() {
        match a.as_str() {
            "--selfcheck" => on = true,
            "--selfcheck-report" => o.report = it.next().unwrap_or_default(),
            "--selfcheck-video" => o.video = it.next().unwrap_or_default(),
            "--selfcheck-audio" => o.audio = it.next().unwrap_or_default(),
            _ => {}
        }
    }
    on.then_some(o)
}

/// Remember the options; true when this is a selfcheck run.
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

fn record(name: &str, ok: bool, detail: Value) {
    CHECKS.lock().unwrap().push(json!({"name": name, "ok": ok, "detail": detail}));
}

/// RMS of an interleaved buffer.
pub fn rms(samples: &[f32]) -> f64 {
    if samples.is_empty() {
        return 0.0;
    }
    (samples.iter().map(|s| (*s as f64) * (*s as f64)).sum::<f64>() / samples.len() as f64).sqrt()
}

/// Fraction of pixels (4 bytes each) whose brightest colour channel is above `floor`.
pub fn lit_fraction(rgba: &[u8], floor: u8) -> f64 {
    let px = rgba.chunks_exact(4);
    let n = px.len();
    if n == 0 {
        return 0.0;
    }
    px.filter(|p| p[0].max(p[1]).max(p[2]) > floor).count() as f64 / n as f64
}

fn bgm_check(audio: String) {
    use singws_bgm_ffi::*;
    let mut err = [0 as std::os::raw::c_char; 256];
    unsafe {
        let abi = singws_bgm_abi_version();
        let rate = 48_000u32;
        let sys = singws_bgm_create_headless(rate);
        if sys.is_null() {
            return record("bgm_engine", false, json!({"abi": abi, "error": "singws_bgm_create_headless returned null"}));
        }
        let Ok(path) = CString::new(audio.clone()) else { return record("bgm_engine", false, json!({"error": "bad audio path"})) };
        let deck = singws_bgm_deck_open(sys, path.as_ptr(), err.as_mut_ptr(), err.len());
        if deck.is_null() {
            let msg = std::ffi::CStr::from_ptr(err.as_ptr()).to_string_lossy().into_owned();
            singws_bgm_destroy(sys);
            return record("bgm_engine", false, json!({"abi": abi, "error": format!("deck_open: {msg}"), "file": audio}));
        }
        let frames_total = singws_bgm_deck_len_frames(deck);
        singws_bgm_load(sys, deck, 1.0, 0, 1.0, 1);
        // pull ~5 s of audio in real time, as the sound card's callback would
        let block = 512u32;
        let mut buf = vec![0f32; 2 * block as usize];
        let (mut blocks, mut silent, mut sum_sq, mut n, mut peak) = (0u32, 0u32, 0f64, 0usize, 0f32);
        let t0 = Instant::now();
        while t0.elapsed() < Duration::from_secs(5) {
            if singws_bgm_render(sys, buf.as_mut_ptr(), block) != 1 {
                break;
            }
            let r = rms(&buf);
            if blocks > 20 && r < 1e-5 {
                silent += 1;
            }
            sum_sq += buf.iter().map(|s| (*s as f64).powi(2)).sum::<f64>();
            n += buf.len();
            peak = buf.iter().fold(peak, |p, s| p.max(s.abs()));
            blocks += 1;
            std::thread::sleep(Duration::from_secs_f64(block as f64 / rate as f64 * 0.9));
        }
        let (mut pos, mut dur) = (0f32, 0f32);
        singws_bgm_get_times(sys, &mut pos, &mut dur);
        let mut stats = [0u64; 16];
        let mut st32 = [0f32; 16];
        let _ = &mut st32;
        singws_bgm_stats(sys, stats.as_mut_ptr(), 16);
        let overall = if n > 0 { (sum_sq / n as f64).sqrt() } else { 0.0 };
        let ok = blocks > 100 && overall > 0.001 && pos > 3.0 && silent == 0;
        record(
            "bgm_engine",
            ok,
            json!({"abi": abi, "file": audio, "deck_frames": frames_total, "blocks": blocks, "rms": overall, "peak": peak, "silent_blocks_after_warmup": silent,
                   "position_s": pos, "duration_s": dur, "underruns": stats[11], "gap_frames": stats[12], "dropped_commands": stats[10], "headless": true}),
        );
        singws_bgm_destroy(sys);
    }
}

impl qobject::SelfCheck {
    fn start(self: Pin<&mut Self>) -> bool {
        let o = options();
        let output_view = native_id(c"SingWS spike output") as f64;
        let preview_view = native_id(c"SingWS spike preview") as f64;
        record("host", true, json!({
            "arch": std::env::consts::ARCH,
            "pid": std::process::id(),
            "exe": std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_default(),
            "singws_home": std::env::var("SINGWS_HOME").unwrap_or_default(),
            "output_view": output_view as u64, "preview_view": preview_view as u64,
            "output_view_state": ns_view_state(output_view as usize), "preview_view_state": ns_view_state(preview_view as usize),
        }));
        // libmpv is dlopen'ed by the bridge; record whether that worked on its own
        match singws_video::mpvapi::api() {
            Ok(_) => record("libmpv", true, json!({"env": std::env::var("SINGWS_LIBMPV").unwrap_or_default()})),
            Err(e) => record("libmpv", false, json!({"error": e})),
        }
        let audio = o.audio.clone();
        *BGM.lock().unwrap() = Some(std::thread::spawn(move || bgm_check(audio)));

        let (Ok(video), true) = (CString::new(o.video.clone()), output_view > 0.0 && preview_view > 0.0) else {
            record("video_bridge_create", false, json!({"error": "no video path or no window ids"}));
            return false;
        };
        let h = singws_video::singws_bridge_create(output_view as usize, preview_view as usize, video.as_ptr(), std::ptr::null());
        if h.is_null() {
            record("video_bridge_create", false, json!({"error": "singws_bridge_create returned null (views or libmpv)"}));
            return false;
        }
        *BRIDGE.lock().unwrap() = h as usize;
        singws_video::singws_bridge_play(h);
        record("video_bridge_create", true, json!({"video": o.video}));
        true
    }

    fn refresh(self: Pin<&mut Self>) {
        let h = *BRIDGE.lock().unwrap() as *mut c_void;
        if h.is_null() {
            return;
        }
        let out = native_id(c"SingWS spike output") as usize;
        let prev = native_id(c"SingWS spike preview") as usize;
        singws_video::singws_bridge_refresh_views(h, out, prev);
        record("video_bridge_refresh_views", true, json!({"present_count_before": singws_video::singws_bridge_present_count(h)}));
    }

    fn probe(self: Pin<&mut Self>) {
        let h = *BRIDGE.lock().unwrap() as *mut c_void;
        if h.is_null() {
            return;
        }
        let (mut w, mut hh, mut stride) = (0i32, 0i32, 0i32);
        let buf = singws_video::singws_bridge_grab_frame(h, &mut w, &mut hh, &mut stride);
        let lit = if buf.is_null() || w <= 0 || hh <= 0 {
            None
        } else {
            let bytes = unsafe { std::slice::from_raw_parts(buf as *const u8, (stride * hh) as usize) };
            let f = lit_fraction(bytes, 24);
            unsafe {
                unsafe extern "C" {
                    fn free(p: *mut c_void);
                }
                free(buf);
            }
            Some(f)
        };
        let pos = singws_video::singws_bridge_position(h);
        let ready = singws_video::singws_bridge_visual_ready(h) != 0;
        let presents = singws_video::singws_bridge_present_count(h);
        record(
            "video_bridge_playback",
            pos > 500 && ready && presents > 30,   // grab_frame is informational: mpv's screenshot cannot convert hardware-decoded (videotoolbox) frames
            json!({"position_ms": pos, "playing": singws_video::singws_bridge_is_playing(h) != 0, "visual_ready": ready,
                   "present_count": presents, "render_thread": singws_video::singws_bridge_render_thread_active(h) != 0,
                   "grab": {"w": w, "h": hh, "lit_fraction": lit}}),
        );
    }

    fn finish(self: Pin<&mut Self>) -> bool {
        let h = std::mem::take(&mut *BRIDGE.lock().unwrap()) as *mut c_void;
        if !h.is_null() {
            singws_video::singws_bridge_destroy(h);
            record("video_bridge_destroy", true, json!({}));
        }
        if let Some(t) = BGM.lock().unwrap().take() {
            if t.join().is_err() {
                record("bgm_engine", false, json!({"error": "BGM thread panicked"}));
            }
        }
        // item (e): the singws-mac platform shim, in-process. Read-only: no System Events script, nothing that can prompt.
        let t = Duration::from_secs(10);
        let script = singws_mac::run_applescript("return 1+1", t);
        record(
            "singws_mac",
            singws_mac::supported() && script.ok && script.out == "2",
            json!({"ax_is_trusted": singws_mac::ax_is_trusted(), "frontmost_app_name": singws_mac::frontmost_app_name(),
                   "applescript_1_plus_1": {"ok": script.ok, "out": script.out, "err": script.err}}),
        );
        let checks = CHECKS.lock().unwrap().clone();
        let doc = json!({"selfcheck": 1, "checks": checks});
        let path = options().report;
        let ok = !path.is_empty() && std::fs::write(&path, serde_json::to_string_pretty(&doc).unwrap()).is_ok();
        let _ = self; // the property is unused; the report file is the output
        ok
    }
}

#[derive(Default)]
pub struct SelfCheckRust {
    status: QString,
}

#[cfg(test)]
mod tests {
    use super::*;
    fn a(v: &[&str]) -> std::vec::IntoIter<String> {
        v.iter().map(|s| s.to_string()).collect::<Vec<_>>().into_iter()
    }
    #[test]
    fn parses_flags_and_values() {
        let o = parse_args(a(&["--selfcheck", "--selfcheck-report", "/r.json", "--selfcheck-video", "v.mp4", "--selfcheck-audio", "a.mp3"])).unwrap();
        assert_eq!(o, Options { report: "/r.json".into(), video: "v.mp4".into(), audio: "a.mp3".into() });
    }
    #[test]
    fn no_flag_is_not_a_selfcheck() {
        assert!(parse_args(a(&["--display-demo", "x.cdg"])).is_none());
        assert!(parse_args(a(&[])).is_none());
    }
    #[test]
    fn rms_of_silence_and_square_wave() {
        assert_eq!(rms(&[]), 0.0);
        assert_eq!(rms(&[0.0; 8]), 0.0);
        assert!((rms(&[0.5, -0.5, 0.5, -0.5]) - 0.5).abs() < 1e-9);
    }
    #[test]
    fn lit_fraction_counts_bright_pixels() {
        let px = [0, 0, 0, 255, 200, 0, 0, 255, 10, 10, 10, 255, 0, 250, 0, 255];
        assert!((lit_fraction(&px, 24) - 0.5).abs() < 1e-9);
        assert_eq!(lit_fraction(&[], 24), 0.0);
    }
}
