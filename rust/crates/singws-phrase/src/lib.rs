//! The waveform-editor analysis of phrase_detect.py in Rust: `peaks`, `rms_envelope`, `suggest_start`, `estimate_tempo_and_beat` (+ `estimate_bpm`, `beat_aligned_loop`) and
//! `detect_sections`. The Python is numpy float32 throughout; this works in f64, so the answers agree to the precision of a float32 FFT, not bit for bit. tools/phrase_parity.py reports
//! how often they agree within tolerance (BPM, beat phase, section times) on synthetic and real audio.
pub mod fft;
pub mod sections;

use fft::{irfft, rfft, C};

/// `peaks(pcm, n_cols)`: (min, max) per column.
pub fn peaks(pcm: &[f32], n_cols: usize) -> Vec<(f32, f32)> {
    let n_cols = n_cols.max(1);
    let mut out = vec![(0.0f32, 0.0f32); n_cols];
    if pcm.is_empty() {
        return out;
    }
    let bin = (pcm.len() / n_cols).max(1);
    let usable = bin * n_cols;
    if usable > pcm.len() || bin * n_cols == 0 {
        let (mn, mx) = pcm.iter().fold((f32::INFINITY, f32::NEG_INFINITY), |a, &v| (a.0.min(v), a.1.max(v)));
        return vec![(mn, mx); n_cols];
    }
    for (c, o) in out.iter_mut().enumerate() {
        let block = &pcm[c * bin..(c + 1) * bin];
        *o = block.iter().fold((f32::INFINITY, f32::NEG_INFINITY), |a, &v| (a.0.min(v), a.1.max(v)));
    }
    out
}

/// `rms_envelope(pcm, sr, hop_ms)`: (env, hop seconds).
pub fn rms_envelope(pcm: &[f32], sr: usize, hop_ms: f64) -> (Vec<f64>, f64) {
    let hop = ((sr as f64 * hop_ms / 1000.0) as usize).max(1);
    let n = pcm.len() / hop;
    if n == 0 {
        return (vec![], hop as f64 / sr as f64);
    }
    let env = (0..n)
        .map(|i| {
            let block = &pcm[i * hop..(i + 1) * hop];
            let mean = block.iter().map(|&v| (v as f64) * (v as f64)).sum::<f64>() / hop as f64;
            (mean + 1e-12).sqrt()
        })
        .collect();
    (env, hop as f64 / sr as f64)
}

/// `_smooth(x, w)`: a moving average, numpy 'same' alignment ((w-1)//2 into the full convolution).
pub fn smooth(x: &[f64], w: usize) -> Vec<f64> {
    if x.is_empty() || w <= 1 {
        return x.to_vec();
    }
    let k = 1.0 / w as f64;
    let off = (w - 1) / 2;
    let n = x.len();
    (0..n)
        .map(|i| {
            let c = i + off; // index in the full convolution
            let lo = c.saturating_sub(w - 1);
            let hi = c.min(n - 1);
            if lo > hi { 0.0 } else { x[lo..=hi].iter().sum::<f64>() * k }
        })
        .collect()
}

fn median(v: &[f64]) -> f64 {
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = s.len();
    if n % 2 == 1 { s[n / 2] } else { (s[n / 2 - 1] + s[n / 2]) / 2.0 }
}

/// `suggest_start(pcm, sr, bpm, search_fraction)`.
pub fn suggest_start(pcm: &[f32], sr: usize, bpm: Option<f64>, search_fraction: f64) -> Option<f64> {
    if pcm.len() < sr {
        return None;
    }
    let (env, hop_s) = rms_envelope(pcm, sr, 50.0);
    if env.len() < 4 {
        return None;
    }
    let env = smooth(&env, 2.max((0.2 / hop_s) as usize));
    let mut novelty: Vec<f64> = (0..env.len()).map(|i| if i == 0 { 0.0 } else { env[i] - env[i - 1] }).collect();
    for v in novelty.iter_mut() {
        if *v < 0.0 {
            *v = 0.0;
        }
    }
    let search_n = 2.max((env.len() as f64 * search_fraction) as usize);
    let window = &novelty[..search_n.min(novelty.len())];
    if window.is_empty() || window.iter().cloned().fold(f64::NEG_INFINITY, f64::max) <= 0.0 {
        return None;
    }
    let floor = median(window) + 1e-6;
    let (idx, &top) = window.iter().enumerate().fold((0usize, &window[0]), |a, (i, v)| if *v > *a.1 { (i, v) } else { a });
    if top < floor * 3.0 {
        return None;
    }
    let mut seconds = idx as f64 * hop_s;
    if seconds <= hop_s {
        return None;
    }
    if let Some(b) = bpm {
        if b > 0.0 {
            let spb = 4.0 * 60.0 / b;
            if spb > 0.0 {
                seconds = (seconds / spb).round_ties_even() * spb;
            }
        }
    }
    Some(seconds.max(0.0))
}

/// `np.hanning(n)`
fn hanning(n: usize) -> Vec<f64> {
    if n == 1 {
        return vec![1.0];
    }
    (0..n).map(|k| 0.5 - 0.5 * (2.0 * std::f64::consts::PI * k as f64 / (n as f64 - 1.0)).cos()).collect()
}

/// `_stft_mag(pcm, n_fft, hop)`: frames x (n_fft/2+1) magnitudes.
pub fn stft_mag(pcm: &[f32], n_fft: usize, hop: usize) -> Vec<Vec<f64>> {
    let mut p: Vec<f64> = pcm.iter().map(|&v| v as f64).collect();
    if p.len() < n_fft {
        p.resize(n_fft, 0.0);
    }
    let n_frames = 1 + (p.len() - n_fft) / hop;
    let win = hanning(n_fft);
    (0..n_frames)
        .map(|f| {
            let frame: Vec<f64> = (0..n_fft).map(|i| p[f * hop + i] * win[i]).collect();
            rfft(&frame, n_fft).iter().map(|c| c.abs()).collect()
        })
        .collect()
}

/// `_onset_envelope(pcm, sr)`: (onset, frames per second) or None.
pub fn onset_envelope(pcm: &[f32], sr: usize) -> Option<(Vec<f64>, f64)> {
    let n_fft = 1024;
    let hop = ((sr as f64 * 0.01) as usize).max(1);
    let mag = stft_mag(pcm, n_fft, hop);
    if mag.len() < 16 {
        return None;
    }
    let mut onset: Vec<f64> = (1..mag.len()).map(|i| mag[i].iter().zip(&mag[i - 1]).map(|(a, b)| (a - b).max(0.0)).sum()).collect();
    if onset.len() < 16 {
        return None; // numpy would broadcast a 16-long smoothing over a shorter envelope and raise
    }
    let sm = smooth(&onset, 16);
    for (o, s) in onset.iter_mut().zip(sm) {
        *o = (*o - s).max(0.0);
    }
    let mx = onset.iter().cloned().fold(0.0, f64::max);
    if mx <= 0.0 {
        return None;
    }
    for o in onset.iter_mut() {
        *o /= mx;
    }
    Some((onset, sr as f64 / hop as f64))
}

fn parabolic_offset(y: &[f64], i: usize) -> f64 {
    if i == 0 || i + 1 >= y.len() {
        return 0.0;
    }
    let (a, b, c) = (y[i - 1], y[i], y[i + 1]);
    let denom = a - 2.0 * b + c;
    if denom == 0.0 {
        return 0.0;
    }
    (0.5 * (a - c) / denom).clamp(-1.0, 1.0)
}

/// `_bar_comb(onset, fps, bpm)`: (downbeat offset seconds, peakiness).
fn bar_comb(onset: &[f64], fps: f64, bpm: f64) -> Option<(f64, f64)> {
    let bar_lag = 4.0 * 60.0 / bpm * fps;
    let l = bar_lag.round_ties_even() as usize;
    if l < 4 {
        return None;
    }
    let k = onset.len() / l;
    if k < 2 {
        return None;
    }
    let mut comb = vec![0.0f64; l];
    for r in 0..k {
        for c in 0..l {
            comb[c] += onset[r * l + c];
        }
    }
    let m = comb.iter().sum::<f64>() / l as f64;
    if m <= 0.0 {
        return None;
    }
    let (phase, &peak) = comb.iter().enumerate().fold((0usize, &comb[0]), |a, (i, v)| if *v > *a.1 { (i, v) } else { a });
    let (a, b, c) = (comb[(phase + l - 1) % l], comb[phase], comb[(phase + 1) % l]);
    let denom = a - 2.0 * b + c;
    let off = if denom != 0.0 { (0.5 * (a - c) / denom).clamp(-1.0, 1.0) } else { 0.0 };
    Some(((phase as f64 + off) / fps, peak / m))
}

#[derive(Debug, Clone, PartialEq)]
pub struct Tempo {
    pub bpm: f64,
    pub first_beat: Option<f64>,
    pub confidence: f64,
}

fn round_to(v: f64, places: i32) -> f64 {
    let f = 10f64.powi(places);
    (v * f).round_ties_even() / f
}

/// `estimate_tempo_and_beat(pcm, sr, min_bpm, max_bpm)`.
pub fn estimate_tempo_and_beat(pcm: &[f32], sr: usize, min_bpm: f64, max_bpm: f64) -> Option<Tempo> {
    if pcm.len() < sr * 5 {
        return None;
    }
    let (onset, fps) = onset_envelope(pcm, sr)?;
    let n = onset.len();
    let spec = rfft(&onset, 2 * n);
    let power: Vec<C> = spec.iter().map(|c| C::new(c.re * c.re + c.im * c.im, 0.0)).collect();
    let ac: Vec<f64> = irfft(&power).into_iter().take(n).collect();
    let acmax = ac.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    if acmax <= 0.0 {
        return None;
    }
    let min_lag = 1.max((fps * 60.0 / max_bpm) as usize);
    let max_lag = (n - 1).min((fps * 60.0 / min_bpm) as usize);
    if max_lag <= min_lag {
        return None;
    }
    let mut best_strength = f64::NEG_INFINITY;
    let mut base_bpm = 0.0;
    for lag in min_lag..=max_lag {
        let bpm = 60.0 * fps / lag as f64;
        let prior = (-0.5 * ((bpm / 120.0).log2() / 0.9).powi(2)).exp();
        let s = ac[lag] * prior;
        if s > best_strength {
            best_strength = s;
            base_bpm = bpm;
        }
    }
    if best_strength <= 0.0 {
        return None;
    }
    let mut best: Option<(f64, f64, f64, f64)> = None;
    for cand in [base_bpm, base_bpm * 2.0, base_bpm / 2.0] {
        if cand < min_bpm || cand > max_bpm {
            continue;
        }
        let Some((first_beat, peak)) = bar_comb(&onset, fps, cand) else { continue };
        let ac_lag = ac[(n - 1).min(1.max((fps * 60.0 / cand).round_ties_even() as usize))];
        let score = peak * ac_lag.max(0.0);
        if best.map_or(true, |b| score > b.0) {
            best = Some((score, cand, first_beat, peak));
        }
    }
    let Some((_score, mut bpm, first_beat, peak)) = best else {
        return Some(Tempo { bpm: round_to(base_bpm, 2), first_beat: None, confidence: 0.0 });
    };
    let lag = fps * 60.0 / bpm;
    let li = lag.round_ties_even() as usize;
    let refined = li as f64 + parabolic_offset(&ac, li);
    if refined > 0.0 {
        bpm = 60.0 * fps / refined;
    }
    Some(Tempo { bpm: round_to(bpm, 2), first_beat: Some(round_to(first_beat, 4)), confidence: round_to((peak / 6.0).min(1.0), 3) })
}

/// `beat_aligned_loop(start_hint, bpm, first_beat, bars)`.
pub fn beat_aligned_loop(start_hint: f64, bpm: f64, first_beat: Option<f64>, bars: i64) -> Option<(f64, f64)> {
    if bpm <= 0.0 || bars <= 0 {
        return None;
    }
    let start_hint = start_hint.max(0.0);
    let bar = 4.0 * 60.0 / bpm;
    let Some(fb) = first_beat else { return Some((start_hint, start_hint + bars as f64 * bar)) };
    let nbars = ((start_hint - fb) / bar).round_ties_even();
    let mut loop_start = fb + (nbars as i64).max(0) as f64 * bar;
    if loop_start < fb {
        loop_start = fb;
    }
    Some((round_to(loop_start, 4), round_to(loop_start + bars as f64 * bar, 4)))
}
