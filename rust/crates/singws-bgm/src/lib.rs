//! Two-deck mixer core. No device, no decoding, no allocation in `mix`.
//! A deck is any `FnMut(&mut [f32]) -> usize` source via the `Source` trait (returns frames written; short = EOF).
//! Crossfade is scheduled in *frames* so it is sample-accurate and independent of block size.
//! Gate for the real engine (cpal + symphonia) is in RUST_MIGRATION_PLAN.md Stage 4; BASS stays the shipped engine.

pub trait Source {
    /// Fill `out` (interleaved stereo) and return the number of FRAMES written. Fewer than requested means end of stream.
    fn read(&mut self, out: &mut [f32]) -> usize;
}

/// Equal-power gains for position `t` in 0..=1: (outgoing, incoming).
pub fn equal_power(t: f32) -> (f32, f32) {
    let t = t.clamp(0.0, 1.0);
    let a = t * core::f32::consts::FRAC_PI_2;
    (a.cos(), a.sin())
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Phase { Solo, Fading { done: u64, total: u64 }, Finished }

pub struct Mixer<A: Source, B: Source> {
    pub a: A,
    pub b: B,
    phase: Phase,
    a_eof: bool,
    scratch_a: Vec<f32>,
    scratch_b: Vec<f32>,
    /// Frames of true silence output while no deck had audio (gap meter).
    pub gap_frames: u64,
}

impl<A: Source, B: Source> Mixer<A, B> {
    pub fn new(a: A, b: B, max_block_frames: usize) -> Self {
        Self { a, b, phase: Phase::Solo, a_eof: false, scratch_a: vec![0.0; max_block_frames * 2], scratch_b: vec![0.0; max_block_frames * 2], gap_frames: 0 }
    }
    pub fn phase(&self) -> Phase { self.phase }
    /// Begin an equal-power crossfade A -> B lasting `frames`.
    pub fn start_crossfade(&mut self, frames: u64) {
        if matches!(self.phase, Phase::Solo) { self.phase = Phase::Fading { done: 0, total: frames.max(1) }; }
    }
    /// Mix `out.len()/2` stereo frames. Never allocates (scratch sized at construction).
    pub fn mix(&mut self, out: &mut [f32]) {
        let frames = out.len() / 2;
        assert!(frames * 2 <= self.scratch_a.len(), "block larger than max_block_frames");
        let (sa, sb) = (&mut self.scratch_a[..frames * 2], &mut self.scratch_b[..frames * 2]);
        sa.fill(0.0);
        sb.fill(0.0);
        match self.phase {
            Phase::Solo => {
                let n = if self.a_eof { 0 } else { self.a.read(sa) };
                if n < frames { self.a_eof = true; }
                out.copy_from_slice(sa);
                self.gap_frames += (frames - n) as u64;
            }
            Phase::Fading { done, total } => {
                let na = if self.a_eof { 0 } else { self.a.read(sa) };
                if na < frames { self.a_eof = true; }
                let nb = self.b.read(sb);
                for i in 0..frames {
                    let t = ((done + i as u64) as f32 / total as f32).min(1.0);
                    let (ga, gb) = equal_power(t);
                    out[2 * i] = sa[2 * i] * ga + sb[2 * i] * gb;
                    out[2 * i + 1] = sa[2 * i + 1] * ga + sb[2 * i + 1] * gb;
                }
                self.gap_frames += (frames - na.max(nb)) as u64;
                let done = done + frames as u64;
                self.phase = if done >= total { Phase::Finished } else { Phase::Fading { done, total } };
            }
            Phase::Finished => {
                let n = self.b.read(sb);
                out.copy_from_slice(sb);
                self.gap_frames += (frames - n) as u64;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Sine { phase: f64, step: f64, amp: f32, left: usize }
    impl Sine {
        fn new(hz: f64, rate: f64, amp: f32, frames: usize) -> Self { Self { phase: 0.0, step: hz / rate, amp, left: frames } }
    }
    impl Source for Sine {
        fn read(&mut self, out: &mut [f32]) -> usize {
            let n = (out.len() / 2).min(self.left);
            for i in 0..n {
                let v = (self.phase * core::f64::consts::TAU).sin() as f32 * self.amp;
                out[2 * i] = v; out[2 * i + 1] = v;
                self.phase += self.step;
            }
            self.left -= n;
            n
        }
    }
    /// Constant-DC source: makes power-sum checks exact (uncorrelated-signal law is checked with sines below).
    struct Dc(f32, usize);
    impl Source for Dc {
        fn read(&mut self, out: &mut [f32]) -> usize {
            let n = (out.len() / 2).min(self.1);
            for v in &mut out[..n * 2] { *v = self.0; }
            self.1 -= n;
            n
        }
    }

    #[test]
    fn equal_power_gains_sum_to_unit_power() {
        for i in 0..=100 {
            let (a, b) = equal_power(i as f32 / 100.0);
            assert!((a * a + b * b - 1.0).abs() < 1e-6);
        }
        assert_eq!(equal_power(0.0).0, 1.0);
        assert!(equal_power(1.0).1 > 0.999_999);
    }

    #[test]
    fn crossfade_is_block_size_independent() {
        let render = |block: usize| {
            let mut m = Mixer::new(Dc(0.5, 10_000), Dc(0.5, 10_000), 4096);
            m.start_crossfade(4000);
            let mut all = vec![];
            let mut buf = vec![0.0; block * 2];
            for _ in 0..(6000 / block) { m.mix(&mut buf); all.extend_from_slice(&buf); }
            all
        };
        let (x, y) = (render(100), render(500));
        for i in 0..x.len().min(y.len()) { assert!((x[i] - y[i]).abs() < 1e-6, "diverged at {i}"); }
    }

    #[test]
    fn rms_level_is_steady_through_crossfade_of_uncorrelated_tones() {
        let rate = 48_000.0;
        let mut m = Mixer::new(Sine::new(440.0, rate, 0.5, 200_000), Sine::new(997.0, rate, 0.5, 200_000), 4800);
        m.start_crossfade(96_000);
        let mut buf = vec![0.0; 4800 * 2];
        let mut levels = vec![];
        for _ in 0..20 {
            m.mix(&mut buf);
            let p: f32 = buf.chunks(2).map(|f| f[0] * f[0]).sum::<f32>() / 4800.0;
            levels.push(10.0 * p.log10());
        }
        let (mn, mx) = levels.iter().fold((f32::MAX, f32::MIN), |(a, b), &v| (a.min(v), b.max(v)));
        assert!(mx - mn < 0.5, "crossfade level swing {:.2} dB (target < 0.5 dB)", mx - mn);
    }

    #[test]
    fn no_gap_when_incoming_deck_is_ready() {
        // A ends inside the fade window; B covers it: zero silent frames.
        let mut m = Mixer::new(Dc(0.4, 3000), Dc(0.4, 20_000), 4096);
        m.start_crossfade(5000);
        let mut buf = vec![0.0; 500 * 2];
        for _ in 0..30 { m.mix(&mut buf); }
        assert_eq!(m.gap_frames, 0);
        assert_eq!(m.phase(), Phase::Finished);
    }

    #[test]
    fn gap_is_counted_when_nothing_is_playing() {
        let mut m = Mixer::new(Dc(0.4, 100), Dc(0.0, 0), 1024);
        let mut buf = vec![0.0; 200 * 2];
        m.mix(&mut buf);
        assert_eq!(m.gap_frames, 100);
    }

    #[test]
    fn mix_does_not_reallocate() {
        let mut m = Mixer::new(Dc(0.1, 1_000_000), Dc(0.1, 1_000_000), 512);
        let (pa, pb) = (m.scratch_a.as_ptr(), m.scratch_b.as_ptr());
        m.start_crossfade(10_000);
        let mut buf = vec![0.0; 512 * 2];
        for _ in 0..100 { m.mix(&mut buf); }
        assert_eq!((pa, pb), (m.scratch_a.as_ptr(), m.scratch_b.as_ptr()));
    }
}
