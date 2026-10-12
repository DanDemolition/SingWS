//! Key and tempo for karaoke audio, built on Signalsmith Stretch.
//!
//! The two controls are independent by construction: the pitch factor is the ONLY input to the transpose, and tempo is only how many
//! output frames are asked for per input block (`n / speed`). Changing either live never touches the other and never restarts the
//! stretcher, so there is no click (measured on the mpv filter; see tools/stretch_pitch_check.py).

use std::os::raw::{c_double, c_int, c_void};

unsafe extern "C" {
    fn singws_stretch_new(channels: c_int, rate: c_int) -> *mut c_void;
    fn singws_stretch_free(h: *mut c_void);
    fn singws_stretch_reset(h: *mut c_void);
    fn singws_stretch_set_pitch(h: *mut c_void, factor: c_double);
    fn singws_stretch_latency(h: *mut c_void) -> c_int;
    fn singws_stretch_output_latency(h: *mut c_void) -> c_int;
    fn singws_stretch_process(h: *mut c_void, input: *const *const f32, n: c_int, output: *const *mut f32, m: c_int);
    fn singws_stretch_flush(h: *mut c_void, output: *const *mut f32, m: c_int);
}

/// Streaming stereo stretcher. `process` takes interleaved stereo and returns interleaved stereo.
pub struct Stretcher {
    h: *mut c_void,
    rate: u32,
    pitch: f64,
    speed: f64,
    carry: f64,
    l_in: Vec<f32>,
    r_in: Vec<f32>,
    l_out: Vec<f32>,
    r_out: Vec<f32>,
}

// The handle is owned exclusively by this struct and only used through &mut self.
unsafe impl Send for Stretcher {}

impl Stretcher {
    pub fn new(rate: u32) -> Option<Self> {
        Self::with_capacity(rate, 0, 0)
    }

    /// Like [`new`](Self::new) but pre-sizes the scratch buffers for blocks of up to `max_in_frames` input and `max_out_frames` output
    /// frames, so that `process_into` / `flush_into` never allocate on the audio thread for blocks within those limits.
    pub fn with_capacity(rate: u32, max_in_frames: usize, max_out_frames: usize) -> Option<Self> {
        let h = unsafe { singws_stretch_new(2, rate as c_int) };
        if h.is_null() {
            return None;
        }
        Some(Self {
            h,
            rate,
            pitch: 1.0,
            speed: 1.0,
            carry: 0.0,
            l_in: Vec::with_capacity(max_in_frames),
            r_in: Vec::with_capacity(max_in_frames),
            l_out: Vec::with_capacity(max_out_frames.max(max_in_frames)),
            r_out: Vec::with_capacity(max_out_frames.max(max_in_frames)),
        })
    }

    pub fn rate(&self) -> u32 {
        self.rate
    }

    /// Key change as a frequency factor (2^(semitones/12)). Takes effect on the next block; never restarts the stretcher.
    pub fn set_pitch_factor(&mut self, factor: f64) {
        self.pitch = factor.clamp(0.1, 10.0);
        unsafe { singws_stretch_set_pitch(self.h, self.pitch) };
    }

    pub fn set_semitones(&mut self, semitones: f64) {
        self.set_pitch_factor(2f64.powf(semitones / 12.0));
    }

    /// Tempo as a playback speed (1.0 = normal, 0.9 = 10% slower). Independent of the pitch.
    pub fn set_speed(&mut self, speed: f64) {
        self.speed = speed.clamp(0.1, 10.0);
    }

    pub fn pitch_factor(&self) -> f64 {
        self.pitch
    }

    pub fn speed(&self) -> f64 {
        self.speed
    }

    /// Input frames held inside the stretcher (input + output latency), for timestamp compensation.
    pub fn latency_frames(&self) -> usize {
        unsafe { singws_stretch_latency(self.h).max(0) as usize }
    }

    /// Frames of INPUT the stretcher must consume before its first output sample (independent of the tempo).
    pub fn input_latency_frames(&self) -> usize {
        unsafe { (singws_stretch_latency(self.h) - singws_stretch_output_latency(self.h)).max(0) as usize }
    }

    /// Frames of OUTPUT between a sample entering the processing window and leaving it. In SONG time this is `speed` times as long.
    pub fn output_latency_frames(&self) -> usize {
        unsafe { singws_stretch_output_latency(self.h).max(0) as usize }
    }

    pub fn reset(&mut self) {
        unsafe { singws_stretch_reset(self.h) };
        self.carry = 0.0;
    }

    /// Feeds `input` (interleaved stereo) and appends the produced frames to `out` (interleaved stereo). The number of output frames is
    /// `input_frames / speed` with the fraction carried to the next call, so the long-run length is exact.
    pub fn process(&mut self, input: &[f32], out: &mut Vec<f32>) {
        let n = input.len() / 2;
        if n == 0 {
            return;
        }
        let want = n as f64 / self.speed + self.carry;
        let m = want.floor() as usize;
        self.carry = want - m as f64;
        self.l_in.clear();
        self.r_in.clear();
        for f in input.chunks_exact(2) {
            self.l_in.push(f[0]);
            self.r_in.push(f[1]);
        }
        self.l_out.resize(m, 0.0);
        self.r_out.resize(m, 0.0);
        let ip = [self.l_in.as_ptr(), self.r_in.as_ptr()];
        let op = [self.l_out.as_mut_ptr(), self.r_out.as_mut_ptr()];
        unsafe { singws_stretch_process(self.h, ip.as_ptr(), n as c_int, op.as_ptr(), m as c_int) };
        out.reserve(m * 2);
        for i in 0..m {
            out.push(self.l_out[i]);
            out.push(self.r_out[i]);
        }
    }

    /// Pull-model processing: feeds ALL of `input` (interleaved stereo) and writes exactly `out.len() / 2` frames to `out`. The caller
    /// decides the ratio by choosing the sizes (input frames per output frame = the speed). Allocation-free within the capacity given to
    /// [`with_capacity`](Self::with_capacity).
    pub fn process_into(&mut self, input: &[f32], out: &mut [f32]) {
        let n = input.len() / 2;
        let m = out.len() / 2;
        if m == 0 {
            return;
        }
        self.l_in.clear();
        self.r_in.clear();
        for f in input.chunks_exact(2) {
            self.l_in.push(f[0]);
            self.r_in.push(f[1]);
        }
        self.l_out.resize(m, 0.0);
        self.r_out.resize(m, 0.0);
        let ip = [self.l_in.as_ptr(), self.r_in.as_ptr()];
        let op = [self.l_out.as_mut_ptr(), self.r_out.as_mut_ptr()];
        unsafe { singws_stretch_process(self.h, ip.as_ptr(), n as c_int, op.as_ptr(), m as c_int) };
        for i in 0..m {
            out[2 * i] = self.l_out[i];
            out[2 * i + 1] = self.r_out[i];
        }
    }

    /// Frames of tail still inside the stretcher when the input ends (what `flush` returns).
    pub fn tail_frames(&self) -> usize {
        unsafe { singws_stretch_output_latency(self.h).max(0) as usize }
    }

    /// Writes up to `out.len() / 2` frames of the tail (call at the end of a song; zero-fills what is not needed). Allocation-free within
    /// the capacity. Returns the number of tail frames written.
    pub fn flush_into(&mut self, out: &mut [f32]) -> usize {
        let m = (out.len() / 2).min(self.tail_frames());
        out.fill(0.0);
        if m == 0 {
            return 0;
        }
        self.l_out.resize(m, 0.0);
        self.r_out.resize(m, 0.0);
        let op = [self.l_out.as_mut_ptr(), self.r_out.as_mut_ptr()];
        unsafe { singws_stretch_flush(self.h, op.as_ptr(), m as c_int) };
        for i in 0..m {
            out[2 * i] = self.l_out[i];
            out[2 * i + 1] = self.r_out[i];
        }
        m
    }

    /// Drains the tail that is still inside the stretcher (call once at the end of a song).
    pub fn flush(&mut self, out: &mut Vec<f32>) {
        let m = unsafe { singws_stretch_output_latency(self.h).max(0) } as usize;
        if m == 0 {
            return;
        }
        self.l_out.resize(m, 0.0);
        self.r_out.resize(m, 0.0);
        let op = [self.l_out.as_mut_ptr(), self.r_out.as_mut_ptr()];
        unsafe { singws_stretch_flush(self.h, op.as_ptr(), m as c_int) };
        for i in 0..m {
            out.push(self.l_out[i]);
            out.push(self.r_out[i]);
        }
    }
}

impl Drop for Stretcher {
    fn drop(&mut self) {
        unsafe { singws_stretch_free(self.h) };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RATE: u32 = 44_100;

    fn sine(freq: f64, seconds: f64) -> Vec<f32> {
        let n = (RATE as f64 * seconds) as usize;
        let mut v = Vec::with_capacity(n * 2);
        for i in 0..n {
            let s = (0.3 * (2.0 * std::f64::consts::PI * freq * i as f64 / RATE as f64).sin()) as f32;
            v.push(s);
            v.push(s);
        }
        v
    }

    /// Frequency of the middle half of a stereo sine, from upward zero crossings (accurate to well under a cent on a clean tone).
    fn measured_hz(stereo: &[f32]) -> f64 {
        let mono: Vec<f32> = stereo.chunks_exact(2).map(|f| f[0]).collect();
        let a = mono.len() / 4;
        let b = mono.len() * 3 / 4;
        let seg = &mono[a..b];
        let mut first = None;
        let mut last = 0.0;
        let mut count = 0usize;
        for i in 1..seg.len() {
            if seg[i - 1] < 0.0 && seg[i] >= 0.0 {
                let frac = -seg[i - 1] as f64 / (seg[i] - seg[i - 1]) as f64;
                let pos = (i - 1) as f64 + frac;
                if first.is_none() {
                    first = Some(pos);
                } else {
                    count += 1;
                }
                last = pos;
            }
        }
        count as f64 / ((last - first.unwrap()) / RATE as f64)
    }

    fn run(freq: f64, semis: f64, speed: f64) -> (f64, f64) {
        let input = sine(freq, 6.0);
        let mut st = Stretcher::new(RATE).unwrap();
        st.set_semitones(semis);
        st.set_speed(speed);
        let mut out = Vec::new();
        for block in input.chunks(2048) {
            st.process(block, &mut out);
        }
        let ratio = (out.len() / 2) as f64 / (input.len() / 2) as f64 * speed;
        (measured_hz(&out), ratio)
    }

    fn cents(measured: f64, expected: f64) -> f64 {
        1200.0 * (measured / expected).log2()
    }

    #[test]
    fn tempo_alone_never_moves_the_key() {
        for speed in [0.5, 0.75, 0.9, 1.1, 1.5, 2.0] {
            let (hz, ratio) = run(440.0, 0.0, speed);
            assert!(cents(hz, 440.0).abs() < 1.0, "speed {speed}: {hz} Hz");
            assert!((ratio - 1.0).abs() < 0.005, "speed {speed}: length ratio {ratio}");
        }
    }

    #[test]
    fn key_alone_is_the_requested_shift() {
        for semis in [-12.0, -5.0, -1.0, 2.0, 7.0, 12.0] {
            let (hz, ratio) = run(440.0, semis, 1.0);
            assert!(cents(hz, 440.0 * 2f64.powf(semis / 12.0)).abs() < 8.0, "{semis}: {hz} Hz");
            assert!((ratio - 1.0).abs() < 0.005);
        }
    }

    #[test]
    fn key_and_tempo_together_are_both_right() {
        for (semis, speed) in [(2.0, 0.9), (-3.0, 1.1), (5.0, 0.75), (-7.0, 1.5)] {
            let (hz, ratio) = run(440.0, semis, speed);
            assert!(cents(hz, 440.0 * 2f64.powf(semis / 12.0)).abs() < 8.0, "{semis}/{speed}: {hz} Hz");
            assert!((ratio - 1.0).abs() < 0.005);
        }
    }

    #[test]
    fn live_changes_do_not_click() {
        // Abrupt tempo AND key jumps in the middle of a stream stay at the sine's own slope (a click is a spike far above it).
        let input = sine(440.0, 8.0);
        let mut st = Stretcher::new(RATE).unwrap();
        let mut out = Vec::new();
        for (i, block) in input.chunks(1024).enumerate() {
            let t = i as f64 * 1024.0 / RATE as f64;
            st.set_speed(if t > 5.0 { 1.0 } else if t > 3.0 { 0.6 } else if t > 1.5 { 1.5 } else { 1.0 });
            st.set_semitones(if t > 6.0 { 0.0 } else if t > 4.0 { -5.0 } else if t > 2.5 { 2.0 } else { 0.0 });
            st.process(block, &mut out);
        }
        let mono: Vec<f32> = out.chunks_exact(2).map(|f| f[0]).collect();
        let skip = RATE as usize / 2; // warm-up
        let worst = mono[skip..].windows(2).map(|w| (w[1] - w[0]).abs()).fold(0.0f32, f32::max);
        assert!(worst < 0.05, "largest sample step {worst} (a clean 440 Hz tone at this level is ~0.019, up to ~0.03 shifted up)");
    }

    #[test]
    fn process_into_matches_process() {
        let input = sine(440.0, 3.0);
        let mut a = Stretcher::new(RATE).unwrap();
        a.set_semitones(2.0);
        a.set_speed(0.8);
        let mut want = Vec::new();
        for block in input.chunks(2048) {
            a.process(block, &mut want);
        }
        let mut b = Stretcher::with_capacity(RATE, 4096, 4096).unwrap();
        b.set_semitones(2.0);
        let mut got = Vec::new();
        let mut carry = 0.0f64;
        for block in input.chunks(2048) {
            let n = block.len() / 2;
            let w = n as f64 / 0.8 + carry;
            let m = w.floor() as usize;
            carry = w - m as f64;
            let mut o = vec![0.0f32; m * 2];
            b.process_into(block, &mut o);
            got.extend_from_slice(&o);
        }
        assert_eq!(want.len(), got.len());
        assert!(want.iter().zip(&got).all(|(x, y)| (x - y).abs() < 1e-6));
    }

    #[test]
    fn flush_returns_the_tail() {
        let input = sine(440.0, 1.0);
        let mut st = Stretcher::new(RATE).unwrap();
        let mut out = Vec::new();
        for block in input.chunks(1024) {
            st.process(block, &mut out);
        }
        let before = out.len();
        st.flush(&mut out);
        assert!(out.len() > before);
        assert!(out.iter().all(|v| v.is_finite()));
    }
}
