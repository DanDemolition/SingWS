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
use singws_bgm::engine::Engine;

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
    let mut faded = false;
    let mut after_fade_frames = 0u64;
    let limit_frames = ((a.seconds() + b.as_ref().map(|b| b.seconds()).unwrap_or(0.0) + 5.0) * rate) as u64;
    let single_len = ((args.lead + args.tail) * rate) as u64;
    let mut peak = 0.0f32;
    loop {
        let (pos, _) = engine.get_times();
        if b.is_some() && !faded && pos as f64 >= xfade_at {
            engine.start_crossfade(args.xfade_ms, 0.0);
            faded = true;
        }
        engine.mix(&mut buf);
        peak = buf.iter().fold(peak, |m, s| m.max(s.abs()));
        out.extend_from_slice(&buf);
        if faded && engine.crossfade_finished() {
            engine.complete_crossfade();
        }
        let frames_out = (out.len() / 2) as u64;
        if faded && !engine.crossfade_active() {
            after_fade_frames += block as u64;
            if after_fade_frames as f64 >= args.tail * rate {
                break;
            }
        }
        if b.is_none() && frames_out >= single_len {
            break;
        }
        if frames_out > limit_frames || engine.source_ended() && !faded && b.is_none() {
            break;
        }
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
