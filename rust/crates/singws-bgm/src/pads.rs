//! Soundboard pads inside the Rust engine (roadmap item 1, "S1": pads off BASS).
//!
//! Rules (operator, 2026-10-11):
//! * several pads can play at the same time;
//! * hitting a pad that is still playing does NOT cut it: each hit starts another instance, layered on the first;
//! * stopping a pad stops EVERY instance of it (the UI does this on Shift-click).
//!
//! Pads go through the SAME effect chain as the music (graphic EQ, then the master chain: gate, tilt EQ, exciter, compressor, limiter), so the
//! operator's tone settings apply to them and the limiter protects against pad + music overs (operator decision 2026-10-11; the old BASS pads
//! bypassed all of that). They are mixed in AFTER the music's volume stage, so the music volume slider and the fades the app does when a
//! karaoke song starts or ends do not touch them: applause stays audible while the music fades out. A clip is decoded to memory by the control side (`decode_file`, so AAC/M4A work); the audio
//! thread only copies samples out of an `Arc`. A fixed pool of voices means nothing is allocated while playing. Anything that would free
//! memory (a clip that is replaced or finished) is handed back through the retired-items outbox instead of being dropped on the audio thread.
//!
//! Click prevention: a voice fades in over 1 ms and a stop fades out over 5 ms.

use std::sync::Arc;

/// Interleaved stereo f32 at the engine rate.
pub type Clip = Arc<Vec<f32>>;

pub const MAX_PAD_SLOTS: usize = 64;
pub const MAX_VOICES: usize = 64;
const START_RAMP_S: f32 = 0.001;
const STOP_FADE_S: f32 = 0.005;

struct Voice {
    active: bool,
    slot: usize,
    clip: Option<Clip>,
    /// Next frame to play.
    pos: usize,
    ramp_done: u32,
    /// 0 = not stopping; otherwise frames of fade-out left / total.
    fade_left: u32,
    fade_total: u32,
    age: u64,
}

impl Voice {
    fn idle() -> Self {
        Self { active: false, slot: 0, clip: None, pos: 0, ramp_done: 0, fade_left: 0, fade_total: 0, age: 0 }
    }
}

pub struct PadBank {
    slot_clip: [Option<Clip>; MAX_PAD_SLOTS],
    slot_gain: [f32; MAX_PAD_SLOTS],
    /// The soundboard volume slider (operator, 2026-10-11): scales every pad. It is NOT the music volume.
    master: f32,
    voices: [Voice; MAX_VOICES],
    start_ramp: u32,
    stop_fade: u32,
    next_age: u64,
    /// Clips released by the audio thread, waiting for the control thread to drop them (preallocated: pushing never allocates).
    garbage: Vec<Clip>,
}

impl PadBank {
    pub fn new(sample_rate: u32) -> Self {
        Self {
            slot_clip: std::array::from_fn(|_| None),
            slot_gain: [1.0; MAX_PAD_SLOTS],
            master: 1.0,
            voices: std::array::from_fn(|_| Voice::idle()),
            start_ramp: ((START_RAMP_S * sample_rate as f32) as u32).max(1),
            stop_fade: ((STOP_FADE_S * sample_rate as f32) as u32).max(1),
            next_age: 0,
            garbage: Vec::with_capacity(2 * MAX_VOICES + MAX_PAD_SLOTS),
        }
    }

    fn retire(&mut self, clip: Option<Clip>) {
        if let Some(c) = clip {
            if self.garbage.len() < self.garbage.capacity() {
                self.garbage.push(c);
            } // else: the outbox is full (nobody is collecting); dropping the Arc here only frees if this was the last reference
        }
    }

    /// A clip the audio thread no longer needs, to be dropped on a non-realtime thread.
    pub fn take_garbage(&mut self) -> Option<Clip> {
        self.garbage.pop()
    }

    /// Set (or clear) the clip a slot plays. Instances already playing keep the clip they started with.
    pub fn set_clip(&mut self, slot: usize, clip: Option<Clip>) {
        if slot >= MAX_PAD_SLOTS {
            return;
        }
        let old = std::mem::replace(&mut self.slot_clip[slot], clip);
        self.retire(old);
    }

    pub fn set_gain(&mut self, slot: usize, gain: f32) {
        if slot < MAX_PAD_SLOTS {
            self.slot_gain[slot] = if gain.is_finite() { gain.clamp(0.0, 1.0) } else { 0.0 };
        }
    }

    /// Soundboard volume (0..=1) applied on top of each pad's own volume.
    pub fn set_master(&mut self, gain: f32) {
        self.master = if gain.is_finite() { gain.clamp(0.0, 1.0) } else { 0.0 };
    }

    /// Start another instance of the slot's clip at `start_frame`. False when the slot has no clip or the start is past its end.
    /// When all voices are busy the oldest instance of the same slot is stolen, else the oldest of all.
    pub fn play(&mut self, slot: usize, start_frame: usize) -> bool {
        if slot >= MAX_PAD_SLOTS {
            return false;
        }
        let Some(clip) = self.slot_clip[slot].clone() else { return false };
        if start_frame >= clip.len() / 2 {
            return false;
        }
        let idx = match self.voices.iter().position(|v| !v.active) {
            Some(i) => i,
            None => {
                let oldest_same = self.voices.iter().enumerate().filter(|(_, v)| v.slot == slot).min_by_key(|(_, v)| v.age).map(|(i, _)| i);
                let oldest_any = self.voices.iter().enumerate().min_by_key(|(_, v)| v.age).map(|(i, _)| i).unwrap_or(0);
                oldest_same.unwrap_or(oldest_any)
            }
        };
        let old = self.voices[idx].clip.take();
        self.retire(old);
        self.next_age += 1;
        self.voices[idx] = Voice { active: true, slot, clip: Some(clip), pos: start_frame, ramp_done: 0, fade_left: 0, fade_total: 0, age: self.next_age };
        true
    }

    /// Stop every instance of the slot (5 ms fade-out each).
    pub fn stop_slot(&mut self, slot: usize) {
        let fade = self.stop_fade;
        for v in self.voices.iter_mut().filter(|v| v.active && v.slot == slot && v.fade_total == 0) {
            v.fade_total = fade;
            v.fade_left = fade;
        }
    }

    pub fn stop_all(&mut self) {
        let fade = self.stop_fade;
        for v in self.voices.iter_mut().filter(|v| v.active && v.fade_total == 0) {
            v.fade_total = fade;
            v.fade_left = fade;
        }
    }

    /// Instances of the slot that are still sounding (a stopping instance counts until its fade ends).
    pub fn playing(&self, slot: usize) -> u32 {
        self.voices.iter().filter(|v| v.active && v.slot == slot).count() as u32
    }

    /// Position (frames) of the most recently started instance of the slot, 0 when none is playing.
    pub fn position_frames(&self, slot: usize) -> u64 {
        self.voices.iter().filter(|v| v.active && v.slot == slot).max_by_key(|v| v.age).map(|v| v.pos as u64).unwrap_or(0)
    }

    pub fn any_playing(&self) -> bool {
        self.voices.iter().any(|v| v.active)
    }

    /// Add every active voice into `out` (interleaved stereo). No allocation.
    pub fn mix_into(&mut self, out: &mut [f32]) {
        if !self.any_playing() {
            return;
        }
        let frames = out.len() / 2;
        for idx in 0..MAX_VOICES {
            if !self.voices[idx].active {
                continue;
            }
            let gain = self.slot_gain[self.voices[idx].slot] * self.master;
            let start_ramp = self.start_ramp;
            let mut finished = false;
            {
                let v = &mut self.voices[idx];
                let clip = v.clip.as_ref().expect("an active voice has a clip");
                let total = clip.len() / 2;
                for i in 0..frames {
                    if v.pos >= total {
                        finished = true;
                        break;
                    }
                    let mut g = gain;
                    if v.ramp_done < start_ramp {
                        g *= (v.ramp_done as f32 + 0.5) / start_ramp as f32;
                        v.ramp_done += 1;
                    }
                    if v.fade_total > 0 {
                        if v.fade_left == 0 {
                            finished = true;
                            break;
                        }
                        g *= v.fade_left as f32 / v.fade_total as f32;
                        v.fade_left -= 1;
                    }
                    out[2 * i] += clip[2 * v.pos] * g;
                    out[2 * i + 1] += clip[2 * v.pos + 1] * g;
                    v.pos += 1;
                }
                if !finished && v.pos >= total {
                    finished = true;
                }
            }
            if finished {
                let clip = self.voices[idx].clip.take();
                self.voices[idx].active = false;
                self.retire(clip);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RATE: u32 = 48_000;

    fn clip(frames: usize, value: f32) -> Clip {
        Arc::new(vec![value; frames * 2])
    }

    fn render(b: &mut PadBank, frames: usize) -> Vec<f32> {
        let mut out = vec![0.0f32; frames * 2];
        b.mix_into(&mut out);
        out
    }

    #[test]
    fn a_hit_plays_the_clip_once_through_and_frees_its_voice() {
        let mut b = PadBank::new(RATE);
        b.set_clip(3, Some(clip(1000, 0.5)));
        assert!(b.play(3, 0));
        assert_eq!(b.playing(3), 1);
        let out = render(&mut b, 600);
        assert!((out[2 * 100] - 0.5).abs() < 1e-6, "past the 1 ms ramp the clip plays at full level");
        assert_eq!(b.position_frames(3), 600);
        let out = render(&mut b, 600);
        assert_eq!(out[2 * 399], 0.5, "the last clip frame");
        assert_eq!(out[2 * 400], 0.0, "silence after the end");
        assert_eq!(b.playing(3), 0);
        assert!(!b.any_playing());
    }

    #[test]
    fn two_different_pads_play_at_the_same_time_and_add() {
        let mut b = PadBank::new(RATE);
        b.set_clip(0, Some(clip(2000, 0.2)));
        b.set_clip(1, Some(clip(2000, 0.3)));
        b.play(0, 0);
        b.play(1, 0);
        let out = render(&mut b, 500);
        assert!((out[2 * 300] - 0.5).abs() < 1e-6);
        assert_eq!((b.playing(0), b.playing(1)), (1, 1));
    }

    #[test]
    fn hitting_a_playing_pad_again_layers_another_instance() {
        let mut b = PadBank::new(RATE);
        b.set_clip(5, Some(clip(4800, 0.25)));
        b.play(5, 0);
        render(&mut b, 2400);
        b.play(5, 0);
        assert_eq!(b.playing(5), 2, "both instances are sounding");
        let out = render(&mut b, 100);
        assert!((out[2 * 99] - 0.5).abs() < 1e-3 || out[2 * 99] > 0.45, "layered: the two instances add, got {}", out[2 * 99]);
        // the first instance ends at its own time, the second goes on
        render(&mut b, 2300);
        assert_eq!(b.playing(5), 1);
        render(&mut b, 3000);
        assert_eq!(b.playing(5), 0);
    }

    #[test]
    fn stop_slot_stops_every_instance_of_that_pad_and_nothing_else() {
        let mut b = PadBank::new(RATE);
        b.set_clip(0, Some(clip(48_000, 0.4)));
        b.set_clip(1, Some(clip(48_000, 0.1)));
        for _ in 0..3 {
            b.play(0, 0);
            render(&mut b, 100);
        }
        b.play(1, 0);
        assert_eq!(b.playing(0), 3);
        b.stop_slot(0);
        let out = render(&mut b, 600);
        assert_eq!(b.playing(0), 0, "all three instances are gone");
        assert_eq!(b.playing(1), 1, "the other pad plays on");
        assert!((out[2 * 599] - 0.1).abs() < 1e-3, "only pad 1 remains audible");
    }

    #[test]
    fn a_stop_fades_out_over_5_ms_without_a_click() {
        let mut b = PadBank::new(RATE);
        b.set_clip(0, Some(clip(48_000, 1.0)));
        b.play(0, 0);
        render(&mut b, 1000);
        b.stop_slot(0);
        let out = render(&mut b, 400);
        let first = out[0];
        let mut prev = first;
        for i in 0..240 {
            let v = out[2 * i];
            assert!(v <= prev + 1e-6, "monotonic fade at {i}");
            assert!(prev - v < 0.02, "no step bigger than {} at {i}", prev - v);
            prev = v;
        }
        assert!(first > 0.95, "starts from full level, not from silence");
        assert!(out[2 * 250] == 0.0, "silent after the fade");
    }

    #[test]
    fn a_start_offset_plays_from_that_frame() {
        let mut b = PadBank::new(RATE);
        let ramp: Vec<f32> = (0..2000).flat_map(|i| [i as f32 / 2000.0; 2]).collect();
        b.set_clip(2, Some(Arc::new(ramp)));
        assert!(b.play(2, 1000));
        let out = render(&mut b, 200);
        assert!((out[2 * 150] - (1150.0 / 2000.0)).abs() < 1e-3);
        assert!(!b.play(2, 2000), "a start at or past the end plays nothing");
    }

    #[test]
    fn volume_is_per_pad_and_live() {
        let mut b = PadBank::new(RATE);
        b.set_clip(0, Some(clip(48_000, 0.8)));
        b.play(0, 0);
        render(&mut b, 500);
        b.set_gain(0, 0.5);
        let out = render(&mut b, 100);
        assert!((out[2 * 50] - 0.4).abs() < 1e-6);
        b.set_gain(0, 7.0);
        assert!((render(&mut b, 10)[0] - 0.8).abs() < 1e-6, "clamped to 1.0 like the BASS volume attribute");
        b.set_gain(0, f32::NAN);
        assert_eq!(render(&mut b, 10)[0], 0.0);
    }

    #[test]
    fn the_soundboard_volume_scales_every_pad_on_top_of_each_pads_own_volume() {
        let mut b = PadBank::new(RATE);
        b.set_clip(0, Some(clip(48_000, 0.8)));
        b.set_clip(1, Some(clip(48_000, 0.8)));
        b.set_gain(1, 0.5);
        b.play(0, 0);
        b.play(1, 0);
        render(&mut b, 500);
        b.set_master(0.5);
        let out = render(&mut b, 50);
        assert!((out[2 * 20] - (0.8 * 0.5 + 0.8 * 0.5 * 0.5)).abs() < 1e-6, "got {}", out[2 * 20]);
        b.set_master(0.0);
        assert_eq!(render(&mut b, 10)[0], 0.0);
    }

    #[test]
    fn a_pad_without_a_clip_does_nothing() {
        let mut b = PadBank::new(RATE);
        assert!(!b.play(9, 0));
        assert!(!b.play(MAX_PAD_SLOTS + 5, 0));
        assert!(!b.any_playing());
    }

    #[test]
    fn a_full_voice_pool_steals_the_oldest_instance_of_the_same_pad_first() {
        let mut b = PadBank::new(RATE);
        b.set_clip(0, Some(clip(480_000, 0.001)));
        b.set_clip(1, Some(clip(480_000, 0.001)));
        b.play(1, 0); // the very oldest voice belongs to pad 1
        for _ in 1..MAX_VOICES {
            b.play(0, 0);
        }
        assert_eq!(b.playing(0) + b.playing(1), MAX_VOICES as u32);
        assert!(b.play(0, 0), "a hit with a full pool still plays");
        assert_eq!(b.playing(1), 1, "pad 1's only instance survives: pad 0 stole from itself");
        assert_eq!(b.playing(0), (MAX_VOICES - 1) as u32);
    }

    #[test]
    fn replacing_a_clip_leaves_playing_instances_alone_and_hands_the_old_clip_back_when_done() {
        let mut b = PadBank::new(RATE);
        let first = clip(2000, 0.5);
        b.set_clip(0, Some(first.clone()));
        b.play(0, 0);
        b.set_clip(0, Some(clip(2000, 0.1)));
        let out = render(&mut b, 300);
        assert!((out[2 * 200] - 0.5).abs() < 1e-6, "the running instance still plays the OLD clip");
        b.play(0, 0);
        let out = render(&mut b, 300);
        assert!((out[2 * 200] - 0.6).abs() < 1e-3, "a new hit plays the new clip (0.5 old + 0.1 new)");
        // finish everything; the old clip must come back through the garbage queue, never be freed on the audio thread
        render(&mut b, 3000);
        let mut returned = 0;
        while let Some(c) = b.take_garbage() {
            if Arc::ptr_eq(&c, &first) {
                returned += 1;
            }
        }
        assert!(returned >= 1);
    }

    #[test]
    fn mixing_does_not_allocate() {
        let mut b = PadBank::new(RATE);
        b.set_clip(0, Some(clip(100_000, 0.1)));
        let cap = b.garbage.capacity();
        for _ in 0..30 {
            b.play(0, 0);
            render(&mut b, 512);
        }
        b.stop_all();
        render(&mut b, 1024);
        assert_eq!(b.garbage.capacity(), cap, "the garbage list never grew");
    }
}
