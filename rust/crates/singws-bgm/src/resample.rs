//! Streaming stereo resampler: the same rubato `Fft` resampler that `decode::resample_stereo` uses on a whole clip, driven chunk by
//! chunk so a decoder thread can feed it packet by packet. The output is sample-for-sample identical to the whole-clip result
//! (startup delay trimmed, length exactly `ceil(ratio * input_frames)`), whatever the chunking of the input; a test pins that.

use audioadapter_buffers::direct::InterleavedSlice;
use rubato::{Fft, FixedSync, Indexing, Resampler};

use crate::decode::DecodeError;

pub struct StreamResampler {
    rs: Option<Fft<f32>>,
    ratio: f64,
    /// Input not yet consumed (interleaved stereo).
    pending: Vec<f32>,
    out_buf: Vec<f32>,
    frames_in: u64,
    frames_out: u64,
    to_trim: usize,
}

impl StreamResampler {
    /// `from == to` makes a pass-through.
    pub fn new(from: u32, to: u32) -> Result<Self, DecodeError> {
        if from == to {
            return Ok(Self { rs: None, ratio: 1.0, pending: vec![], out_buf: vec![], frames_in: 0, frames_out: 0, to_trim: 0 });
        }
        let rs = Fft::<f32>::new(from as usize, to as usize, 1024, 2, FixedSync::Input).map_err(|e| DecodeError::Resample(e.to_string()))?;
        let max_out = rs.output_frames_max();
        let to_trim = rs.output_delay();
        Ok(Self {
            ratio: to as f64 / from as f64,
            rs: Some(rs),
            pending: Vec::new(),
            out_buf: vec![0.0; max_out * 2],
            frames_in: 0,
            frames_out: 0,
            to_trim,
        })
    }

    /// Frames the finished output will contain for `input_frames` input frames.
    pub fn output_len_for(&self, input_frames: u64) -> u64 {
        if self.rs.is_none() { input_frames } else { (self.ratio * input_frames as f64).ceil() as u64 }
    }

    /// Feed interleaved stereo; resampled frames are appended to `out`.
    pub fn push(&mut self, input: &[f32], out: &mut Vec<f32>) -> Result<(), DecodeError> {
        if self.rs.is_none() {
            out.extend_from_slice(input);
            self.frames_in += (input.len() / 2) as u64;
            self.frames_out += (input.len() / 2) as u64;
            return Ok(());
        }
        self.frames_in += (input.len() / 2) as u64;
        self.pending.extend_from_slice(input);
        loop {
            let need = self.rs.as_ref().unwrap().input_frames_next();
            // rubato's own loop only processes a full chunk while MORE than a chunk remains (the last chunk goes through the
            // partial path); keep that rule so the result is identical to `process_all`.
            if self.pending.len() / 2 <= need {
                break;
            }
            let (used, produced) = self.run_chunk(None, need)?;
            self.pending.drain(..used * 2);
            self.emit(produced, out);
        }
        Ok(())
    }

    /// Flush: processes the tail and the filter delay, appending the last frames to `out`.
    pub fn finish(&mut self, out: &mut Vec<f32>) -> Result<(), DecodeError> {
        let Some(rs) = self.rs.as_ref() else { return Ok(()) };
        let _ = rs;
        let expected = self.output_len_for(self.frames_in);
        let left = self.pending.len() / 2;
        if left > 0 {
            let need = self.rs.as_ref().unwrap().input_frames_next();
            let (_, produced) = self.run_chunk(Some(left), need.max(left))?;
            self.pending.clear();
            self.emit(produced, out);
        }
        // pump silence until the whole expected output (plus the delay still to drop) has come out
        let mut guard = 0;
        while self.frames_out < expected && guard < 64 {
            let need = self.rs.as_ref().unwrap().input_frames_next();
            let (_, produced) = self.run_chunk(Some(0), need)?;
            self.emit(produced, out);
            guard += 1;
        }
        Ok(())
    }

    fn run_chunk(&mut self, partial: Option<usize>, need: usize) -> Result<(usize, usize), DecodeError> {
        let rs = self.rs.as_mut().unwrap();
        let frames_avail = (self.pending.len() / 2).max(need);
        if self.pending.len() < frames_avail * 2 {
            self.pending.resize(frames_avail * 2, 0.0);
        }
        let input = InterleavedSlice::new(&self.pending, 2, frames_avail).map_err(|e| DecodeError::Resample(e.to_string()))?;
        let out_frames = self.out_buf.len() / 2;
        let mut output = InterleavedSlice::new_mut(&mut self.out_buf, 2, out_frames).map_err(|e| DecodeError::Resample(e.to_string()))?;
        let indexing = Indexing { input_offset: 0, output_offset: 0, active_channels_mask: None, partial_len: partial };
        rs.process_into_buffer(&input, &mut output, Some(&indexing)).map_err(|e| DecodeError::Resample(e.to_string()))
    }

    /// Append `produced` frames from `out_buf` to `out`, dropping the startup delay first and never exceeding the expected length.
    fn emit(&mut self, produced: usize, out: &mut Vec<f32>) {
        let mut start = 0usize;
        if self.to_trim > 0 {
            let drop = self.to_trim.min(produced);
            start = drop;
            self.to_trim -= drop;
        }
        let mut n = produced - start;
        // The length is only final once all input is in; while streaming, never emit past what ratio * frames_in allows.
        let cap = self.output_len_for(self.frames_in).saturating_sub(self.frames_out) as usize;
        n = n.min(cap);
        out.extend_from_slice(&self.out_buf[start * 2..(start + n) * 2]);
        self.frames_out += n as u64;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::decode::resample_stereo;

    fn noise_and_tone(frames: usize) -> Vec<f32> {
        let mut seed = 12345u32;
        let mut v = Vec::with_capacity(frames * 2);
        for i in 0..frames {
            seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
            let n = (seed >> 8) as f32 / (1u32 << 24) as f32 - 0.5;
            let t = (2.0 * std::f64::consts::PI * 997.0 * i as f64 / 44_100.0).sin() as f32;
            v.push(0.3 * t + 0.1 * n);
            v.push(0.3 * t - 0.1 * n);
        }
        v
    }

    fn stream(input: &[f32], chunk_frames: usize, from: u32, to: u32) -> Vec<f32> {
        let mut rs = StreamResampler::new(from, to).unwrap();
        let mut out = Vec::new();
        for c in input.chunks(chunk_frames * 2) {
            rs.push(c, &mut out).unwrap();
        }
        rs.finish(&mut out).unwrap();
        out
    }

    #[test]
    fn streaming_equals_whole_clip_for_any_chunking() {
        let input = noise_and_tone(50_000);
        let whole = resample_stereo(input.clone(), 44_100, 48_000).unwrap();
        for chunk in [1usize, 7, 441, 1152, 4096, 49_999, 50_000] {
            let s = stream(&input, chunk, 44_100, 48_000);
            assert_eq!(s.len(), whole.len(), "length differs for chunk {chunk}");
            let worst = s.iter().zip(&whole).map(|(a, b)| (a - b).abs()).fold(0.0f32, f32::max);
            assert!(worst < 1e-6, "chunk {chunk}: worst difference {worst}");
        }
    }

    #[test]
    fn a_clip_shorter_than_one_chunk_still_comes_out_complete() {
        let input = noise_and_tone(300);
        let whole = resample_stereo(input.clone(), 44_100, 48_000).unwrap();
        let s = stream(&input, 100, 44_100, 48_000);
        assert_eq!(s.len(), whole.len());
    }

    #[test]
    fn same_rate_passes_through() {
        let input = noise_and_tone(1000);
        assert_eq!(stream(&input, 77, 48_000, 48_000), input);
    }
}
