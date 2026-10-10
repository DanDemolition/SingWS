//! File decoding for the BGM engine (milestone 1): symphonia decodes to interleaved f32, the result is made stereo and resampled to
//! the engine rate with rubato's FFT resampler (a fixed ratio, so no drift tracking is needed), then served as an in-memory deck.
//!
//! This is the OFFLINE path: a whole track is decoded before playing. The live engine will decode on a worker thread into a ring
//! buffer (milestone 3); the per-frame result must be identical, which is what the tests pin.

use std::fs::File;
use std::path::Path;
use std::sync::Arc;

use audioadapter_buffers::direct::InterleavedSlice;
use rubato::{Fft, FixedSync, Resampler};
use symphonia::core::codecs::audio::AudioDecoderOptions;
use symphonia::core::errors::Error as SymError;
use symphonia::core::formats::probe::Hint;
use symphonia::core::formats::{FormatOptions, TrackType};
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;

use crate::engine::DeckSource;

/// Encoder delay (576) plus decoder delay (529) that BASS skips on an MP3 without a LAME header.
pub const MP3_DEFAULT_DELAY_FRAMES: usize = 1105;

#[derive(Debug)]
pub enum DecodeError {
    Unreadable(String),
    Unsupported(String),
    NoAudio,
    Corrupt(String),
    Resample(String),
}

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DecodeError::Unreadable(s) => write!(f, "cannot read file: {s}"),
            DecodeError::Unsupported(s) => write!(f, "unsupported: {s}"),
            DecodeError::NoAudio => write!(f, "no audio track"),
            DecodeError::Corrupt(s) => write!(f, "corrupt: {s}"),
            DecodeError::Resample(s) => write!(f, "resampler: {s}"),
        }
    }
}
impl std::error::Error for DecodeError {}

/// A whole track as interleaved stereo f32 at `rate`.
#[derive(Clone)]
pub struct Decoded {
    pub rate: u32,
    pub samples: Arc<Vec<f32>>,
    /// Packets that could not be decoded and were skipped (damaged MP3s); 0 for a clean file.
    pub skipped_packets: u32,
}

impl Decoded {
    pub fn frames(&self) -> u64 {
        (self.samples.len() / 2) as u64
    }
    pub fn seconds(&self) -> f64 {
        self.frames() as f64 / self.rate as f64
    }
    /// A fresh deck over the same samples (cheap: shares the buffer).
    pub fn deck(&self) -> MemDeck {
        MemDeck { samples: self.samples.clone(), pos: 0 }
    }
}

/// Stereo-fy interleaved `channels`-channel audio: mono is duplicated, more than two channels keep the front pair.
pub fn to_stereo(interleaved: &[f32], channels: usize, out: &mut Vec<f32>) {
    match channels {
        0 => {}
        1 => {
            out.reserve(interleaved.len() * 2);
            for &s in interleaved {
                out.push(s);
                out.push(s);
            }
        }
        2 => out.extend_from_slice(interleaved),
        n => {
            out.reserve(interleaved.len() / n * 2);
            for frame in interleaved.chunks_exact(n) {
                out.push(frame[0]);
                out.push(frame[1]);
            }
        }
    }
}

/// Resample interleaved stereo from `from` to `to` Hz (returns the input untouched when the rates match).
pub fn resample_stereo(samples: Vec<f32>, from: u32, to: u32) -> Result<Vec<f32>, DecodeError> {
    if from == to || samples.is_empty() {
        return Ok(samples);
    }
    let frames = samples.len() / 2;
    let mut rs = Fft::<f32>::new(from as usize, to as usize, 1024, 2, FixedSync::Input)
        .map_err(|e| DecodeError::Resample(e.to_string()))?;
    let input = InterleavedSlice::new(&samples, 2, frames).map_err(|e| DecodeError::Resample(e.to_string()))?;
    let out = rs.process_all(&input, frames, None).map_err(|e| DecodeError::Resample(e.to_string()))?;
    Ok(out.take_data())
}

/// Decode `path` to stereo f32 at `target_rate`.
pub fn decode_file(path: &Path, target_rate: u32) -> Result<Decoded, DecodeError> {
    let file = File::open(path).map_err(|e| DecodeError::Unreadable(e.to_string()))?;
    let mss = MediaSourceStream::new(Box::new(file), Default::default());
    let mut hint = Hint::new();
    if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
        hint.with_extension(&ext.to_ascii_lowercase());
    }
    let mut format = symphonia::default::get_probe()
        .probe(&hint, mss, FormatOptions::default(), MetadataOptions::default())
        .map_err(|e| DecodeError::Unsupported(format!("probe failed: {e}")))?;
    let (track_id, params, track_delay) = {
        let track = format.default_track(TrackType::Audio).ok_or(DecodeError::NoAudio)?;
        let params = track
            .codec_params
            .as_ref()
            .and_then(|p| p.audio())
            .ok_or_else(|| DecodeError::Unsupported("track has no audio codec parameters".into()))?
            .clone();
        (track.id, params, track.delay)
    };
    let is_mp3 = params.codec == symphonia::core::codecs::audio::well_known::CODEC_ID_MP3;
    let mut decoder = symphonia::default::get_codecs()
        .make_audio_decoder(&params, &AudioDecoderOptions::default())
        .map_err(|e| DecodeError::Unsupported(format!("codec: {e}")))?;

    let mut stereo: Vec<f32> = Vec::new();
    let mut scratch: Vec<f32> = Vec::new();
    let mut rate = 0u32;
    let mut channels = 0usize;
    let mut skipped = 0u32;
    let mut consecutive = 0u32;
    loop {
        let packet = match format.next_packet() {
            Ok(Some(p)) => p,
            Ok(None) => break,
            Err(SymError::IoError(_)) if rate != 0 => break, // truncated file: keep what decoded
            Err(SymError::ResetRequired) => break,
            Err(e) => return Err(DecodeError::Corrupt(format!("read: {e}"))),
        };
        if packet.track_id != track_id {
            continue;
        }
        let buf = match decoder.decode(&packet) {
            Ok(b) => b,
            Err(SymError::DecodeError(_)) | Err(SymError::IoError(_)) => {
                skipped += 1;
                consecutive += 1;
                if consecutive > 100 {
                    return Err(DecodeError::Corrupt("too many undecodable packets".into()));
                }
                continue;
            }
            Err(e) => return Err(DecodeError::Corrupt(format!("decode: {e}"))),
        };
        consecutive = 0;
        let r = buf.spec().rate();
        let c = buf.spec().channels().count();
        if r == 0 || c == 0 {
            skipped += 1;
            continue;
        }
        if rate == 0 {
            rate = r;
            channels = c;
        } else if r != rate || c != channels {
            skipped += 1; // mid-stream format change in a damaged file: do not mix layouts
            continue;
        }
        scratch.resize(buf.samples_interleaved(), 0.0);
        buf.copy_to_slice_interleaved(&mut scratch);
        to_stereo(&scratch, channels, &mut stereo);
    }
    if rate == 0 || stereo.is_empty() {
        return Err(DecodeError::NoAudio);
    }
    // MP3 with no encoder-delay header (no LAME tag): BASS still drops the standard 576 + 529 start samples, symphonia drops none.
    // Do the same so positions and crossfade offsets agree with the engine being replaced (measured: 1105 frames = 25.06 ms at 44.1 kHz).
    if is_mp3 && track_delay.unwrap_or(0) == 0 {
        let skip = (MP3_DEFAULT_DELAY_FRAMES * 2).min(stereo.len());
        stereo.drain(..skip);
    }
    let samples = resample_stereo(stereo, rate, target_rate)?;
    Ok(Decoded { rate: target_rate, samples: Arc::new(samples), skipped_packets: skipped })
}

/// An in-memory deck over decoded stereo audio.
pub struct MemDeck {
    samples: Arc<Vec<f32>>,
    pos: u64,
}

impl DeckSource for MemDeck {
    fn read(&mut self, out: &mut [f32]) -> usize {
        let total = (self.samples.len() / 2) as u64;
        let want = (out.len() / 2) as u64;
        let n = want.min(total.saturating_sub(self.pos)) as usize;
        let start = self.pos as usize * 2;
        out[..n * 2].copy_from_slice(&self.samples[start..start + n * 2]);
        self.pos += n as u64;
        n
    }
    fn seek(&mut self, frame: u64) -> bool {
        if frame > (self.samples.len() / 2) as u64 {
            return false;
        }
        self.pos = frame;
        true
    }
    fn len_frames(&self) -> Option<u64> {
        Some((self.samples.len() / 2) as u64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sine(rate: u32, secs: f64, hz: f64) -> Vec<f32> {
        let n = (rate as f64 * secs) as usize;
        let mut v = Vec::with_capacity(n * 2);
        for i in 0..n {
            let s = (2.0 * std::f64::consts::PI * hz * i as f64 / rate as f64).sin() as f32 * 0.5;
            v.push(s);
            v.push(s);
        }
        v
    }

    fn dominant_hz(samples: &[f32], rate: u32) -> f64 {
        // zero-crossing estimate on the left channel is enough for a clean sine
        let mut crossings = 0usize;
        let mut prev = samples[0];
        for f in samples.chunks(2).skip(1) {
            if prev < 0.0 && f[0] >= 0.0 {
                crossings += 1;
            }
            prev = f[0];
        }
        crossings as f64 / (samples.len() as f64 / 2.0 / rate as f64)
    }

    #[test]
    fn mono_is_duplicated_and_extra_channels_keep_the_front_pair() {
        let mut out = vec![];
        to_stereo(&[0.1, 0.2], 1, &mut out);
        assert_eq!(out, vec![0.1, 0.1, 0.2, 0.2]);
        let mut out = vec![];
        to_stereo(&[1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 3, &mut out);
        assert_eq!(out, vec![1.0, 2.0, 4.0, 5.0]);
    }

    #[test]
    fn resampling_keeps_pitch_and_length() {
        let x = sine(44_100, 2.0, 1000.0);
        let y = resample_stereo(x, 44_100, 48_000).unwrap();
        let secs = y.len() as f64 / 2.0 / 48_000.0;
        assert!((secs - 2.0).abs() < 0.01, "length {secs}");
        let hz = dominant_hz(&y[2000..y.len() - 2000], 48_000);
        assert!((hz - 1000.0).abs() < 5.0, "pitch {hz}");
    }

    #[test]
    fn resampling_does_not_change_the_level() {
        let x = sine(44_100, 1.0, 440.0);
        let y = resample_stereo(x.clone(), 44_100, 48_000).unwrap();
        let rms = |v: &[f32]| (v.iter().map(|s| (*s as f64).powi(2)).sum::<f64>() / v.len() as f64).sqrt();
        let (a, b) = (rms(&x), rms(&y[4000..y.len() - 4000]));
        assert!(((b / a).log10() * 20.0).abs() < 0.1, "level changed {a} -> {b}");
    }

    #[test]
    fn same_rate_is_a_no_op() {
        let x = sine(48_000, 0.1, 440.0);
        assert_eq!(resample_stereo(x.clone(), 48_000, 48_000).unwrap(), x);
    }

    #[test]
    fn mem_deck_reads_seeks_and_reports_the_end() {
        let d = Decoded { rate: 48_000, samples: Arc::new((0..20).map(|i| i as f32).collect()), skipped_packets: 0 };
        let mut deck = d.deck();
        assert_eq!(deck.len_frames(), Some(10));
        let mut buf = [0.0f32; 8];
        assert_eq!(deck.read(&mut buf), 4);
        assert_eq!(buf, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]);
        assert!(deck.seek(8));
        assert_eq!(deck.read(&mut buf), 2);
        assert_eq!(&buf[..4], &[16.0, 17.0, 18.0, 19.0]);
        assert_eq!(deck.read(&mut buf), 0);
        assert!(!deck.seek(11));
    }

    #[test]
    fn a_missing_file_is_an_error_not_a_panic() {
        assert!(matches!(decode_file(Path::new("/nonexistent/x.mp3"), 48_000), Err(DecodeError::Unreadable(_))));
    }
}
