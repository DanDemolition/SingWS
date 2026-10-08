//! C ABI over `singws-dsp` (Stage 2 of RUST_MIGRATION_PLAN.md).
//!
//! The point of this crate: `singws_master_dsp_proc` has exactly BASS's `DSPPROC` signature, so Python hands BASS
//! *this function's address* and a pointer to a `Master` as the `user` argument, and the audio thread never runs
//! Python. Configuration (parameters, stream format, reset) arrives from the GUI thread through a tiny side
//! channel that the audio thread only ever `try_lock`s, so neither side can make the other wait.
//!
//! Lifetime rule for the caller: remove the DSP from BASS (`BASS_ChannelRemoveDSP`) before `singws_master_destroy`.
#![deny(unsafe_op_in_unsafe_fn)]

use std::ffi::c_void;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::time::Instant;

use std::ffi::{CString, c_char};
use std::sync::OnceLock;

use singws_dsp::{MasterProcessor, PARAM_COUNT, PARAM_NAMES};

/// Bump when any exported signature or the stats layout changes; the Python wrapper checks it.
pub const ABI_VERSION: u32 = 1;
/// Number of u64 values `singws_master_stats` writes.
pub const STAT_COUNT: usize = 8 + HIST_BUCKETS;
const HIST_BUCKETS: usize = 9;
/// Upper edges (ns) of the per-block processing-time histogram; the last bucket is "slower than all of these".
const HIST_EDGES_NS: [u64; HIST_BUCKETS - 1] = [100_000, 250_000, 500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000, 25_000_000];
const RESTART_GAP_NS: u64 = 2_000_000_000;

struct Pending {
    params: Option<[f64; PARAM_COUNT]>,
    stream: Option<(f64, usize)>,
    reset: bool,
}

pub struct Master {
    inner: Mutex<MasterProcessor>,
    pending: Mutex<Pending>,
    has_pending: AtomicBool,
    enabled: AtomicBool,
    sample_rate_bits: AtomicU64,
    epoch: Instant,
    gain_db_bits: [AtomicU64; 3],
    // timing counters (see STAT_COUNT)
    blocks: AtomicU64,
    total_ns: AtomicU64,
    max_ns: AtomicU64,
    over_budget: AtomicU64,
    gap_max_ns: AtomicU64,
    gaps_100ms: AtomicU64,
    gaps_500ms: AtomicU64,
    restarts: AtomicU64,
    hist: [AtomicU64; HIST_BUCKETS],
    last_start_ns: AtomicU64,
}

impl Master {
    fn new(sample_rate: f64, channels: usize) -> Self {
        Self {
            inner: Mutex::new(MasterProcessor::new(sample_rate, channels)),
            pending: Mutex::new(Pending { params: None, stream: None, reset: false }),
            has_pending: AtomicBool::new(false),
            enabled: AtomicBool::new(false),
            sample_rate_bits: AtomicU64::new(sample_rate.to_bits()),
            epoch: Instant::now(),
            gain_db_bits: [AtomicU64::new(0f64.to_bits()), AtomicU64::new(0f64.to_bits()), AtomicU64::new(0f64.to_bits())],
            blocks: AtomicU64::new(0),
            total_ns: AtomicU64::new(0),
            max_ns: AtomicU64::new(0),
            over_budget: AtomicU64::new(0),
            gap_max_ns: AtomicU64::new(0),
            gaps_100ms: AtomicU64::new(0),
            gaps_500ms: AtomicU64::new(0),
            restarts: AtomicU64::new(0),
            hist: std::array::from_fn(|_| AtomicU64::new(0)),
            last_start_ns: AtomicU64::new(u64::MAX),
        }
    }

    /// The audio-thread entry. Never blocks: if the processor is momentarily held by another caller the block is
    /// left unprocessed rather than waited for.
    fn run(&self, buf: &mut [f32]) {
        let Ok(mut inner) = self.inner.try_lock() else { return };
        if self.has_pending.swap(false, Relaxed) {
            match self.pending.try_lock() {
                Ok(mut p) => {
                    if let Some(v) = p.params.take() {
                        inner.set_params(&v);
                    }
                    if let Some((sr, ch)) = p.stream.take() {
                        inner.configure_stream(sr, ch);
                    }
                    if p.reset {
                        p.reset = false;
                        inner.reset_state();
                    }
                }
                Err(_) => self.has_pending.store(true, Relaxed),
            }
        }
        if !self.enabled.load(Relaxed) {
            return;
        }
        let start = Instant::now();
        inner.process_interleaved(buf);
        let end = Instant::now();
        let gr = inner.gain_reduction_db();
        drop(inner);
        for (slot, v) in self.gain_db_bits.iter().zip(gr) {
            slot.store(v.to_bits(), Relaxed);
        }
        self.record(start, end, buf.len());
    }

    fn record(&self, start: Instant, end: Instant, floats: usize) {
        let dur = end.duration_since(start).as_nanos() as u64;
        self.blocks.fetch_add(1, Relaxed);
        self.total_ns.fetch_add(dur, Relaxed);
        self.max_ns.fetch_max(dur, Relaxed);
        let rate = f64::from_bits(self.sample_rate_bits.load(Relaxed)).max(1.0);
        // Interleaved stereo assumed for the budget: a block is `floats / 2` frames long.
        let block_ns = (floats as f64 / 2.0 / rate * 1e9) as u64;
        if block_ns > 0 && dur * 2 > block_ns {
            self.over_budget.fetch_add(1, Relaxed);
        }
        let idx = HIST_EDGES_NS.iter().position(|&e| dur <= e).unwrap_or(HIST_BUCKETS - 1);
        self.hist[idx].fetch_add(1, Relaxed);
        let now = start.duration_since(self.epoch).as_nanos() as u64;
        let prev = self.last_start_ns.swap(now, Relaxed);
        if prev != u64::MAX {
            let gap = now.saturating_sub(prev);
            if gap > RESTART_GAP_NS {
                self.restarts.fetch_add(1, Relaxed);
            } else {
                self.gap_max_ns.fetch_max(gap, Relaxed);
                if gap > 100_000_000 {
                    self.gaps_100ms.fetch_add(1, Relaxed);
                }
                if gap > 500_000_000 {
                    self.gaps_500ms.fetch_add(1, Relaxed);
                }
            }
        }
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn singws_dsp_abi_version() -> u32 {
    ABI_VERSION
}

#[unsafe(no_mangle)]
pub extern "C" fn singws_master_param_count() -> u32 {
    PARAM_COUNT as u32
}

/// Comma-separated parameter names in the order `singws_master_set_params` expects, so the caller can check
/// them against `DEFAULT_PARAMS` and refuse to run on a mismatch. The pointer is valid for the life of the library.
#[unsafe(no_mangle)]
pub extern "C" fn singws_master_param_names() -> *const c_char {
    static NAMES: OnceLock<CString> = OnceLock::new();
    NAMES.get_or_init(|| CString::new(PARAM_NAMES.join(",")).unwrap_or_default()).as_ptr()
}

#[unsafe(no_mangle)]
pub extern "C" fn singws_master_stat_count() -> u32 {
    STAT_COUNT as u32
}

#[unsafe(no_mangle)]
pub extern "C" fn singws_master_create(sample_rate: f64, channels: u32) -> *mut c_void {
    catch_unwind(|| Box::into_raw(Box::new(Master::new(sample_rate, channels as usize))) as *mut c_void)
        .unwrap_or(std::ptr::null_mut())
}

/// # Safety
/// `h` must come from `singws_master_create`, must not be used afterwards, and no audio thread may still be inside the callback.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_master_destroy(h: *mut c_void) {
    if !h.is_null() {
        drop(unsafe { Box::from_raw(h as *mut Master) });
    }
}

fn master<'a>(h: *mut c_void) -> Option<&'a Master> {
    if h.is_null() { None } else { Some(unsafe { &*(h as *const Master) }) }
}

/// Replace all parameters (in `DEFAULT_PARAMS` order). Returns 0 on success, -1 on a bad pointer or count.
///
/// # Safety
/// `values` must point to `count` readable f64s.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_master_set_params(h: *mut c_void, values: *const f64, count: u32) -> i32 {
    let (Some(m), false) = (master(h), values.is_null()) else { return -1 };
    if count as usize != PARAM_COUNT {
        return -1;
    }
    let mut arr = [0.0f64; PARAM_COUNT];
    arr.copy_from_slice(unsafe { std::slice::from_raw_parts(values, PARAM_COUNT) });
    let Ok(mut p) = m.pending.lock() else { return -1 };
    p.params = Some(arr);
    m.has_pending.store(true, Relaxed);
    0
}

#[unsafe(no_mangle)]
pub extern "C" fn singws_master_set_enabled(h: *mut c_void, enabled: i32) {
    if let Some(m) = master(h) {
        m.enabled.store(enabled != 0, Relaxed);
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn singws_master_configure(h: *mut c_void, sample_rate: f64, channels: u32) {
    if let Some(m) = master(h) {
        m.sample_rate_bits.store(sample_rate.to_bits(), Relaxed);
        if let Ok(mut p) = m.pending.lock() {
            p.stream = Some((sample_rate, channels as usize));
            m.has_pending.store(true, Relaxed);
        }
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn singws_master_reset(h: *mut c_void) {
    if let Some(m) = master(h) {
        if let Ok(mut p) = m.pending.lock() {
            p.reset = true;
            m.has_pending.store(true, Relaxed);
        }
    }
}

/// Process `floats` interleaved f32 samples in place (tests and the Python fallback; the audio thread uses the DSPPROC below).
///
/// # Safety
/// `buf` must point to `floats` writable f32s.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_master_process_f32(h: *mut c_void, buf: *mut f32, floats: u32) -> i32 {
    let (Some(m), false) = (master(h), buf.is_null()) else { return -1 };
    let slice = unsafe { std::slice::from_raw_parts_mut(buf, floats as usize) };
    match catch_unwind(AssertUnwindSafe(|| m.run(slice))) {
        Ok(()) => 0,
        Err(_) => -2,
    }
}

/// Write gate, compressor and limiter gain (dB) as of the last processed block into `out[3]`.
///
/// # Safety
/// `out` must point to 3 writable f64s.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_master_gain_reduction(h: *mut c_void, out: *mut f64) {
    if let (Some(m), false) = (master(h), out.is_null()) {
        for i in 0..3 {
            unsafe { *out.add(i) = f64::from_bits(m.gain_db_bits[i].load(Relaxed)) };
        }
    }
}

/// Copy the timing counters into `out[STAT_COUNT]` and (if `reset != 0`) zero them. Layout:
/// blocks, total_ns, max_ns, over_budget, gap_max_ns, gaps_over_100ms, gaps_over_500ms, restarts, then the histogram buckets.
///
/// # Safety
/// `out` must point to `STAT_COUNT` writable u64s.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_master_stats(h: *mut c_void, out: *mut u64, reset: i32) {
    let (Some(m), false) = (master(h), out.is_null()) else { return };
    let take = |a: &AtomicU64| if reset != 0 { a.swap(0, Relaxed) } else { a.load(Relaxed) };
    let mut values = [0u64; STAT_COUNT];
    values[0] = take(&m.blocks);
    values[1] = take(&m.total_ns);
    values[2] = take(&m.max_ns);
    values[3] = take(&m.over_budget);
    values[4] = take(&m.gap_max_ns);
    values[5] = take(&m.gaps_100ms);
    values[6] = take(&m.gaps_500ms);
    values[7] = take(&m.restarts);
    for (i, slot) in m.hist.iter().enumerate() {
        values[8 + i] = take(slot);
    }
    unsafe { std::ptr::copy_nonoverlapping(values.as_ptr(), out, STAT_COUNT) };
}

/// BASS `DSPPROC`: `void CALLBACK (HDSP handle, DWORD channel, void *buffer, DWORD length, void *user)`.
/// `user` is the pointer from `singws_master_create`; `length` is in bytes of interleaved f32.
///
/// # Safety
/// Called by BASS on its audio thread with a valid buffer and the `user` pointer given to `BASS_ChannelSetDSP`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn singws_master_dsp_proc(_handle: u32, _channel: u32, buffer: *mut c_void, length: u32, user: *mut c_void) {
    if buffer.is_null() || length == 0 {
        return;
    }
    let Some(m) = master(user) else { return };
    let floats = (length / 4) as usize;
    let slice = unsafe { std::slice::from_raw_parts_mut(buffer as *mut f32, floats) };
    // A panic must never unwind into BASS's thread.
    let _ = catch_unwind(AssertUnwindSafe(|| m.run(slice)));
}
