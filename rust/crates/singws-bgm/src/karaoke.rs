//! The karaoke song as its own voice inside the engine (roadmap item 3, step 2).
//!
//! The song plays through its OWN chain, separate from the background music: source (streaming decode) -> Signalsmith key/tempo ->
//! normalization and volume (slide) -> the karaoke graphic EQ -> the exact master chain. The result is ADDED to the output after the music
//! chain, so the music can fade out underneath it exactly as it does today. Key and tempo are separate controls: the key is the stretcher's
//! transpose factor, the tempo is only how many source frames are consumed per output frame.
//!
//! Positions are in SONG time (what the lyrics follow), not output time: `audible_position_s` is the source position minus the audio still
//! held inside the stretcher, so it stays correct while the tempo changes. Nothing here allocates on the audio thread: scratch buffers are
//! sized at construction (tempo is limited to 0.25x..4x so the source block is bounded).

use std::time::{Duration, Instant};

use singws_stretch::Stretcher;

use crate::engine::{DeckSource, Effect, MAX_BLOCK_FRAMES, VolumeSlide};
use crate::eq::{EqHandle, GraphicEq};
use crate::fx::{MasterEffect, MasterHandle};

/// The master's clock as last reported by the control side.
#[derive(Clone, Copy)]
pub struct SlaveClock {
    /// Song seconds the picture is at, `rate` song seconds per wall second (0 = paused), read at `at`.
    pub pos: f64,
    pub rate: f64,
    pub at: Instant,
}

/// Largest tempo trim the voice applies to stay on the clock (0.8%: inaudible, no pitch change).
const MAX_TRIM: f64 = 0.008;
/// An error beyond this (seconds of song time) is not trimmed away but resynchronised with a seek.
const RESYNC_ERR: f64 = 0.6;
/// Time over which a trim removes an error (seconds).
const TRIM_WINDOW_S: f64 = 4.0;

pub const MIN_TEMPO: f64 = 0.25;
pub const MAX_TEMPO: f64 = 4.0;
/// Click-prevention fade-in after a start or seek (seconds).
const RAMP_IN_S: f32 = 0.005;

/// Control-side handles for the karaoke chain's parameters (atomics inside; safe from any thread).
#[derive(Clone)]
pub struct KaraokeHandles {
    pub eq: EqHandle,
    pub master: MasterHandle,
}

pub struct KaraokeVoice {
    rate: u32,
    src: Option<Box<dyn DeckSource>>,
    stretch: Stretcher,
    speed: f64,
    carry: f64,
    /// Source frames consumed since the start of the song (absolute: a seek sets it to the target).
    in_pos: u64,
    /// Output frames this voice has produced since the last start/seek (the audible position is zero until the stretcher has warmed up).
    produced: u64,
    /// Source frame the current run started from (a seek or the start): the audible position never goes before it.
    seek_origin: u64,
    len_frames: Option<u64>,
    playing: bool,
    paused: bool,
    /// The source ran out (the tail is still being drained).
    eos: bool,
    /// The tail has been drained: the song is over.
    finished: bool,
    volume: VolumeSlide,
    norm: f32,
    ramp_done: u32,
    ramp_len: u32,
    in_buf: Vec<f32>,
    out_buf: Vec<f32>,
    effects: Vec<Box<dyn Effect>>,
    /// Slaved mode: the picture (mpv) is the master. `(song position, rate, when read, output offset)`; the voice trims its own tempo by a
    /// tiny amount to stay on it (inaudible) and resynchronises with a seek only when it is badly off.
    slave: Option<SlaveClock>,
    /// Current tempo trim applied on top of the user's tempo (fraction, e.g. +0.004 = 0.4% faster).
    trim: f64,
    /// Output latency of the sound card in seconds: the mix runs this far AHEAD of what is heard, so the voice aims that far behind.
    latency_s: f64,
    last_resync: Option<Instant>,
    /// Frames of silence produced while the source could not deliver yet (a seek in progress).
    pub pending_frames: u64,
    pub underrun_frames: u64,
}

impl KaraokeVoice {
    pub fn new(rate: u32) -> (Self, KaraokeHandles) {
        let max_in = (MAX_BLOCK_FRAMES as f64 * MAX_TEMPO) as usize + 16;
        let stretch = Stretcher::with_capacity(rate, max_in, MAX_BLOCK_FRAMES).expect("stretcher");
        let (eq, eq_h) = GraphicEq::new(rate);
        let (master, master_h) = MasterEffect::new(rate);
        let voice = Self {
            rate,
            src: None,
            stretch,
            speed: 1.0,
            carry: 0.0,
            in_pos: 0,
            produced: 0,
            seek_origin: 0,
            len_frames: None,
            playing: false,
            paused: false,
            eos: false,
            finished: false,
            volume: VolumeSlide::new(1.0),
            norm: 1.0,
            ramp_done: u32::MAX,
            ramp_len: ((RAMP_IN_S * rate as f32) as u32).max(1),
            in_buf: vec![0.0; max_in * 2],
            out_buf: vec![0.0; MAX_BLOCK_FRAMES * 2],
            effects: vec![Box::new(eq) as Box<dyn Effect>, Box::new(master) as Box<dyn Effect>],
            slave: None,
            trim: 0.0,
            latency_s: 0.0,
            last_resync: None,
            pending_frames: 0,
            underrun_frames: 0,
        };
        (voice, KaraokeHandles { eq: eq_h, master: master_h })
    }

    fn reset_stream(&mut self, at_frame: u64) {
        self.stretch.reset();
        self.carry = 0.0;
        self.produced = 0;
        self.seek_origin = at_frame;
        self.in_pos = at_frame;
        self.eos = false;
        self.finished = false;
        self.ramp_done = 0;
    }

    /// Replaces the song. Returns the previous source so the caller can retire it off the audio thread.
    pub fn load(&mut self, src: Box<dyn DeckSource>, norm: f32, paused: bool, volume: Option<f32>) -> Option<Box<dyn DeckSource>> {
        self.len_frames = src.len_frames();
        let old = self.src.replace(src);
        self.norm = crate::engine::clamp_norm(norm);
        if let Some(v) = volume {
            self.volume.set(crate::engine::clamp_volume(v));
        }
        self.reset_stream(0);
        self.playing = !paused;
        self.paused = paused;
        old
    }

    /// Stops and unloads. Returns the source for retirement.
    pub fn stop(&mut self) -> Option<Box<dyn DeckSource>> {
        self.playing = false;
        self.paused = false;
        self.eos = false;
        self.finished = false;
        self.len_frames = None;
        self.in_pos = 0;
        self.produced = 0;
        self.seek_origin = 0;
        self.src.take()
    }

    pub fn play(&mut self) -> bool {
        if self.src.is_none() || self.finished {
            return false;
        }
        if !self.playing {
            self.ramp_done = 0; // resume with a short fade-in
        }
        self.playing = true;
        self.paused = false;
        true
    }

    pub fn pause(&mut self) -> bool {
        if self.src.is_none() || !self.playing {
            return false;
        }
        self.playing = false;
        self.paused = true;
        true
    }

    /// Jump to a song position. The source seeks (a streaming source answers `poll_pending` until it has data), the stretcher restarts.
    pub fn seek_seconds(&mut self, seconds: f32) -> bool {
        let Some(src) = self.src.as_mut() else { return false };
        let mut frame = (seconds.max(0.0) as f64 * self.rate as f64) as u64;
        if let Some(len) = self.len_frames {
            frame = frame.min(len.saturating_sub(1));
        }
        if !src.seek(frame) {
            return false;
        }
        self.reset_stream(frame);
        true
    }

    /// Tempo as a percentage (100 = normal). Live: no restart, no effect on the key.
    pub fn set_tempo_percent(&mut self, percent: f64) {
        self.speed = (percent / 100.0).clamp(MIN_TEMPO, MAX_TEMPO);
        self.stretch.set_speed(self.speed);
    }

    /// Key in semitones. Live: no restart, no effect on the tempo.
    pub fn set_semitones(&mut self, semitones: f64) {
        self.stretch.set_semitones(semitones.clamp(-24.0, 24.0));
    }

    /// Slaved mode on (`Some`) or off. The control side calls this every few tens of milliseconds with the picture's position; between
    /// calls the voice extrapolates, so a stalled GUI thread does not matter.
    pub fn set_slave_clock(&mut self, clock: Option<(f64, f64)>) {
        self.set_slave_clock_at(clock, Instant::now());
    }

    pub fn set_slave_clock_at(&mut self, clock: Option<(f64, f64)>, at: Instant) {
        self.slave = clock.map(|(pos, rate)| SlaveClock { pos, rate, at });
        if self.slave.is_none() {
            self.trim = 0.0;
            self.stretch.set_speed(self.speed);
        }
    }

    pub fn set_output_latency_seconds(&mut self, s: f64) {
        self.latency_s = s.clamp(0.0, 1.0);
    }

    pub fn is_slaved(&self) -> bool {
        self.slave.is_some()
    }

    pub fn set_volume(&mut self, v: f32) {
        self.volume.set(crate::engine::clamp_volume(v));
    }

    pub fn slide_volume(&mut self, v: f32, ms: u32) {
        let frames = (ms as u64 * self.rate as u64) / 1000;
        self.volume.slide_to(crate::engine::clamp_volume(v), frames);
    }

    pub fn volume_now(&self) -> f32 {
        self.volume.current()
    }

    pub fn set_norm(&mut self, f: f32) {
        self.norm = crate::engine::clamp_norm(f);
    }

    pub fn has_song(&self) -> bool {
        self.src.is_some()
    }
    pub fn is_playing(&self) -> bool {
        self.playing
    }
    pub fn is_paused(&self) -> bool {
        self.paused
    }
    pub fn is_finished(&self) -> bool {
        self.finished
    }
    pub fn tempo(&self) -> f64 {
        self.speed
    }

    /// Song position in seconds as heard: the source position minus the audio still inside the stretcher. That is its input latency
    /// plus its output latency in SONG time (`speed` times as long as in output time), so it stays right while the tempo changes.
    pub fn audible_position_s(&self) -> f32 {
        let inside = self.stretch.input_latency_frames() as f64 + self.speed * self.stretch.output_latency_frames() as f64;
        let audible = (self.in_pos as f64 - inside).max(self.seek_origin as f64);
        if self.produced == 0 { self.seek_origin as f32 / self.rate as f32 } else { (audible / self.rate as f64) as f32 }
    }

    pub fn duration_s(&self) -> f32 {
        self.len_frames.map(|l| (l as f64 / self.rate as f64) as f32).unwrap_or(0.0)
    }

    /// Adds this voice's output for the block to `out` (interleaved stereo, at most MAX_BLOCK_FRAMES frames). No allocation.
    pub fn mix_into(&mut self, out: &mut [f32]) {
        self.mix_into_at(out, Instant::now());
    }

    /// Slaved mode: compare where this voice is with where the master says the song should be NOW, and nudge the tempo.
    fn follow_master(&mut self, now: Instant) {
        let Some(c) = self.slave else { return };
        if c.rate <= 0.0 {
            self.trim = 0.0; // the master is paused; the control side pauses this voice too
            return;
        }
        let expected = c.pos + now.saturating_duration_since(c.at).as_secs_f64() * c.rate;
        // the mix runs ahead of the speakers by the device latency (in song time: latency * rate), so aim that far ahead
        let aim = expected + self.latency_s * c.rate;
        let err = self.audible_position_s() as f64 - aim; // + : this voice is ahead of the picture
        if err.abs() > RESYNC_ERR {
            let allowed = self.last_resync.map(|t| now.saturating_duration_since(t) > Duration::from_millis(800)).unwrap_or(true);
            if allowed {
                self.last_resync = Some(now);
                self.seek_seconds(aim.max(0.0) as f32);
                self.trim = 0.0;
            }
            return;
        }
        let want = (-err / TRIM_WINDOW_S).clamp(-MAX_TRIM, MAX_TRIM);
        self.trim += (want - self.trim) * 0.05; // smooth: no audible wobble
    }

    pub fn mix_into_at(&mut self, out: &mut [f32], now: Instant) {
        if !self.playing || self.finished {
            return;
        }
        if self.slave.is_some() {
            self.follow_master(now);
            self.stretch.set_speed((self.speed * (1.0 + self.trim)).clamp(MIN_TEMPO, MAX_TEMPO));
        }
        let Some(src) = self.src.as_mut() else { return };
        let frames = out.len() / 2;
        if frames == 0 || frames > MAX_BLOCK_FRAMES {
            return;
        }
        if src.poll_pending() {
            self.pending_frames += frames as u64;
            return;
        }
        let o = &mut self.out_buf[..frames * 2];
        if self.eos {
            // the source is exhausted: drain the stretcher's tail, then the song is over
            self.stretch.flush_into(o);
            self.finished = true;
        } else {
            let eff = if self.slave.is_some() { (self.speed * (1.0 + self.trim)).clamp(MIN_TEMPO, MAX_TEMPO) } else { self.speed };
            let want = frames as f64 * eff + self.carry;
            let n = (want.floor() as usize).min(self.in_buf.len() / 2);
            self.carry = want - n as f64;
            let ib = &mut self.in_buf[..n * 2];
            let got = src.read(ib);
            if got < n {
                ib[got * 2..].fill(0.0);
                self.eos = true;
            }
            self.stretch.process_into(ib, o);
            self.in_pos += got as u64;
            self.produced += frames as u64;
        }
        // volume (slide), normalization, and the start/seek fade-in
        let norm = self.norm;
        for i in 0..frames {
            let mut g = self.volume.next() * norm;
            if self.ramp_done < self.ramp_len {
                g *= (self.ramp_done as f32 + 0.5) / self.ramp_len as f32;
                self.ramp_done += 1;
            }
            o[2 * i] *= g;
            o[2 * i + 1] *= g;
        }
        for fx in self.effects.iter_mut() {
            fx.process(o);
        }
        for (d, s) in out.iter_mut().zip(o.iter()) {
            *d += *s;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::Engine;

    const RATE: u32 = 48_000;

    /// A sine of `hz` lasting `seconds`, seekable, optionally "pending" for a number of polls after a seek.
    struct Song {
        hz: f64,
        total: u64,
        pos: u64,
        pending_polls: u32,
        seek_delay_polls: u32,
    }
    impl Song {
        fn new(hz: f64, seconds: f64) -> Self {
            Self { hz, total: (seconds * RATE as f64) as u64, pos: 0, pending_polls: 0, seek_delay_polls: 0 }
        }
    }
    impl DeckSource for Song {
        fn read(&mut self, out: &mut [f32]) -> usize {
            let n = ((out.len() / 2) as u64).min(self.total - self.pos.min(self.total)) as usize;
            for i in 0..n {
                let v = (0.3 * (std::f64::consts::TAU * self.hz * (self.pos + i as u64) as f64 / RATE as f64).sin()) as f32;
                out[2 * i] = v;
                out[2 * i + 1] = v;
            }
            self.pos += n as u64;
            n
        }
        fn seek(&mut self, frame: u64) -> bool {
            self.pos = frame.min(self.total);
            self.pending_polls = self.seek_delay_polls;
            true
        }
        fn len_frames(&self) -> Option<u64> {
            Some(self.total)
        }
        fn poll_pending(&mut self) -> bool {
            if self.pending_polls > 0 {
                self.pending_polls -= 1;
                true
            } else {
                false
            }
        }
    }

    fn hz_of(mono: &[f32]) -> f64 {
        let mut first = None;
        let mut last = 0.0;
        let mut count = 0usize;
        for i in 1..mono.len() {
            if mono[i - 1] < 0.0 && mono[i] >= 0.0 {
                let frac = -mono[i - 1] as f64 / (mono[i] - mono[i - 1]) as f64;
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

    /// Renders `blocks` blocks of 1024 frames from the voice alone.
    fn render(v: &mut KaraokeVoice, blocks: usize) -> Vec<f32> {
        let mut all = Vec::new();
        for _ in 0..blocks {
            let mut b = vec![0.0f32; 1024 * 2];
            v.mix_into(&mut b);
            all.extend_from_slice(&b);
        }
        all
    }

    fn mono(v: &[f32]) -> Vec<f32> {
        v.chunks_exact(2).map(|f| f[0]).collect()
    }

    #[test]
    fn plays_and_reports_song_time() {
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.load(Box::new(Song::new(440.0, 30.0)), 1.0, false, Some(1.0));
        let out = render(&mut v, 94); // ~2 s
        assert!(out.iter().any(|s| s.abs() > 0.1), "audible");
        let pos = v.audible_position_s();
        assert!((pos - 2.0).abs() < 0.25, "song position {pos} after ~2 s of output");
        assert!((v.duration_s() - 30.0).abs() < 0.01);
    }

    #[test]
    fn tempo_changes_the_song_clock_not_the_pitch() {
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.set_tempo_percent(50.0);
        v.load(Box::new(Song::new(440.0, 30.0)), 1.0, false, Some(1.0));
        let out = render(&mut v, 190); // ~4 s of output at half speed = ~2 s of song
        let pos = v.audible_position_s();
        assert!((pos - 2.0).abs() < 0.3, "song position {pos} (4 s output at 50%)");
        let m = mono(&out);
        let mid = &m[m.len() / 4..m.len() * 3 / 4];
        assert!((hz_of(mid) / 440.0 - 1.0).abs() < 0.003, "tempo moved the pitch: {} Hz", hz_of(mid));
    }

    #[test]
    fn key_moves_the_pitch_not_the_clock() {
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.set_semitones(2.0);
        v.load(Box::new(Song::new(440.0, 30.0)), 1.0, false, Some(1.0));
        let out = render(&mut v, 190);
        let m = mono(&out);
        let mid = &m[m.len() / 4..m.len() * 3 / 4];
        let want = 440.0 * 2f64.powf(2.0 / 12.0);
        assert!((1200.0 * (hz_of(mid) / want).log2()).abs() < 8.0, "{} Hz vs {want}", hz_of(mid));
        assert!((v.audible_position_s() - 4.0).abs() < 0.3, "key moved the clock: {}", v.audible_position_s());
    }

    #[test]
    fn seek_jumps_the_song_clock_and_waits_for_a_pending_source() {
        let (mut v, _h) = KaraokeVoice::new(RATE);
        let mut song = Song::new(440.0, 60.0);
        song.seek_delay_polls = 3;
        v.load(Box::new(song), 1.0, false, Some(1.0));
        render(&mut v, 20);
        assert!(v.seek_seconds(40.0));
        let silent = render(&mut v, 3);
        assert!(silent.iter().all(|s| *s == 0.0), "silence while the source is not ready");
        assert!((v.audible_position_s() - 40.0).abs() < 0.01, "clock must not run while pending");
        let out = render(&mut v, 47); // ~1 s
        assert!(out.iter().any(|s| s.abs() > 0.1));
        let pos = v.audible_position_s();
        assert!((pos - 41.0).abs() < 0.2, "after the seek: {pos}");
    }


    /// A burst of 1 kHz tone starting at song time `at` seconds, silence elsewhere: the onset marks exactly which song instant is sounding.
    struct Burst {
        at: u64,
        pos: u64,
        total: u64,
    }
    impl DeckSource for Burst {
        fn read(&mut self, out: &mut [f32]) -> usize {
            let n = ((out.len() / 2) as u64).min(self.total - self.pos.min(self.total)) as usize;
            for i in 0..n {
                let p = self.pos + i as u64;
                let v = if p >= self.at && p < self.at + RATE as u64 / 2 {
                    (0.5 * (std::f64::consts::TAU * 1000.0 * (p - self.at) as f64 / RATE as f64).sin()) as f32
                } else {
                    0.0
                };
                out[2 * i] = v;
                out[2 * i + 1] = v;
            }
            self.pos += n as u64;
            n
        }
        fn seek(&mut self, frame: u64) -> bool {
            self.pos = frame.min(self.total);
            true
        }
        fn len_frames(&self) -> Option<u64> {
            Some(self.total)
        }
    }

    #[test]
    fn the_reported_clock_is_the_song_instant_that_is_sounding() {
        // At the moment the burst's onset leaves the voice, the reported song position must equal the burst's start (2.0 s) plus the part of
        // the block that already played, at any tempo. (An earlier version reported the SOURCE position, ~100 ms ahead of what was heard.)
        for (tempo, tol) in [(100.0, 0.03), (50.0, 0.04), (150.0, 0.05), (80.0, 0.04)] {
            let (mut v, _h) = KaraokeVoice::new(RATE);
            v.set_tempo_percent(tempo);
            let at = 4 * RATE as u64;
            v.load(Box::new(Burst { at, pos: 0, total: 20 * RATE as u64 }), 1.0, false, Some(1.0));
            let block = 128usize;
            let mut found = None;
            for _ in 0..(RATE as usize * 12 / block) {
                let mut b = vec![0.0f32; block * 2];
                v.mix_into(&mut b);
                if let Some(i) = (0..block).find(|&i| b[2 * i].abs() > 0.05) {
                    // reported position is as of the END of this block; the onset was `block - i` output frames earlier
                    let pos_end = v.audible_position_s() as f64;
                    let onset_song_time = pos_end - (block - i) as f64 / RATE as f64 * (tempo / 100.0);
                    found = Some(onset_song_time);
                    break;
                }
            }
            let onset = found.expect("the burst never sounded");
            eprintln!("tempo {tempo}%: burst starts at 4.000 s, clock said {onset:.4} s ({:+.1} ms)", (onset - 4.0) * 1000.0);
            assert!((onset - 4.0).abs() < tol, "tempo {tempo}%: the burst that starts at 4.000 s sounded when the clock said {onset:.3} s");
        }
    }


    /// Drives a slaved voice with a synthetic clock: 10 ms blocks, `clock_at(t)` is where the master says the song is at time t.
    fn run_slaved(v: &mut KaraokeVoice, seconds: f64, base: Instant, start_t: f64, clock_pos: impl Fn(f64) -> f64, rate: f64) -> f64 {
        let blocks = (seconds * 100.0) as usize;
        for i in 0..blocks {
            let t = start_t + i as f64 * 0.01;
            let now = base + Duration::from_secs_f64(t);
            if i % 3 == 0 {
                v.set_slave_clock_at(Some((clock_pos(t), rate)), now); // the control side reports every ~30 ms
            }
            let mut b = vec![0.0f32; 480 * 2];
            v.mix_into_at(&mut b, now);
        }
        start_t + blocks as f64 * 0.01
    }

    #[test]
    fn a_slaved_voice_closes_a_small_offset_with_an_inaudible_tempo_trim() {
        let base = Instant::now();
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.load(Box::new(Song::new(440.0, 120.0)), 1.0, false, Some(1.0));
        // the master is 120 ms BEHIND the voice from the start (the voice began early)
        let _ = run_slaved(&mut v, 1.0, base, 0.0, |t| t - 0.12, 1.0);
        let t = run_slaved(&mut v, 25.0, base, 1.0, |t| t - 0.12, 1.0);
        let err = v.audible_position_s() as f64 - (t - 0.12);
        assert!(err.abs() < 0.03, "still {err:.3} s off after 25 s");
        assert!(v.trim.abs() <= MAX_TRIM + 1e-9, "trim {} exceeds the inaudible limit", v.trim);
        assert!(v.last_resync.is_none(), "a small offset must not cause a seek");
    }

    #[test]
    fn a_slaved_voice_resynchronises_after_the_master_jumps() {
        let base = Instant::now();
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.load(Box::new(Song::new(440.0, 300.0)), 1.0, false, Some(1.0));
        let t = run_slaved(&mut v, 3.0, base, 0.0, |t| t, 1.0);
        // the picture is sought to 100 s: the master's clock jumps
        let t = run_slaved(&mut v, 2.0, base, t, |x| 100.0 + (x - t), 1.0);
        let err = v.audible_position_s() as f64 - (100.0 + 2.0);
        assert!(err.abs() < 0.3, "after the jump the voice is {err:.3} s off");
        assert!(v.last_resync.is_some());
    }

    #[test]
    fn a_paused_master_does_not_move_the_voice_tempo() {
        let base = Instant::now();
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.load(Box::new(Song::new(440.0, 60.0)), 1.0, false, Some(1.0));
        run_slaved(&mut v, 2.0, base, 0.0, |_| 5.0, 0.0);
        assert_eq!(v.trim, 0.0);
    }

    #[test]
    fn a_slaved_voice_aims_ahead_by_the_device_latency() {
        let base = Instant::now();
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.set_output_latency_seconds(0.080);
        v.load(Box::new(Song::new(440.0, 120.0)), 1.0, false, Some(1.0));
        let t = run_slaved(&mut v, 30.0, base, 0.0, |t| t, 1.0);
        // the mix position must lead the master by the latency so that what is HEARD matches the picture
        let lead = v.audible_position_s() as f64 - t;
        assert!((lead - 0.080).abs() < 0.03, "the voice leads the master by {lead:.3} s, wanted 0.080");
    }

    #[test]
    fn slaving_follows_a_tempo_change_of_the_master() {
        let base = Instant::now();
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.load(Box::new(Song::new(440.0, 300.0)), 1.0, false, Some(1.0));
        let t = run_slaved(&mut v, 5.0, base, 0.0, |t| t, 1.0);
        // both go to 80%: the control side sets the voice's own tempo and reports the new rate
        v.set_tempo_percent(80.0);
        let p0 = t;
        let t2 = run_slaved(&mut v, 20.0, base, t, |x| p0 + (x - t) * 0.8, 0.8);
        let err = v.audible_position_s() as f64 - (p0 + (t2 - t) * 0.8);
        assert!(err.abs() < 0.12, "after a tempo change the voice is {err:.3} s off");
    }

    #[test]
    fn pause_holds_the_clock_and_resume_continues() {
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.load(Box::new(Song::new(440.0, 30.0)), 1.0, false, Some(1.0));
        render(&mut v, 47);
        assert!(v.pause());
        let p1 = v.audible_position_s();
        let silent = render(&mut v, 20);
        assert!(silent.iter().all(|s| *s == 0.0));
        assert_eq!(v.audible_position_s(), p1);
        assert!(v.play());
        render(&mut v, 47);
        assert!(v.audible_position_s() > p1 + 0.8);
    }

    #[test]
    fn the_song_ends_after_its_tail_and_then_stays_silent() {
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.load(Box::new(Song::new(440.0, 2.0)), 1.0, false, Some(1.0));
        render(&mut v, 120); // well past 2 s
        assert!(v.is_finished());
        let after = render(&mut v, 10);
        assert!(after.iter().all(|s| *s == 0.0));
        assert!((v.audible_position_s() - 2.0).abs() < 0.3);
    }

    #[test]
    fn a_volume_slide_fades_the_song_out() {
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.load(Box::new(Song::new(440.0, 30.0)), 1.0, false, Some(1.0));
        render(&mut v, 10);
        v.slide_volume(0.0, 200);
        render(&mut v, 20); // ~0.43 s: the slide (0.2 s) is long over
        let tail = render(&mut v, 4);
        let peak = tail.iter().fold(0.0f32, |a, s| a.max(s.abs()));
        assert!(peak < 0.002, "still audible after the fade: {peak}");
    }

    #[test]
    fn the_karaoke_chain_is_separate_from_the_music_chain() {
        // The song joins after the music effects: a music-side EQ boost must not touch it, and the karaoke EQ must.
        let mut e = Engine::new(RATE);
        e.set_ramp_in_seconds(0.0);
        let h = e.karaoke_handles();
        e.karaoke_load(Box::new(Song::new(1000.0, 10.0)), 1.0, false, Some(1.0));
        let mut flat = vec![0.0f32; 4096 * 2];
        for _ in 0..4 {
            e.mix(&mut flat);
        }
        let rms_flat = (flat.iter().map(|s| s * s).sum::<f32>() / flat.len() as f32).sqrt();
        h.eq.set_gains_db(&[0.0, 0.0, 0.0, 0.0, 0.0, 12.0, 0.0, 0.0, 0.0, 0.0]); // +12 dB at 1 kHz
        h.eq.set_enabled(true);
        let mut boosted = vec![0.0f32; 4096 * 2];
        for _ in 0..4 {
            e.mix(&mut boosted);
        }
        let rms_boost = (boosted.iter().map(|s| s * s).sum::<f32>() / boosted.len() as f32).sqrt();
        assert!(rms_boost > rms_flat * 3.0, "karaoke EQ had no effect: {rms_flat} -> {rms_boost}");
        assert!(e.karaoke().is_playing());
    }

    #[test]
    fn the_scratch_buffers_never_move() {
        let (mut v, _h) = KaraokeVoice::new(RATE);
        v.set_tempo_percent(400.0);
        v.load(Box::new(Song::new(440.0, 60.0)), 1.0, false, Some(1.0));
        let (pi, po) = (v.in_buf.as_ptr(), v.out_buf.as_ptr());
        let mut big = vec![0.0f32; MAX_BLOCK_FRAMES * 2];
        for _ in 0..5 {
            v.mix_into(&mut big);
        }
        v.set_tempo_percent(25.0);
        for _ in 0..5 {
            v.mix_into(&mut big);
        }
        assert_eq!((pi, po), (v.in_buf.as_ptr(), v.out_buf.as_ptr()));
    }
}
