//! C ABI over `singws-bgm` (roadmap item 1, milestone 5). Python loads this with ctypes (`rust_bgm_engine.py`).
//!
//! Threading contract for the caller:
//! * every `singws_bgm_*` command/state function is cheap and non-blocking and may be called from any thread;
//! * `singws_bgm_deck_open` opens a file and waits for the first audio: call it from a worker thread, never the UI thread;
//! * a deck pointer is consumed by the command that takes it (`load`, `preload`); free an unused one with `deck_free`.
//! Every function catches panics and reports failure through its return value instead of unwinding into the caller.
#![deny(unsafe_op_in_unsafe_fn)]

use std::ffi::{CStr, CString, c_char, c_void};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::Path;
use std::sync::atomic::Ordering::Relaxed;
use std::sync::OnceLock;
use std::time::Duration;

use singws_bgm::engine::DeckSource;
use singws_bgm::pads::{Clip, MAX_PAD_SLOTS};
use singws_bgm::rt::{Command, FLAG_ENDED, FLAG_PAUSED, FLAG_PLAYING};
use singws_bgm::system::BgmSystem;
use singws_dsp::{PARAM_COUNT, PARAM_NAMES};

/// Bump when any exported signature or the stats layout changes; the Python adapter checks it.
pub const ABI_VERSION: u32 = 2;
/// Number of u64 values `singws_bgm_stats` writes.
pub const STAT_COUNT: usize = 16;

pub struct Sys(BgmSystem);
pub struct Deck(Box<dyn DeckSource>);
/// A decoded soundboard clip (shared with the engine once assigned to a pad slot).
pub struct ClipHandle(Clip);

fn guard<T>(default: T, f: impl FnOnce() -> T) -> T {
    catch_unwind(AssertUnwindSafe(f)).unwrap_or(default)
}

fn write_err(buf: *mut c_char, len: usize, msg: &str) {
    if buf.is_null() || len == 0 {
        return;
    }
    let bytes = msg.as_bytes();
    let n = bytes.len().min(len - 1);
    // SAFETY: the caller promises `buf` has room for `len` bytes.
    unsafe {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), buf as *mut u8, n);
        *buf.add(n) = 0;
    }
}

unsafe fn sys<'a>(p: *mut Sys) -> Option<&'a Sys> {
    // SAFETY: caller passes a pointer from `singws_bgm_create` that has not been destroyed.
    unsafe { p.as_ref() }
}

unsafe fn cstr(p: *const c_char) -> Option<String> {
    if p.is_null() {
        return None;
    }
    // SAFETY: caller passes a NUL-terminated string.
    unsafe { CStr::from_ptr(p) }.to_str().ok().map(|s| s.to_string())
}

#[unsafe(no_mangle)]
pub extern "C" fn singws_bgm_abi_version() -> u32 {
    ABI_VERSION
}

#[unsafe(no_mangle)]
pub extern "C" fn singws_bgm_stat_count() -> u32 {
    STAT_COUNT as u32
}

#[unsafe(no_mangle)]
pub extern "C" fn singws_bgm_master_param_count() -> u32 {
    PARAM_COUNT as u32
}

/// Comma-separated parameter names in the order `singws_bgm_set_master_params` expects. Static; never free it.
#[unsafe(no_mangle)]
pub extern "C" fn singws_bgm_master_param_names() -> *const c_char {
    static NAMES: OnceLock<CString> = OnceLock::new();
    NAMES.get_or_init(|| CString::new(PARAM_NAMES.join(",")).unwrap_or_default()).as_ptr()
}

/// Start an output. `device` NULL/empty = system default; a name that is not found falls back to the default (see `fell_back`).
/// Returns NULL on failure with a message in `err`.
///
/// # Safety
/// `device` must be NULL or NUL-terminated; `err` must point to `err_len` writable bytes (or be NULL).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_create(device: *const c_char, preferred_rate: u32, buffer_frames: u32, err: *mut c_char, err_len: usize) -> *mut Sys {
    guard(std::ptr::null_mut(), || {
        // SAFETY: forwarded from this function's contract.
        let dev = unsafe { cstr(device) };
        match BgmSystem::start(dev.as_deref(), preferred_rate.max(8000), if buffer_frames == 0 { None } else { Some(buffer_frames) }) {
            Ok(s) => Box::into_raw(Box::new(Sys(s))),
            Err(e) => {
                write_err(err, err_len, &e.to_string());
                std::ptr::null_mut()
            }
        }
    })
}

/// A system with no sound card, for tests and tools: audio is pulled with `singws_bgm_render`.
#[unsafe(no_mangle)]
pub extern "C" fn singws_bgm_create_headless(rate: u32) -> *mut Sys {
    guard(std::ptr::null_mut(), || Box::into_raw(Box::new(Sys(BgmSystem::headless(rate.max(8000))))))
}

/// Headless only: run queued commands and mix `frames` stereo frames into `out` (2 * frames floats). 1 on success.
///
/// # Safety
/// `p` is a live headless handle; `out` has room for `2 * frames` floats.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_render(p: *mut Sys, out: *mut f32, frames: u32) -> i32 {
    guard(0, || {
        // SAFETY: contract above.
        let Some(s) = (unsafe { sys(p) }) else { return 0 };
        if out.is_null() {
            return 0;
        }
        // SAFETY: the caller supplies 2 * frames writable floats.
        let buf = unsafe { std::slice::from_raw_parts_mut(out, frames as usize * 2) };
        s.0.render(buf) as i32
    })
}

/// # Safety
/// `p` must come from `singws_bgm_create` and not be used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_destroy(p: *mut Sys) {
    if p.is_null() {
        return;
    }
    // SAFETY: ownership returns from the caller. Dropping stops the stream.
    let boxed = unsafe { Box::from_raw(p) };
    let _ = catch_unwind(AssertUnwindSafe(move || {
        boxed.0.control.collect_garbage();
        drop(boxed);
    }));
}

/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_rate(p: *mut Sys) -> u32 {
    // SAFETY: contract above.
    unsafe { sys(p) }.map(|s| s.0.rate).unwrap_or(0)
}

/// # Safety
/// `p` is a live handle; `buf` has `len` writable bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_device_name(p: *mut Sys, buf: *mut c_char, len: usize) {
    // SAFETY: contract above.
    if let Some(s) = unsafe { sys(p) } {
        write_err(buf, len, &s.0.device_name);
    }
}

/// 1 when a named device was requested but not found (the default is in use).
///
/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_fell_back(p: *mut Sys) -> i32 {
    // SAFETY: contract above.
    unsafe { sys(p) }.map(|s| s.0.fell_back_to_default as i32).unwrap_or(0)
}

/// 1 when the stream reported the device is gone: the caller should destroy this and create a new one.
///
/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_device_lost(p: *mut Sys) -> i32 {
    // SAFETY: contract above.
    unsafe { sys(p) }.map(|s| s.0.device_lost() as i32).unwrap_or(1)
}

/// Open a file as a streaming deck at the output rate. Slow: worker thread only. NULL on failure.
///
/// # Safety
/// `p` is a live handle; `path` is NUL-terminated UTF-8; `err` has `err_len` writable bytes (or is NULL).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_deck_open(p: *mut Sys, path: *const c_char, err: *mut c_char, err_len: usize) -> *mut Deck {
    guard(std::ptr::null_mut(), || {
        // SAFETY: contract above.
        let (Some(s), Some(path)) = (unsafe { sys(p) }, unsafe { cstr(path) }) else {
            write_err(err, err_len, "bad handle or path");
            return std::ptr::null_mut();
        };
        match s.0.open_deck(Path::new(&path)) {
            Ok(d) => Box::into_raw(Box::new(Deck(d))),
            Err(e) => {
                write_err(err, err_len, &e.to_string());
                std::ptr::null_mut()
            }
        }
    })
}

/// Total frames if known, else -1.
///
/// # Safety
/// `d` is a live deck pointer.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_deck_len_frames(d: *mut Deck) -> i64 {
    // SAFETY: contract above.
    unsafe { d.as_ref() }.and_then(|d| d.0.len_frames()).map(|n| n as i64).unwrap_or(-1)
}

/// Free a deck that was never handed to `load`/`preload`.
///
/// # Safety
/// `d` is a deck pointer not yet consumed.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_deck_free(d: *mut Deck) {
    if !d.is_null() {
        // SAFETY: ownership returns from the caller.
        let _ = catch_unwind(AssertUnwindSafe(|| drop(unsafe { Box::from_raw(d) })));
    }
}

fn send(p: *mut Sys, c: impl FnOnce() -> Command) -> i32 {
    guard(0, || {
        // SAFETY: callers of the exported wrappers promise a live handle.
        match unsafe { sys(p) } {
            Some(s) => s.0.control.send(c()) as i32,
            None => 0,
        }
    })
}

macro_rules! simple_cmd {
    ($name:ident, $cmd:expr) => {
        /// # Safety
        /// `p` is a live handle.
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn $name(p: *mut Sys) -> i32 {
            send(p, || $cmd)
        }
    };
}
simple_cmd!(singws_bgm_play, Command::Play);
simple_cmd!(singws_bgm_pause, Command::Pause);
simple_cmd!(singws_bgm_stop, Command::Stop);
simple_cmd!(singws_bgm_invalidate_preload, Command::InvalidatePreload);
simple_cmd!(singws_bgm_complete_crossfade, Command::CompleteCrossfade);
simple_cmd!(singws_bgm_cancel_crossfade, Command::CancelCrossfade);

/// Replace everything with one track. Consumes `deck`. `has_volume` 0 keeps the current master volume.
///
/// # Safety
/// `p` live handle; `deck` an unconsumed deck pointer.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_load(p: *mut Sys, deck: *mut Deck, norm: f32, paused: i32, volume: f32, has_volume: i32) -> i32 {
    if deck.is_null() {
        return 0;
    }
    // SAFETY: ownership of the deck moves into the command.
    let d = unsafe { Box::from_raw(deck) };
    send(p, move || Command::Load { deck: d.0, norm, paused: paused != 0, volume: if has_volume != 0 { Some(volume) } else { None } })
}

/// Hold the next track paused and silent. Consumes `deck`.
///
/// # Safety
/// `p` live handle; `deck` an unconsumed deck pointer.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_preload(p: *mut Sys, deck: *mut Deck, norm: f32) -> i32 {
    if deck.is_null() {
        return 0;
    }
    // SAFETY: ownership of the deck moves into the command.
    let d = unsafe { Box::from_raw(deck) };
    send(p, move || Command::Preload { deck: d.0, norm })
}

macro_rules! f32_cmd {
    ($name:ident, $variant:ident) => {
        /// # Safety
        /// `p` is a live handle.
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn $name(p: *mut Sys, v: f32) -> i32 {
            send(p, || Command::$variant(v))
        }
    };
}
f32_cmd!(singws_bgm_set_master_volume, SetMasterVolume);
f32_cmd!(singws_bgm_set_primary_norm, SetPrimaryNorm);
f32_cmd!(singws_bgm_set_secondary_norm, SetSecondaryNorm);
f32_cmd!(singws_bgm_seek, Seek);

/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_slide_master_volume(p: *mut Sys, volume: f32, ms: u32) -> i32 {
    send(p, || Command::SlideMasterVolume { volume, ms })
}

/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_start_crossfade(p: *mut Sys, ms: u32, start_seconds: f32) -> i32 {
    send(p, || Command::StartCrossfade { ms, start_seconds })
}

/// Wait until every command sent so far has run (or the timeout passes). 1 when they have.
///
/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_flush(p: *mut Sys, timeout_ms: u32) -> i32 {
    guard(0, || {
        // SAFETY: contract above.
        unsafe { sys(p) }.map(|s| s.0.control.flush(Duration::from_millis(timeout_ms as u64)) as i32).unwrap_or(0)
    })
}

/// Free decks the audio thread has retired. Call now and then from any non-realtime thread.
///
/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_collect_garbage(p: *mut Sys) -> u32 {
    // SAFETY: contract above.
    guard(0, || unsafe { sys(p) }.map(|s| s.0.control.collect_garbage() as u32).unwrap_or(0))
}

/// Audible position and duration of the playing track, seconds.
///
/// # Safety
/// `p` is a live handle; `pos` and `dur` are writable.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_get_times(p: *mut Sys, pos: *mut f32, dur: *mut f32) {
    // SAFETY: contract above.
    if let Some(s) = unsafe { sys(p) } {
        let (a, b) = s.0.control.snapshot.times();
        // SAFETY: the caller supplies writable pointers.
        unsafe {
            if !pos.is_null() {
                *pos = a;
            }
            if !dur.is_null() {
                *dur = b;
            }
        }
    }
}

/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_meter(p: *mut Sys) -> f32 {
    // SAFETY: contract above.
    unsafe { sys(p) }.map(|s| s.0.control.snapshot.meter_level()).unwrap_or(0.0)
}

/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_engine_master_volume(p: *mut Sys) -> f32 {
    // SAFETY: contract above.
    unsafe { sys(p) }.map(|s| s.0.control.snapshot.master_volume()).unwrap_or(0.0)
}

/// Bit set of the `FLAG_*` constants in `singws_bgm::rt` (playing 1, paused 2, ended 4, crossfade active 8, crossfade finished 16,
/// has primary 32, has secondary 64).
///
/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_flags(p: *mut Sys) -> u32 {
    // SAFETY: contract above.
    unsafe { sys(p) }.map(|s| s.0.control.snapshot.flags.load(Relaxed)).unwrap_or(0)
}

/// # Safety
/// `p` is a live handle; `gains` points at 10 floats (dB).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_set_eq(p: *mut Sys, enabled: i32, gains: *const f32) -> i32 {
    guard(0, || {
        // SAFETY: contract above.
        let Some(s) = (unsafe { sys(p) }) else { return 0 };
        if !gains.is_null() {
            let mut g = [0.0f32; 10];
            // SAFETY: the caller promises 10 floats.
            g.copy_from_slice(unsafe { std::slice::from_raw_parts(gains, 10) });
            s.0.eq.set_gains_db(&g);
        }
        s.0.eq.set_enabled(enabled != 0);
        1
    })
}

/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_set_master_enabled(p: *mut Sys, enabled: i32) {
    // SAFETY: contract above.
    if let Some(s) = unsafe { sys(p) } {
        s.0.master.set_enabled(enabled != 0);
    }
}

/// Replace all master-chain parameters; `n` must equal `singws_bgm_master_param_count()`. 0 on success, -1 otherwise.
///
/// # Safety
/// `p` is a live handle; `values` points at `n` doubles in `PARAM_NAMES` order.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_set_master_params(p: *mut Sys, values: *const f64, n: u32) -> i32 {
    guard(-1, || {
        // SAFETY: contract above.
        let Some(s) = (unsafe { sys(p) }) else { return -1 };
        if values.is_null() || n as usize != PARAM_COUNT {
            return -1;
        }
        let mut v = [0.0f64; PARAM_COUNT];
        // SAFETY: `n` doubles are readable.
        v.copy_from_slice(unsafe { std::slice::from_raw_parts(values, PARAM_COUNT) });
        s.0.master.set_params(&v);
        0
    })
}

// ---- soundboard pads (ABI 2) --------------------------------------------------------------------------------------------------------

/// Decode a clip to memory at the output rate (any format the decoder reads, AAC/M4A included). Slow: worker thread. NULL on failure (or when the
/// clip is longer than 300 s) with a message in `err`.
///
/// # Safety
/// `p` is a live handle; `path` is NUL-terminated UTF-8; `err` has `err_len` writable bytes (or is NULL).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_clip_open(p: *mut Sys, path: *const c_char, err: *mut c_char, err_len: usize) -> *mut ClipHandle {
    guard(std::ptr::null_mut(), || {
        // SAFETY: contract above.
        let (Some(s), Some(path)) = (unsafe { sys(p) }, unsafe { cstr(path) }) else {
            write_err(err, err_len, "bad handle or path");
            return std::ptr::null_mut();
        };
        match s.0.load_clip(Path::new(&path)) {
            Ok(c) => Box::into_raw(Box::new(ClipHandle(c))),
            Err(e) => {
                write_err(err, err_len, &e.to_string());
                std::ptr::null_mut()
            }
        }
    })
}

/// Length of a decoded clip in frames.
///
/// # Safety
/// `c` is a live clip handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_clip_frames(c: *mut ClipHandle) -> i64 {
    // SAFETY: contract above.
    unsafe { c.as_ref() }.map(|c| (c.0.len() / 2) as i64).unwrap_or(-1)
}

/// Free a clip handle. The engine keeps its own reference to a clip assigned to a pad, so this is always safe to call.
///
/// # Safety
/// `c` is a clip handle from `singws_bgm_clip_open`, not used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_clip_free(c: *mut ClipHandle) {
    if !c.is_null() {
        // SAFETY: ownership returns from the caller. Dropping a reference is cheap; a freed last reference here is on the caller's thread.
        let _ = catch_unwind(AssertUnwindSafe(|| drop(unsafe { Box::from_raw(c) })));
    }
}

/// Assign a clip to a pad slot (0..63), or clear the slot with a NULL clip. Instances already playing keep the clip they started with.
///
/// # Safety
/// `p` is a live handle; `c` is NULL or a live clip handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_pad_set_clip(p: *mut Sys, slot: u32, c: *mut ClipHandle) -> i32 {
    if slot as usize >= MAX_PAD_SLOTS {
        return 0;
    }
    // SAFETY: contract above.
    let clip = unsafe { c.as_ref() }.map(|c| c.0.clone());
    send(p, move || Command::PadSetClip { slot: slot as u8, clip })
}

/// Start another instance of the slot's clip at `start_frame` (instances layer).
///
/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_pad_play(p: *mut Sys, slot: u32, start_frame: u64) -> i32 {
    if slot as usize >= MAX_PAD_SLOTS {
        return 0;
    }
    send(p, || Command::PadPlay { slot: slot as u8, start_frame })
}

/// Stop every instance of the slot.
///
/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_pad_stop(p: *mut Sys, slot: u32) -> i32 {
    if slot as usize >= MAX_PAD_SLOTS {
        return 0;
    }
    send(p, || Command::PadStop { slot: slot as u8 })
}

/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_pad_stop_all(p: *mut Sys) -> i32 {
    send(p, || Command::PadStopAll)
}

/// One pad's own volume (0..1).
///
/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_pad_set_gain(p: *mut Sys, slot: u32, gain: f32) -> i32 {
    if slot as usize >= MAX_PAD_SLOTS {
        return 0;
    }
    send(p, || Command::PadSetGain { slot: slot as u8, gain })
}

/// The soundboard volume (0..1): scales every pad, independent of the music volume.
///
/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_pad_set_master(p: *mut Sys, gain: f32) -> i32 {
    send(p, || Command::PadSetMaster(gain))
}

/// Instances of the slot that are sounding (as of the last audio block).
///
/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_pad_playing(p: *mut Sys, slot: u32) -> u32 {
    // SAFETY: contract above.
    unsafe { sys(p) }
        .and_then(|s| s.0.control.snapshot.pad_playing.get(slot as usize).map(|a| a.load(Relaxed)))
        .unwrap_or(0)
}

/// Position in frames of the newest instance of the slot (0 when none is sounding).
///
/// # Safety
/// `p` is a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_pad_position_frames(p: *mut Sys, slot: u32) -> u64 {
    // SAFETY: contract above.
    unsafe { sys(p) }
        .and_then(|s| s.0.control.snapshot.pad_position.get(slot as usize).map(|a| a.load(Relaxed)))
        .unwrap_or(0)
}

/// Fill `out[..n]` with: callbacks, max callback us, total callback us, over-budget callbacks, driver latency us, buffer frames,
/// xruns, route changes, other errors, device lost, commands dropped, deck underruns, gap frames, seek-pending frames, blocks,
/// reserved (16 values).
///
/// # Safety
/// `p` is a live handle; `out` has room for `n` u64.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_bgm_stats(p: *mut Sys, out: *mut u64, n: i32) {
    // SAFETY: contract above.
    let Some(s) = (unsafe { sys(p) }) else { return };
    if out.is_null() {
        return;
    }
    let st = s.0.stats();
    let snap = &s.0.control.snapshot;
    let vals: [u64; STAT_COUNT] = [
        st.callbacks.load(Relaxed),
        st.max_callback_us.load(Relaxed),
        st.total_callback_us.load(Relaxed),
        st.over_budget.load(Relaxed),
        st.latency_us.load(Relaxed),
        st.buffer_frames.load(Relaxed),
        st.xruns.load(Relaxed),
        st.route_changes.load(Relaxed),
        st.other_errors.load(Relaxed),
        st.device_lost.load(Relaxed) as u64,
        snap.commands_dropped.load(Relaxed),
        s.0.deck_underruns.load(Relaxed),
        snap.gap_frames.load(Relaxed),
        snap.pending_frames.load(Relaxed),
        snap.blocks.load(Relaxed),
        0,
    ];
    for (i, v) in vals.iter().enumerate().take(n.max(0) as usize) {
        // SAFETY: `out` has room for `n` values.
        unsafe { *out.add(i) = *v };
    }
}

// Re-exported so the flags are visible to tests of this crate.
pub const FLAGS: (u32, u32, u32) = (FLAG_PLAYING, FLAG_PAUSED, FLAG_ENDED);

#[allow(dead_code)]
fn _assert_c_void_used(_: *mut c_void) {}
