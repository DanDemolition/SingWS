//! One object that owns a running BGM output: negotiated device, real-time engine with its effect chain (EQ then master chain), the
//! command channel and the control handles. This is what the C API and the probe tool drive.

use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use crate::decode::{decode_file, DecodeError};
use crate::pads::Clip;
use crate::device::{negotiate, AudioOutput, DeviceError, Stats};
use crate::engine::{DeckSource, Effect, Engine};
use crate::eq::{EqHandle, GraphicEq};
use crate::fx::{MasterEffect, MasterHandle};
use crate::rt::{new_pair, RtControl, RtEngine};
use crate::stream::StreamingDeck;

/// Longest soundboard clip (held decoded in memory: 300 s of stereo f32 at 48 kHz is about 115 MB).
pub const MAX_CLIP_SECONDS: f64 = 300.0;

pub struct BgmSystem {
    pub control: Arc<RtControl>,
    pub eq: EqHandle,
    pub master: MasterHandle,
    /// The karaoke song's own EQ and master chain (separate from the music's).
    pub karaoke_eq: EqHandle,
    pub karaoke_master: MasterHandle,
    output: Option<AudioOutput>,
    /// Headless mode (tests, tools): no sound card; the caller pulls audio with [`BgmSystem::render`].
    headless: Option<Mutex<RtEngine>>,
    headless_stats: Arc<Stats>,
    pub rate: u32,
    pub device_name: String,
    pub fell_back_to_default: bool,
    pub rate_differs_from_preferred: bool,
    /// Underruns summed over every deck this system opened.
    pub deck_underruns: Arc<AtomicU64>,
}

impl BgmSystem {
    pub fn start(device: Option<&str>, preferred_rate: u32, buffer_frames: Option<u32>) -> Result<Self, DeviceError> {
        let n = negotiate(device, preferred_rate, buffer_frames)?;
        let (fell_back, rate_differs, rate, name) = (n.fell_back_to_default, n.rate_differs_from_preferred, n.rate, n.name.clone());
        let mut engine = Engine::new(rate);
        let (eq_fx, eq) = GraphicEq::new(rate);
        let (master_fx, master) = MasterEffect::new(rate);
        engine.set_effects(vec![Box::new(eq_fx) as Box<dyn Effect>, Box::new(master_fx) as Box<dyn Effect>]);
        let kh = engine.karaoke_handles();
        let (rt, control) = new_pair(engine);
        let output = AudioOutput::start(n, rt)?;
        Ok(Self {
            control,
            eq,
            master,
            karaoke_eq: kh.eq,
            karaoke_master: kh.master,
            output: Some(output),
            headless: None,
            headless_stats: Arc::new(Stats::default()),
            rate,
            device_name: name,
            fell_back_to_default: fell_back,
            rate_differs_from_preferred: rate_differs,
            deck_underruns: Arc::new(AtomicU64::new(0)),
        })
    }

    /// The same engine and effect chain with no sound card. Audio is pulled with [`BgmSystem::render`].
    pub fn headless(rate: u32) -> Self {
        let mut engine = Engine::new(rate);
        let (eq_fx, eq) = GraphicEq::new(rate);
        let (master_fx, master) = MasterEffect::new(rate);
        engine.set_effects(vec![Box::new(eq_fx) as Box<dyn Effect>, Box::new(master_fx) as Box<dyn Effect>]);
        let kh = engine.karaoke_handles();
        let (rt, control) = new_pair(engine);
        Self {
            control,
            eq,
            master,
            karaoke_eq: kh.eq,
            karaoke_master: kh.master,
            output: None,
            headless: Some(Mutex::new(rt)),
            headless_stats: Arc::new(Stats::default()),
            rate,
            device_name: "(headless)".into(),
            fell_back_to_default: false,
            rate_differs_from_preferred: false,
            deck_underruns: Arc::new(AtomicU64::new(0)),
        }
    }

    /// Headless only: run queued commands and mix `out.len() / 2` stereo frames. False when this system has a real output.
    pub fn render(&self, out: &mut [f32]) -> bool {
        match &self.headless {
            Some(rt) => {
                rt.lock().unwrap_or_else(|p| p.into_inner()).process(out);
                true
            }
            None => false,
        }
    }

    pub fn stats(&self) -> &Arc<Stats> {
        match &self.output {
            Some(o) => &o.stats,
            None => &self.headless_stats,
        }
    }

    pub fn device_lost(&self) -> bool {
        self.stats().device_lost.load(Ordering::Relaxed)
    }

    /// Decode a soundboard clip to memory at the output rate (any format symphonia reads, AAC/M4A included). Slow: worker thread.
    /// Clips longer than [`MAX_CLIP_SECONDS`] are refused: a pad is a short effect, and the whole clip is held in memory.
    pub fn load_clip(&self, path: &Path) -> Result<Clip, DecodeError> {
        let d = decode_file(path, self.rate)?;
        if d.seconds() > MAX_CLIP_SECONDS {
            return Err(DecodeError::Unsupported(format!("clip is {:.0} s long; soundboard clips are limited to {MAX_CLIP_SECONDS:.0} s", d.seconds())));
        }
        Ok(d.samples.clone())
    }

    /// Open a streaming deck for `path` at the output rate. Slow (opens the file, waits for the first audio): call it from a worker thread.
    pub fn open_deck(&self, path: &Path) -> Result<Box<dyn DeckSource>, DecodeError> {
        let deck = StreamingDeck::open(path, self.rate)?.with_underrun_sink(self.deck_underruns.clone());
        Ok(Box::new(deck))
    }
}
