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

#[cfg(test)]
mod tests {
    use super::*;

    /// stereo samples: `windows` windows of 100 frames at sr 1000, each at a constant amplitude
    fn pcm(levels: &[f32]) -> Vec<f32> {
        levels.iter().flat_map(|&a| std::iter::repeat(a).take(200)).collect()
    }

    fn db(a: f32) -> f64 {
        20.0 * (a as f64).log10()
    }

    #[test]
    fn percentile_and_median_follow_numpy() {
        let v: Vec<f64> = (1..=10).map(|x| x as f64).collect();
        assert_eq!(percentile(&v, 90.0), 9.1);
        assert_eq!(percentile(&v, 10.0), 1.9);
        assert!((percentile(&v, 95.0) - 9.55).abs() < 1e-12);
        assert_eq!(percentile(&v, 0.0), 1.0);
        assert_eq!(percentile(&v, 100.0), 10.0);
        assert_eq!(percentile(&[5.0], 90.0), 5.0);
        assert_eq!(median(&[3.0, 1.0, 2.0]), 2.0);
        assert_eq!(median(&[4.0, 1.0, 3.0, 2.0]), 2.5);
    }

    #[test]
    fn a_window_exactly_at_the_threshold_is_not_loud() {
        let a = 0.5f32;
        assert_eq!(lead_silence(&pcm(&[a, a, a, a, a, a, a]), 1000, db(a), 0.5), 0.0);
        assert!(!audible_through_end(&pcm(&[a; 20]), 1000, 0.5, db(a)));
        assert!(audible_through_end(&pcm(&[a; 20]), 1000, 0.5, db(a) - 0.001));
        assert!((trailing_silence(&pcm(&[0.9, 0.9, 0.9, 0.9, 0.9, a]), 1000, db(a)).unwrap() - 0.1).abs() < 1e-12);
    }

    #[test]
    fn the_lead_silence_is_clamped_to_ten_seconds() {
        let mut levels = vec![0.0f32; 120];
        levels.extend([0.5; 5]);
        assert_eq!(lead_silence(&pcm(&levels), 1000, -50.0, 0.5), 10.0);
    }

    #[test]
    fn a_two_sample_decode_is_shorter_than_one_window_not_empty() {
        assert_eq!(trailing_silence(&[0.1, 0.1], 1000, -65.0), Err(ScanError::ShorterThanWindow));
        assert_eq!(trailing_silence(&[], 1000, -65.0), Err(ScanError::NoFrames));
        assert_eq!(trailing_silence(&[0.1], 1000, -65.0), Err(ScanError::NoFrames));
    }

    #[test]
    fn a_steady_hiss_after_the_music_is_trailing_silence_by_the_adaptive_rule() {
        let hiss = 0.003f32; // about -50 dBFS
        let mut levels = vec![0.1f32; 40];
        levels.extend(vec![hiss; 40]);
        assert_eq!(trailing_silence(&pcm(&levels), 1000, -65.0).unwrap(), 4.0);
    }

    #[test]
    fn the_adaptive_rule_needs_more_than_floor_plus_three_seconds_of_windows() {
        let hiss = 0.003f32;
        let mut levels = vec![0.1f32; 15];
        levels.extend(vec![hiss; 35]); // exactly 50 windows: floor 20 + tail 30
        assert_eq!(trailing_silence(&pcm(&levels), 1000, -65.0).unwrap(), 0.0);
        let mut more = vec![0.1f32; 16];
        more.extend(vec![hiss; 35]);
        assert_eq!(trailing_silence(&pcm(&more), 1000, -65.0).unwrap(), 3.5);
    }

    #[test]
    fn a_hiss_louder_than_minus_46_or_wobbling_or_too_close_to_the_music_is_not_dead_air() {
        let mut loud_hiss = vec![0.1f32; 40];
        loud_hiss.extend(vec![0.006f32; 40]); // about -44 dBFS
        assert_eq!(trailing_silence(&pcm(&loud_hiss), 1000, -65.0).unwrap(), 0.0);
        let mut quiet_music = vec![0.008f32; 40]; // about -42 dBFS, less than 12 dB above the floor
        quiet_music.extend(vec![0.003f32; 40]);
        assert_eq!(trailing_silence(&pcm(&quiet_music), 1000, -65.0).unwrap(), 0.0);
        let mut wobble = vec![0.1f32; 40];
        for i in 0..40 {
            wobble.push(if i % 2 == 0 { 0.003 } else { 0.0015 }); // 6 dB apart
        }
        assert_eq!(trailing_silence(&pcm(&wobble), 1000, -65.0).unwrap(), 0.0);
    }
}
