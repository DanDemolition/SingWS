//! Streaming deck: a worker thread decodes the file, makes it stereo, resamples it to the engine rate and feeds a lock-free ring;
//! the audio callback only pops from the ring (`DeckSource::read`). Replaces the whole-file `MemDeck` for the live engine
//! (a 4-minute track would otherwise hold ~92 MB per deck).
//!
//! Semantics the engine relies on:
//! * `read` returns fewer frames than asked ONLY at the true end of the track. If the worker has not caught up yet (underrun) the
//!   rest of the block is silence, the position still advances (it is the audio clock) and `underruns` counts it.
//! * `seek` is sample-accurate in the output domain: the worker restarts about one second before the target (so the resampler
//!   has settled), drops what comes before it, and `seek` returns once some audio is ready (bounded wait).
//! * Dropping the deck stops the worker without blocking.

use std::fs::File;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use symphonia::core::codecs::audio::{AudioDecoder, AudioDecoderOptions};
use symphonia::core::errors::Error as SymError;
use symphonia::core::formats::probe::Hint;
use symphonia::core::formats::{FormatOptions, FormatReader, SeekMode, SeekTo, TrackType};
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::units::Timestamp;

use crate::decode::{to_stereo, DecodeError, MP3_DEFAULT_DELAY_FRAMES};
use crate::engine::DeckSource;
use crate::resample::StreamResampler;
use crate::ring::Ring;

const RING_SECONDS: f64 = 2.0;
const PREROLL_S: f64 = 1.0;
const PREFILL_S: f64 = 0.2;
/// After a seek the deck stays pending until this much of the new position is buffered (the worker decodes far faster than real time).
const SEEK_PREFILL_S: f64 = 0.03;

struct Session {
    format: Box<dyn FormatReader>,
    decoder: Box<dyn AudioDecoder>,
    track_id: u32,
    off: i64,
    resampler: StreamResampler,
    start_abs: i64,
    drop_out: u64,
    scratch: Vec<f32>,
    stereo: Vec<f32>,
    outv: Vec<f32>,
    finished: bool,
}

fn gcd(a: u64, b: u64) -> u64 {
    if b == 0 { a } else { gcd(b, a % b) }
}

fn open_format(path: &Path) -> Result<(Box<dyn FormatReader>, bool), DecodeError> {
    let file = File::open(path).map_err(|e| DecodeError::Unreadable(e.to_string()))?;
    let mss = MediaSourceStream::new(Box::new(file), Default::default());
    let mut hint = Hint::new();
    if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
        hint.with_extension(&ext.to_ascii_lowercase());
    }
    let format = symphonia::default::get_probe()
        .probe(&hint, mss, FormatOptions::default(), MetadataOptions::default())
        .map_err(|e| DecodeError::Unsupported(format!("probe failed: {e}")))?;
    Ok((format, true))
}

/// Open the file and position a decode session so that output frame `target_out` is the first one kept.
/// Also returns the output length the container header promises, when it has a frame count.
fn open_session(path: &Path, to_rate: u32, target_out: u64) -> Result<(Session, Option<u64>), DecodeError> {
    let (mut format, _) = open_format(path)?;
    let (track_id, params, delay, num_frames, time_base_den) = {
        let track = format.default_track(TrackType::Audio).ok_or(DecodeError::NoAudio)?;
        let params = track
            .codec_params
            .as_ref()
            .and_then(|p| p.audio())
            .ok_or_else(|| DecodeError::Unsupported("track has no audio codec parameters".into()))?
            .clone();
        (track.id, params, track.delay, track.num_frames, track.time_base.map(|t| t.denom.get() as u64))
    };
    let src_rate = params.sample_rate.ok_or_else(|| DecodeError::Unsupported("unknown sample rate".into()))?;
    let is_mp3 = params.codec == symphonia::core::codecs::audio::well_known::CODEC_ID_MP3;
    let off: i64 = if is_mp3 && delay.unwrap_or(0) == 0 { MP3_DEFAULT_DELAY_FRAMES as i64 } else { 0 };
    let ts_in_frames = time_base_den.map(|d| d == src_rate as u64).unwrap_or(true);
    let mut decoder = symphonia::default::get_codecs()
        .make_audio_decoder(&params, &AudioDecoderOptions::default())
        .map_err(|e| DecodeError::Unsupported(format!("codec: {e}")))?;

    // Output frame k of a resampler that starts at source frame A is global output frame A*to/from + k, which is an integer only when
    // A is a multiple of from/gcd(from, to): start there.
    let g = gcd(src_rate as u64, to_rate as u64);
    let step = (src_rate as u64 / g) as i64;
    let ratio_num = (to_rate as u64 / g) as i64;
    let target_src = (target_out as f64 * src_rate as f64 / to_rate as f64).floor() as i64;
    let want_start = target_src - (PREROLL_S * src_rate as f64) as i64;
    let mut start_abs = 0i64;
    if want_start > 0 && ts_in_frames {
        let want_ts = want_start + off;
        match format.seek(SeekMode::Accurate, SeekTo::Timestamp { ts: Timestamp::from(want_ts), track_id }) {
            Ok(seeked) => {
                decoder.reset();
                let actual_abs = seeked.actual_ts.get() - off;
                start_abs = ((actual_abs.max(0) + step - 1) / step) * step;
            }
            Err(_) => start_abs = 0, // cannot seek: decode from the start and discard
        }
        if start_abs == 0 {
            // seek failed or landed at the start: reopen cleanly from the beginning
            let (f2, _) = open_format(path)?;
            format = f2;
            decoder.reset();
        }
    } else if want_start > 0 {
        start_abs = 0; // timestamps are not in frames for this stream: always decode from the start
    }
    let aligned = ((start_abs + step - 1) / step) * step;
    let start_out = (aligned / step) * ratio_num;
    let drop_out = (target_out as i64 - start_out).max(0) as u64;
    let resampler = StreamResampler::new(src_rate, to_rate)?;
    let est_total_out = num_frames.map(|n| resampler.output_len_for((n as i64 - off).max(0) as u64));
    let session = Session {
        format,
        decoder,
        track_id,
        off,
        resampler,
        start_abs: aligned,
        drop_out,
        scratch: Vec::new(),
        stereo: Vec::new(),
        outv: Vec::new(),
        finished: false,
    };
    Ok((session, est_total_out))
}

impl Session {
    /// Decode the next packet and return resampled stereo frames (possibly empty). `None` at the end of the stream.
    fn next_chunk(&mut self) -> Result<Option<&[f32]>, DecodeError> {
        self.outv.clear();
        if self.finished {
            return Ok(None);
        }
        loop {
            let packet = match self.format.next_packet() {
                Ok(Some(p)) => p,
                Ok(None) | Err(SymError::IoError(_)) | Err(SymError::ResetRequired) => {
                    self.resampler.finish(&mut self.outv)?;
                    self.finished = true;
                    self.apply_drop_out();
                    return Ok(if self.outv.is_empty() { None } else { Some(&self.outv) });
                }
                Err(e) => return Err(DecodeError::Corrupt(format!("read: {e}"))),
            };
            if packet.track_id != self.track_id {
                continue;
            }
            let buf = match self.decoder.decode(&packet) {
                Ok(b) => b,
                Err(SymError::DecodeError(_)) | Err(SymError::IoError(_)) => continue,
                Err(e) => return Err(DecodeError::Corrupt(format!("decode: {e}"))),
            };
            let channels = buf.spec().channels().count();
            if channels == 0 {
                continue;
            }
            self.scratch.resize(buf.samples_interleaved(), 0.0);
            buf.copy_to_slice_interleaved(&mut self.scratch);
            self.stereo.clear();
            to_stereo(&self.scratch, channels, &mut self.stereo);
            let n = (self.stereo.len() / 2) as i64;
            // the decoded buffer has already had `trim_start` frames cut from its front (encoder delay), so it begins that far into the packet
            let abs0 = packet.pts.get() + packet.trim_start.get() as i64 - self.off;
            // keep only frames at or after the session start (drops the default MP3 delay and the pre-roll before the aligned start)
            let skip = (self.start_abs - abs0).clamp(0, n) as usize;
            if skip as i64 >= n {
                continue;
            }
            self.resampler.push(&self.stereo[skip * 2..], &mut self.outv)?;
            self.apply_drop_out();
            if !self.outv.is_empty() {
                return Ok(Some(&self.outv));
            }
        }
    }

    fn apply_drop_out(&mut self) {
        if self.drop_out > 0 {
            let have = (self.outv.len() / 2) as u64;
            let d = self.drop_out.min(have);
            self.outv.drain(..(d * 2) as usize);
            self.drop_out -= d;
        }
    }
}

struct Shared {
    ring: Ring,
    want_gen: AtomicU64,
    ack_gen: AtomicU64,
    /// Generation whose ring has been emptied by the consumer; the worker pushes nothing new before this matches.
    flushed_gen: AtomicU64,
    /// Generation whose stream has been fully decoded (everything is in the ring or already read).
    done_gen: AtomicU64,
    target_out: AtomicU64,
    stop: AtomicBool,
    failed: AtomicBool,
    /// Output frames the finished track really has (set when the decoder reaches the end), else `u64::MAX`.
    actual_total: AtomicU64,
    underruns: AtomicU64,
    error: Mutex<Option<String>>,
}

pub struct StreamingDeck {
    shared: Arc<Shared>,
    worker: Option<JoinHandle<()>>,
    epoch: u64,
    /// A seek has been requested and the worker has not yet acknowledged it.
    seeking: bool,
    /// The worker has restarted; waiting for the first ~30 ms of the new position to arrive.
    settling: bool,
    est_total: Option<u64>,
    path: PathBuf,
    pub rate: u32,
    /// Also counted here when set (the application aggregates all decks).
    underrun_sink: Option<Arc<AtomicU64>>,
}

impl StreamingDeck {
    pub fn open(path: &Path, target_rate: u32) -> Result<Self, DecodeError> {
        let (session, est) = open_session(path, target_rate, 0)?;
        let ring_samples = (target_rate as f64 * RING_SECONDS) as usize * 2;
        let shared = Arc::new(Shared {
            ring: Ring::new(ring_samples),
            want_gen: AtomicU64::new(0),
            ack_gen: AtomicU64::new(0),
            flushed_gen: AtomicU64::new(0),
            done_gen: AtomicU64::new(u64::MAX),
            target_out: AtomicU64::new(0),
            stop: AtomicBool::new(false),
            failed: AtomicBool::new(false),
            actual_total: AtomicU64::new(u64::MAX),
            underruns: AtomicU64::new(0),
            error: Mutex::new(None),
        });
        let sh = shared.clone();
        let p = path.to_path_buf();
        let worker = std::thread::Builder::new()
            .name("singws-bgm-decode".into())
            .spawn(move || worker_main(sh, p, target_rate, session))
            .map_err(|e| DecodeError::Unreadable(format!("cannot start decoder thread: {e}")))?;
        let deck = Self { shared, worker: Some(worker), epoch: 0, seeking: false, settling: false, est_total: est, path: path.to_path_buf(), rate: target_rate, underrun_sink: None };
        deck.wait_ready(Duration::from_secs(3));
        Ok(deck)
    }

    pub fn with_underrun_sink(mut self, sink: Arc<AtomicU64>) -> Self {
        self.underrun_sink = Some(sink);
        self
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn underruns(&self) -> u64 {
        self.shared.underruns.load(Ordering::Relaxed)
    }

    pub fn failed(&self) -> Option<String> {
        if self.shared.failed.load(Ordering::Relaxed) { self.shared.error.lock().ok().and_then(|e| e.clone()) } else { None }
    }

    /// Seconds of audio waiting in the ring.
    pub fn buffered_seconds(&self) -> f64 {
        self.shared.ring.len() as f64 / 2.0 / self.rate as f64
    }

    fn wait_ready(&self, timeout: Duration) {
        let need = (PREFILL_S * self.rate as f64) as usize * 2;
        let t0 = Instant::now();
        loop {
            let s = &self.shared;
            if s.ring.len() >= need || s.done_gen.load(Ordering::Acquire) == self.epoch || s.failed.load(Ordering::Relaxed) {
                return;
            }
            if t0.elapsed() > timeout {
                return;
            }
            std::thread::sleep(Duration::from_millis(1));
        }
    }
}

impl DeckSource for StreamingDeck {
    fn read(&mut self, out: &mut [f32]) -> usize {
        let want = out.len();
        if self.poll_pending() {
            // callers that skip `poll_pending` (the engine does not) get silence while a seek is in flight
            out.fill(0.0);
            return want / 2;
        }
        let got = self.shared.ring.pop(out);
        if got >= want {
            return want / 2;
        }
        let finished = self.shared.done_gen.load(Ordering::Acquire) == self.epoch;
        // Re-check after reading `done`: the worker publishes all samples before it sets `done`, so anything still in the ring is real.
        let got = got + if finished { self.shared.ring.pop(&mut out[got..]) } else { 0 };
        if got >= want {
            return want / 2;
        }
        if finished || self.shared.failed.load(Ordering::Relaxed) {
            return got / 2;
        }
        // underrun: the worker is behind. Silence for the rest of the block; the position (audio clock) still advances.
        out[got..].fill(0.0);
        self.shared.underruns.fetch_add(1, Ordering::Relaxed);
        if let Some(sink) = &self.underrun_sink {
            sink.fetch_add(1, Ordering::Relaxed);
        }
        want / 2
    }

    /// Non-blocking: records the target and returns at once. The audio thread finishes the hand-over in `poll_pending` (emptying the
    /// ring once the worker has acknowledged) and the engine plays silence, without advancing, until the new audio arrives.
    fn seek(&mut self, frame: u64) -> bool {
        let s = &self.shared;
        if s.failed.load(Ordering::Relaxed) || s.stop.load(Ordering::Relaxed) {
            return false;
        }
        self.epoch += 1;
        s.target_out.store(frame, Ordering::Release);
        s.want_gen.store(self.epoch, Ordering::Release);
        self.seeking = true;
        self.settling = false;
        true
    }

    fn poll_pending(&mut self) -> bool {
        let s = &self.shared;
        if self.seeking {
            if s.failed.load(Ordering::Relaxed) {
                self.seeking = false;
                return false;
            }
            if s.ack_gen.load(Ordering::Acquire) != self.epoch {
                return true;
            }
            // the worker has stopped pushing old audio: empty the ring, then let it push the new position
            s.ring.discard_all();
            s.flushed_gen.store(self.epoch, Ordering::Release);
            self.seeking = false;
            self.settling = true;
        }
        if self.settling {
            let need = (SEEK_PREFILL_S * self.rate as f64) as usize * 2;
            if s.ring.len() >= need || s.done_gen.load(Ordering::Acquire) == self.epoch || s.failed.load(Ordering::Relaxed) {
                self.settling = false;
            } else {
                return true;
            }
        }
        false
    }

    fn len_frames(&self) -> Option<u64> {
        let actual = self.shared.actual_total.load(Ordering::Acquire);
        if actual != u64::MAX { Some(actual) } else { self.est_total }
    }
}

impl Drop for StreamingDeck {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Release);
        // Do not join: dropping a deck may happen on the audio-control path; the worker exits within a few milliseconds by itself.
        self.worker.take();
    }
}

fn worker_main(sh: Arc<Shared>, path: PathBuf, rate: u32, first: Session) {
    let mut session = Some(first);
    let mut my_gen = 0u64;
    let mut frames_made = 0u64; // output frames produced in this generation
    let mut start_frame = 0u64;
    loop {
        if sh.stop.load(Ordering::Acquire) {
            return;
        }
        let want = sh.want_gen.load(Ordering::Acquire);
        if want != my_gen || session.is_none() {
            my_gen = want;
            let target = sh.target_out.load(Ordering::Acquire);
            match open_session(&path, rate, target) {
                Ok((s, _)) => {
                    session = Some(s);
                    start_frame = target;
                    frames_made = 0;
                }
                Err(e) => {
                    *sh.error.lock().unwrap() = Some(e.to_string());
                    sh.failed.store(true, Ordering::Release);
                    sh.ack_gen.store(my_gen, Ordering::Release);
                    return;
                }
            }
            sh.done_gen.store(u64::MAX, Ordering::Release);
            sh.ack_gen.store(my_gen, Ordering::Release);
            // wait for the consumer to empty the ring of the old generation before pushing anything new
            while sh.flushed_gen.load(Ordering::Acquire) != my_gen {
                if sh.stop.load(Ordering::Acquire) || sh.want_gen.load(Ordering::Acquire) != my_gen {
                    break;
                }
                std::thread::sleep(Duration::from_micros(200));
            }
            if sh.want_gen.load(Ordering::Acquire) != my_gen {
                continue;
            }
        }
        let Some(s) = session.as_mut() else { continue };
        if s.finished {
            // everything is in the ring: announce the end once, then idle until a seek or stop
            if sh.done_gen.load(Ordering::Acquire) != my_gen && sh.want_gen.load(Ordering::Acquire) == my_gen {
                sh.actual_total.store(start_frame + frames_made, Ordering::Release);
                sh.done_gen.store(my_gen, Ordering::Release);
            }
            std::thread::sleep(Duration::from_millis(4));
            continue;
        }
        match s.next_chunk() {
            Ok(Some(chunk)) => {
                let chunk: Vec<f32> = chunk.to_vec();
                let mut off = 0usize;
                while off < chunk.len() {
                    if sh.stop.load(Ordering::Acquire) || sh.want_gen.load(Ordering::Acquire) != my_gen {
                        break;
                    }
                    let n = sh.ring.push(&chunk[off..]);
                    off += n;
                    if n == 0 {
                        std::thread::sleep(Duration::from_millis(2));
                    }
                }
                if off >= chunk.len() {
                    frames_made += (chunk.len() / 2) as u64;
                }
            }
            Ok(None) => {
                if sh.want_gen.load(Ordering::Acquire) == my_gen {
                    // end of the stream (its tail was flushed by `next_chunk`)
                    sh.actual_total.store(start_frame + frames_made, Ordering::Release);
                    sh.done_gen.store(my_gen, Ordering::Release);
                }
            }
            Err(e) => {
                *sh.error.lock().unwrap() = Some(e.to_string());
                sh.failed.store(true, Ordering::Release);
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::decode::decode_file;

    fn write_wav(path: &Path, rate: u32, channels: u16, frames: usize) {
        let mut seed = 99u32;
        let mut data = Vec::with_capacity(frames * channels as usize * 2);
        for i in 0..frames {
            seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
            let n = ((seed >> 16) as i32 - 32768) as f64 / 32768.0;
            let t = (2.0 * std::f64::consts::PI * 523.25 * i as f64 / rate as f64).sin();
            for c in 0..channels {
                let v = 0.5 * t + 0.05 * n * (1.0 + c as f64);
                data.extend_from_slice(&((v * 32767.0) as i16).to_le_bytes());
            }
        }
        let mut f = Vec::new();
        f.extend_from_slice(b"RIFF");
        f.extend_from_slice(&(36 + data.len() as u32).to_le_bytes());
        f.extend_from_slice(b"WAVEfmt ");
        f.extend_from_slice(&16u32.to_le_bytes());
        f.extend_from_slice(&1u16.to_le_bytes());
        f.extend_from_slice(&channels.to_le_bytes());
        f.extend_from_slice(&rate.to_le_bytes());
        f.extend_from_slice(&(rate * channels as u32 * 2).to_le_bytes());
        f.extend_from_slice(&(channels * 2).to_le_bytes());
        f.extend_from_slice(&16u16.to_le_bytes());
        f.extend_from_slice(b"data");
        f.extend_from_slice(&(data.len() as u32).to_le_bytes());
        f.extend_from_slice(&data);
        std::fs::write(path, f).unwrap();
    }

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("singws-bgm-stream-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d.join(name)
    }

    /// Seek and wait (as the engine does, by polling) until the new position can be read.
    fn seek_ready(deck: &mut StreamingDeck, frame: u64) {
        assert!(deck.seek(frame), "seek {frame}");
        let t0 = Instant::now();
        while deck.poll_pending() {
            assert!(t0.elapsed() < Duration::from_secs(3), "seek {frame} never became ready");
            std::thread::sleep(Duration::from_micros(200));
        }
        // like a real player: let the worker get ahead of the reader before reading
        let t1 = Instant::now();
        while deck.buffered_seconds() < 0.1 && deck.shared.done_gen.load(Ordering::Acquire) != deck.epoch && t1.elapsed() < Duration::from_secs(3) {
            std::thread::sleep(Duration::from_micros(200));
        }
    }

    fn read_all(deck: &mut StreamingDeck, max_frames: usize) -> Vec<f32> {
        let mut out = Vec::new();
        let mut buf = vec![0.0f32; 1000 * 2];
        let t0 = Instant::now();
        loop {
            // a real player never outruns the decoder for long; wait for data so an underrun's silence is not mistaken for audio
            let w0 = Instant::now();
            while deck.shared.ring.len() < buf.len()
                && deck.shared.done_gen.load(Ordering::Acquire) != deck.epoch
                && !deck.shared.failed.load(Ordering::Relaxed)
                && w0.elapsed() < Duration::from_secs(5)
            {
                std::thread::sleep(Duration::from_micros(200));
            }
            let n = deck.read(&mut buf);
            out.extend_from_slice(&buf[..n * 2]);
            if n < 1000 || out.len() / 2 >= max_frames {
                break;
            }
            if t0.elapsed() > Duration::from_secs(20) {
                panic!("read timed out: got {} frames, ring {} samples, done {} want {} failed {:?} underruns {} err {:?}", out.len() / 2, deck.shared.ring.len(), deck.shared.done_gen.load(Ordering::Relaxed), deck.shared.want_gen.load(Ordering::Relaxed), deck.failed(), deck.underruns(), deck.shared.error.lock().unwrap());
            }
        }
        out
    }

    #[test]
    fn a_full_read_equals_the_whole_file_decode_44k_to_48k() {
        let p = tmp("full.wav");
        write_wav(&p, 44_100, 2, 44_100 * 6);
        let whole = decode_file(&p, 48_000).unwrap();
        let mut deck = StreamingDeck::open(&p, 48_000).unwrap();
        let got = read_all(&mut deck, usize::MAX);
        assert_eq!(got.len(), whole.samples.len(), "length");
        let worst = got.iter().zip(whole.samples.iter()).map(|(a, b)| (a - b).abs()).fold(0.0f32, f32::max);
        assert!(worst < 1e-6, "worst difference {worst}");
        assert_eq!(deck.len_frames(), Some(whole.frames()));
    }

    #[test]
    fn the_same_rate_and_a_mono_file_work() {
        let p = tmp("mono.wav");
        write_wav(&p, 48_000, 1, 48_000 * 2);
        let whole = decode_file(&p, 48_000).unwrap();
        let mut deck = StreamingDeck::open(&p, 48_000).unwrap();
        let got = read_all(&mut deck, usize::MAX);
        assert_eq!(got, *whole.samples);
    }

    #[test]
    fn seeks_land_on_the_exact_frame() {
        let p = tmp("seek.wav");
        write_wav(&p, 44_100, 2, 44_100 * 12);
        let whole = decode_file(&p, 48_000).unwrap();
        let mut deck = StreamingDeck::open(&p, 48_000).unwrap();
        for target in [0u64, 1, 4799, 48_000, 123_457, 300_000, 5_000, 560_000] {
            seek_ready(&mut deck, target);
            let mut buf = vec![0.0f32; 2400 * 2];
            assert_eq!(deck.read(&mut buf), 2400);
            let want = &whole.samples[target as usize * 2..(target as usize + 2400) * 2];
            let worst = buf.iter().zip(want).map(|(a, b)| (a - b).abs()).fold(0.0f32, f32::max);
            assert!(worst < 1e-4, "seek {target}: worst difference {worst}");
        }
    }

    #[test]
    fn read_stops_short_only_at_the_true_end() {
        let p = tmp("end.wav");
        write_wav(&p, 48_000, 2, 48_000);
        let mut deck = StreamingDeck::open(&p, 48_000).unwrap();
        seek_ready(&mut deck, 47_000);
        let mut buf = vec![0.0f32; 2000 * 2];
        let n = deck.read(&mut buf);
        assert_eq!(n, 1000, "exactly the frames that exist");
        assert_eq!(deck.read(&mut buf), 0);
    }

    #[test]
    fn a_slow_reader_never_loses_or_repeats_audio() {
        let p = tmp("slow.wav");
        write_wav(&p, 44_100, 2, 44_100 * 8);
        let whole = decode_file(&p, 48_000).unwrap();
        let mut deck = StreamingDeck::open(&p, 48_000).unwrap();
        // reader far slower than the worker: the 2 s ring fills and the worker waits
        let mut got = Vec::new();
        let mut buf = vec![0.0f32; 512 * 2];
        loop {
            let n = deck.read(&mut buf);
            got.extend_from_slice(&buf[..n * 2]);
            if n < 512 {
                break;
            }
            if got.len() / 2 % 20_480 == 0 {
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        assert_eq!(got.len(), whole.samples.len());
        assert!(got.iter().zip(whole.samples.iter()).all(|(a, b)| (a - b).abs() < 1e-6));
        assert_eq!(deck.underruns(), 0, "no underruns expected with an idle machine");
    }

    #[test]
    fn seek_returns_at_once_and_the_deck_reports_pending_until_audio_arrives() {
        let p = tmp("pending.wav");
        write_wav(&p, 44_100, 2, 44_100 * 20);
        let mut deck = StreamingDeck::open(&p, 48_000).unwrap();
        let t0 = Instant::now();
        assert!(deck.seek(500_000));
        assert!(t0.elapsed() < Duration::from_millis(2), "seek blocked for {:?}", t0.elapsed());
        let mut saw_pending = false;
        let t1 = Instant::now();
        while deck.poll_pending() {
            saw_pending = true;
            assert!(t1.elapsed() < Duration::from_secs(3));
            std::thread::sleep(Duration::from_micros(100));
        }
        assert!(saw_pending || t1.elapsed() < Duration::from_millis(1));
        let mut buf = vec![0.0f32; 480 * 2];
        assert_eq!(deck.read(&mut buf), 480);
        assert_eq!(deck.underruns(), 0);
    }

    #[test]
    fn dropping_a_deck_does_not_block() {
        let p = tmp("drop.wav");
        write_wav(&p, 44_100, 2, 44_100 * 30);
        let deck = StreamingDeck::open(&p, 48_000).unwrap();
        let t0 = Instant::now();
        drop(deck);
        assert!(t0.elapsed() < Duration::from_millis(50));
    }

    #[test]
    fn a_missing_or_garbage_file_is_an_error() {
        assert!(StreamingDeck::open(Path::new("/nonexistent/x.mp3"), 48_000).is_err());
        let p = tmp("garbage.mp3");
        std::fs::write(&p, vec![7u8; 5000]).unwrap();
        assert!(StreamingDeck::open(&p, 48_000).is_err());
    }
}
