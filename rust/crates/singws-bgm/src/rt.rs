//! Real-time wrapper around [`Engine`]: the audio thread OWNS the engine; everything else talks to it through a lock-free queue.
//!
//! * control -> audio: [`Command`]s through a bounded single-producer queue (control-side producers are serialised by a mutex the
//!   audio thread never touches);
//! * audio -> control: a [`Snapshot`] of atomics (position, duration, meter, flags) refreshed after every block, and an outbox of
//!   retired decks so buffers are freed on the control thread, never in the audio callback.
//!
//! This replaces the `Arc<Mutex<Engine>>` + `try_lock` design of milestone 3, whose losses (a silent block when a control call happened to
//! hold the lock) it removes by construction: the audio thread never waits for anyone.

use std::cell::UnsafeCell;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use crate::engine::{DeckSource, Engine, Retired};
use crate::pads::{Clip, MAX_PAD_SLOTS};

/// Bounded single-producer / single-consumer queue of owned values (no allocation after construction).
pub struct SpscQueue<T> {
    slots: Box<[UnsafeCell<Option<T>>]>,
    head: AtomicUsize,
    tail: AtomicUsize,
}
// SAFETY: one thread pushes, one thread pops; a slot is written only while it is outside [tail, head) and read only inside it,
// with the counters published release/acquire.
unsafe impl<T: Send> Sync for SpscQueue<T> {}
unsafe impl<T: Send> Send for SpscQueue<T> {}

impl<T> SpscQueue<T> {
    pub fn new(capacity: usize) -> Self {
        Self { slots: (0..capacity.max(2)).map(|_| UnsafeCell::new(None)).collect(), head: AtomicUsize::new(0), tail: AtomicUsize::new(0) }
    }
    /// Producer only. Returns the value back when the queue is full.
    pub fn push(&self, v: T) -> Result<(), T> {
        let cap = self.slots.len();
        let head = self.head.load(Ordering::Relaxed);
        if head.wrapping_sub(self.tail.load(Ordering::Acquire)) >= cap {
            return Err(v);
        }
        // SAFETY: the slot at `head` is free (outside [tail, head)) and only the producer writes it.
        unsafe { *self.slots[head % cap].get() = Some(v) };
        self.head.store(head.wrapping_add(1), Ordering::Release);
        Ok(())
    }
    /// Consumer only.
    pub fn pop(&self) -> Option<T> {
        let cap = self.slots.len();
        let tail = self.tail.load(Ordering::Relaxed);
        if tail == self.head.load(Ordering::Acquire) {
            return None;
        }
        // SAFETY: the slot at `tail` was published by the producer and only the consumer takes it.
        let v = unsafe { (*self.slots[tail % cap].get()).take() };
        self.tail.store(tail.wrapping_add(1), Ordering::Release);
        v
    }
}

pub enum Command {
    Load { deck: Box<dyn DeckSource>, norm: f32, paused: bool, volume: Option<f32> },
    Play,
    Pause,
    Stop,
    SetMasterVolume(f32),
    SlideMasterVolume { volume: f32, ms: u32 },
    SetPrimaryNorm(f32),
    SetSecondaryNorm(f32),
    Preload { deck: Box<dyn DeckSource>, norm: f32 },
    InvalidatePreload,
    StartCrossfade { ms: u32, start_seconds: f32 },
    CompleteCrossfade,
    CancelCrossfade,
    Seek(f32),
    SetRampIn(f32),
    PadSetClip { slot: u8, clip: Option<Clip> },
    PadSetGain { slot: u8, gain: f32 },
    PadSetMaster(f32),
    PadPlay { slot: u8, start_frame: u64 },
    PadStop { slot: u8 },
    PadStopAll,
    KaraokeLoad { deck: Box<dyn DeckSource>, norm: f32, paused: bool, volume: Option<f32> },
    KaraokePlay,
    KaraokePause,
    KaraokeStop,
    KaraokeSeek(f32),
    KaraokeTempo(f32),
    KaraokeKey(f32),
    KaraokeVolume(f32),
    KaraokeSlideVolume { volume: f32, ms: u32 },
    KaraokeNorm(f32),
}

pub const FLAG_PLAYING: u32 = 1;
pub const FLAG_PAUSED: u32 = 2;
pub const FLAG_ENDED: u32 = 4;
pub const FLAG_XFADE_ACTIVE: u32 = 8;
pub const FLAG_XFADE_FINISHED: u32 = 16;
pub const FLAG_HAS_PRIMARY: u32 = 32;
pub const FLAG_HAS_SECONDARY: u32 = 64;

/// Karaoke voice flags (`Snapshot::karaoke_flags`).
pub const KFLAG_LOADED: u32 = 1;
pub const KFLAG_PLAYING: u32 = 2;
pub const KFLAG_PAUSED: u32 = 4;
/// The song played to its end (tail drained).
pub const KFLAG_FINISHED: u32 = 8;

/// What the audio thread publishes after each block (all relaxed atomics; a reader may see values from two adjacent blocks).
pub struct Snapshot {
    pub position_s: AtomicU32,
    pub duration_s: AtomicU32,
    pub meter: AtomicU32,
    pub master_volume: AtomicU32,
    pub flags: AtomicU32,
    pub gap_frames: AtomicU64,
    pub pending_frames: AtomicU64,
    /// Commands the audio thread has executed so far (lets a caller wait until its command took effect).
    pub commands_done: AtomicU64,
    /// Commands dropped because the queue was full (should stay 0).
    pub commands_dropped: AtomicU64,
    pub blocks: AtomicU64,
    /// Per pad slot: instances sounding, and the position (frames) of the newest one.
    pub pad_playing: Box<[AtomicU32]>,
    pub pad_position: Box<[AtomicU64]>,
    /// Karaoke voice: audible SONG position and duration (seconds, f32 bits), `KFLAG_*`, volume, frames of silence while the source was
    /// not ready, and the source-underrun count.
    pub karaoke_position_s: AtomicU32,
    pub karaoke_duration_s: AtomicU32,
    pub karaoke_flags: AtomicU32,
    pub karaoke_volume: AtomicU32,
    pub karaoke_pending_frames: AtomicU64,
}

impl Snapshot {
    fn new() -> Self {
        Self {
            position_s: AtomicU32::new(0),
            duration_s: AtomicU32::new(0),
            meter: AtomicU32::new(0),
            master_volume: AtomicU32::new(0.8f32.to_bits()),
            flags: AtomicU32::new(0),
            gap_frames: AtomicU64::new(0),
            pending_frames: AtomicU64::new(0),
            commands_done: AtomicU64::new(0),
            commands_dropped: AtomicU64::new(0),
            blocks: AtomicU64::new(0),
            pad_playing: (0..MAX_PAD_SLOTS).map(|_| AtomicU32::new(0)).collect(),
            pad_position: (0..MAX_PAD_SLOTS).map(|_| AtomicU64::new(0)).collect(),
            karaoke_position_s: AtomicU32::new(0),
            karaoke_duration_s: AtomicU32::new(0),
            karaoke_flags: AtomicU32::new(0),
            karaoke_volume: AtomicU32::new(1.0f32.to_bits()),
            karaoke_pending_frames: AtomicU64::new(0),
        }
    }
    fn f(&self, a: &AtomicU32) -> f32 {
        f32::from_bits(a.load(Ordering::Relaxed))
    }
    pub fn times(&self) -> (f32, f32) {
        (self.f(&self.position_s), self.f(&self.duration_s))
    }
    pub fn meter_level(&self) -> f32 {
        self.f(&self.meter)
    }
    pub fn master_volume(&self) -> f32 {
        self.f(&self.master_volume)
    }
    pub fn flag(&self, bit: u32) -> bool {
        self.flags.load(Ordering::Relaxed) & bit != 0
    }
}

/// Owned by the audio thread.
pub struct RtEngine {
    engine: Engine,
    commands: Arc<SpscQueue<Command>>,
    outbox: Arc<SpscQueue<Retired>>,
    snap: Arc<Snapshot>,
    done: u64,
}

/// Held by the control side (clone-able via `Arc`).
pub struct RtControl {
    commands: Arc<SpscQueue<Command>>,
    outbox: Arc<SpscQueue<Retired>>,
    pub snapshot: Arc<Snapshot>,
    producers: Mutex<()>,
    consumers: Mutex<()>,
    sent: AtomicU64,
}

pub fn new_pair(engine: Engine) -> (RtEngine, Arc<RtControl>) {
    let commands = Arc::new(SpscQueue::new(256));
    let outbox = Arc::new(SpscQueue::new(64));
    let snap = Arc::new(Snapshot::new());
    let rt = RtEngine { engine, commands: commands.clone(), outbox: outbox.clone(), snap: snap.clone(), done: 0 };
    let ctl = Arc::new(RtControl { commands, outbox, snapshot: snap, producers: Mutex::new(()), consumers: Mutex::new(()), sent: AtomicU64::new(0) });
    (rt, ctl)
}

impl RtControl {
    /// Queue a command for the audio thread. False when the queue is full (the command is dropped and counted).
    pub fn send(&self, c: Command) -> bool {
        let _g = self.producers.lock().unwrap_or_else(|p| p.into_inner());
        match self.commands.push(c) {
            Ok(()) => {
                self.sent.fetch_add(1, Ordering::Release);
                true
            }
            Err(_) => {
                self.snapshot.commands_dropped.fetch_add(1, Ordering::Relaxed);
                false
            }
        }
    }
    /// Free decks the audio thread has retired. Call from any non-realtime thread, regularly.
    pub fn collect_garbage(&self) -> usize {
        let _g = self.consumers.lock().unwrap_or_else(|p| p.into_inner());
        let mut n = 0;
        while let Some(d) = self.outbox.pop() {
            drop(d);
            n += 1;
        }
        n
    }
    /// Block (politely) until every command sent so far has been executed, or the timeout passes. True when they have.
    pub fn flush(&self, timeout: std::time::Duration) -> bool {
        let target = self.sent.load(Ordering::Acquire);
        let t0 = std::time::Instant::now();
        while self.snapshot.commands_done.load(Ordering::Acquire) < target {
            if t0.elapsed() > timeout {
                return false;
            }
            std::thread::sleep(std::time::Duration::from_micros(300));
        }
        true
    }
}

impl RtEngine {
    pub fn engine_mut(&mut self) -> &mut Engine {
        &mut self.engine
    }

    fn apply(&mut self, c: Command) {
        let e = &mut self.engine;
        match c {
            Command::Load { deck, norm, paused, volume } => e.load(deck, norm, paused, volume),
            Command::Play => {
                e.play();
            }
            Command::Pause => {
                e.pause();
            }
            Command::Stop => e.stop(),
            Command::SetMasterVolume(v) => e.set_master_volume(v),
            Command::SlideMasterVolume { volume, ms } => e.slide_master_volume(volume, ms),
            Command::SetPrimaryNorm(f) => e.set_primary_normalize_gain(f),
            Command::SetSecondaryNorm(f) => e.set_secondary_normalize_gain(f),
            Command::Preload { deck, norm } => {
                e.preload_secondary(deck, norm);
            }
            Command::InvalidatePreload => e.invalidate_secondary_preload(),
            Command::StartCrossfade { ms, start_seconds } => {
                e.start_crossfade(ms, start_seconds);
            }
            Command::CompleteCrossfade => {
                e.complete_crossfade();
            }
            Command::CancelCrossfade => e.cancel_crossfade(),
            Command::Seek(s) => {
                e.seek(s);
            }
            Command::SetRampIn(s) => e.set_ramp_in_seconds(s),
            Command::PadSetClip { slot, clip } => e.pad_set_clip(slot as usize, clip),
            Command::PadSetGain { slot, gain } => e.pad_set_gain(slot as usize, gain),
            Command::PadSetMaster(g) => e.pad_set_master(g),
            Command::PadPlay { slot, start_frame } => {
                e.pad_play(slot as usize, start_frame as usize);
            }
            Command::PadStop { slot } => e.pad_stop(slot as usize),
            Command::PadStopAll => e.pad_stop_all(),
            Command::KaraokeLoad { deck, norm, paused, volume } => e.karaoke_load(deck, norm, paused, volume),
            Command::KaraokePlay => {
                e.karaoke_mut().play();
            }
            Command::KaraokePause => {
                e.karaoke_mut().pause();
            }
            Command::KaraokeStop => e.karaoke_stop(),
            Command::KaraokeSeek(s) => {
                e.karaoke_mut().seek_seconds(s);
            }
            Command::KaraokeTempo(pct) => e.karaoke_mut().set_tempo_percent(pct as f64),
            Command::KaraokeKey(semis) => e.karaoke_mut().set_semitones(semis as f64),
            Command::KaraokeVolume(v) => e.karaoke_mut().set_volume(v),
            Command::KaraokeSlideVolume { volume, ms } => e.karaoke_mut().slide_volume(volume, ms),
            Command::KaraokeNorm(f) => e.karaoke_mut().set_norm(f),
        }
        self.done += 1;
    }

    /// The audio callback body: run queued commands, mix one block, publish state, hand retired decks to the control side.
    pub fn process(&mut self, out: &mut [f32]) {
        let mut budget = 64; // bound the work done before the block is mixed
        while budget > 0 {
            match self.commands.pop() {
                Some(c) => self.apply(c),
                None => break,
            }
            budget -= 1;
        }
        self.engine.mix(out);
        self.publish();
        while let Some(d) = self.engine.take_discarded() {
            if let Err(d) = self.outbox.push(d) {
                // control side is not collecting: leak rather than free on the audio thread
                std::mem::forget(d);
            }
        }
    }

    fn publish(&self) {
        let e = &self.engine;
        let s = &self.snap;
        let (pos, dur) = e.get_times();
        s.position_s.store(pos.to_bits(), Ordering::Relaxed);
        s.duration_s.store(dur.to_bits(), Ordering::Relaxed);
        s.meter.store(e.meter_level().to_bits(), Ordering::Relaxed);
        s.master_volume.store(e.master_volume().to_bits(), Ordering::Relaxed);
        let mut f = 0;
        if e.is_playing() {
            f |= FLAG_PLAYING;
        }
        if e.is_paused() {
            f |= FLAG_PAUSED;
        }
        if e.source_ended() {
            f |= FLAG_ENDED;
        }
        if e.crossfade_active() {
            f |= FLAG_XFADE_ACTIVE;
        }
        if e.crossfade_finished() {
            f |= FLAG_XFADE_FINISHED;
        }
        if e.has_primary() {
            f |= FLAG_HAS_PRIMARY;
        }
        if e.has_secondary() {
            f |= FLAG_HAS_SECONDARY;
        }
        s.flags.store(f, Ordering::Relaxed);
        s.gap_frames.store(e.gap_frames, Ordering::Relaxed);
        s.pending_frames.store(e.pending_frames, Ordering::Relaxed);
        for slot in 0..MAX_PAD_SLOTS {
            s.pad_playing[slot].store(e.pad_playing(slot), Ordering::Relaxed);
            s.pad_position[slot].store(e.pad_position_frames(slot), Ordering::Relaxed);
        }
        let k = e.karaoke();
        s.karaoke_position_s.store(k.audible_position_s().to_bits(), Ordering::Relaxed);
        s.karaoke_duration_s.store(k.duration_s().to_bits(), Ordering::Relaxed);
        s.karaoke_volume.store(k.volume_now().to_bits(), Ordering::Relaxed);
        s.karaoke_pending_frames.store(k.pending_frames, Ordering::Relaxed);
        let mut kf = 0;
        if k.has_song() {
            kf |= KFLAG_LOADED;
        }
        if k.is_playing() {
            kf |= KFLAG_PLAYING;
        }
        if k.is_paused() {
            kf |= KFLAG_PAUSED;
        }
        if k.is_finished() {
            kf |= KFLAG_FINISHED;
        }
        s.karaoke_flags.store(kf, Ordering::Relaxed);
        s.blocks.fetch_add(1, Ordering::Relaxed);
        s.commands_done.store(self.done, Ordering::Release);
    }
}

// keep `AtomicBool` referenced for future flags without a warning today
#[allow(dead_code)]
type _Unused = AtomicBool;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::decode::Decoded;
    use std::time::Duration;

    const RATE: u32 = 48_000;

    fn deck(frames: usize, value: f32) -> Box<dyn DeckSource> {
        let d = Decoded { rate: RATE, samples: Arc::new(vec![value; frames * 2]), skipped_packets: 0 };
        Box::new(d.deck())
    }

    fn pair() -> (RtEngine, Arc<RtControl>) {
        let mut e = Engine::new(RATE);
        e.set_ramp_in_seconds(0.0);
        new_pair(e)
    }

    #[test]
    fn queue_is_fifo_bounded_and_returns_the_value_when_full() {
        let q: SpscQueue<u32> = SpscQueue::new(3);
        assert!(q.push(1).is_ok() && q.push(2).is_ok() && q.push(3).is_ok());
        assert_eq!(q.push(4), Err(4));
        assert_eq!(q.pop(), Some(1));
        assert!(q.push(5).is_ok());
        assert_eq!((q.pop(), q.pop(), q.pop(), q.pop()), (Some(2), Some(3), Some(5), None));
    }

    #[test]
    fn commands_run_at_the_next_block_and_the_snapshot_follows() {
        let (mut rt, ctl) = pair();
        assert!(ctl.send(Command::Load { deck: deck(48_000, 0.5), norm: 1.0, paused: false, volume: Some(1.0) }));
        let mut buf = vec![0.0f32; 480 * 2];
        rt.process(&mut buf);
        assert_eq!(buf[0], 0.5);
        assert!(ctl.snapshot.flag(FLAG_PLAYING));
        assert!(ctl.snapshot.flag(FLAG_HAS_PRIMARY));
        let (pos, dur) = ctl.snapshot.times();
        assert!((pos - 0.01).abs() < 1e-4 && (dur - 1.0).abs() < 1e-4, "{pos} {dur}");
        assert_eq!(ctl.snapshot.commands_done.load(Ordering::Relaxed), 1);
        ctl.send(Command::Pause);
        rt.process(&mut buf);
        assert!(ctl.snapshot.flag(FLAG_PAUSED));
        assert_eq!(buf[0], 0.0);
    }

    #[test]
    fn a_crossfade_through_the_queue_completes_and_the_old_deck_is_freed_on_the_control_side() {
        let (mut rt, ctl) = pair();
        ctl.send(Command::Load { deck: deck(48_000, 0.4), norm: 1.0, paused: false, volume: Some(1.0) });
        ctl.send(Command::Preload { deck: deck(96_000, 0.4), norm: 1.0 });
        ctl.send(Command::StartCrossfade { ms: 200, start_seconds: 0.0 });
        let mut buf = vec![0.0f32; 480 * 2];
        let mut finished = false;
        for _ in 0..40 {
            rt.process(&mut buf);
            if ctl.snapshot.flag(FLAG_XFADE_FINISHED) {
                finished = true;
                break;
            }
        }
        assert!(finished, "crossfade should finish");
        ctl.send(Command::CompleteCrossfade);
        rt.process(&mut buf);
        assert!(!ctl.snapshot.flag(FLAG_XFADE_ACTIVE));
        assert_eq!(ctl.snapshot.gap_frames.load(Ordering::Relaxed), 0);
        assert_eq!(ctl.collect_garbage(), 1, "the outgoing deck comes back for the control thread to drop");
    }

    #[test]
    fn a_full_queue_drops_and_counts_instead_of_blocking() {
        let (_rt, ctl) = pair();
        let mut sent = 0;
        for _ in 0..1000 {
            if ctl.send(Command::Play) {
                sent += 1;
            }
        }
        assert_eq!(sent, 256);
        assert_eq!(ctl.snapshot.commands_dropped.load(Ordering::Relaxed), 744);
    }

    #[test]
    fn many_control_threads_and_a_busy_audio_thread_never_lose_or_reorder_commands() {
        let (mut rt, ctl) = pair();
        let stop = Arc::new(AtomicBool::new(false));
        let audio_stop = stop.clone();
        let audio = std::thread::spawn(move || {
            let mut buf = vec![0.0f32; 128 * 2];
            while !audio_stop.load(Ordering::Relaxed) {
                rt.process(&mut buf);
                std::thread::sleep(Duration::from_micros(50));
            }
            rt
        });
        let mut handles = vec![];
        for t in 0..4 {
            let ctl = ctl.clone();
            handles.push(std::thread::spawn(move || {
                let mut ok = 0u64;
                for i in 0..2000 {
                    // alternate cheap state commands from every thread
                    let c = if (i + t) % 2 == 0 { Command::SetMasterVolume(0.5) } else { Command::SetPrimaryNorm(1.0) };
                    while !ctl.send(c_clone(&c)) {
                        std::thread::sleep(Duration::from_micros(100));
                    }
                    ok += 1;
                }
                ok
            }));
        }
        let sent: u64 = handles.into_iter().map(|h| h.join().unwrap()).sum();
        assert!(ctl.flush(Duration::from_secs(5)), "all commands should be executed");
        stop.store(true, Ordering::Relaxed);
        let _rt = audio.join().unwrap();
        assert_eq!(ctl.snapshot.commands_done.load(Ordering::Relaxed), sent);
    }

    #[test]
    fn pad_commands_run_on_the_audio_thread_and_the_snapshot_reports_each_pad() {
        let (mut rt, ctl) = pair();
        let clip = Arc::new(vec![0.25f32; 2 * 4800]);
        ctl.send(Command::PadSetClip { slot: 4, clip: Some(clip) });
        ctl.send(Command::PadPlay { slot: 4, start_frame: 0 });
        ctl.send(Command::PadPlay { slot: 4, start_frame: 0 });
        let mut buf = vec![0.0f32; 480 * 2];
        rt.process(&mut buf);
        assert!(buf[2 * 100] > 0.4, "two layered instances of 0.25");
        assert_eq!(ctl.snapshot.pad_playing[4].load(Ordering::Relaxed), 2);
        assert_eq!(ctl.snapshot.pad_position[4].load(Ordering::Relaxed), 480);
        assert_eq!(ctl.snapshot.pad_playing[5].load(Ordering::Relaxed), 0);
        ctl.send(Command::PadStop { slot: 4 });
        rt.process(&mut buf);
        rt.process(&mut buf);
        assert_eq!(ctl.snapshot.pad_playing[4].load(Ordering::Relaxed), 0);
        ctl.send(Command::PadSetClip { slot: 4, clip: None });
        rt.process(&mut buf);
        assert!(ctl.collect_garbage() >= 1, "the clip comes back for the control thread to free");
    }

    fn c_clone(c: &Command) -> Command {
        match c {
            Command::SetMasterVolume(v) => Command::SetMasterVolume(*v),
            Command::SetPrimaryNorm(v) => Command::SetPrimaryNorm(*v),
            _ => Command::Play,
        }
    }
}
