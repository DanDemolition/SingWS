//! Offline renderer: plays one or two real files through the Rust BGM engine core and writes the mix to a WAV, so a crossfade can
//! be listened to, measured, and compared with what the BASS engine does with the same files (milestone 2 of the BGM roadmap).
//!
//!   singws-bgm-render --out mix.wav [--rate 48000] [--xfade-ms 5000] [--xfade-at SECONDS] [--lead 12] [--tail 8]
//!                     [--norm-a DB] [--norm-b DB] [--volume 0.8] A.mp3 [B.mp3]
//!
//! With one file it just renders (from `--start`, default 0) for `--lead + --tail` seconds. With two it renders A until `--xfade-at`
//! (default: 8 s before A ends), crossfades into B, and keeps going for `--tail` seconds after the fade. Prints the length, peak,
//! gap frames (true silence while playing) and per-second RMS around the fade so level steadiness can be read without a DAW.

use std::io::Write;
use std::path::PathBuf;

use singws_bgm::decode::decode_file;
use singws_bgm::engine::{Effect, Engine};
use singws_bgm::eq::GraphicEq;
use singws_bgm::fx::MasterEffect;

struct Args {
    out: PathBuf,
    rate: u32,
    xfade_ms: u32,
    xfade_at: Option<f64>,
    lead: f64,
    tail: f64,
    start: f64,
    norm_a_db: f32,
    norm_b_db: f32,
    volume: f32,
    /// Play the single file through the effect chain, start to finish (no crossfade), and write the result.
    process: bool,
    eq_gains: Option<[f32; 10]>,
    /// Karaoke key (semitones) and tempo (percent); applied BEFORE the effect chain, in --process mode.
    key: f64,
    tempo: f64,
    master_params: Option<PathBuf>,
    files: Vec<PathBuf>,
}

fn parse() -> Result<Args, String> {
    let mut a = Args {
        out: PathBuf::from("bgm_mix.wav"),
        rate: 48_000,
        xfade_ms: 5000,
        xfade_at: None,
        lead: 12.0,
        tail: 8.0,
        start: 0.0,
        norm_a_db: 0.0,
        norm_b_db: 0.0,
        volume: 0.8,
        process: false,
        eq_gains: None,
        key: 0.0,
        tempo: 100.0,
        master_params: None,
        files: vec![],
    };
    let mut it = std::env::args().skip(1);
    while let Some(arg) = it.next() {
        let mut val = |name: &str| it.next().ok_or_else(|| format!("{name} needs a value"));
        match arg.as_str() {
            "--out" => a.out = PathBuf::from(val("--out")?),
            "--rate" => a.rate = val("--rate")?.parse().map_err(|e| format!("--rate: {e}"))?,
            "--xfade-ms" => a.xfade_ms = val("--xfade-ms")?.parse().map_err(|e| format!("--xfade-ms: {e}"))?,
            "--xfade-at" => a.xfade_at = Some(val("--xfade-at")?.parse().map_err(|e| format!("--xfade-at: {e}"))?),
            "--lead" => a.lead = val("--lead")?.parse().map_err(|e| format!("--lead: {e}"))?,
            "--tail" => a.tail = val("--tail")?.parse().map_err(|e| format!("--tail: {e}"))?,
            "--start" => a.start = val("--start")?.parse().map_err(|e| format!("--start: {e}"))?,
            "--norm-a" => a.norm_a_db = val("--norm-a")?.parse().map_err(|e| format!("--norm-a: {e}"))?,
            "--norm-b" => a.norm_b_db = val("--norm-b")?.parse().map_err(|e| format!("--norm-b: {e}"))?,
            "--volume" => a.volume = val("--volume")?.parse().map_err(|e| format!("--volume: {e}"))?,
            "--process" => a.process = true,
            "--key" => a.key = val("--key")?.parse().map_err(|e| format!("--key: {e}"))?,
            "--tempo" => a.tempo = val("--tempo")?.parse().map_err(|e| format!("--tempo: {e}"))?,
            "--eq" => {
                let v: Vec<f32> = val("--eq")?.split(',').map(|x| x.trim().parse().map_err(|e| format!("--eq: {e}"))).collect::<Result<_, _>>()?;
                if v.len() != 10 {
                    return Err("--eq needs ten comma-separated gains in dB".into());
                }
                let mut g = [0.0f32; 10];
                g.copy_from_slice(&v);
                a.eq_gains = Some(g);
            }
            "--master-params" => a.master_params = Some(PathBuf::from(val("--master-params")?)),
            "-h" | "--help" => return Err(String::new()),
            s if s.starts_with("--") => return Err(format!("unknown option {s}")),
            _ => a.files.push(PathBuf::from(arg)),
        }
    }
    if a.files.is_empty() || a.files.len() > 2 {
        return Err("give one or two audio files".into());
    }
    Ok(a)
}

fn db_to_gain(db: f32) -> f32 {
    10f32.powf(db / 20.0)
}

/// 32-bit float stereo WAV.
fn write_wav(path: &PathBuf, rate: u32, samples: &[f32]) -> std::io::Result<()> {
    let data_len = (samples.len() * 4) as u32;
    let mut f = std::io::BufWriter::new(std::fs::File::create(path)?);
    f.write_all(b"RIFF")?;
    f.write_all(&(36 + data_len).to_le_bytes())?;
    f.write_all(b"WAVEfmt ")?;
    f.write_all(&16u32.to_le_bytes())?;
    f.write_all(&3u16.to_le_bytes())?; // IEEE float
    f.write_all(&2u16.to_le_bytes())?;
    f.write_all(&rate.to_le_bytes())?;
    f.write_all(&(rate * 8).to_le_bytes())?;
    f.write_all(&8u16.to_le_bytes())?;
    f.write_all(&32u16.to_le_bytes())?;
    f.write_all(b"data")?;
    f.write_all(&data_len.to_le_bytes())?;
    for s in samples {
        f.write_all(&s.to_le_bytes())?;
    }
    f.flush()
}

fn main() {
    let args = match parse() {
        Ok(a) => a,
        Err(msg) => {
            if !msg.is_empty() {
                eprintln!("error: {msg}\n");
            }
            eprintln!("usage: singws-bgm-render --out mix.wav [--rate 48000] [--xfade-ms 5000] [--xfade-at S] [--lead 12] [--tail 8]\n       [--start S] [--norm-a DB] [--norm-b DB] [--volume 0.8] A.mp3 [B.mp3]");
            std::process::exit(2);
        }
    };
    let decode = |p: &PathBuf| match decode_file(p, args.rate) {
        Ok(d) => {
            eprintln!("decoded {} : {:.2} s{}", p.display(), d.seconds(), if d.skipped_packets > 0 { format!(" ({} packets skipped)", d.skipped_packets) } else { String::new() });
            d
        }
        Err(e) => {
            eprintln!("error: {}: {e}", p.display());
            std::process::exit(1);
        }
    };
    let a = decode(&args.files[0]);
    let b = args.files.get(1).map(decode);

    if args.process {
        // Whole file through [EQ, master chain] at unity: the engine path the app would use, driven offline.
        let mut engine = Engine::new(args.rate);
        engine.set_ramp_in_seconds(0.0);
        let (eq, eq_h) = GraphicEq::new(args.rate);
        let (master, master_h) = MasterEffect::new(args.rate);
        if let Some(g) = args.eq_gains {
            eq_h.set_gains_db(&g);
            eq_h.set_enabled(true);
        }
        // Parameter file: "name value" lines; a line "at N" starts a new full parameter set that takes effect at frame N.
        let mut schedule: Vec<(usize, Vec<(String, f64)>)> = vec![];
        if let Some(path) = &args.master_params {
            let text = std::fs::read_to_string(path).unwrap_or_else(|e| {
                eprintln!("error: {}: {e}", path.display());
                std::process::exit(1);
            });
            schedule.push((0, vec![]));
            for l in text.lines() {
                let mut it = l.split_whitespace();
                match (it.next(), it.next()) {
                    (Some("at"), Some(n)) => schedule.push((n.parse().unwrap_or(0), vec![])),
                    (Some(k), Some(v)) => {
                        if let Ok(v) = v.parse() {
                            schedule.last_mut().unwrap().1.push((k.to_string(), v));
                        }
                    }
                    _ => {}
                }
            }
            master_h.set_enabled(true);
        }
        let apply = |pairs: &Vec<(String, f64)>| {
            let refs: Vec<(&str, f64)> = pairs.iter().map(|(k, v)| (k.as_str(), *v)).collect();
            let unknown = master_h.set_named(&refs);
            if !unknown.is_empty() {
                eprintln!("error: unknown master parameter(s): {}", unknown.join(", "));
                std::process::exit(1);
            }
        };
        // the first section applies before any audio
        if let Some((_, pairs)) = schedule.first() {
            apply(pairs);
        }
        engine.set_effects(vec![Box::new(eq) as Box<dyn Effect>, Box::new(master) as Box<dyn Effect>]);
        // Karaoke key/tempo: stretch first (the effects then act on what is heard), a no-op at key 0 / 100%.
        let a = if args.key != 0.0 || args.tempo != 100.0 {
            let mut st = singws_stretch::Stretcher::new(args.rate).unwrap_or_else(|| {
                eprintln!("error: cannot start the stretcher");
                std::process::exit(1);
            });
            st.set_semitones(args.key);
            st.set_speed(args.tempo / 100.0);
            let mut stretched: Vec<f32> = Vec::with_capacity(a.samples.len());
            for block in a.samples.chunks(2048) {
                st.process(block, &mut stretched);
            }
            st.flush(&mut stretched);
            singws_bgm::decode::Decoded { rate: a.rate, samples: std::sync::Arc::new(stretched), skipped_packets: a.skipped_packets }
        } else {
            a
        };
        engine.load(Box::new(a.deck()), 1.0, false, Some(args.volume));
        let total = a.frames() as usize;
        let mut out: Vec<f32> = Vec::with_capacity(total * 2);
        let mut buf = vec![0.0f32; 1024 * 2];
        let mut done = 0usize;
        let mut next_section = 1usize;
        while done < total {
            let mut n = (total - done).min(1024);
            if let Some((at, _)) = schedule.get(next_section) {
                if *at <= done {
                    apply(&schedule[next_section].1);
                    next_section += 1;
                    continue;
                }
                n = n.min(*at - done);
            }
            engine.mix(&mut buf[..n * 2]);
            out.extend_from_slice(&buf[..n * 2]);
            done += n;
        }
        if let Err(e) = write_wav(&args.out, args.rate, &out) {
            eprintln!("error: cannot write {}: {e}", args.out.display());
            std::process::exit(1);
        }
        println!("processed {} frames -> {}", total, args.out.display());
        return;
    }

    let mut engine = Engine::new(args.rate);
    engine.load(Box::new(a.deck()), db_to_gain(args.norm_a_db), true, Some(args.volume));
    let xfade_at = args.xfade_at.unwrap_or((a.seconds() - 8.0).max(0.0));
    let first = if b.is_some() { (xfade_at - args.lead).max(0.0) } else { args.start };
    if first > 0.0 {
        engine.seek(first as f32);
    }
    if let Some(b) = &b {
        engine.preload_secondary(Box::new(b.deck()), db_to_gain(args.norm_b_db));
    }
    engine.play();

    let block = 1024usize;
    let mut buf = vec![0.0f32; block * 2];
    let mut out: Vec<f32> = Vec::new();
    let rate = args.rate as f64;
    let mut peak = 0.0f32;
    let mut run = |engine: &mut Engine, frames: u64, out: &mut Vec<f32>, peak: &mut f32| {
        // Exactly `frames` frames, in blocks of at most `block`.
        let mut left = frames;
        while left > 0 {
            let n = left.min(block as u64) as usize;
            engine.mix(&mut buf[..n * 2]);
            *peak = buf[..n * 2].iter().fold(*peak, |m, s| m.max(s.abs()));
            out.extend_from_slice(&buf[..n * 2]);
            left -= n as u64;
        }
    };
    if b.is_some() {
        // The fade starts on the exact frame `lead` seconds after the render began, so another engine rendering the same
        // scenario can be compared sample for sample.
        run(&mut engine, ((xfade_at - first) * rate).round() as u64, &mut out, &mut peak);
        let (tp, td) = engine.get_times();
        println!("times at fade start: pos {tp:.4} dur {td:.4}");
        println!("meter at fade start: {:.4}", engine.meter_level());
        engine.start_crossfade(args.xfade_ms, 0.0);
        let limit = ((b.as_ref().unwrap().seconds() + 5.0) * rate) as u64;
        let mut after = 0u64;
        let mut total = 0u64;
        loop {
            run(&mut engine, block as u64, &mut out, &mut peak);
            total += block as u64;
            if engine.crossfade_finished() {
                engine.complete_crossfade();
            }
            if !engine.crossfade_active() {
                after += block as u64;
                if after as f64 >= args.tail * rate {
                    break;
                }
            }
            if total > limit {
                break;
            }
        }
    } else {
        run(&mut engine, ((args.lead + args.tail) * rate) as u64, &mut out, &mut peak);
    }
    if let Err(e) = write_wav(&args.out, args.rate, &out) {
        eprintln!("error: cannot write {}: {e}", args.out.display());
        std::process::exit(1);
    }
    let frames = out.len() / 2;
    println!("wrote {} : {:.2} s at {} Hz", args.out.display(), frames as f64 / rate, args.rate);
    println!("peak {:.2} dBFS | gap frames {} ({:.1} ms of silence while playing)", 20.0 * peak.max(1e-9).log10(), engine.gap_frames, engine.gap_frames as f64 * 1000.0 / rate);
    // RMS per second so a level dip or jump at the fade is visible.
    let per = args.rate as usize;
    let mut line = String::from("rms dB per second:");
    for (i, chunk) in out.chunks(per * 2).enumerate() {
        let p: f64 = chunk.iter().map(|s| (*s as f64).powi(2)).sum::<f64>() / chunk.len().max(1) as f64;
        line += &format!(" {}:{:.1}", i, 10.0 * p.max(1e-12).log10());
    }
    println!("{line}");
}
