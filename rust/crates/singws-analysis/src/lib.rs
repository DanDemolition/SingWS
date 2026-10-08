//! SingWS analysis prototype (Stage 1 of RUST_MIGRATION_PLAN.md).
//!
//! One streaming pass over a music file (or the MP3 inside an MP3+G ZIP) producing the numbers the
//! app currently gets from a libmpv job: BS.1770 integrated loudness, sample peak, duration and the
//! first/last audible times. It is deliberately I/O-light and has no unsafe code. Nothing in the
//! app imports it yet.
#![forbid(unsafe_code)]

pub mod silence;

use std::fmt;
use std::fs::File;
use std::io::{Cursor, Read};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use ebur128::{EbuR128, Mode};
use serde::Serialize;
use symphonia::core::codecs::audio::AudioDecoderOptions;
use symphonia::core::errors::Error as SymError;
use symphonia::core::formats::probe::Hint;
use symphonia::core::formats::{FormatOptions, TrackType};
use symphonia::core::io::{MediaSource, MediaSourceStream};
use symphonia::core::meta::MetadataOptions;

use silence::SilenceDetector;

/// Bump whenever a change could alter results, so the app can re-run older cache entries.
pub const ENGINE_VERSION: u32 = 1;

/// The app rejects integrated loudness outside this range (`_parse_ebur128`).
const LUFS_RANGE: (f64, f64) = (-70.0, 0.0);
/// Largest MP3+G member we will inflate into memory.
const MAX_ARCHIVE_MEMBER: u64 = 256 * 1024 * 1024;
/// Give up on a stream that keeps failing to decode.
const MAX_CONSECUTIVE_DECODE_ERRORS: u32 = 200;
const AUDIO_EXTENSIONS: &[&str] = &["mp3", "flac", "wav", "m4a", "aac", "ogg", "oga", "mp4", "m4b", "alac"];

#[derive(Debug, Clone)]
pub struct AnalysisOptions {
    /// Produce 100 ms RMS windows in dB (BGM fade analysis).
    pub want_envelope: bool,
    /// `silencedetect` noise floor, dB (the app uses -55).
    pub noise_floor_db: f32,
    /// `silencedetect` minimum silence, seconds (the app uses 0.3).
    pub min_silence_s: f32,
    /// Refuse audio longer than this.
    pub max_seconds: f64,
    /// Per-file wall-clock limit.
    pub timeout: Option<Duration>,
}

impl Default for AnalysisOptions {
    fn default() -> Self {
        Self { want_envelope: false, noise_floor_db: -55.0, min_silence_s: 0.3, max_seconds: 3.0 * 3600.0, timeout: Some(Duration::from_secs(120)) }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct AnalysisResult {
    /// Gated integrated loudness, LUFS. `None` when the audio is too quiet/short to measure or out of range.
    pub integrated_lufs: Option<f64>,
    /// Sample peak over all channels, dBFS.
    pub sample_peak_dbfs: f64,
    pub duration_s: f64,
    pub sample_rate: u32,
    pub channels: u32,
    pub audio_start_s: Option<f64>,
    pub audio_end_s: Option<f64>,
    pub envelope_100ms_db: Option<Vec<f32>>,
    /// Packets that failed to decode and were skipped (damaged files).
    pub skipped_packets: u32,
    pub engine_version: u32,
}

#[derive(Debug)]
pub enum AnalysisError {
    /// Could not open the file or archive.
    Unreadable(String),
    /// Format, codec or archive method we do not handle (caller should fall back to libmpv).
    Unsupported(String),
    /// Opened fine but contains no audio.
    NoAudio,
    /// Damaged beyond use.
    Corrupt(String),
    TooLong,
    Cancelled,
    Timeout,
}

impl AnalysisError {
    /// Stable short code for the machine-readable output.
    pub fn code(&self) -> &'static str {
        match self {
            Self::Unreadable(_) => "unreadable",
            Self::Unsupported(_) => "unsupported",
            Self::NoAudio => "no_audio",
            Self::Corrupt(_) => "corrupt",
            Self::TooLong => "too_long",
            Self::Cancelled => "cancelled",
            Self::Timeout => "timeout",
        }
    }
}

impl fmt::Display for AnalysisError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unreadable(m) | Self::Unsupported(m) | Self::Corrupt(m) => write!(f, "{}: {m}", self.code()),
            other => write!(f, "{}", other.code()),
        }
    }
}

impl std::error::Error for AnalysisError {}

fn lower_ext(path: &Path) -> String {
    path.extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase()
}

/// Analyse a music file, or the best audio member of a `.zip` (MP3+G).
pub fn analyze_path(path: &Path, opts: &AnalysisOptions, cancel: &AtomicBool) -> Result<AnalysisResult, AnalysisError> {
    if lower_ext(path) == "zip" {
        let (bytes, ext) = read_archive_audio(path)?;
        analyze_source(Box::new(Cursor::new(bytes)), &ext, opts, cancel)
    } else {
        let file = File::open(path).map_err(|e| AnalysisError::Unreadable(e.to_string()))?;
        analyze_source(Box::new(file), &lower_ext(path), opts, cancel)
    }
}

/// Analyse already-open media. `ext` is only a probe hint.
pub fn analyze_source(
    source: Box<dyn MediaSource>,
    ext: &str,
    opts: &AnalysisOptions,
    cancel: &AtomicBool,
) -> Result<AnalysisResult, AnalysisError> {
    let started = Instant::now();
    let mss = MediaSourceStream::new(source, Default::default());
    let mut hint = Hint::new();
    if !ext.is_empty() {
        hint.with_extension(ext);
    }
    let mut format = symphonia::default::get_probe()
        .probe(&hint, mss, FormatOptions::default(), MetadataOptions::default())
        .map_err(|e| AnalysisError::Unsupported(format!("probe failed: {e}")))?;

    let (track_id, params) = {
        let track = format.default_track(TrackType::Audio).ok_or(AnalysisError::NoAudio)?;
        let params = track
            .codec_params
            .as_ref()
            .and_then(|p| p.audio())
            .ok_or_else(|| AnalysisError::Unsupported("track has no audio codec parameters".into()))?
            .clone();
        (track.id, params)
    };
    let mut decoder = symphonia::default::get_codecs()
        .make_audio_decoder(&params, &AudioDecoderOptions::default())
        .map_err(|e| AnalysisError::Unsupported(format!("codec: {e}")))?;

    let mut state: Option<Stream> = None;
    let mut scratch: Vec<f32> = Vec::new();
    let mut skipped: u32 = 0;
    let mut consecutive_errors: u32 = 0;

    loop {
        if cancel.load(Ordering::Relaxed) {
            return Err(AnalysisError::Cancelled);
        }
        if let Some(limit) = opts.timeout {
            if started.elapsed() > limit {
                return Err(AnalysisError::Timeout);
            }
        }
        let packet = match format.next_packet() {
            Ok(Some(p)) => p,
            Ok(None) => break,
            Err(SymError::IoError(_)) if state.is_some() => break, // truncated file: keep what decoded
            Err(SymError::ResetRequired) => break,
            Err(e) => return Err(AnalysisError::Corrupt(format!("read: {e}"))),
        };
        if packet.track_id != track_id {
            continue;
        }
        let buf = match decoder.decode(&packet) {
            Ok(b) => b,
            Err(SymError::DecodeError(_)) | Err(SymError::IoError(_)) => {
                skipped += 1;
                consecutive_errors += 1;
                if consecutive_errors > MAX_CONSECUTIVE_DECODE_ERRORS {
                    return Err(AnalysisError::Corrupt("too many undecodable packets".into()));
                }
                continue;
            }
            Err(e) => return Err(AnalysisError::Corrupt(format!("decode: {e}"))),
        };
        consecutive_errors = 0;

        let rate = buf.spec().rate();
        let channels = buf.spec().channels().count() as u32;
        if rate == 0 || channels == 0 {
            skipped += 1;
            continue;
        }
        let stream = match state.as_mut() {
            Some(s) => s,
            None => state.insert(Stream::new(rate, channels, opts)?),
        };
        if stream.rate != rate || stream.channels != channels {
            skipped += 1; // mid-stream format change (seen in damaged MP3s): ignore, do not mix layouts
            continue;
        }
        scratch.resize(buf.samples_interleaved(), 0.0);
        buf.copy_to_slice_interleaved(&mut scratch);
        stream.push(&scratch)?;
        if stream.frames as f64 / rate as f64 > opts.max_seconds {
            return Err(AnalysisError::TooLong);
        }
    }

    let stream = state.ok_or(AnalysisError::NoAudio)?;
    stream.finish(skipped)
}

/// Everything that accumulates while decoding.
struct Stream {
    rate: u32,
    channels: u32,
    frames: u64,
    peak: f32,
    meter: EbuR128,
    silence: SilenceDetector,
    envelope: Option<EnvelopeBuilder>,
}

impl Stream {
    fn new(rate: u32, channels: u32, opts: &AnalysisOptions) -> Result<Self, AnalysisError> {
        let meter = EbuR128::new(channels, rate, Mode::I)
            .map_err(|e| AnalysisError::Unsupported(format!("loudness meter: {e}")))?;
        Ok(Self {
            rate,
            channels,
            frames: 0,
            peak: 0.0,
            meter,
            silence: SilenceDetector::new(opts.noise_floor_db, opts.min_silence_s, rate),
            envelope: opts.want_envelope.then(|| EnvelopeBuilder::new(rate, channels as usize)),
        })
    }

    fn push(&mut self, interleaved: &[f32]) -> Result<(), AnalysisError> {
        let ch = self.channels as usize;
        let usable = interleaved.len() - interleaved.len() % ch;
        let block = &interleaved[..usable];
        for s in block {
            let a = s.abs();
            if a > self.peak {
                self.peak = a;
            }
        }
        self.meter.add_frames_f32(block).map_err(|e| AnalysisError::Corrupt(format!("meter: {e}")))?;
        self.silence.push(block, ch);
        if let Some(env) = self.envelope.as_mut() {
            env.push(block);
        }
        self.frames += (usable / ch) as u64;
        Ok(())
    }

    fn finish(mut self, skipped: u32) -> Result<AnalysisResult, AnalysisError> {
        let duration = self.frames as f64 / self.rate as f64;
        // Same synthetic 0.5 s tail the libmpv path adds, so a closing silence always produces an end event.
        self.silence.push_silence((0.5 * self.rate as f64).round() as u64);
        let events = self.silence.finish();
        let (audio_start, audio_end) = silence::boundaries(&events, duration);
        let lufs = self
            .meter
            .loudness_global()
            .ok()
            .filter(|v| v.is_finite() && *v >= LUFS_RANGE.0 && *v <= LUFS_RANGE.1);
        // Not clamped: hot MP3s decode above full scale and the libmpv cache stores those positive peaks too.
        let peak_db = if self.peak > 0.0 { 20.0 * (self.peak as f64).log10() } else { -200.0 };
        Ok(AnalysisResult {
            integrated_lufs: lufs.map(round1),
            sample_peak_dbfs: round1(peak_db),
            duration_s: duration,
            sample_rate: self.rate,
            channels: self.channels,
            audio_start_s: audio_start,
            audio_end_s: audio_end,
            envelope_100ms_db: self.envelope.map(|e| e.finish()),
            skipped_packets: skipped,
            engine_version: ENGINE_VERSION,
        })
    }
}

/// The app's cache stores one decimal place.
fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
}

/// 100 ms RMS windows over all channels, in dB, clamped like `_parse_transition_envelope`.
struct EnvelopeBuilder {
    window_frames: usize,
    channels: usize,
    acc: f64,
    n: usize,
    out: Vec<f32>,
}

impl EnvelopeBuilder {
    fn new(rate: u32, channels: usize) -> Self {
        Self { window_frames: (rate / 10).max(1) as usize, channels, acc: 0.0, n: 0, out: Vec::new() }
    }
    fn push(&mut self, interleaved: &[f32]) {
        for frame in interleaved.chunks_exact(self.channels) {
            for s in frame {
                self.acc += (*s as f64) * (*s as f64);
            }
            self.n += 1;
            if self.n == self.window_frames {
                let ms = self.acc / (self.n * self.channels) as f64;
                let db = if ms > 0.0 { 10.0 * ms.log10() } else { -96.0 };
                self.out.push(db.clamp(-96.0, 6.0) as f32);
                self.acc = 0.0;
                self.n = 0;
            }
        }
    }
    fn finish(self) -> Vec<f32> {
        self.out
    }
}

/// Find the audio member of an MP3+G archive and inflate it (bounded).
fn read_archive_audio(path: &Path) -> Result<(Vec<u8>, String), AnalysisError> {
    let file = File::open(path).map_err(|e| AnalysisError::Unreadable(e.to_string()))?;
    let mut archive = zip::ZipArchive::new(file).map_err(|e| AnalysisError::Corrupt(format!("zip: {e}")))?;
    let mut best: Option<(usize, String, u64)> = None;
    for i in 0..archive.len() {
        let entry = archive.by_index_raw(i).map_err(|e| AnalysisError::Corrupt(format!("zip entry: {e}")))?;
        if entry.is_dir() {
            continue;
        }
        let name = entry.name().to_string();
        // Skip macOS resource-fork junk (`__MACOSX/`, `._name`).
        let base = name.rsplit('/').next().unwrap_or(&name);
        if name.starts_with("__MACOSX/") || base.starts_with("._") {
            continue;
        }
        let ext = Path::new(base).extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase();
        if !AUDIO_EXTENSIONS.contains(&ext.as_str()) {
            continue;
        }
        let size = entry.size();
        if best.as_ref().is_none_or(|(_, _, s)| size > *s) {
            best = Some((i, ext, size));
        }
    }
    let (index, ext, size) = best.ok_or(AnalysisError::NoAudio)?;
    if size > MAX_ARCHIVE_MEMBER {
        return Err(AnalysisError::TooLong);
    }
    let entry = archive.by_index(index).map_err(|e| match e {
        zip::result::ZipError::UnsupportedArchive(m) => AnalysisError::Unsupported(format!("zip method: {m}")),
        other => AnalysisError::Corrupt(format!("zip member: {other}")),
    })?;
    let mut bytes = Vec::with_capacity(size as usize);
    entry
        .take(MAX_ARCHIVE_MEMBER + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| AnalysisError::Corrupt(format!("zip inflate: {e}")))?;
    if bytes.len() as u64 > MAX_ARCHIVE_MEMBER {
        return Err(AnalysisError::TooLong);
    }
    Ok((bytes, ext))
}
