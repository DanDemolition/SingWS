//! The full master chain (gate, tilt EQ, exciter, compressor, look-ahead limiter, output clip) as an engine [`Effect`].
//! It is the existing `singws-dsp` processor used directly, with no foreign-function hop, so BASS's "DSP callback into Rust"
//! disappears: the engine's own audio callback runs it. Parameters and the on/off switch arrive through a [`MasterHandle`].
//!
//! `MasterProcessor::set_params` rebuilds (and clears) the processor state, exactly as the Python and FFI versions do today, so a
//! slider move behaves the same as in the shipped app.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

use singws_dsp::{DEFAULTS, MasterProcessor, PARAM_COUNT, PARAM_NAMES};

use crate::engine::Effect;

struct Shared {
    enabled: AtomicBool,
    params: [AtomicU64; PARAM_COUNT],
    /// Odd while a writer is storing, even when stable; readers retry (next block) if it changed under them.
    seq: AtomicU64,
}

#[derive(Clone)]
pub struct MasterHandle(Arc<Shared>);

impl MasterHandle {
    pub fn set_enabled(&self, on: bool) {
        self.0.enabled.store(on, Ordering::Release);
    }
    /// Replace all 28 parameters (order = `PARAM_NAMES`).
    pub fn set_params(&self, values: &[f64; PARAM_COUNT]) {
        let s = &self.0;
        s.seq.fetch_add(1, Ordering::AcqRel);
        for (slot, v) in s.params.iter().zip(values) {
            slot.store(v.to_bits(), Ordering::Relaxed);
        }
        s.seq.fetch_add(1, Ordering::AcqRel);
    }
    /// Set parameters by name on top of the defaults; unknown names are returned.
    pub fn set_named(&self, pairs: &[(&str, f64)]) -> Vec<String> {
        let mut values = DEFAULTS;
        let mut unknown = vec![];
        for (name, v) in pairs {
            match PARAM_NAMES.iter().position(|n| n == name) {
                Some(i) => values[i] = *v,
                None => unknown.push((*name).to_string()),
            }
        }
        self.set_params(&values);
        unknown
    }
}

pub struct MasterEffect {
    shared: Arc<Shared>,
    proc: MasterProcessor,
    seen_seq: u64,
}

impl MasterEffect {
    /// Starts disabled (pass-through) with the default parameters, like a new processor in the app.
    pub fn new(sample_rate: u32) -> (Self, MasterHandle) {
        let shared = Arc::new(Shared {
            enabled: AtomicBool::new(false),
            params: std::array::from_fn(|i| AtomicU64::new(DEFAULTS[i].to_bits())),
            seq: AtomicU64::new(0),
        });
        let fx = Self { shared: shared.clone(), proc: MasterProcessor::new(sample_rate as f64, 2), seen_seq: 0 };
        (fx, MasterHandle(shared))
    }

    /// Gain of each dynamics stage as of the last block, in dB: [gate, compressor, limiter].
    pub fn gain_reduction_db(&self) -> [f64; 3] {
        self.proc.gain_reduction_db()
    }

    fn refresh(&mut self) {
        let s1 = self.shared.seq.load(Ordering::Acquire);
        if s1 == self.seen_seq || s1 % 2 == 1 {
            return;
        }
        let mut values = [0.0f64; PARAM_COUNT];
        for (v, slot) in values.iter_mut().zip(&self.shared.params) {
            *v = f64::from_bits(slot.load(Ordering::Relaxed));
        }
        if self.shared.seq.load(Ordering::Acquire) != s1 {
            return; // torn read: try again next block
        }
        self.proc.set_params(&values);
        self.seen_seq = s1;
    }
}

impl Effect for MasterEffect {
    fn process(&mut self, buf: &mut [f32]) {
        self.refresh();
        if self.shared.enabled.load(Ordering::Acquire) {
            self.proc.process_interleaved(buf);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RATE: u32 = 48_000;

    fn hot_square(n: usize) -> Vec<f32> {
        (0..n).flat_map(|i| {
            let v = if (i / 100) % 2 == 0 { 1.4 } else { -1.4 };
            [v, v]
        }).collect()
    }

    #[test]
    fn disabled_is_bit_exact_pass_through() {
        let (mut fx, _h) = MasterEffect::new(RATE);
        let mut buf = hot_square(4800);
        let before = buf.clone();
        fx.process(&mut buf);
        assert_eq!(buf, before);
    }

    #[test]
    fn enabled_limits_to_the_ceiling_and_the_ceiling_follows_a_parameter_change() {
        let (mut fx, h) = MasterEffect::new(RATE);
        h.set_named(&[("limiter_enabled", 1.0), ("limiter_ceiling_db", -1.0), ("comp_enabled", 0.0), ("eq_enabled", 0.0), ("output_ceiling_db", -0.1)]);
        h.set_enabled(true);
        let mut buf = hot_square(48_000);
        for c in buf.chunks_mut(1024) {
            fx.process(c);
        }
        let ceil = 10f32.powf(-1.0 / 20.0);
        let peak = buf[4800..].iter().fold(0.0f32, |m, v| m.max(v.abs()));
        assert!(peak <= ceil + 1e-3, "peak {peak} over the -1 dB ceiling {ceil}");
        assert!(peak > ceil * 0.9, "limiter should hold the signal near the ceiling, got {peak}");
        // move the ceiling to -6 dB while running
        h.set_named(&[("limiter_enabled", 1.0), ("limiter_ceiling_db", -6.0), ("comp_enabled", 0.0), ("eq_enabled", 0.0), ("output_ceiling_db", -0.1)]);
        let mut buf2 = hot_square(48_000);
        for c in buf2.chunks_mut(1024) {
            fx.process(c);
        }
        let ceil6 = 10f32.powf(-6.0 / 20.0);
        let peak6 = buf2[4800..].iter().fold(0.0f32, |m, v| m.max(v.abs()));
        assert!(peak6 <= ceil6 + 1e-3 && peak6 > ceil6 * 0.9, "after the change peak {peak6}, ceiling {ceil6}");
    }

    #[test]
    fn named_parameters_reject_typos() {
        let (_fx, h) = MasterEffect::new(RATE);
        assert_eq!(h.set_named(&[("limiter_enabled", 1.0), ("limiter_cieling_db", -3.0)]), vec!["limiter_cieling_db".to_string()]);
    }

    #[test]
    fn matches_the_dsp_crate_directly() {
        let (mut fx, h) = MasterEffect::new(RATE);
        h.set_named(&[("gate_enabled", 1.0), ("comp_ratio", 3.0), ("exciter_mix", 0.2)]);
        h.set_enabled(true);
        let mut direct = MasterProcessor::new(RATE as f64, 2);
        let mut v = DEFAULTS;
        v[PARAM_NAMES.iter().position(|n| *n == "gate_enabled").unwrap()] = 1.0;
        v[PARAM_NAMES.iter().position(|n| *n == "comp_ratio").unwrap()] = 3.0;
        v[PARAM_NAMES.iter().position(|n| *n == "exciter_mix").unwrap()] = 0.2;
        direct.set_params(&v);
        let sig: Vec<f32> = (0..9600).flat_map(|i| {
            let x = (0.4 * (2.0 * std::f64::consts::PI * 330.0 * i as f64 / RATE as f64).sin()) as f32;
            [x, x]
        }).collect();
        let (mut a, mut b) = (sig.clone(), sig);
        fx.process(&mut a);
        direct.process_interleaved(&mut b);
        assert_eq!(a, b);
    }
}
