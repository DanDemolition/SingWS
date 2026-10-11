//! Rust replacement for the Objective-C mpv bridge (roadmap item 2), exporting the SAME `singws_bridge_*` C ABI so
//! `mpv_playback_iina.py` needs no change. See RUST_VIDEO_BRIDGE_PLAN.md.
//!
//! V2 (this milestone): the control plane runs headless (`vo=null`). Every function that needs a picture surface (views, background
//! video, transitions, rotation hosts, frame grab) exists with the right signature but is a documented no-op until V3-V5; the
//! Python side already tolerates that (it checks `visual_ready` and the optional exports).
pub mod audio_device;
pub mod core;
pub mod log;
pub mod mpvapi;
pub mod scanner;

use crate::core::Player;
use std::ffi::{c_char, c_double, c_int, c_void, CStr};
use std::sync::atomic::{AtomicBool, Ordering};

fn text(p: *const c_char) -> Option<String> {
    if p.is_null() {
        None
    } else {
        Some(unsafe { CStr::from_ptr(p) }.to_string_lossy().into_owned())
    }
}

fn player<'a>(h: *mut c_void) -> Option<&'a Player> {
    if h.is_null() {
        None
    } else {
        Some(unsafe { &*(h as *const Player) })
    }
}

static RENDER_THREAD_REQUESTED: AtomicBool = AtomicBool::new(false);

// ---- lifecycle ---------------------------------------------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn singws_bridge_set_log_callback(cb: Option<log::LogFn>) {
    log::set_sink(cb);
}
#[no_mangle]
pub extern "C" fn singws_bridge_set_render_thread(enabled: c_int) {
    RENDER_THREAD_REQUESTED.store(enabled != 0, Ordering::SeqCst);
}
#[no_mangle]
pub extern "C" fn singws_bridge_render_thread_active(_h: *mut c_void) -> c_int {
    0 // V5
}
#[no_mangle]
pub extern "C" fn singws_bridge_present_count(_h: *mut c_void) -> u64 {
    0 // V3
}

/// `outputView` / `previewView` are ignored until V3 (no picture surface yet).
#[no_mangle]
pub extern "C" fn singws_bridge_create(_output_view: usize, _preview_view: usize, video: *const c_char, audio: *const c_char) -> *mut c_void {
    let (Some(v), a) = (text(video), text(audio).filter(|s| !s.is_empty())) else { return std::ptr::null_mut() };
    match Player::create(&v, a.as_deref()) {
        Some(p) => Box::into_raw(p) as *mut c_void,
        None => std::ptr::null_mut(),
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_load(h: *mut c_void, video: *const c_char, audio: *const c_char) -> c_int {
    let (Some(p), Some(v)) = (player(h), text(video)) else { return 0 };
    if v.is_empty() {
        return 0;
    }
    p.load(&v, text(audio).filter(|s| !s.is_empty()).as_deref()) as c_int
}
#[no_mangle]
pub extern "C" fn singws_bridge_destroy(h: *mut c_void) {
    if !h.is_null() {
        drop(unsafe { Box::from_raw(h as *mut Player) });
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_refresh_views(_h: *mut c_void, _output_view: usize, _preview_view: usize) {}

// ---- transport ---------------------------------------------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn singws_bridge_play(h: *mut c_void) {
    if let Some(p) = player(h) {
        p.set_paused(false)
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_pause(h: *mut c_void) {
    if let Some(p) = player(h) {
        p.set_paused(true)
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_stop(h: *mut c_void) {
    if let Some(p) = player(h) {
        p.stop()
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_seek(h: *mut c_void, ms: i64) {
    if let Some(p) = player(h) {
        p.seek_ms(ms)
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_position(h: *mut c_void) -> i64 {
    player(h).map(|p| p.position_ms()).unwrap_or(0)
}
#[no_mangle]
pub extern "C" fn singws_bridge_duration(h: *mut c_void) -> i64 {
    player(h).map(|p| p.duration_ms()).unwrap_or(0)
}
#[no_mangle]
pub extern "C" fn singws_bridge_is_playing(h: *mut c_void) -> c_int {
    player(h).map(|p| p.is_playing() as c_int).unwrap_or(0)
}
#[no_mangle]
pub extern "C" fn singws_bridge_at_end(h: *mut c_void) -> c_int {
    player(h).map(|p| p.at_end() as c_int).unwrap_or(0)
}
#[no_mangle]
pub extern "C" fn singws_bridge_visual_ready(h: *mut c_void) -> c_int {
    player(h).map(|p| p.visual_ready() as c_int).unwrap_or(0)
}

// ---- audio -------------------------------------------------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn singws_bridge_set_volume(h: *mut c_void, v: c_double) {
    if let Some(p) = player(h) {
        p.set_volume_percent(v)
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_set_device(h: *mut c_void, name: *const c_char) {
    if let Some(p) = player(h) {
        p.set_audio_device(&text(name).unwrap_or_default())
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_set_tempo(h: *mut c_void, percent: c_int) {
    if let Some(p) = player(h) {
        p.set_tempo_percent(percent)
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_set_key(h: *mut c_void, semitones: c_int) {
    if let Some(p) = player(h) {
        p.set_semitones(semitones)
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_set_dsp_chain(h: *mut c_void, chain: *const c_char) {
    if let Some(p) = player(h) {
        p.set_dsp_chain(&text(chain).unwrap_or_default())
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_set_audio_delay(h: *mut c_void, seconds: c_double) {
    if let Some(p) = player(h) {
        p.set_audio_delay(seconds)
    }
}

// ---- picture surface (V3-V5: not implemented yet; see the module comment) --------------------------------------------------

#[no_mangle]
pub extern "C" fn singws_bridge_set_sidefill(_h: *mut c_void, _mode: c_int) {}
#[no_mangle]
pub extern "C" fn singws_bridge_begin_transition(_h: *mut c_void, _ms: c_int) {}
#[no_mangle]
pub extern "C" fn singws_bridge_load_background(_h: *mut c_void, _path: *const c_char, _opacity: c_double) -> c_int {
    0
}
#[no_mangle]
pub extern "C" fn singws_bridge_background_at_end(_h: *mut c_void) -> c_int {
    0
}
#[no_mangle]
pub extern "C" fn singws_bridge_background_position(_h: *mut c_void) -> i64 {
    0
}
#[no_mangle]
pub extern "C" fn singws_bridge_background_paused(_h: *mut c_void) -> c_int {
    0
}
#[no_mangle]
pub extern "C" fn singws_bridge_set_background_opacity(_h: *mut c_void, _opacity: c_double) {}
#[no_mangle]
pub extern "C" fn singws_bridge_stop_background(_h: *mut c_void) {}
#[no_mangle]
pub extern "C" fn singws_bridge_set_rotation_host(_h: *mut c_void, _host: usize, _enabled: c_int) {}
#[no_mangle]
pub extern "C" fn singws_bridge_set_rotation_spotlight(_h: *mut c_void, _host: usize, _enabled: c_int) {}
#[no_mangle]
pub extern "C" fn singws_bridge_grab_frame(_h: *mut c_void, _w: *mut c_int, _hh: *mut c_int, _stride: *mut c_int) -> *mut c_void {
    std::ptr::null_mut()
}
#[no_mangle]
pub extern "C" fn singws_bridge_free_frame(buf: *mut c_void) {
    extern "C" {
        fn free(p: *mut c_void);
    }
    if !buf.is_null() {
        unsafe { free(buf) }
    }
}

// ---- analysis ----------------------------------------------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn singws_bridge_scan_silence(
    path: *const c_char,
    noise_db: c_double,
    lead_minimum: c_double,
    trail_minimum: c_double,
    lead: *mut c_double,
    trail: *mut c_double,
    duration: *mut c_double,
) -> c_int {
    unsafe {
        if !lead.is_null() {
            *lead = 0.0;
        }
        if !trail.is_null() {
            *trail = 0.0;
        }
        if !duration.is_null() {
            *duration = 0.0;
        }
    }
    if lead.is_null() || trail.is_null() || duration.is_null() {
        return 0;
    }
    let Some(p) = text(path) else { return 0 };
    match scanner::scan_silence(&p, noise_db, lead_minimum, trail_minimum) {
        Some((l, t, d)) => {
            unsafe {
                *lead = l;
                *trail = t;
                *duration = d;
            }
            1
        }
        None => 0,
    }
}
#[no_mangle]
pub extern "C" fn singws_bridge_scanner_shutdown() {
    scanner::shutdown();
}
