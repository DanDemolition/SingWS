//! `detect_sections` and its helpers (phrase_detect.py): a self-similarity matrix of chroma + timbre features, Foote checkerboard novelty, scipy-style peak picking
//! (`find_peaks` with distance, height and prominence), heuristic Intro/Verse/Chorus/Instrumental/Outro labels. f64 throughout.
use crate::stft_mag;

#[derive(Debug, Clone, PartialEq)]
pub struct Section {
    pub seconds: f64,
    pub label: String,
    pub from_type: &'static str,
    pub to_type: &'static str,
    pub confidence: f64,
}

fn hz2mel(f: f64) -> f64 {
    2595.0 * (1.0 + f / 700.0).log10()
}
fn mel2hz(m: f64) -> f64 {
    700.0 * (10f64.powf(m / 2595.0) - 1.0)
}

/// `_mel_filterbank(sr, n_fft, n_mels)`: n_mels x bins.
fn mel_filterbank(sr: usize, n_fft: usize, n_mels: usize) -> Vec<Vec<f64>> {
    let bins = n_fft / 2 + 1;
    let (lo, hi) = (hz2mel(0.0), hz2mel(sr as f64 / 2.0));
    let step = (hi - lo) / (n_mels + 1) as f64;
    let mels: Vec<f64> = (0..n_mels + 2).map(|i| if i == n_mels + 1 { hi } else { lo + i as f64 * step }).collect();
    let binf: Vec<i64> = mels.iter().map(|&m| (((n_fft + 1) as f64 * mel2hz(m) / sr as f64).floor() as i64).clamp(0, bins as i64 - 1)).collect();
    let mut fb = vec![vec![0.0; bins]; n_mels];
    for m in 1..=n_mels {
        let (mut l, mut c, mut r) = (binf[m - 1], binf[m], binf[m + 1]);
        c = c.max(l + 1);
        r = r.max(c + 1);
        l = l.min(bins as i64 - 2);
        c = c.min(bins as i64 - 1);
        r = r.min(bins as i64 - 1);
        if c > l {
            for k in l..c {
                fb[m - 1][k as usize] = (k - l) as f64 / (c - l) as f64;
            }
        }
        if r > c {
            for k in c..r {
                fb[m - 1][k as usize] = (r - k) as f64 / (r - c) as f64;
            }
        }
    }
    fb
}

/// `_chroma(mag, sr, n_fft)`: frames x 12.
fn chroma(mag: &[Vec<f64>], sr: usize, n_fft: usize) -> Vec<Vec<f64>> {
    let bins = mag[0].len();
    let pc: Vec<i64> = (0..bins)
        .map(|k| {
            let f = k as f64 * sr as f64 / n_fft as f64;
            if f > 0.0 {
                let midi = 69.0 + 12.0 * ((f + 1e-9) / 440.0).log2();
                (midi.round_ties_even() as i64).rem_euclid(12)
            } else {
                -1
            }
        })
        .collect();
    mag.iter()
        .map(|row| {
            let mut out = vec![0.0; 12];
            for (k, &v) in row.iter().enumerate() {
                if pc[k] >= 0 {
                    out[pc[k] as usize] += v;
                }
            }
            out
        })
        .collect()
}

/// `scipy.fft.dct(x, type=2, norm='ortho')` for one row.
fn dct2_ortho(x: &[f64]) -> Vec<f64> {
    let n = x.len();
    (0..n)
        .map(|k| {
            let s: f64 = x.iter().enumerate().map(|(i, &v)| v * (std::f64::consts::PI * k as f64 * (2 * i + 1) as f64 / (2.0 * n as f64)).cos()).sum();
            s * 2.0 * if k == 0 { (1.0 / (4.0 * n as f64)).sqrt() } else { (1.0 / (2.0 * n as f64)).sqrt() }
        })
        .collect()
}

/// `_timbre(mag, melfb, n_mfcc=12)`: frames x 12 (coefficient 0 dropped).
fn timbre(mag: &[Vec<f64>], melfb: &[Vec<f64>]) -> Vec<Vec<f64>> {
    mag.iter()
        .map(|row| {
            let logmel: Vec<f64> = melfb.iter().map(|f| (f.iter().zip(row).map(|(a, b)| a * b).sum::<f64>() + 1e-6).ln()).collect();
            dct2_ortho(&logmel)[1..13].to_vec()
        })
        .collect()
}

fn l2norm_rows(x: &[Vec<f64>]) -> Vec<Vec<f64>> {
    x.iter()
        .map(|r| {
            let n = r.iter().map(|v| v * v).sum::<f64>().sqrt();
            let n = if n == 0.0 { 1.0 } else { n };
            r.iter().map(|v| v / n).collect()
        })
        .collect()
}

/// `_foote_novelty(ssm, M)`
fn foote_novelty(ssm: &[Vec<f64>], m: usize) -> Vec<f64> {
    let t = ssm.len();
    if t == 0 {
        return vec![];
    }
    let g: Vec<f64> = (0..2 * m).map(|i| i as f64 - m as f64).collect();
    let mut kern = vec![vec![0.0; 2 * m]; 2 * m];
    for (r, gy) in g.iter().enumerate() {
        for (c, gx) in g.iter().enumerate() {
            let gauss = (-0.5 * ((gx / (m as f64 / 2.0)).powi(2) + (gy / (m as f64 / 2.0)).powi(2))).exp();
            let sign = if gx * gy > 0.0 { 1.0 } else if gx * gy < 0.0 { -1.0 } else { 0.0 };
            kern[r][c] = sign * gauss;
        }
    }
    let at = |r: i64, c: i64| ssm[r.clamp(0, t as i64 - 1) as usize][c.clamp(0, t as i64 - 1) as usize];
    (0..t)
        .map(|i| {
            let mut s = 0.0;
            for r in 0..2 * m {
                for c in 0..2 * m {
                    s += at(i as i64 + r as i64 - m as i64, i as i64 + c as i64 - m as i64) * kern[r][c];
                }
            }
            s.max(0.0)
        })
        .collect()
}

/// scipy `find_peaks(x, distance=, height=, prominence=)` (in scipy's order: maxima, height, distance, prominence).
fn find_peaks(x: &[f64], distance: f64, height: f64, prominence: f64) -> Vec<usize> {
    let n = x.len();
    let mut peaks: Vec<usize> = vec![];
    if n >= 3 {
        let mut i = 1;
        while i < n - 1 {
            if x[i - 1] < x[i] {
                let mut ahead = i + 1;
                while ahead < n - 1 && x[ahead] == x[i] {
                    ahead += 1;
                }
                if x[ahead] < x[i] {
                    let (left, right) = (i, ahead - 1);
                    peaks.push((left + right) / 2);
                    i = ahead;
                }
            }
            i += 1;
        }
    }
    peaks.retain(|&p| x[p] >= height);
    // distance
    let d = distance.ceil() as i64;
    let size = peaks.len();
    let mut keep = vec![true; size];
    let mut order: Vec<usize> = (0..size).collect();
    order.sort_by(|&a, &b| x[peaks[a]].partial_cmp(&x[peaks[b]]).unwrap());
    for idx in (0..size).rev() {
        let j = order[idx];
        if !keep[j] {
            continue;
        }
        let mut k = j as i64 - 1;
        while k >= 0 && (peaks[j] as i64 - peaks[k as usize] as i64) < d {
            keep[k as usize] = false;
            k -= 1;
        }
        let mut k = j + 1;
        while k < size && (peaks[k] as i64 - peaks[j] as i64) < d {
            keep[k] = false;
            k += 1;
        }
    }
    let peaks: Vec<usize> = peaks.iter().zip(&keep).filter(|(_, &k)| k).map(|(p, _)| *p).collect();
    peaks
        .into_iter()
        .filter(|&p| {
            let (mut left_min, mut i) = (x[p], p as i64);
            while i >= 0 && x[i as usize] <= x[p] {
                if x[i as usize] < left_min {
                    left_min = x[i as usize];
                }
                i -= 1;
            }
            let (mut right_min, mut i) = (x[p], p);
            while i < n && x[i] <= x[p] {
                if x[i] < right_min {
                    right_min = x[i];
                }
                i += 1;
            }
            x[p] - left_min.max(right_min) >= prominence
        })
        .collect()
}

struct Seg {
    energy: f64,
    vocal: f64,
    repetition: f64,
}

fn segment_descriptors(mag: &[Vec<f64>], sr: usize, n_fft: usize, ssm: &[Vec<f64>], bounds: &[usize]) -> Vec<Seg> {
    let bins = mag[0].len();
    let vocal_band: Vec<bool> = (0..bins).map(|k| { let f = k as f64 * sr as f64 / n_fft as f64; (200.0..=4000.0).contains(&f) }).collect();
    let energy: Vec<f64> = mag.iter().map(|r| r.iter().sum::<f64>() + 1e-9).collect();
    let vocal: Vec<f64> = mag.iter().map(|r| r.iter().zip(&vocal_band).filter(|(_, &b)| b).map(|(v, _)| v).sum()).collect();
    let t = ssm.len();
    let mut segs = vec![];
    for i in 0..bounds.len() - 1 {
        let (a, mut b) = (bounds[i], bounds[i + 1]);
        b = b.max(a + 1);
        let mean = |v: &[f64]| v[a..b].iter().sum::<f64>() / (b - a) as f64;
        let e = mean(&energy);
        let v = mean(&vocal) / mean(&energy);
        let outside = t - (b - a).min(t);
        let rep = if outside > 0 {
            let mut s = 0.0;
            for row in &ssm[a..b] {
                for (c, val) in row.iter().enumerate() {
                    if c < a || c >= b {
                        s += val;
                    }
                }
            }
            s / ((b - a) * outside) as f64
        } else {
            0.0
        };
        segs.push(Seg { energy: e, vocal: v, repetition: rep });
    }
    segs
}

fn median(v: &[f64]) -> f64 {
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = s.len();
    if n % 2 == 1 { s[n / 2] } else { (s[n / 2 - 1] + s[n / 2]) / 2.0 }
}

fn label_segments(segs: &[Seg]) -> Vec<&'static str> {
    let n = segs.len();
    if n == 0 {
        return vec![];
    }
    let e: Vec<f64> = segs.iter().map(|s| s.energy).collect();
    let v: Vec<f64> = segs.iter().map(|s| s.vocal).collect();
    let r: Vec<f64> = segs.iter().map(|s| s.repetition).collect();
    let emax = e.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let emax = if emax > 0.0 { emax } else { 1.0 };
    let vmed = median(&v);
    let vocal: Vec<bool> = v.iter().map(|&x| x >= 1e-6f64.max(0.6 * vmed)).collect();
    let mut types: Vec<&'static str> = (0..n).map(|i| if vocal[i] { "Verse" } else { "Instrumental" }).collect();
    let vidx: Vec<usize> = (0..n).filter(|&i| vocal[i]).collect();
    if !vidx.is_empty() {
        let rv: Vec<f64> = vidx.iter().map(|&i| r[i]).collect();
        let ev: Vec<f64> = vidx.iter().map(|&i| e[i]).collect();
        let (rmin, rmax) = (rv.iter().cloned().fold(f64::INFINITY, f64::min), rv.iter().cloned().fold(f64::NEG_INFINITY, f64::max));
        let (emin, emx) = (ev.iter().cloned().fold(f64::INFINITY, f64::min), ev.iter().cloned().fold(f64::NEG_INFINITY, f64::max));
        let comb: Vec<f64> = (0..vidx.len()).map(|j| (rv[j] - rmin) / ((rmax - rmin) + 1e-9) + (ev[j] - emin) / ((emx - emin) + 1e-9)).collect();
        let cmax = comb.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let thr = cmax * 0.7;
        for (j, &i) in vidx.iter().enumerate() {
            types[i] = if cmax > 0.0 && comb[j] >= thr { "Chorus" } else { "Verse" };
        }
    }
    if e[0] < 0.5 * emax || !vocal[0] {
        types[0] = "Intro";
    }
    if n > 1 && (e[n - 1] < 0.5 * emax || !vocal[n - 1]) {
        types[n - 1] = "Outro";
    }
    types
}

/// `detect_sections(pcm, sr, bpm, max_seconds=360, hop_s=0.25, n_fft=2048, max_markers=8)`.
pub fn detect_sections(pcm: &[f32], sr: usize, bpm: Option<f64>) -> Vec<Section> {
    let (max_seconds, hop_s, n_fft, max_markers) = (360.0f64, 0.25f64, 2048usize, 8usize);
    if pcm.len() < sr * 8 {
        return vec![];
    }
    let mut pcm = pcm;
    if pcm.len() > (sr as f64 * max_seconds) as usize {
        pcm = &pcm[..(sr as f64 * max_seconds) as usize];
    }
    let hop = ((sr as f64 * hop_s) as usize).max(1);
    let mag = stft_mag(pcm, n_fft, hop);
    if mag.len() < 8 {
        return vec![];
    }
    let melfb = mel_filterbank(sr, n_fft, 32);
    let ch = l2norm_rows(&chroma(&mag, sr, n_fft));
    let tb = l2norm_rows(&timbre(&mag, &melfb));
    let feat: Vec<Vec<f64>> = l2norm_rows(&ch.iter().zip(&tb).map(|(a, b)| a.iter().chain(b.iter()).cloned().collect()).collect::<Vec<Vec<f64>>>());
    let t = feat.len();
    let ssm: Vec<Vec<f64>> = (0..t).map(|i| (0..t).map(|j| feat[i].iter().zip(&feat[j]).map(|(a, b)| a * b).sum()).collect()).collect();
    let m = 4.max((4.0 / hop_s).round_ties_even() as usize);
    let nov = foote_novelty(&ssm, m);
    let nmax = nov.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    if nmax <= 0.0 {
        return vec![];
    }
    let novn: Vec<f64> = nov.iter().map(|v| v / nmax).collect();
    let min_dist = 1.max((5.0 / hop_s).round_ties_even() as usize);
    let peaks_idx = find_peaks(&novn, min_dist as f64, 0.06, 0.05);
    if peaks_idx.is_empty() {
        return vec![];
    }
    let mut bounds = vec![0usize];
    bounds.extend(peaks_idx.iter().copied());
    bounds.push(mag.len());
    let types = label_segments(&segment_descriptors(&mag, sr, n_fft, &ssm, &bounds));
    let spb = match bpm {
        Some(b) if b > 0.0 => 4.0 * 60.0 / b,
        _ => 0.0,
    };
    let mut results: Vec<Section> = vec![];
    for (k, &pidx) in peaks_idx.iter().enumerate() {
        let mut sec = pidx as f64 * hop_s;
        if spb > 0.0 {
            sec = (sec / spb).round_ties_even() * spb;
        }
        let prev_t = types.get(k).copied().unwrap_or("Verse");
        let next_t = types.get(k + 1).copied().unwrap_or("Verse");
        results.push(Section { seconds: sec.max(0.0), label: format!("{prev_t}\u{2192}{next_t}"), from_type: prev_t, to_type: next_t, confidence: novn[pidx] });
    }
    results.sort_by(|a, b| b.confidence.partial_cmp(&a.confidence).unwrap());
    results.truncate(max_markers);
    results.sort_by(|a, b| a.seconds.partial_cmp(&b.seconds).unwrap());
    let mut deduped: Vec<Section> = vec![];
    for r in results {
        if deduped.last().map_or(false, |d| (r.seconds - d.seconds).abs() < 5.0) {
            continue;
        }
        deduped.push(r);
    }
    deduped
}
