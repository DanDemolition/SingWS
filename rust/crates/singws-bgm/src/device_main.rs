//! Sound-card probe for the BGM engine.
//!   singws-bgm-device list
//!   singws-bgm-device probe [--device NAME] [--seconds 20] [--rate 48000] [--buffer FRAMES] [--hammer] [--no-seek] [--audible] FILE_A [FILE_B]
//!
//! `probe` runs the real engine (streaming decks, crossfade at the half-way point when two files are given) over the real output
//! device and reports callback timing, driver latency, deck underruns and stream errors. It is SILENT unless `--audible` is given
//! (master volume 0). `--hammer` additionally drives the engine from a control thread every 2 ms (status reads, commands, and a seek
//! once a second) to show that control traffic cannot make the audio thread drop blocks.

use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

use singws_bgm::device::list_output_devices;
use singws_bgm::rt::{Command, FLAG_XFADE_ACTIVE, FLAG_XFADE_FINISHED};
use singws_bgm::system::BgmSystem;

fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() {
        eprintln!("usage: singws-bgm-device list | probe [--device NAME] [--seconds N] [--rate HZ] [--buffer FRAMES] [--hammer] [--no-seek] [--audible] A [B]");
        std::process::exit(2);
    }
    let cmd = args.remove(0);
    match cmd.as_str() {
        "list" => {
            for d in list_output_devices() {
                println!("{}{}  | {} ch, default {} Hz, 48k {} | id {}", if d.is_default { "* " } else { "  " }, d.name, d.channels, d.default_rate, if d.supports_48k { "yes" } else { "no" }, d.id);
            }
        }
        "probe" => probe(args),
        _ => {
            eprintln!("unknown command {cmd}");
            std::process::exit(2);
        }
    }
}

fn probe(args: Vec<String>) {
    let (mut device, mut seconds, mut rate, mut buffer) = (None::<String>, 20u64, 48_000u32, None::<u32>);
    let (mut hammer, mut audible, mut no_seek) = (false, false, false);
    let mut pad_clip: Option<PathBuf> = None;
    let mut files: Vec<PathBuf> = vec![];
    let mut it = args.into_iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--device" => device = it.next(),
            "--seconds" => seconds = it.next().and_then(|v| v.parse().ok()).unwrap_or(20),
            "--rate" => rate = it.next().and_then(|v| v.parse().ok()).unwrap_or(48_000),
            "--buffer" => buffer = it.next().and_then(|v| v.parse().ok()),
            "--hammer" => hammer = true,
            "--audible" => audible = true,
            "--no-seek" => no_seek = true,
            "--pads" => pad_clip = it.next().map(PathBuf::from),
            _ => files.push(PathBuf::from(a)),
        }
    }
    if files.is_empty() {
        eprintln!("give at least one audio file");
        std::process::exit(2);
    }
    let sys = match BgmSystem::start(device.as_deref(), rate, buffer) {
        Ok(s) => Arc::new(s),
        Err(e) => {
            eprintln!("error: {e}");
            std::process::exit(1);
        }
    };
    println!("device: {} | {} Hz{}{}", sys.device_name, sys.rate, if sys.fell_back_to_default { " | NAMED DEVICE NOT FOUND, default used" } else { "" }, if sys.rate_differs_from_preferred { " | rate differs from preferred, decks resampled to it" } else { "" });
    let open = |p: &PathBuf| match sys.open_deck(p) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("error: {}: {e}", p.display());
            std::process::exit(1);
        }
    };
    let volume = if audible { 0.8 } else { 0.0 };
    let first = open(&files[0]);
    let first_len = first.len_frames();
    sys.control.send(Command::Load { deck: first, norm: 1.0, paused: false, volume: Some(volume) });
    if let Some(len) = first_len {
        // start close enough to the end that the fade happens inside the run
        let start = (len as f64 / sys.rate as f64 - seconds as f64 * 0.75).max(0.0);
        sys.control.send(Command::Seek(start as f32));
    }
    if let Some(p) = files.get(1) {
        sys.control.send(Command::Preload { deck: open(p), norm: 1.0 });
    }
    let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let stop_flag = stop.clone();
    // --pads CLIP: four pad slots share one clip; every ~250 ms a random slot is hit (layering on itself), every 3 s one slot is stopped (all its
    // instances), every 7 s everything is stopped. Pad volume is 0 unless --audible, so it is silent but fully processed.
    let pads_thread = pad_clip.as_ref().map(|p| {
        let clip = match sys.load_clip(p) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("error: {}: {e}", p.display());
                std::process::exit(1);
            }
        };
        for slot in 0..4u8 {
            sys.control.send(Command::PadSetClip { slot, clip: Some(clip.clone()) });
        }
        sys.control.send(Command::PadSetMaster(if audible { 0.5 } else { 0.0 }));
        let (sys, stop) = (sys.clone(), stop_flag.clone());
        std::thread::spawn(move || {
            let mut hits = 0u64;
            let mut seed = 0x2545F491u32;
            let t0 = Instant::now();
            let mut last_stop = Instant::now();
            let mut last_all = Instant::now();
            while !stop.load(Ordering::Relaxed) {
                seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
                let slot = ((seed >> 24) % 4) as u8;
                sys.control.send(Command::PadPlay { slot, start_frame: 0 });
                hits += 1;
                if last_stop.elapsed() > Duration::from_secs(3) {
                    sys.control.send(Command::PadStop { slot: ((seed >> 20) % 4) as u8 });
                    last_stop = Instant::now();
                }
                if last_all.elapsed() > Duration::from_secs(7) {
                    sys.control.send(Command::PadStopAll);
                    last_all = Instant::now();
                }
                sys.control.collect_garbage();
                let _ = t0;
                std::thread::sleep(Duration::from_millis(250));
            }
            hits
        })
    });
    println!("running {seconds} s{}{}...", if audible { " AUDIBLE" } else { " (silent)" }, if hammer { " with control-thread hammering" } else { "" });
    let hammer_thread = hammer.then(|| {
        let (sys, stop) = (sys.clone(), stop.clone());
        std::thread::spawn(move || {
            let mut calls = 0u64;
            let mut last_seek = Instant::now();
            while !stop.load(Ordering::Relaxed) {
                let snap = &sys.control.snapshot;
                let _ = (snap.times(), snap.meter_level(), snap.master_volume());
                sys.control.send(Command::SetMasterVolume(if audible { 0.8 } else { 0.0 }));
                if !no_seek && last_seek.elapsed() > Duration::from_secs(1) && !snap.flag(FLAG_XFADE_ACTIVE) {
                    let (pos, _) = snap.times();
                    sys.control.send(Command::Seek(pos.max(0.0)));
                    last_seek = Instant::now();
                }
                sys.control.collect_garbage();
                calls += 1;
                std::thread::sleep(Duration::from_millis(2));
            }
            calls
        })
    });
    let t0 = Instant::now();
    let mut faded = false;
    while t0.elapsed() < Duration::from_secs(seconds) {
        std::thread::sleep(Duration::from_millis(250));
        if !faded && files.len() > 1 && t0.elapsed() > Duration::from_secs(seconds / 2) {
            sys.control.send(Command::StartCrossfade { ms: 2000, start_seconds: 0.0 });
            faded = true;
        }
        if sys.control.snapshot.flag(FLAG_XFADE_FINISHED) {
            sys.control.send(Command::CompleteCrossfade);
        }
        sys.control.collect_garbage();
        if sys.device_lost() {
            break;
        }
    }
    stop.store(true, Ordering::Relaxed);
    let pad_hits = pads_thread.map(|h| h.join().unwrap_or(0));
    let calls = hammer_thread.map(|h| h.join().unwrap_or(0));
    let s = sys.stats();
    let snap = &sys.control.snapshot;
    println!("callbacks {} | buffer {} frames ({:.1} ms) | callback mean {:.0} us, max {} us | over half-budget {}",
        s.callbacks.load(Ordering::Relaxed), s.buffer_frames.load(Ordering::Relaxed), s.buffer_frames.load(Ordering::Relaxed) as f64 * 1000.0 / sys.rate as f64,
        s.mean_callback_us(), s.max_callback_us.load(Ordering::Relaxed), s.over_budget.load(Ordering::Relaxed));
    println!("driver-reported output latency {:.1} ms", s.latency_us.load(Ordering::Relaxed) as f64 / 1000.0);
    let gap = snap.gap_frames.load(Ordering::Relaxed);
    let dropped = snap.commands_dropped.load(Ordering::Relaxed);
    let underruns = sys.deck_underruns.load(Ordering::Relaxed);
    println!("xruns {} | route changes {} | other errors {} | device lost {} | engine gap frames {} | seek-pending frames {} | deck underruns {} | commands dropped {}",
        s.xruns.load(Ordering::Relaxed), s.route_changes.load(Ordering::Relaxed), s.other_errors.load(Ordering::Relaxed), s.device_lost.load(Ordering::Relaxed),
        gap, snap.pending_frames.load(Ordering::Relaxed), underruns, dropped);
    if let Some(h) = pad_hits {
        let snap = &sys.control.snapshot;
        let peak_layers = (0..4).map(|i| snap.pad_playing[i].load(Ordering::Relaxed)).max().unwrap_or(0);
        println!("pad hits sent: {h} (layered on 4 slots; a stop every 3 s, stop-all every 7 s); instances still sounding at the end: {peak_layers}");
    }
    if let Some(c) = calls {
        println!("control-thread iterations during the run: {c}");
    }
    let err = s.last_error.lock().map(|l| l.clone()).unwrap_or_default();
    if !err.is_empty() {
        println!("last stream error: {err}");
    }
    let ok = s.over_budget.load(Ordering::Relaxed) == 0 && !s.device_lost.load(Ordering::Relaxed) && s.xruns.load(Ordering::Relaxed) == 0 && gap == 0 && dropped == 0 && (underruns == 0 || hammer);
    println!("RESULT: {}", if ok { "PASS" } else { "REVIEW" });
}
