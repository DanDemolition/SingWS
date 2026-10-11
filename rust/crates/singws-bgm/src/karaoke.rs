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

use singws_stretch::Stretcher;

use crate::engine::{DeckSource, Effect, MAX_BLOCK_FRAMES, VolumeSlide};
use crate::eq::{EqHandle, GraphicEq};
use crate::fx::{MasterEffect, MasterHandle};

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
    /// Source frames currently held inside the stretcher (latency bookkeeping for the audible position).
    held: f64,
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
            held: 0.0,
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
            pending_frames: 0,
            underrun_frames: 0,
        };
        (voice, KaraokeHandles { eq: eq_h, master: master_h })
    }

    fn reset_stream(&mut self, at_frame: u64) {
        self.stretch.reset();
        self.carry = 0.0;
        self.held = 0.0;
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
        self.held = 0.0;
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

    /// Song position in seconds as heard (source position minus what the stretcher still holds).
    pub fn audible_position_s(&self) -> f32 {
        (((self.in_pos as f64 - self.held).max(0.0)) / self.rate as f64) as f32
    }

    pub fn duration_s(&self) -> f32 {
        self.len_frames.map(|l| (l as f64 / self.rate as f64) as f32).unwrap_or(0.0)
    }

    /// Adds this voice's output for the block to `out` (interleaved stereo, at most MAX_BLOCK_FRAMES frames). No allocation.
    pub fn mix_into(&mut self, out: &mut [f32]) {
        if !self.playing || self.finished {
            return;
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
            let want = frames as f64 * self.speed + self.carry;
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
            self.held += n as f64 - frames as f64 * self.speed;
            if self.held < 0.0 {
                self.held = 0.0;
            }
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
