//! The 10-band graphic EQ of the background music, as an engine [`Effect`].
//!
//! BASS builds it from ten DX8 PARAMEQ effects (centres 31.5 Hz .. 16 kHz, bandwidth 12 semitones = one octave, gain clamped to +/-12 dB).
//! The Rust version is ten peaking biquads (RBJ cookbook, bandwidth 1.0 octave). The match was MEASURED, not assumed: BASS's real
//! response with one band at +12 dB agrees with this formula to within 0.12 dB at 40 frequencies (tools/bgm_eq_response.py; a table of
//! that measurement is pinned in the tests below).
//!
//! Gains/enable arrive through an [`EqHandle`] (atomics): the audio thread notices a new generation at the start of a block and
//! recomputes coefficients in place; filter state is kept so a slider move does not click.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::Arc;

use crate::engine::Effect;

pub const EQ_BANDS_HZ: [f64; 10] = [31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0];
pub const EQ_MAX_GAIN_DB: f32 = 12.0;
/// Bandwidth between the half-gain points, in octaves (BASS: 12 semitones).
pub const EQ_BANDWIDTH_OCTAVES: f64 = 1.0;
/// A band counts as flat below this (the app attaches the EQ only when it is enabled and not flat).
const FLAT_DB: f32 = 0.01;

struct Shared {
    enabled: AtomicBool,
    gains: [AtomicU32; 10],
    generation: AtomicU64,
}

/// Control-side handle: cheap to clone, safe to use from any thread.
#[derive(Clone)]
pub struct EqHandle(Arc<Shared>);

impl EqHandle {
    pub fn set_enabled(&self, on: bool) {
        self.0.enabled.store(on, Ordering::Release);
        self.0.generation.fetch_add(1, Ordering::Release);
    }
    pub fn set_gains_db(&self, gains: &[f32; 10]) {
        for (slot, g) in self.0.gains.iter().zip(gains) {
            slot.store(g.clamp(-EQ_MAX_GAIN_DB, EQ_MAX_GAIN_DB).to_bits(), Ordering::Relaxed);
        }
        self.0.generation.fetch_add(1, Ordering::Release);
    }
}

#[derive(Clone, Copy, Default)]
struct Coef {
    b0: f64,
    b1: f64,
    b2: f64,
    a1: f64,
    a2: f64,
}

/// RBJ peaking filter.
pub fn peaking(fc: f64, gain_db: f64, bw_octaves: f64, rate: f64) -> [f64; 5] {
    let a = 10f64.powf(gain_db / 40.0);
    let w0 = 2.0 * std::f64::consts::PI * fc.min(rate * 0.49) / rate;
    let alpha = w0.sin() * (std::f64::consts::LN_2 / 2.0 * bw_octaves * w0 / w0.sin()).sinh();
    let a0 = 1.0 + alpha / a;
    [(1.0 + alpha * a) / a0, -2.0 * w0.cos() / a0, (1.0 - alpha * a) / a0, -2.0 * w0.cos() / a0, (1.0 - alpha / a) / a0]
}

pub struct GraphicEq {
    shared: Arc<Shared>,
    rate: f64,
    seen: u64,
    active: bool,
    coef: [Coef; 10],
    z: [[[f64; 2]; 2]; 10],
}

impl GraphicEq {
    /// Returns the effect (to put in the engine's chain) and its control handle. Starts disabled and flat.
    pub fn new(sample_rate: u32) -> (Self, EqHandle) {
        let shared = Arc::new(Shared { enabled: AtomicBool::new(false), gains: Default::default(), generation: AtomicU64::new(1) });
        let fx = Self { shared: shared.clone(), rate: sample_rate as f64, seen: 0, active: false, coef: [Coef::default(); 10], z: [[[0.0; 2]; 2]; 10] };
        (fx, EqHandle(shared))
    }

    fn refresh(&mut self) {
        let gen_now = self.shared.generation.load(Ordering::Acquire);
        if gen_now == self.seen {
            return;
        }
        let enabled = self.shared.enabled.load(Ordering::Acquire);
        let mut any = false;
        for i in 0..10 {
            let g = f32::from_bits(self.shared.gains[i].load(Ordering::Relaxed));
            any |= g.abs() > FLAT_DB;
            let c = peaking(EQ_BANDS_HZ[i], g as f64, EQ_BANDWIDTH_OCTAVES, self.rate);
            self.coef[i] = Coef { b0: c[0], b1: c[1], b2: c[2], a1: c[3], a2: c[4] };
        }
        self.active = enabled && any;
        self.seen = gen_now;
    }
}

impl Effect for GraphicEq {
    fn process(&mut self, buf: &mut [f32]) {
        self.refresh();
        if !self.active {
            return;
        }
        for frame in buf.chunks_exact_mut(2) {
            for (ch, s) in frame.iter_mut().enumerate() {
                let mut x = *s as f64;
                for band in 0..10 {
                    let c = self.coef[band];
                    let z = &mut self.z[band][ch];
                    let y = c.b0 * x + z[0];
                    z[0] = c.b1 * x - c.a1 * y + z[1];
                    z[1] = c.b2 * x - c.a2 * y;
                    x = y;
                }
                *s = x as f32;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RATE: u32 = 48_000;

    /// Steady-state gain in dB of the EQ at `hz`, measured with a sine.
    fn measure(handle: &EqHandle, fx: &mut GraphicEq, hz: f64) -> f64 {
        let _ = handle;
        let n = RATE as usize;
        let mut buf = Vec::with_capacity(n * 2);
        for i in 0..n {
            let v = (0.1 * (2.0 * std::f64::consts::PI * hz * i as f64 / RATE as f64).sin()) as f32;
            buf.push(v);
            buf.push(v);
        }
        let reference = buf.clone();
        for chunk in buf.chunks_mut(1024) {
            fx.process(chunk);
        }
        let rms = |v: &[f32]| (v[RATE as usize / 2..].iter().map(|s| (*s as f64).powi(2)).sum::<f64>() / (v.len() - RATE as usize / 2) as f64).sqrt();
        20.0 * (rms(&buf) / rms(&reference)).log10()
    }

    /// BASS's own measured response with only the 1 kHz band at +12 dB (tools/bgm_eq_response.py, 2026-10-10).
    const BASS_1K_PLUS12: [(f64, f64); 26] = [
        (98.0, 0.10), (140.0, 0.15), (200.0, 0.32), (285.0, 0.70), (340.0, 1.01), (406.0, 1.51), (485.0, 2.28), (579.0, 3.51),
        (691.0, 5.55), (825.0, 8.82), (985.0, 11.97), (1176.0, 9.49), (1403.0, 6.00), (1675.0, 3.78), (2000.0, 2.42), (2388.0, 1.59),
        (2850.0, 1.06), (3403.0, 0.71), (4062.0, 0.48), (4849.0, 0.32), (5789.0, 0.22), (6910.0, 0.15), (8249.0, 0.09),
        (9848.0, 0.06), (11756.0, 0.04), (20.0, 0.02),
    ];

    #[test]
    fn matches_the_measured_bass_response_for_a_single_band() {
        let (mut fx, h) = GraphicEq::new(RATE);
        let mut g = [0.0f32; 10];
        g[5] = 12.0;
        h.set_gains_db(&g);
        h.set_enabled(true);
        for (hz, want) in BASS_1K_PLUS12 {
            let got = measure(&h, &mut fx, hz);
            assert!((got - want).abs() < 0.3, "{hz} Hz: rust {got:.2} dB vs BASS {want:.2} dB");
        }
    }

    #[test]
    fn disabled_or_flat_is_bit_exact_pass_through() {
        let (mut fx, h) = GraphicEq::new(RATE);
        let mut buf: Vec<f32> = (0..2000).map(|i| (i as f32 * 0.01).sin()).collect();
        let before = buf.clone();
        fx.process(&mut buf);
        assert_eq!(buf, before, "starts disabled");
        h.set_enabled(true);
        fx.process(&mut buf);
        assert_eq!(buf, before, "enabled but flat");
        h.set_gains_db(&[5.0; 10]);
        h.set_enabled(false);
        fx.process(&mut buf);
        assert_eq!(buf, before, "gains set but disabled");
    }

    #[test]
    fn gains_are_clamped_to_twelve_db_like_bass() {
        let (mut fx, h) = GraphicEq::new(RATE);
        let mut g = [0.0f32; 10];
        g[5] = 30.0;
        h.set_gains_db(&g);
        h.set_enabled(true);
        let got = measure(&h, &mut fx, 1000.0);
        assert!((got - 12.0).abs() < 0.3, "{got}");
    }

    #[test]
    fn a_live_change_applies_at_the_next_block_without_a_click() {
        let (mut fx, h) = GraphicEq::new(RATE);
        h.set_enabled(true);
        let mut g = [0.0f32; 10];
        g[4] = 6.0;
        h.set_gains_db(&g);
        let sine = |n: usize, off: usize| -> Vec<f32> {
            (0..n).flat_map(|i| {
                let v = (0.2 * (2.0 * std::f64::consts::PI * 500.0 * (off + i) as f64 / RATE as f64).sin()) as f32;
                [v, v]
            }).collect()
        };
        let mut a = sine(4800, 0);
        fx.process(&mut a);
        g[4] = 10.0;
        h.set_gains_db(&g);
        let mut b = sine(4800, 4800);
        fx.process(&mut b);
        // continuity at the join: no step larger than the signal's own slope
        let step = (b[0] - a[a.len() - 2]).abs();
        assert!(step < 0.05, "discontinuity {step}");
        let peak_a = a[2000..].iter().fold(0.0f32, |m, v| m.max(v.abs()));
        let peak_b = b[3000..].iter().fold(0.0f32, |m, v| m.max(v.abs()));
        assert!(peak_b > peak_a * 1.2, "level should rise: {peak_a} -> {peak_b}");
    }

    #[test]
    fn works_at_44100_too() {
        let (mut fx, h) = GraphicEq::new(44_100);
        let mut g = [0.0f32; 10];
        g[9] = 12.0;
        h.set_gains_db(&g);
        h.set_enabled(true);
        let n = 44_100usize;
        let mut buf: Vec<f32> = (0..n).flat_map(|i| {
            let v = (0.1 * (2.0 * std::f64::consts::PI * 16000.0 * i as f64 / 44_100.0).sin()) as f32;
            [v, v]
        }).collect();
        let r0 = (buf[n..].iter().map(|s| (*s as f64).powi(2)).sum::<f64>() / n as f64).sqrt();
        fx.process(&mut buf);
        let r1 = (buf[n..].iter().map(|s| (*s as f64).powi(2)).sum::<f64>() / n as f64).sqrt();
        let db = 20.0 * (r1 / r0).log10();
        assert!((db - 12.0).abs() < 0.8, "16 kHz band at 44.1 kHz: {db}");
    }
}
