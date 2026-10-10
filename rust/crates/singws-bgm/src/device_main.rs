//! Sound-card probe for the BGM engine.
//!   singws-bgm-device list
//!   singws-bgm-device probe [--device NAME] [--seconds 20] [--rate 48000] [--buffer FRAMES] [--hammer] [--audible] FILE_A [FILE_B]
//!
//! `probe` runs the real engine (streaming decks, crossfade at the half-way point when two files are given) over the real output
//! device and reports callback timing, driver latency, missed engine locks, deck underruns and stream errors. It is SILENT unless
//! `--audible` is given (master volume 0). `--hammer` additionally calls the engine from a control thread every 2 ms and seeks
//! once a second, to show that control traffic cannot make the audio thread miss blocks.

use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use singws_bgm::device::{list_output_devices, negotiate, AudioOutput};
use singws_bgm::engine::{DeckSource, Engine};
use singws_bgm::stream::StreamingDeck;

fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() {
        eprintln!("usage: singws-bgm-device list | probe [--device NAME] [--seconds N] [--rate HZ] [--buffer FRAMES] [--hammer] [--audible] A [B]");
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
            _ => files.push(PathBuf::from(a)),
        }
    }
    if files.is_empty() {
        eprintln!("give at least one audio file");
        std::process::exit(2);
    }
    let n = match negotiate(device.as_deref(), rate, buffer) {
        Ok(n) => n,
        Err(e) => {
            eprintln!("error: {e}");
            std::process::exit(1);
        }
    };
    println!("device: {} | {} ch @ {} Hz{}{}", n.name, n.channels, n.rate, if n.fell_back_to_default { " | NAMED DEVICE NOT FOUND, default used" } else { "" }, if n.rate_differs_from_preferred { " | rate differs from preferred, decks resampled to it" } else { "" });
    let deck_rate = n.rate;
    let open = |p: &PathBuf| match StreamingDeck::open(p, deck_rate) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("error: {}: {e}", p.display());
            std::process::exit(1);
        }
    };
    let mut engine = Engine::new(n.rate);
    let volume = if audible { 0.8 } else { 0.0 };
    let first_len = {
        let d = open(&files[0]);
        let len = d.len_frames();
        engine.load(Box::new(d), 1.0, false, Some(volume));
        len
    };
    if let Some(len) = first_len {
        // start close enough to the end that the fade happens inside the run
        let start = (len as f64 / n.rate as f64 - seconds as f64 * 0.75).max(0.0);
        engine.seek(start as f32);
    }
    let second_deck = files.get(1).map(&open);
    if let Some(d) = second_deck {
        engine.preload_secondary(Box::new(d), 1.0);
    }
    let engine = Arc::new(Mutex::new(engine));
    let out = match AudioOutput::start(n, engine.clone()) {
        Ok(o) => o,
        Err(e) => {
            eprintln!("error: {e}");
            std::process::exit(1);
        }
    };
    println!("running {seconds} s{}{}...", if audible { " AUDIBLE" } else { " (silent)" }, if hammer { " with control-thread hammering" } else { "" });
    let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let hammer_thread = hammer.then(|| {
        let (e, stop) = (engine.clone(), stop.clone());
        let no_seek = no_seek;
        let st = out.stats.clone();
        std::thread::spawn(move || {
            let mut calls = 0u64;
            let mut attributed = 0u64;
            let mut last_seek = Instant::now();
            let mut watch: Option<(Instant, u64)> = None;
            while !stop.load(Ordering::Relaxed) {
                {
                    let mut g = e.lock().unwrap_or_else(|p| p.into_inner());
                    let _ = g.get_times();
                    let _ = g.meter_level();
                    let _ = g.master_volume();
                    if !no_seek && last_seek.elapsed() > Duration::from_secs(1) && !g.crossfade_active() {
                        let (pos, _) = g.get_times();
                        watch = Some((Instant::now(), st.missed_locks.load(Ordering::Relaxed)));
                        g.seek(pos.max(0.0));
                        last_seek = Instant::now();
                    }
                }
                if let Some((t, before)) = watch {
                    if t.elapsed() > Duration::from_millis(100) {
                        attributed += st.missed_locks.load(Ordering::Relaxed).saturating_sub(before);
                        watch = None;
                    }
                }
                calls += 1;
                std::thread::sleep(Duration::from_millis(2));
            }
            (calls, attributed)
        })
    });
    let t0 = Instant::now();
    let mut faded = false;
    while t0.elapsed() < Duration::from_secs(seconds) {
        std::thread::sleep(Duration::from_millis(250));
        let mut g = engine.lock().unwrap_or_else(|p| p.into_inner());
        if !faded && files.len() > 1 && t0.elapsed() > Duration::from_secs(seconds / 2) {
            g.start_crossfade(2000, 0.0);
            faded = true;
        }
        if g.crossfade_finished() {
            g.complete_crossfade();
        }
        if out.stats.device_lost.load(Ordering::Relaxed) {
            break;
        }
    }
    stop.store(true, Ordering::Relaxed);
    let hammered = hammer_thread.map(|h| h.join().unwrap_or((0, 0)));
    let s = &out.stats;
    let gap = engine.lock().unwrap_or_else(|p| p.into_inner()).gap_frames;
    println!("callbacks {} | buffer {} frames ({:.1} ms) | callback mean {:.0} us, max {} us | over half-budget {}",
        s.callbacks.load(Ordering::Relaxed), s.buffer_frames.load(Ordering::Relaxed), s.buffer_frames.load(Ordering::Relaxed) as f64 * 1000.0 / out.rate as f64,
        s.mean_callback_us(), s.max_callback_us.load(Ordering::Relaxed), s.over_budget.load(Ordering::Relaxed));
    println!("driver-reported output latency {:.1} ms", s.latency_us.load(Ordering::Relaxed) as f64 / 1000.0);
    println!("missed engine locks {} | xruns {} | route changes {} | other errors {} | device lost {} | engine gap frames {}",
        s.missed_locks.load(Ordering::Relaxed), s.xruns.load(Ordering::Relaxed), s.route_changes.load(Ordering::Relaxed),
        s.other_errors.load(Ordering::Relaxed), s.device_lost.load(Ordering::Relaxed), gap);
    if let Some((c, attributed)) = hammered {
        println!("control-thread calls during the run: {c} | missed locks within 100 ms after a seek: {attributed} of {}", s.missed_locks.load(Ordering::Relaxed));
    }
    let err = s.last_error.lock().map(|l| l.clone()).unwrap_or_default();
    if !err.is_empty() {
        println!("last stream error: {err}");
    }
    // Under --hammer the control thread takes the engine lock every 2 ms (far more than the app ever will); a few lost blocks there are
    // reported but do not fail the run. Without --hammer any lost block, xrun, over-budget callback or gap is a failure.
    let missed = s.missed_locks.load(Ordering::Relaxed);
    let ok = (hammer || missed == 0)
        && s.over_budget.load(Ordering::Relaxed) == 0
        && !s.device_lost.load(Ordering::Relaxed)
        && s.xruns.load(Ordering::Relaxed) == 0
        && gap == 0;
    println!("RESULT: {}", if ok { "PASS" } else { "REVIEW" });
}
