//! The pure cores of `phrase_detect.detect_lead_silence`, `detect_trailing_silence` and `audible_through_end` (numpy over decoded 16 kHz stereo PCM). The decoding itself is
//! the caller's (libmpv in the app, symphonia here); these functions take the interleaved samples (int16 / 32768 as float32, like the Python) and answer exactly what the numpy
//! code answers: per-100 ms-window RMS of each channel, the louder channel's dBFS, thresholds, and the adaptive-floor rule for an analogue hiss tail.
//! The reductions Python does with `np.mean(axis=...)` over a non-contiguous axis add rows one after the other, which is what is done here, so the floats agree bit for bit.
//! tools/tail_silence_parity.py compares them with the real Python on synthetic PCM.

/// Why a scan could not answer (Python raises ValueError / returns 0.0 depending on `raise_on_error`).
#[derive(Debug, PartialEq)]
pub enum ScanError {
    NoFrames,
    ShorterThanWindow,
}

fn window_rms_db(samples: &[f32], sr: usize) -> Option<(Vec<f64>, usize)> {
    let frames = samples.len() / 2;
    let window = (sr / 10).max(1);
    let n_windows = frames / window;
    if n_windows == 0 {
        return None;
    }
    let mut db = Vec::with_capacity(n_windows);
    for w in 0..n_windows {
        let (mut l, mut r) = (0.0f64, 0.0f64);
        for f in 0..window {
            let i = (w * window + f) * 2;
            let (a, b) = (samples[i] as f64, samples[i + 1] as f64);
            l += a * a;
            r += b * b;
        }
        let (l, r) = ((l / window as f64).sqrt(), (r / window as f64).sqrt());
        let m = l.max(r);
        db.push(20.0 * m.max(1e-9).log10());
    }
    Some((db, window))
}

/// numpy's `np.percentile(x, q)` (linear interpolation) on an unsorted slice.
fn percentile(values: &[f64], q: f64) -> f64 {
    let mut v = values.to_vec();
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let virtual_index = q / 100.0 * (v.len() as f64 - 1.0);
    let lo = virtual_index.floor();
    let t = virtual_index - lo;
    let (a, b) = (v[lo as usize], v[(lo as usize + 1).min(v.len() - 1)]);
    let diff = b - a;
    if t >= 0.5 { b - diff * (1.0 - t) } else { a + diff * t }
}

fn median(values: &[f64]) -> f64 {
    let mut v = values.to_vec();
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = v.len();
    if n % 2 == 1 { v[n / 2] } else { (v[n / 2 - 1] + v[n / 2]) / 2.0 }
}

/// `detect_lead_silence(path, noise_db, min_silence, ..., sr)` over decoded samples; 0.0 whenever Python answers 0.0.
pub fn lead_silence(samples: &[f32], sr: usize, noise_db: f64, min_silence: f64) -> f64 {
    let min_silence = min_silence.max(0.0);
    let Some((db, window)) = window_rms_db(samples, sr) else { return 0.0 };
    let Some(first) = db.iter().position(|&d| d > noise_db) else { return 0.0 };
    let silence_end = first as f64 * (window as f64 / sr as f64);
    if silence_end >= min_silence { silence_end.min(10.0).max(0.0) } else { 0.0 }
}

/// `detect_trailing_silence` over the decoded tail.
pub fn trailing_silence(samples: &[f32], sr: usize, noise_db: f64) -> Result<f64, ScanError> {
    let frames = samples.len() / 2;
    if frames == 0 {
        return Err(ScanError::NoFrames);
    }
    let Some((db, window)) = window_rms_db(samples, sr) else { return Err(ScanError::ShorterThanWindow) };
    let n_windows = db.len();
    let Some(last_loud) = db.iter().rposition(|&d| d > noise_db) else { return Ok(0.0) };
    let window_s = window as f64 / sr as f64;
    let decoded_s = n_windows as f64 * window_s;
    let trailing = decoded_s - ((last_loud as f64 + 1.0) * window_s);
    if trailing > 0.0 {
        return Ok(trailing.max(0.0));
    }
    let floor_windows = ((2.0 / window_s).round_ties_even() as usize).max(1);
    let minimum_tail_windows = ((3.0 / window_s).round_ties_even() as usize).max(1);
    if db.len() <= floor_windows + minimum_tail_windows {
        return Ok(0.0);
    }
    let terminal = &db[db.len() - floor_windows..];
    let floor_db = median(terminal);
    let floor_spread = percentile(terminal, 90.0) - percentile(terminal, 10.0);
    if floor_db > -46.0 || floor_spread > 3.0 {
        return Ok(0.0);
    }
    let programme_peak = percentile(&db[..db.len() - floor_windows], 95.0);
    if programme_peak < floor_db + 12.0 {
        return Ok(0.0);
    }
    let adaptive = (-40.0f64).min(floor_db + 6.0);
    let Some(meaningful_last) = db.iter().rposition(|&d| d > adaptive) else { return Ok(0.0) };
    let adaptive_trailing_windows = n_windows - (meaningful_last + 1);
    if adaptive_trailing_windows < minimum_tail_windows {
        return Ok(0.0);
    }
    Ok(adaptive_trailing_windows as f64 * window_s)
}

/// `audible_through_end` over the decoded tail (the last `window_seconds` must clear `threshold_db`).
pub fn audible_through_end(samples: &[f32], sr: usize, window_seconds: f64, threshold_db: f64) -> bool {
    let window_seconds = window_seconds.max(0.1);
    let frames = samples.len() / 2;
    let need = (window_seconds * sr as f64).round_ties_even() as usize;
    if frames < need {
        return false;
    }
    let tail = &samples[(frames - need) * 2..frames * 2];
    let (mut l, mut r) = (0.0f64, 0.0f64);
    for f in 0..need {
        let (a, b) = (tail[f * 2] as f64, tail[f * 2 + 1] as f64);
        l += a * a;
        r += b * b;
    }
    let (l, r) = ((l / need as f64).sqrt(), (r / need as f64).sqrt());
    let level_db = 20.0 * l.max(r).max(1e-9).log10();
    level_db > threshold_db
}
