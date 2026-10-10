//! Sound-card output for the BGM engine (cpal). Split in two steps because the engine's rate must equal the stream's:
//!   1. [`negotiate`] picks the device (by name, with fallback to the system default) and a stereo f32 config, preferring 48 kHz;
//!   2. [`AudioOutput::start`] opens the stream over an `Arc<Mutex<Engine>>`.
//!
//! The callback only `try_lock`s the engine: if a control call happens to hold it (microseconds) the block is silence and
//! `missed_locks` counts it, so the audio thread never waits. Control code must keep its critical sections short.
//! Every callback is timed; stream errors (device unplugged, route changed, xrun) are counted and flagged, never panic.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{BufferSize, Device, ErrorKind, SampleFormat, Stream, StreamConfig};

use crate::engine::{Engine, MAX_BLOCK_FRAMES};

#[derive(Debug)]
pub enum DeviceError {
    NoHost(String),
    NoDevice(String),
    NoUsableConfig(String),
    RateMismatch { engine: u32, stream: u32 },
    Stream(String),
}
impl std::fmt::Display for DeviceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DeviceError::NoHost(s) => write!(f, "no audio host: {s}"),
            DeviceError::NoDevice(s) => write!(f, "output device not found: {s}"),
            DeviceError::NoUsableConfig(s) => write!(f, "no usable output configuration: {s}"),
            DeviceError::RateMismatch { engine, stream } => write!(f, "engine rate {engine} Hz does not match the stream rate {stream} Hz"),
            DeviceError::Stream(s) => write!(f, "stream error: {s}"),
        }
    }
}
impl std::error::Error for DeviceError {}

#[derive(Clone, Debug)]
pub struct DeviceInfo {
    pub id: String,
    pub name: String,
    pub is_default: bool,
    pub default_rate: u32,
    pub channels: u16,
    pub supports_48k: bool,
}

fn device_name(d: &Device) -> String {
    d.description().map(|x| x.name().to_string()).unwrap_or_else(|_| "(unknown)".into())
}

fn stereo_f32_ranges(d: &Device) -> Vec<cpal::SupportedStreamConfigRange> {
    d.supported_output_configs()
        .map(|it| it.filter(|c| c.sample_format() == SampleFormat::F32 && c.channels() >= 2).collect())
        .unwrap_or_default()
}

pub fn list_output_devices() -> Vec<DeviceInfo> {
    let host = cpal::default_host();
    let default_id = host.default_output_device().and_then(|d| d.id().ok()).map(|i| i.to_string());
    let mut out = vec![];
    if let Ok(devs) = host.output_devices() {
        for d in devs {
            let id = d.id().map(|i| i.to_string()).unwrap_or_default();
            let cfg = d.default_output_config().ok();
            out.push(DeviceInfo {
                is_default: Some(&id) == default_id.as_ref(),
                name: device_name(&d),
                default_rate: cfg.as_ref().map(|c| c.sample_rate()).unwrap_or(0),
                channels: cfg.as_ref().map(|c| c.channels()).unwrap_or(0),
                supports_48k: stereo_f32_ranges(&d).iter().any(|r| r.min_sample_rate() <= 48_000 && r.max_sample_rate() >= 48_000),
                id,
            });
        }
    }
    out
}

/// The device and stream settings chosen for an output.
pub struct Negotiated {
    device: Device,
    config: StreamConfig,
    pub name: String,
    pub rate: u32,
    pub channels: u16,
    /// A named device was requested but not present, so the system default was used.
    pub fell_back_to_default: bool,
    /// The preferred rate was not available on the device, so its own rate is used (decks are resampled to it).
    pub rate_differs_from_preferred: bool,
}

fn find_device(name: &str) -> Option<Device> {
    let host = cpal::default_host();
    let want = name.trim().to_lowercase();
    let devs: Vec<Device> = host.output_devices().ok()?.collect();
    devs.iter()
        .find(|d| device_name(d).to_lowercase() == want)
        .or_else(|| devs.iter().find(|d| device_name(d).to_lowercase().contains(&want)))
        .cloned()
}

/// Choose the output. `name` empty/None = system default; a name that is not present falls back to the default and says so.
pub fn negotiate(name: Option<&str>, preferred_rate: u32, buffer_frames: Option<u32>) -> Result<Negotiated, DeviceError> {
    let host = cpal::default_host();
    let mut fell_back = false;
    let device = match name.map(str::trim).filter(|n| !n.is_empty()) {
        Some(n) => match find_device(n) {
            Some(d) => d,
            None => {
                fell_back = true;
                host.default_output_device().ok_or_else(|| DeviceError::NoDevice(format!("{n} (and no default output)")))?
            }
        },
        None => host.default_output_device().ok_or_else(|| DeviceError::NoDevice("no default output device".into()))?,
    };
    let dev_name = device_name(&device);
    let ranges = stereo_f32_ranges(&device);
    // prefer exactly two channels at the preferred rate, then any >= 2 channels at it, then the device default
    let mut pick: Option<cpal::SupportedStreamConfig> = None;
    let mut ordered = ranges.clone();
    ordered.sort_by_key(|r| r.channels());
    for r in &ordered {
        if let Some(c) = r.clone().try_with_sample_rate(preferred_rate) {
            pick = Some(c);
            break;
        }
    }
    let chosen = match pick {
        Some(c) => c,
        None => {
            let d = device.default_output_config().map_err(|e| DeviceError::NoUsableConfig(format!("{dev_name}: {e}")))?;
            if d.sample_format() != SampleFormat::F32 || d.channels() < 2 {
                return Err(DeviceError::NoUsableConfig(format!("{dev_name}: default config {d:?} is not stereo f32")));
            }
            d
        }
    };
    let mut config: StreamConfig = chosen.clone().into();
    if let Some(b) = buffer_frames {
        config.buffer_size = BufferSize::Fixed(b);
    }
    Ok(Negotiated {
        rate: chosen.sample_rate(),
        channels: chosen.channels(),
        rate_differs_from_preferred: chosen.sample_rate() != preferred_rate,
        device,
        config,
        name: dev_name,
        fell_back_to_default: fell_back,
    })
}

/// Counters updated by the audio callback and the error callback.
#[derive(Default)]
pub struct Stats {
    pub callbacks: AtomicU64,
    pub frames: AtomicU64,
    /// Blocks played as silence because a control call held the engine lock.
    pub missed_locks: AtomicU64,
    pub max_callback_us: AtomicU64,
    pub total_callback_us: AtomicU64,
    /// Callbacks that took more than half of their own buffer duration.
    pub over_budget: AtomicU64,
    /// Output latency the driver reported on the last callback (callback time -> predicted playback time).
    pub latency_us: AtomicU64,
    pub buffer_frames: AtomicU64,
    pub xruns: AtomicU64,
    pub route_changes: AtomicU64,
    pub other_errors: AtomicU64,
    pub device_lost: AtomicBool,
    pub last_error: Mutex<String>,
}

impl Stats {
    pub fn mean_callback_us(&self) -> f64 {
        let n = self.callbacks.load(Ordering::Relaxed).max(1);
        self.total_callback_us.load(Ordering::Relaxed) as f64 / n as f64
    }
}

pub struct AudioOutput {
    _stream: Stream,
    pub stats: Arc<Stats>,
    pub device_name: String,
    pub rate: u32,
    pub channels: u16,
}

impl AudioOutput {
    pub fn start(n: Negotiated, engine: Arc<Mutex<Engine>>) -> Result<Self, DeviceError> {
        {
            let e = engine.lock().unwrap_or_else(|p| p.into_inner());
            if e.sample_rate() != n.rate {
                return Err(DeviceError::RateMismatch { engine: e.sample_rate(), stream: n.rate });
            }
        }
        let stats = Arc::new(Stats::default());
        let (st_cb, st_err) = (stats.clone(), stats.clone());
        let ch = n.channels as usize;
        let rate = n.rate as f64;
        let mut scratch = vec![0.0f32; MAX_BLOCK_FRAMES * 2];
        let data_cb = move |data: &mut [f32], info: &cpal::OutputCallbackInfo| {
            let t0 = Instant::now();
            let frames = data.len() / ch;
            match engine.try_lock() {
                Ok(mut e) => {
                    if ch == 2 {
                        e.mix(data);
                    } else {
                        let mut done = 0usize;
                        while done < frames {
                            let k = (frames - done).min(MAX_BLOCK_FRAMES);
                            e.mix(&mut scratch[..k * 2]);
                            for i in 0..k {
                                let o = (done + i) * ch;
                                data[o] = scratch[2 * i];
                                data[o + 1] = scratch[2 * i + 1];
                                for c in 2..ch {
                                    data[o + c] = 0.0;
                                }
                            }
                            done += k;
                        }
                    }
                }
                Err(_) => {
                    data.fill(0.0);
                    st_cb.missed_locks.fetch_add(1, Ordering::Relaxed);
                }
            }
            let us = t0.elapsed().as_micros() as u64;
            st_cb.callbacks.fetch_add(1, Ordering::Relaxed);
            st_cb.frames.fetch_add(frames as u64, Ordering::Relaxed);
            st_cb.total_callback_us.fetch_add(us, Ordering::Relaxed);
            st_cb.max_callback_us.fetch_max(us, Ordering::Relaxed);
            st_cb.buffer_frames.store(frames as u64, Ordering::Relaxed);
            if us as f64 > 0.5 * frames as f64 / rate * 1e6 {
                st_cb.over_budget.fetch_add(1, Ordering::Relaxed);
            }
            let ts = info.timestamp();
            st_cb.latency_us.store(ts.playback.saturating_duration_since(ts.callback).as_micros() as u64, Ordering::Relaxed);
        };
        let err_cb = move |err: cpal::Error| {
            match err.kind() {
                ErrorKind::Xrun => {
                    st_err.xruns.fetch_add(1, Ordering::Relaxed);
                }
                ErrorKind::DeviceChanged => {
                    st_err.route_changes.fetch_add(1, Ordering::Relaxed);
                }
                ErrorKind::DeviceNotAvailable => {
                    st_err.device_lost.store(true, Ordering::Relaxed);
                }
                _ => {
                    st_err.other_errors.fetch_add(1, Ordering::Relaxed);
                }
            }
            if let Ok(mut l) = st_err.last_error.lock() {
                *l = err.to_string();
            }
        };
        let stream = n
            .device
            .build_output_stream(n.config.clone(), data_cb, err_cb, None)
            .map_err(|e| DeviceError::Stream(e.to_string()))?;
        stream.play().map_err(|e| DeviceError::Stream(e.to_string()))?;
        Ok(Self { _stream: stream, stats, device_name: n.name, rate: n.rate, channels: n.channels })
    }
}
