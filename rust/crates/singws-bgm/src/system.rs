//! One object that owns a running BGM output: negotiated device, real-time engine with its effect chain (EQ then master chain), the
//! command channel and the control handles. This is what the C API and the probe tool drive.

use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use crate::decode::DecodeError;
use crate::device::{negotiate, AudioOutput, DeviceError, Stats};
use crate::engine::{DeckSource, Effect, Engine};
use crate::eq::{EqHandle, GraphicEq};
use crate::fx::{MasterEffect, MasterHandle};
use crate::rt::{new_pair, RtControl, RtEngine};
use crate::stream::StreamingDeck;

pub struct BgmSystem {
    pub control: Arc<RtControl>,
    pub eq: EqHandle,
    pub master: MasterHandle,
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
        let (rt, control) = new_pair(engine);
        let output = AudioOutput::start(n, rt)?;
        Ok(Self {
            control,
            eq,
            master,
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
        let (rt, control) = new_pair(engine);
        Self {
            control,
            eq,
            master,
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

    /// Open a streaming deck for `path` at the output rate. Slow (opens the file, waits for the first audio): call it from a worker thread.
    pub fn open_deck(&self, path: &Path) -> Result<Box<dyn DeckSource>, DecodeError> {
        let deck = StreamingDeck::open(path, self.rate)?.with_underrun_sink(self.deck_underruns.clone());
        Ok(Box::new(deck))
    }
}
