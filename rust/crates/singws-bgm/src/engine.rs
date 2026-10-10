//! Device-free two-deck background-music engine core (Stage 4, milestone 1).
//!
//! This mirrors the contract of `bass_background_engine.BassBackgroundEngine` (see RUST_BGM_ENGINE_CONTRACT.md) without any
//! sound card, decoder or threads in it: every operation is a plain method and [`Engine::mix`] is the audio callback body.
//! That makes each rule testable to the sample and lets the same core sit behind cpal (milestone 3).
//!
//! Rules carried over from BASS (numbers are the ones the Python engine uses):
//! * stereo f32 at one fixed engine rate; the master volume is clamped to 0..=1, per-deck normalization to 0.05..=4,
//!   and a deck's gain is `norm` (the crossfade envelope owns the fade, never the normalization);
//! * a crossfade is two 65-node envelopes (outgoing `cos`, incoming `sin`, linear between nodes) started in the same block,
//!   its length clamped to what the incoming file still has (minimum 50 ms);
//! * the preloaded next deck is held paused: it consumes nothing until a crossfade starts;
//! * times and `source_ended` follow the AUDIBLE position (frames actually mixed), not the decoder.
//!
//! Realtime rules for `mix`: no allocation, no locks, no I/O. Decks are expected to be fed by a decoder thread through a ring
//! buffer in the real engine; here a [`DeckSource`] is simply asked for frames.

/// Maximum frames per `mix` call (scratch buffers are sized once).
pub const MAX_BLOCK_FRAMES: usize = 8192;
/// Nodes in a crossfade envelope, as in the BASS engine (64 segments).
pub const ENVELOPE_SEGMENTS: usize = 64;
/// Shortest crossfade BASS would schedule.
pub const MIN_CROSSFADE_S: f32 = 0.05;
/// The meter looks at this much audio (BASS_Mixer_ChannelGetLevelEx window).
pub const METER_WINDOW_S: f32 = 0.05;

/// A stream of stereo f32 frames at the engine rate.
pub trait DeckSource: Send {
    /// Fill `out` (interleaved L,R) and return the number of FRAMES written; fewer than asked means end of stream.
    fn read(&mut self, out: &mut [f32]) -> usize;
    /// Seek to a frame. Returns false when that is not possible.
    fn seek(&mut self, frame: u64) -> bool;
    /// Total length in frames, when known.
    fn len_frames(&self) -> Option<u64>;
}

/// A processing stage applied to the mixed output after the master volume (EQ, master chain, ...).
pub trait Effect: Send {
    fn process(&mut self, interleaved: &mut [f32]);
}

/// Normalization clamp, as `_norm_factor`.
pub fn clamp_norm(v: f32) -> f32 {
    if v.is_finite() { v.clamp(0.05, 4.0) } else { 1.0 }
}

/// Master volume clamp, as `_gain`.
pub fn clamp_volume(v: f32) -> f32 {
    if v.is_finite() { v.clamp(0.0, 1.0) } else { 0.0 }
}

/// Gain of one crossfade envelope at `frame` of `total` (piecewise linear over 65 nodes, as BASS interpolates them).
pub fn envelope_gain(frame: u64, total: u64, incoming: bool) -> f32 {
    if total == 0 || frame >= total {
        return if incoming { 1.0 } else { 0.0 };
    }
    let p = frame as f64 / total as f64 * ENVELOPE_SEGMENTS as f64;
    let i = (p.floor() as usize).min(ENVELOPE_SEGMENTS - 1);
    let frac = (p - i as f64) as f32;
    let node = |k: usize| -> f32 {
        if k >= ENVELOPE_SEGMENTS {
            return if incoming { 1.0 } else { 0.0 };
        }
        let angle = k as f32 / ENVELOPE_SEGMENTS as f32 * core::f32::consts::FRAC_PI_2;
        if incoming { angle.sin() } else { angle.cos() }
    };
    let (a, b) = (node(i), node(i + 1));
    a + (b - a) * frac
}

/// A volume that can slide logarithmically (linear in dB), like `BASS_ChannelSlideAttribute(BASS_SLIDE_LOG)`.
struct VolumeSlide {
    gain: f32,
    target: f32,
    ratio: f32,
    frames_left: u64,
}

impl VolumeSlide {
    const FLOOR: f32 = 1.0e-4; // -80 dB: the start of a slide from silence
    fn new(gain: f32) -> Self {
        Self { gain, target: gain, ratio: 1.0, frames_left: 0 }
    }
    fn set(&mut self, gain: f32) {
        self.gain = gain;
        self.target = gain;
        self.frames_left = 0;
    }
    fn slide_to(&mut self, target: f32, frames: u64) {
        if frames == 0 || (target - self.gain).abs() < 1.0e-9 {
            self.set(target);
            return;
        }
        let from = self.gain.max(Self::FLOOR);
        let to = target.max(Self::FLOOR);
        self.target = target;
        self.frames_left = frames;
        self.ratio = (to / from).powf(1.0 / frames as f32);
        self.gain = from;
    }
    #[inline]
    fn next(&mut self) -> f32 {
        if self.frames_left == 0 {
            return self.gain;
        }
        self.frames_left -= 1;
        if self.frames_left == 0 {
            self.gain = self.target;
        } else {
            self.gain *= self.ratio;
        }
        self.gain
    }
}

/// Sliding RMS over the last [`METER_WINDOW_S`] of one deck, no allocation after construction.
struct Meter {
    sq: Vec<[f32; 2]>,
    idx: usize,
    sum: [f64; 2],
}

impl Meter {
    fn new(rate: f32) -> Self {
        let n = (METER_WINDOW_S * rate).max(1.0) as usize;
        Self { sq: vec![[0.0; 2]; n], idx: 0, sum: [0.0; 2] }
    }
    #[inline]
    fn push(&mut self, l: f32, r: f32) {
        let new = [l * l, r * r];
        let old = self.sq[self.idx];
        self.sum[0] += (new[0] - old[0]) as f64;
        self.sum[1] += (new[1] - old[1]) as f64;
        self.sq[self.idx] = new;
        self.idx += 1;
        if self.idx == self.sq.len() {
            self.idx = 0;
        }
    }
    fn rms(&self) -> [f32; 2] {
        let n = self.sq.len() as f64;
        [(self.sum[0].max(0.0) / n).sqrt() as f32, (self.sum[1].max(0.0) / n).sqrt() as f32]
    }
}

struct Deck {
    src: Box<dyn DeckSource>,
    norm: f32,
    /// Audible position: frames of this deck already mixed into the output.
    pos: u64,
    len: Option<u64>,
    ended: bool,
    meter: Meter,
}

impl Deck {
    fn new(src: Box<dyn DeckSource>, norm: f32, rate: f32) -> Self {
        let len = src.len_frames();
        Self { src, norm: clamp_norm(norm), pos: 0, len, ended: false, meter: Meter::new(rate) }
    }
}

struct Crossfade {
    total: u64,
    done: u64,
    /// Secondary position at which the fade is complete.
    end_pos: u64,
}

/// The engine core. All methods are called from one thread at a time (the real engine funnels them through a command queue).
pub struct Engine {
    rate: f32,
    primary: Option<Deck>,
    secondary: Option<Deck>,
    crossfade: Option<Crossfade>,
    master: VolumeSlide,
    playing: bool,
    effects: Vec<Box<dyn Effect>>,
    scratch_a: Vec<f32>,
    scratch_b: Vec<f32>,
    /// Frames of silence produced while playing with nothing to play (the gap meter).
    pub gap_frames: u64,
}

impl Engine {
    pub fn new(sample_rate: u32) -> Self {
        Self {
            rate: sample_rate as f32,
            primary: None,
            secondary: None,
            crossfade: None,
            master: VolumeSlide::new(0.8),
            playing: false,
            effects: Vec::new(),
            scratch_a: vec![0.0; MAX_BLOCK_FRAMES * 2],
            scratch_b: vec![0.0; MAX_BLOCK_FRAMES * 2],
            gap_frames: 0,
        }
    }

    pub fn sample_rate(&self) -> u32 {
        self.rate as u32
    }

    // ---- transport -----------------------------------------------------------------------------------------------
    /// Replace everything with one loaded track (paused unless `paused` is false).
    pub fn load(&mut self, src: Box<dyn DeckSource>, norm_gain: f32, paused: bool, volume: Option<f32>) {
        self.stop();
        if let Some(v) = volume {
            self.master.set(clamp_volume(v));
        }
        self.primary = Some(Deck::new(src, norm_gain, self.rate));
        self.playing = !paused;
    }
    pub fn play(&mut self) -> bool {
        if self.primary.is_none() {
            return false;
        }
        self.playing = true;
        true
    }
    pub fn pause(&mut self) -> bool {
        self.playing = false;
        true
    }
    pub fn stop(&mut self) {
        self.crossfade = None;
        self.secondary = None;
        self.primary = None;
        self.playing = false;
    }
    pub fn is_playing(&self) -> bool {
        self.playing && self.primary.is_some()
    }
    pub fn is_paused(&self) -> bool {
        !self.playing && self.primary.is_some()
    }

    // ---- volume --------------------------------------------------------------------------------------------------
    pub fn set_master_volume(&mut self, v: f32) {
        self.master.set(clamp_volume(v));
    }
    pub fn slide_master_volume(&mut self, v: f32, duration_ms: u32) {
        let frames = (duration_ms as f64 / 1000.0 * self.rate as f64) as u64;
        self.master.slide_to(clamp_volume(v), frames);
    }
    pub fn master_volume(&self) -> f32 {
        self.master.target
    }
    pub fn set_primary_normalize_gain(&mut self, f: f32) {
        if let Some(d) = self.primary.as_mut() {
            d.norm = clamp_norm(f);
        }
    }
    pub fn set_secondary_normalize_gain(&mut self, f: f32) {
        if let Some(d) = self.secondary.as_mut() {
            d.norm = clamp_norm(f);
        }
    }

    // ---- effects -------------------------------------------------------------------------------------------------
    /// Replace the effect chain (EQ first, master chain last, as in the BASS mixer).
    pub fn set_effects(&mut self, effects: Vec<Box<dyn Effect>>) {
        self.effects = effects;
    }

    // ---- crossfade -----------------------------------------------------------------------------------------------
    /// Hold the next track paused and silent. False when there is no primary or a crossfade is already running.
    pub fn preload_secondary(&mut self, src: Box<dyn DeckSource>, norm_gain: f32) -> bool {
        if self.primary.is_none() || self.crossfade.is_some() {
            return false;
        }
        self.secondary = Some(Deck::new(src, norm_gain, self.rate));
        true
    }
    pub fn invalidate_secondary_preload(&mut self) {
        if self.crossfade.is_none() {
            self.secondary = None;
        }
    }
    pub fn has_secondary(&self) -> bool {
        self.secondary.is_some()
    }
    /// Begin the crossfade into the preloaded track, which starts `start_seconds` into itself.
    pub fn start_crossfade(&mut self, duration_ms: u32, start_seconds: f32) -> bool {
        if self.primary.is_none() || self.crossfade.is_some() {
            return false;
        }
        let rate = self.rate;
        let Some(sec) = self.secondary.as_mut() else { return false };
        let length = sec.len.unwrap_or(u64::MAX);
        if start_seconds > 0.0 {
            let offset = (start_seconds as f64 * rate as f64) as u64;
            if offset > 0 && offset < length && sec.src.seek(offset) {
                sec.pos = offset;
            }
        }
        let available_s = (length.saturating_sub(sec.pos)) as f64 / rate as f64;
        let seconds = (duration_ms as f64 / 1000.0).min(available_s).max(MIN_CROSSFADE_S as f64);
        let total = ((seconds * rate as f64) as u64).max(8);
        let end_pos = sec.pos + total;
        self.crossfade = Some(Crossfade { total, done: 0, end_pos });
        self.playing = true;
        true
    }
    /// True once the envelope is complete AND the incoming track has been heard that far (or ended).
    pub fn crossfade_finished(&self) -> bool {
        let (Some(cf), Some(sec)) = (self.crossfade.as_ref(), self.secondary.as_ref()) else { return false };
        cf.done >= cf.total && (sec.ended || sec.pos >= cf.end_pos)
    }
    pub fn crossfade_active(&self) -> bool {
        self.crossfade.is_some()
    }
    /// Promote the incoming deck to primary.
    pub fn complete_crossfade(&mut self) -> bool {
        let Some(sec) = self.secondary.take() else { return false };
        self.primary = Some(sec);
        self.crossfade = None;
        true
    }
    pub fn cancel_crossfade(&mut self) {
        self.crossfade = None;
        self.secondary = None;
    }

    // ---- position ------------------------------------------------------------------------------------------------
    /// (audible position, duration) of the primary, in seconds.
    pub fn get_times(&self) -> (f32, f32) {
        match self.primary.as_ref() {
            Some(d) => (
                d.pos as f32 / self.rate,
                d.len.map(|l| l as f32 / self.rate).unwrap_or(0.0),
            ),
            None => (0.0, 0.0),
        }
    }
    pub fn seek(&mut self, seconds: f32) -> bool {
        let rate = self.rate;
        let Some(d) = self.primary.as_mut() else { return false };
        let mut target = (seconds.max(0.0) as f64 * rate as f64) as u64;
        if let Some(len) = d.len {
            target = target.min(len.saturating_sub((0.001 * rate) as u64));
        }
        if d.src.seek(target) {
            d.pos = target;
            d.ended = false;
            true
        } else {
            false
        }
    }
    pub fn source_ended(&self) -> bool {
        match self.primary.as_ref() {
            None => true,
            Some(d) => {
                d.ended
                    || d.len.map(|l| d.pos as f64 >= l as f64 - 0.02 * self.rate as f64).unwrap_or(false)
            }
        }
    }
    /// 0..=1 level of the audible deck (secondary while crossfading), like `meter_level`: RMS over 50 ms, times four.
    pub fn meter_level(&self) -> f32 {
        let deck = if self.crossfade.is_some() { self.secondary.as_ref() } else { self.primary.as_ref() };
        match deck {
            None => 0.0,
            Some(d) => {
                let [l, r] = d.meter.rms();
                (l.max(r) * 4.0).clamp(0.0, 1.0)
            }
        }
    }

    // ---- the audio callback --------------------------------------------------------------------------------------
    /// Mix `out.len() / 2` stereo frames. Never allocates. Paused or empty: silence (empty while playing counts as a gap).
    pub fn mix(&mut self, out: &mut [f32]) {
        let mut offset = 0;
        while offset < out.len() {
            let chunk = (out.len() - offset).min(MAX_BLOCK_FRAMES * 2) & !1;
            if chunk == 0 {
                break;
            }
            self.mix_chunk(&mut out[offset..offset + chunk]);
            offset += chunk;
        }
    }

    fn mix_chunk(&mut self, out: &mut [f32]) {
        let frames = out.len() / 2;
        out.fill(0.0);
        if !self.playing || self.primary.is_none() {
            return;
        }
        let a = &mut self.scratch_a[..frames * 2];
        let b = &mut self.scratch_b[..frames * 2];
        a.fill(0.0);
        b.fill(0.0);
        let mut na = 0usize;
        if let Some(p) = self.primary.as_mut() {
            if !p.ended {
                na = p.src.read(a);
                if na < frames {
                    p.ended = true;
                }
            }
        }
        let mut nb = 0usize;
        if self.crossfade.is_some() {
            if let Some(s) = self.secondary.as_mut() {
                if !s.ended {
                    nb = s.src.read(b);
                    if nb < frames {
                        s.ended = true;
                    }
                }
            }
        }
        let (norm_a, norm_b) = (
            self.primary.as_ref().map(|d| d.norm).unwrap_or(1.0),
            self.secondary.as_ref().map(|d| d.norm).unwrap_or(1.0),
        );
        let (cf_total, cf_done) = self.crossfade.as_ref().map(|c| (c.total, c.done)).unwrap_or((0, 0));
        let crossing = self.crossfade.is_some();
        for i in 0..frames {
            let (ga, gb) = if crossing {
                let f = cf_done + i as u64;
                (norm_a * envelope_gain(f, cf_total, false), norm_b * envelope_gain(f, cf_total, true))
            } else {
                (norm_a, 0.0)
            };
            let m = self.master.next();
            let (al, ar) = (a[2 * i] * ga, a[2 * i + 1] * ga);
            let (bl, br) = (b[2 * i] * gb, b[2 * i + 1] * gb);
            if let Some(p) = self.primary.as_mut() {
                if i < na {
                    p.meter.push(al, ar);
                } else {
                    p.meter.push(0.0, 0.0);
                }
            }
            if crossing {
                if let Some(s) = self.secondary.as_mut() {
                    if i < nb {
                        s.meter.push(bl, br);
                    } else {
                        s.meter.push(0.0, 0.0);
                    }
                }
            }
            out[2 * i] = (al + bl) * m;
            out[2 * i + 1] = (ar + br) * m;
        }
        if let Some(p) = self.primary.as_mut() {
            p.pos += na as u64;
        }
        if crossing {
            if let Some(s) = self.secondary.as_mut() {
                s.pos += nb as u64;
            }
            if let Some(c) = self.crossfade.as_mut() {
                c.done += frames as u64;
            }
        }
        let audible = na.max(nb);
        if audible < frames {
            self.gap_frames += (frames - audible) as u64;
        }
        for fx in self.effects.iter_mut() {
            fx.process(out);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RATE: u32 = 48_000;

    /// A sine at `hz`, `amp`, `seconds` long.
    struct Tone {
        phase: f64,
        step: f64,
        amp: f32,
        left: u64,
        total: u64,
        pos: u64,
    }
    impl Tone {
        fn new(hz: f64, amp: f32, seconds: f64) -> Self {
            let total = (seconds * RATE as f64) as u64;
            Self { phase: 0.0, step: hz / RATE as f64, amp, left: total, total, pos: 0 }
        }
    }
    impl DeckSource for Tone {
        fn read(&mut self, out: &mut [f32]) -> usize {
            let n = ((out.len() / 2) as u64).min(self.left) as usize;
            for i in 0..n {
                let v = (self.phase * core::f64::consts::TAU).sin() as f32 * self.amp;
                out[2 * i] = v;
                out[2 * i + 1] = v;
                self.phase += self.step;
            }
            self.left -= n as u64;
            self.pos += n as u64;
            n
        }
        fn seek(&mut self, frame: u64) -> bool {
            if frame > self.total {
                return false;
            }
            self.phase = frame as f64 * self.step;
            self.left = self.total - frame;
            self.pos = frame;
            true
        }
        fn len_frames(&self) -> Option<u64> {
            Some(self.total)
        }
    }

    /// Every sample equals its frame index (to check seeks and that nothing is skipped or repeated).
    struct Counter {
        next: u64,
        total: u64,
    }
    impl DeckSource for Counter {
        fn read(&mut self, out: &mut [f32]) -> usize {
            let n = ((out.len() / 2) as u64).min(self.total - self.next) as usize;
            for i in 0..n {
                out[2 * i] = (self.next + i as u64) as f32;
                out[2 * i + 1] = (self.next + i as u64) as f32;
            }
            self.next += n as u64;
            n
        }
        fn seek(&mut self, frame: u64) -> bool {
            self.next = frame.min(self.total);
            true
        }
        fn len_frames(&self) -> Option<u64> {
            Some(self.total)
        }
    }

    fn render(e: &mut Engine, frames: usize) -> Vec<f32> {
        let mut v = vec![0.0; frames * 2];
        e.mix(&mut v);
        v
    }
    fn rms_db(block: &[f32]) -> f32 {
        let p: f32 = block.chunks(2).map(|f| f[0] * f[0]).sum::<f32>() / (block.len() / 2) as f32;
        10.0 * p.max(1e-12).log10()
    }

    #[test]
    fn envelope_nodes_match_the_bass_curve() {
        assert_eq!(envelope_gain(0, 1000, false), 1.0);
        assert_eq!(envelope_gain(0, 1000, true), 0.0);
        assert_eq!(envelope_gain(1000, 1000, false), 0.0);
        assert_eq!(envelope_gain(1000, 1000, true), 1.0);
        // exactly on a node: segment 32 of 64 is the 45 degree point
        let (o, i) = (envelope_gain(500, 1000, false), envelope_gain(500, 1000, true));
        assert!((o - core::f32::consts::FRAC_1_SQRT_2).abs() < 1e-4 && (i - o).abs() < 1e-4);
        // equal power at every node
        for k in 0..=64u64 {
            let f = k * 1000 / 64;
            let (o, i) = (envelope_gain(f, 1000, false), envelope_gain(f, 1000, true));
            assert!((o * o + i * i - 1.0).abs() < 0.01, "node {k}");
        }
    }

    #[test]
    fn crossfade_keeps_the_level_steady_for_uncorrelated_tones() {
        let mut e = Engine::new(RATE);
        e.set_master_volume(1.0);
        e.load(Box::new(Tone::new(440.0, 0.5, 20.0)), 1.0, false, None);
        assert!(e.preload_secondary(Box::new(Tone::new(997.0, 0.5, 20.0)), 1.0));
        assert!(e.start_crossfade(4000, 0.0));
        let out = render(&mut e, RATE as usize * 4);
        let levels: Vec<f32> = out.chunks(4800 * 2).map(rms_db).collect();
        let (mn, mx) = levels.iter().fold((f32::MAX, f32::MIN), |(a, b), &v| (a.min(v), b.max(v)));
        assert!(mx - mn < 0.5, "swing {:.2} dB", mx - mn);
        assert!(e.crossfade_finished());
        assert!(e.complete_crossfade());
        assert!(!e.crossfade_active() && !e.has_secondary());
    }

    #[test]
    fn the_preloaded_deck_consumes_nothing_until_the_crossfade_starts() {
        let mut e = Engine::new(RATE);
        e.load(Box::new(Counter { next: 0, total: 100_000 }), 1.0, false, Some(1.0));
        assert!(e.preload_secondary(Box::new(Counter { next: 0, total: 100_000 }), 1.0));
        let _ = render(&mut e, 4800);
        assert_eq!(e.secondary.as_ref().unwrap().pos, 0);
        assert_eq!(e.primary.as_ref().unwrap().pos, 4800);
    }

    #[test]
    fn a_crossfade_is_clamped_to_what_the_incoming_file_has_left() {
        let mut e = Engine::new(RATE);
        e.load(Box::new(Tone::new(440.0, 0.5, 20.0)), 1.0, false, None);
        e.preload_secondary(Box::new(Tone::new(997.0, 0.5, 1.0)), 1.0);
        assert!(e.start_crossfade(5000, 0.0)); // asked for 5 s, the file has 1 s
        assert_eq!(e.crossfade.as_ref().unwrap().total, RATE as u64);
        // and never shorter than 50 ms
        let mut e = Engine::new(RATE);
        e.load(Box::new(Tone::new(440.0, 0.5, 20.0)), 1.0, false, None);
        e.preload_secondary(Box::new(Tone::new(997.0, 0.5, 20.0)), 1.0);
        e.start_crossfade(1, 0.0);
        assert_eq!(e.crossfade.as_ref().unwrap().total, (MIN_CROSSFADE_S * RATE as f32) as u64);
    }

    #[test]
    fn no_gap_when_the_outgoing_track_ends_inside_the_fade() {
        let mut e = Engine::new(RATE);
        e.load(Box::new(Tone::new(440.0, 0.5, 3.0)), 1.0, false, Some(1.0));
        e.preload_secondary(Box::new(Tone::new(997.0, 0.5, 20.0)), 1.0);
        // seek the outgoing track near its end, so it runs out two seconds into a four second fade
        assert!(e.seek(2.0));
        assert!(e.start_crossfade(4000, 0.0));
        let _ = render(&mut e, RATE as usize * 6);
        assert_eq!(e.gap_frames, 0);
    }

    #[test]
    fn a_gap_is_counted_when_nothing_is_left_to_play() {
        let mut e = Engine::new(RATE);
        e.load(Box::new(Tone::new(440.0, 0.5, 0.1)), 1.0, false, None);
        let _ = render(&mut e, RATE as usize / 2);
        assert!(e.gap_frames > 0);
        assert!(e.source_ended());
    }

    #[test]
    fn seeking_lands_on_the_right_frame_and_nothing_is_skipped() {
        let mut e = Engine::new(RATE);
        e.load(Box::new(Counter { next: 0, total: 10 * RATE as u64 }), 1.0, false, Some(1.0));
        let first = render(&mut e, 100);
        assert_eq!(first[0], 0.0);
        assert_eq!(first[198], 99.0);
        assert!(e.seek(1.0));
        let after = render(&mut e, 4);
        assert_eq!(after[0], 48_000.0 + 0.0);
        assert_eq!(after[6], 48_003.0);
        let (pos, dur) = e.get_times();
        assert!((pos - (1.0 + 4.0 / RATE as f32)).abs() < 1e-4 && (dur - 10.0).abs() < 1e-4);
        // seeking past the end stays just inside the file
        assert!(e.seek(1000.0));
        assert!(e.get_times().0 < 10.0);
    }

    #[test]
    fn pause_outputs_silence_and_does_not_advance() {
        let mut e = Engine::new(RATE);
        e.load(Box::new(Counter { next: 0, total: 100_000 }), 1.0, false, Some(1.0));
        let _ = render(&mut e, 100);
        assert!(e.pause());
        assert!(e.is_paused() && !e.is_playing());
        let silent = render(&mut e, 100);
        assert!(silent.iter().all(|&v| v == 0.0));
        assert_eq!(e.get_times().0, 100.0 / RATE as f32);
        assert!(e.play());
        assert_eq!(render(&mut e, 1)[0], 100.0);
    }

    #[test]
    fn normalization_and_master_volume_clamp_like_bass() {
        assert_eq!(clamp_norm(0.0), 0.05);
        assert_eq!(clamp_norm(10.0), 4.0);
        assert_eq!(clamp_norm(f32::NAN), 1.0);
        assert_eq!(clamp_volume(2.0), 1.0);
        assert_eq!(clamp_volume(-1.0), 0.0);
        let mut e = Engine::new(RATE);
        e.load(Box::new(Counter { next: 1000, total: 1_000_000 }), 0.5, false, Some(0.5));
        let out = render(&mut e, 2);
        assert_eq!(out[0], 1000.0 * 0.5 * 0.5); // norm 0.5, master 0.5
    }

    #[test]
    fn a_master_slide_is_smooth_monotonic_and_lands_exactly() {
        let mut e = Engine::new(RATE);
        e.load(Box::new(Tone::new(0.0001, 1.0, 20.0)), 1.0, false, Some(1.0));
        // a flat 1.0 signal at the start of the tone is ~0, so use a DC source instead
        struct Dc(u64);
        impl DeckSource for Dc {
            fn read(&mut self, out: &mut [f32]) -> usize {
                out.fill(1.0);
                out.len() / 2
            }
            fn seek(&mut self, _f: u64) -> bool {
                true
            }
            fn len_frames(&self) -> Option<u64> {
                Some(self.0)
            }
        }
        e.load(Box::new(Dc(10_000_000)), 1.0, false, Some(1.0));
        e.slide_master_volume(0.0, 100); // 100 ms
        let out = render(&mut e, RATE as usize / 5);
        let ch: Vec<f32> = out.chunks(2).map(|f| f[0]).collect();
        let slide_frames = (0.1 * RATE as f32) as usize;
        for w in ch[..slide_frames].windows(2) {
            assert!(w[1] <= w[0] + 1e-6, "not monotonic");
        }
        assert_eq!(ch[slide_frames + 10], 0.0);
        assert!(ch[0] > 0.9, "starts near the old level");
        assert_eq!(e.master_volume(), 0.0);
    }

    #[test]
    fn effects_run_after_the_master_volume() {
        struct Double;
        impl Effect for Double {
            fn process(&mut self, b: &mut [f32]) {
                for v in b {
                    *v *= 2.0;
                }
            }
        }
        let mut e = Engine::new(RATE);
        e.load(Box::new(Counter { next: 10, total: 1000 }), 1.0, false, Some(0.5));
        e.set_effects(vec![Box::new(Double)]);
        assert_eq!(render(&mut e, 1)[0], 10.0 * 0.5 * 2.0);
    }

    #[test]
    fn the_meter_reads_the_audible_deck_like_bass() {
        let mut e = Engine::new(RATE);
        e.load(Box::new(Tone::new(1000.0, 0.25, 5.0)), 1.0, false, Some(1.0));
        let _ = render(&mut e, RATE as usize / 2);
        let level = e.meter_level();
        // RMS of a 0.25 sine is 0.1768; BASS reports rms * 4, clamped to 1.0
        assert!((level - 0.707).abs() < 0.03, "level {level}");
        let mut e = Engine::new(RATE);
        e.load(Box::new(Tone::new(1000.0, 1.0, 5.0)), 1.0, false, Some(1.0));
        let _ = render(&mut e, RATE as usize / 2);
        assert_eq!(e.meter_level(), 1.0);
    }

    #[test]
    fn mix_never_reallocates_even_for_huge_blocks() {
        let mut e = Engine::new(RATE);
        e.load(Box::new(Tone::new(440.0, 0.5, 60.0)), 1.0, false, None);
        let (pa, pb) = (e.scratch_a.as_ptr(), e.scratch_b.as_ptr());
        let _ = render(&mut e, MAX_BLOCK_FRAMES * 3 + 123);
        assert_eq!((pa, pb), (e.scratch_a.as_ptr(), e.scratch_b.as_ptr()));
    }

    #[test]
    fn cancel_and_stop_reset_everything() {
        let mut e = Engine::new(RATE);
        e.load(Box::new(Tone::new(440.0, 0.5, 20.0)), 1.0, false, None);
        e.preload_secondary(Box::new(Tone::new(997.0, 0.5, 20.0)), 1.0);
        e.start_crossfade(1000, 0.0);
        e.cancel_crossfade();
        assert!(!e.crossfade_active() && !e.has_secondary() && e.is_playing());
        e.stop();
        assert!(!e.is_playing() && e.source_ended());
        assert!(!e.play());
    }
}
