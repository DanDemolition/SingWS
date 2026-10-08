//! Port of `singws_master_audio.MasterAudioProcessor`.
//!
//! Signal order (unchanged): gate/expander -> tilt EQ -> optional exciter -> compressor + makeup
//! -> limiter -> hard clip. All dynamics share one detection signal, so the same gain is applied to
//! every channel. Every smoother is the same one-pole `y[n] = (1-a)x[n] + a*y[n-1]` the Python
//! version runs through `scipy.signal.lfilter`, and every biquad is direct-form-II-transposed like
//! `scipy.signal.sosfilt`, so results agree to floating-point rounding regardless of block size.

use std::f64::consts::PI;

/// Interleaved channel counts above this are passed through untouched.
pub const MAX_CHANNELS: usize = 8;
const MAX_EQ_SECTIONS: usize = 3;

/// Parameter order is `singws_master_audio.DEFAULT_PARAMS` insertion order; the Python wrapper
/// refuses to run if this count or order ever disagrees.
pub const PARAM_NAMES: [&str; 28] = [
    "gate_enabled",
    "gate_threshold_db",
    "gate_ratio",
    "gate_attack_ms",
    "gate_release_ms",
    "gate_floor_db",
    "eq_enabled",
    "low_shelf_hz",
    "low_shelf_db",
    "presence_hz",
    "presence_db",
    "presence_q",
    "high_shelf_hz",
    "high_shelf_db",
    "exciter_mix",
    "exciter_hz",
    "comp_enabled",
    "comp_threshold_db",
    "comp_ratio",
    "comp_knee_db",
    "comp_attack_ms",
    "comp_release_ms",
    "comp_makeup_db",
    "limiter_enabled",
    "limiter_ceiling_db",
    "limiter_detector_ms",
    "limiter_release_ms",
    "output_ceiling_db",
];
pub const PARAM_COUNT: usize = PARAM_NAMES.len();

/// Defaults identical to `DEFAULT_PARAMS`.
const DEFAULTS: [f64; PARAM_COUNT] = [
    0.0, -58.0, 1.6, 5.0, 140.0, -18.0, // gate
    1.0, 90.0, 1.0, 3200.0, 1.0, 0.7, 9000.0, 1.5, // eq
    0.0, 4000.0, // exciter
    1.0, -20.0, 2.0, 6.0, 18.0, 180.0, 4.0, // compressor
    1.0, -1.0, 1.2, 80.0, // limiter
    -0.1, // output ceiling
];

/// Named view of the parameter array.
#[derive(Clone, Copy, Debug)]
pub struct Params {
    pub gate_enabled: f64,
    pub gate_threshold_db: f64,
    pub gate_ratio: f64,
    pub gate_attack_ms: f64,
    pub gate_release_ms: f64,
    pub gate_floor_db: f64,
    pub eq_enabled: f64,
    pub low_shelf_hz: f64,
    pub low_shelf_db: f64,
    pub presence_hz: f64,
    pub presence_db: f64,
    pub presence_q: f64,
    pub high_shelf_hz: f64,
    pub high_shelf_db: f64,
    pub exciter_mix: f64,
    pub exciter_hz: f64,
    pub comp_enabled: f64,
    pub comp_threshold_db: f64,
    pub comp_ratio: f64,
    pub comp_knee_db: f64,
    pub comp_attack_ms: f64,
    pub comp_release_ms: f64,
    pub comp_makeup_db: f64,
    pub limiter_enabled: f64,
    pub limiter_ceiling_db: f64,
    pub limiter_detector_ms: f64,
    pub limiter_release_ms: f64,
    pub output_ceiling_db: f64,
}

impl Params {
    pub fn from_array(v: &[f64; PARAM_COUNT]) -> Self {
        Self {
            gate_enabled: v[0],
            gate_threshold_db: v[1],
            gate_ratio: v[2],
            gate_attack_ms: v[3],
            gate_release_ms: v[4],
            gate_floor_db: v[5],
            eq_enabled: v[6],
            low_shelf_hz: v[7],
            low_shelf_db: v[8],
            presence_hz: v[9],
            presence_db: v[10],
            presence_q: v[11],
            high_shelf_hz: v[12],
            high_shelf_db: v[13],
            exciter_mix: v[14],
            exciter_hz: v[15],
            comp_enabled: v[16],
            comp_threshold_db: v[17],
            comp_ratio: v[18],
            comp_knee_db: v[19],
            comp_attack_ms: v[20],
            comp_release_ms: v[21],
            comp_makeup_db: v[22],
            limiter_enabled: v[23],
            limiter_ceiling_db: v[24],
            limiter_detector_ms: v[25],
            limiter_release_ms: v[26],
            output_ceiling_db: v[27],
        }
    }

    pub fn defaults() -> Self {
        Self::from_array(&DEFAULTS)
    }
}

fn db_to_lin(db: f64) -> f64 {
    10f64.powf(db / 20.0)
}

/// `_one_pole_alpha`
fn one_pole_alpha(time_ms: f64, sample_rate: f64) -> f64 {
    let tau = (time_ms / 1000.0).max(1e-4);
    (-1.0 / (sample_rate.max(1.0) * tau)).exp()
}

/// Normalised biquad (a0 == 1).
#[derive(Clone, Copy, Default)]
struct Biquad {
    b0: f64,
    b1: f64,
    b2: f64,
    a1: f64,
    a2: f64,
}

impl Biquad {
    fn from_rbj(b0: f64, b1: f64, b2: f64, a0: f64, a1: f64, a2: f64) -> Self {
        Self { b0: b0 / a0, b1: b1 / a0, b2: b2 / a0, a1: a1 / a0, a2: a2 / a0 }
    }

    fn low_shelf(freq: f64, gain_db: f64, sr: f64, q: f64) -> Self {
        let a = 10f64.powf(gain_db / 40.0);
        let w0 = 2.0 * PI * freq / sr;
        let (cw, sw) = (w0.cos(), w0.sin());
        let alpha = sw / 2.0 * ((a + 1.0 / a) * (1.0 / q - 1.0) + 2.0).sqrt();
        let tsa = 2.0 * a.sqrt() * alpha;
        Self::from_rbj(
            a * ((a + 1.0) - (a - 1.0) * cw + tsa),
            2.0 * a * ((a - 1.0) - (a + 1.0) * cw),
            a * ((a + 1.0) - (a - 1.0) * cw - tsa),
            (a + 1.0) + (a - 1.0) * cw + tsa,
            -2.0 * ((a - 1.0) + (a + 1.0) * cw),
            (a + 1.0) + (a - 1.0) * cw - tsa,
        )
    }

    fn high_shelf(freq: f64, gain_db: f64, sr: f64, q: f64) -> Self {
        let a = 10f64.powf(gain_db / 40.0);
        let w0 = 2.0 * PI * freq / sr;
        let (cw, sw) = (w0.cos(), w0.sin());
        let alpha = sw / 2.0 * ((a + 1.0 / a) * (1.0 / q - 1.0) + 2.0).sqrt();
        let tsa = 2.0 * a.sqrt() * alpha;
        Self::from_rbj(
            a * ((a + 1.0) + (a - 1.0) * cw + tsa),
            -2.0 * a * ((a - 1.0) + (a + 1.0) * cw),
            a * ((a + 1.0) + (a - 1.0) * cw - tsa),
            (a + 1.0) - (a - 1.0) * cw + tsa,
            2.0 * ((a - 1.0) - (a + 1.0) * cw),
            (a + 1.0) - (a - 1.0) * cw - tsa,
        )
    }

    fn peaking(freq: f64, gain_db: f64, sr: f64, q: f64) -> Self {
        let a = 10f64.powf(gain_db / 40.0);
        let w0 = 2.0 * PI * freq / sr;
        let (cw, sw) = (w0.cos(), w0.sin());
        let alpha = sw / (2.0 * q);
        Self::from_rbj(1.0 + alpha * a, -2.0 * cw, 1.0 - alpha * a, 1.0 + alpha / a, -2.0 * cw, 1.0 - alpha / a)
    }

    fn highpass(freq: f64, sr: f64, q: f64) -> Self {
        let w0 = 2.0 * PI * freq / sr;
        let (cw, sw) = (w0.cos(), w0.sin());
        let alpha = sw / (2.0 * q);
        Self::from_rbj((1.0 + cw) / 2.0, -(1.0 + cw), (1.0 + cw) / 2.0, 1.0 + alpha, -2.0 * cw, 1.0 - alpha)
    }

    /// Direct form II transposed, the form `scipy.signal.sosfilt` uses.
    #[inline]
    fn tick(&self, x: f64, z: &mut [f64; 2]) -> f64 {
        let y = self.b0 * x + z[0];
        z[0] = flush(self.b1 * x - self.a1 * y + z[1]);
        z[1] = flush(self.b2 * x - self.a2 * y);
        y
    }
}

/// Keep recursive states out of the denormal range (a CPU-time hazard on silence tails). The threshold is
/// ~12 orders of magnitude below one 16-bit step, so it cannot change what is heard.
#[inline]
fn flush(v: f64) -> f64 {
    if v.abs() < 1e-30 { 0.0 } else { v }
}

/// One-pole smoother state: `y = (1-a)*x + a*y_prev` (the lfilter form the Python code runs).
#[inline]
fn smooth(x: f64, alpha: f64, prev: &mut f64) -> f64 {
    let y = (1.0 - alpha) * x + alpha * *prev;
    *prev = flush(y);
    y
}

/// The processor. Not thread-safe by itself: the FFI layer hands it one owner (the audio thread) and
/// moves configuration changes in through a side channel.
pub struct MasterProcessor {
    sample_rate: f64,
    channels: usize,
    params: Params,

    eq: [Biquad; MAX_EQ_SECTIONS],
    eq_n: usize,
    exc: Option<Biquad>,
    gate_det_a: f64,
    gate_rel_a: f64,
    comp_det_a: f64,
    comp_rel_a: f64,
    lim_det_a: f64,
    lim_rel_a: f64,
    comp_makeup_lin: f64,
    lim_ceiling_lin: f64,
    out_ceiling_lin: f64,

    eq_z: [[[f64; 2]; MAX_CHANNELS]; MAX_EQ_SECTIONS],
    exc_z: [[f64; 2]; MAX_CHANNELS],
    gate_det: f64,
    gate_gain: f64,
    comp_det: f64,
    comp_gain: f64,
    lim_det: f64,
    lim_gain: f64,
    lim_out_last: f64,
}

impl MasterProcessor {
    pub fn new(sample_rate: f64, channels: usize) -> Self {
        let mut p = Self {
            sample_rate,
            channels: channels.max(1),
            params: Params::defaults(),
            eq: [Biquad::default(); MAX_EQ_SECTIONS],
            eq_n: 0,
            exc: None,
            gate_det_a: 0.0,
            gate_rel_a: 0.0,
            comp_det_a: 0.0,
            comp_rel_a: 0.0,
            lim_det_a: 0.0,
            lim_rel_a: 0.0,
            comp_makeup_lin: 1.0,
            lim_ceiling_lin: 1.0,
            out_ceiling_lin: 1.0,
            eq_z: [[[0.0; 2]; MAX_CHANNELS]; MAX_EQ_SECTIONS],
            exc_z: [[0.0; 2]; MAX_CHANNELS],
            gate_det: 0.0,
            gate_gain: 1.0,
            comp_det: 0.0,
            comp_gain: 1.0,
            lim_det: 0.0,
            lim_gain: 1.0,
            lim_out_last: 1.0,
        };
        p.rebuild();
        p
    }

    pub fn params(&self) -> &Params {
        &self.params
    }

    /// `set_params`: a full replacement, then rebuild (which, like the Python version, clears all state).
    pub fn set_params(&mut self, values: &[f64; PARAM_COUNT]) {
        self.params = Params::from_array(values);
        self.rebuild();
    }

    /// `configure_stream`: a no-op when rate and channel count are unchanged.
    pub fn configure_stream(&mut self, sample_rate: f64, channels: usize) {
        let ch = channels.max(1);
        if (sample_rate - self.sample_rate).abs() < 0.5 && ch == self.channels {
            return;
        }
        self.sample_rate = sample_rate;
        self.channels = ch;
        self.rebuild();
    }

    /// `reset_state`: forget all filter and envelope memory.
    pub fn reset_state(&mut self) {
        self.reset_state_inner();
    }

    /// Current gain of each dynamics stage as of the last processed sample, in dB (`gain_reduction_db`).
    pub fn gain_reduction_db(&self) -> [f64; 3] {
        let to_db = |g: f64| 20.0 * g.max(1e-7).log10();
        [to_db(self.gate_gain_last()), to_db(self.comp_gain), to_db(self.lim_gain_last())]
    }

    // The Python code reports the *final smoothed* gain of each stage; the limiter reports min(target, smoothed).
    fn gate_gain_last(&self) -> f64 {
        self.gate_gain
    }
    fn lim_gain_last(&self) -> f64 {
        self.lim_out_last
    }

    fn reset_state_inner(&mut self) {
        self.eq_z = [[[0.0; 2]; MAX_CHANNELS]; MAX_EQ_SECTIONS];
        self.exc_z = [[0.0; 2]; MAX_CHANNELS];
        // Detectors start at silence; gain smoothers start at unity so the first block plays at full level.
        self.gate_det = 0.0;
        self.gate_gain = 1.0;
        self.comp_det = 0.0;
        self.comp_gain = 1.0;
        self.lim_det = 0.0;
        self.lim_gain = 1.0;
        self.lim_out_last = 1.0;
    }

    fn rebuild(&mut self) {
        let p = self.params;
        let sr = self.sample_rate;
        self.eq_n = 0;
        if p.eq_enabled >= 0.5 {
            if p.low_shelf_db.abs() > 0.01 {
                self.eq[self.eq_n] = Biquad::low_shelf(p.low_shelf_hz, p.low_shelf_db, sr, 0.707);
                self.eq_n += 1;
            }
            if p.presence_db.abs() > 0.01 {
                self.eq[self.eq_n] = Biquad::peaking(p.presence_hz, p.presence_db, sr, p.presence_q);
                self.eq_n += 1;
            }
            if p.high_shelf_db.abs() > 0.01 {
                self.eq[self.eq_n] = Biquad::high_shelf(p.high_shelf_hz, p.high_shelf_db, sr, 0.707);
                self.eq_n += 1;
            }
        }
        self.exc = if p.exciter_mix > 0.001 { Some(Biquad::highpass(p.exciter_hz, sr, 0.707)) } else { None };

        self.gate_det_a = one_pole_alpha(p.gate_attack_ms, sr);
        self.gate_rel_a = one_pole_alpha(p.gate_release_ms, sr);
        self.comp_det_a = one_pole_alpha(p.comp_attack_ms, sr);
        self.comp_rel_a = one_pole_alpha(p.comp_release_ms, sr);
        self.lim_det_a = one_pole_alpha(p.limiter_detector_ms, sr);
        self.lim_rel_a = one_pole_alpha(p.limiter_release_ms, sr);
        self.comp_makeup_lin = db_to_lin(p.comp_makeup_db);
        self.lim_ceiling_lin = db_to_lin(p.limiter_ceiling_db);
        self.out_ceiling_lin = db_to_lin(p.output_ceiling_db);
        self.reset_state_inner();
    }

    /// Process interleaved samples in place. No allocation, no locks. Buffers whose length is not a whole
    /// number of frames, or with more channels than `MAX_CHANNELS`, are left untouched (the Python version
    /// likewise returns the input for a ragged block).
    pub fn process_interleaved(&mut self, buf: &mut [f32]) {
        let ch = self.channels;
        if ch > MAX_CHANNELS || buf.len() % ch != 0 {
            return;
        }
        let p = self.params;
        let gate_on = p.gate_enabled >= 0.5 && p.gate_ratio > 1.0;
        let comp_on = p.comp_enabled >= 0.5 && p.comp_ratio > 1.0;
        let lim_on = p.limiter_enabled >= 0.5;
        let exc_mix = p.exciter_mix;
        let inv_ch = 1.0 / ch as f64;
        let knee = p.comp_knee_db.max(0.0);
        let slope = 1.0 / p.comp_ratio - 1.0;

        let mut x = [0.0f64; MAX_CHANNELS];
        for frame in buf.chunks_exact_mut(ch) {
            for c in 0..ch {
                x[c] = frame[c] as f64;
            }

            // ---- Gate / downward expander ----
            if gate_on {
                let mut sum = 0.0;
                for c in 0..ch {
                    sum += x[c].abs();
                }
                let det = sum * inv_ch;
                let env = smooth(det, self.gate_det_a, &mut self.gate_det).max(1e-7);
                let env_db = 20.0 * env.log10();
                let deficit = (env_db - p.gate_threshold_db).min(0.0);
                let gain_db = (deficit * (p.gate_ratio - 1.0)).max(p.gate_floor_db);
                let target = 10f64.powf(gain_db / 20.0);
                let g = smooth(target, self.gate_rel_a, &mut self.gate_gain);
                for c in 0..ch {
                    x[c] *= g;
                }
            }

            // ---- Tilt EQ ----
            for s in 0..self.eq_n {
                let bq = self.eq[s];
                for c in 0..ch {
                    x[c] = bq.tick(x[c], &mut self.eq_z[s][c]);
                }
            }

            // ---- Exciter ----
            if let Some(hp) = self.exc {
                if exc_mix > 0.001 {
                    for c in 0..ch {
                        let h = hp.tick(x[c], &mut self.exc_z[c]);
                        let excited = (h * 2.0).tanh() * 0.5;
                        x[c] += excited * exc_mix;
                    }
                }
            }

            // ---- Compressor (soft knee) ----
            if comp_on {
                let mut sum = 0.0;
                for c in 0..ch {
                    sum += x[c].abs();
                }
                let det = sum * inv_ch;
                let env = smooth(det, self.comp_det_a, &mut self.comp_det).max(1e-7);
                let over = 20.0 * env.log10() - p.comp_threshold_db;
                let gr = if knee > 0.0 {
                    let half = knee / 2.0;
                    if over > -half && over < half {
                        let k = over + half;
                        slope * (k * k) / (2.0 * knee)
                    } else if over >= half {
                        slope * over
                    } else {
                        0.0
                    }
                } else if over > 0.0 {
                    slope * over
                } else {
                    0.0
                };
                let target = 10f64.powf(gr / 20.0);
                let g = smooth(target, self.comp_rel_a, &mut self.comp_gain);
                for c in 0..ch {
                    x[c] *= g;
                }
                for c in 0..ch {
                    x[c] *= self.comp_makeup_lin;
                }
            }

            // ---- Limiter (instantaneous attack via min with the target) ----
            if lim_on {
                let mut peak = 0.0f64;
                for c in 0..ch {
                    peak = peak.max(x[c].abs());
                }
                let penv = smooth(peak, self.lim_det_a, &mut self.lim_det).max(1e-7);
                let target = (self.lim_ceiling_lin / penv).min(1.0);
                let smoothed = smooth(target, self.lim_rel_a, &mut self.lim_gain);
                let lg = target.min(smoothed);
                self.lim_out_last = lg;
                for c in 0..ch {
                    x[c] *= lg;
                }
            }

            // ---- Hard clip guard ----
            let ceil = self.out_ceiling_lin;
            for c in 0..ch {
                frame[c] = x[c].clamp(-ceil, ceil) as f32;
            }
        }
    }
}
