//! `singws-analyze`: batch front end used by the comparison harness (and later, optionally, as an isolated helper).
//!
//! Usage: singws-analyze [--jobs N] [--envelope] [--timeout SECONDS] <path>...
//!        singws-analyze --compare ~/SingWS/loudness.json [--jobs N] [--limit N]   (READ-ONLY accuracy + speed report)
//!        singws-analyze [--jobs N] ... --stdin     (one path per line)
//! Prints one JSON object per input on stdout (completion order); logs go to stderr.
use std::io::{BufRead, Write};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
use std::time::{Duration, Instant};

use rayon::prelude::*;
use serde_json::json;
use singws_analysis::{AnalysisOptions, analyze_path};

fn main() {
    let mut jobs = 1usize;
    let mut opts = AnalysisOptions::default();
    let mut paths: Vec<PathBuf> = Vec::new();
    let mut from_stdin = false;
    let mut compare: Option<PathBuf> = None;
    let mut limit = 0usize;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--jobs" => jobs = args.next().and_then(|v| v.parse().ok()).unwrap_or(1).max(1),
            "--timeout" => {
                opts.timeout = args.next().and_then(|v| v.parse::<f64>().ok()).map(Duration::from_secs_f64);
            }
            "--envelope" => opts.want_envelope = true,
            "--stdin" => from_stdin = true,
            "--compare" => compare = args.next().map(PathBuf::from),
            "--limit" => limit = args.next().and_then(|v| v.parse().ok()).unwrap_or(0),
            "--version" => {
                println!("singws-analyze engine_version={}", singws_analysis::ENGINE_VERSION);
                return;
            }
            "--help" | "-h" => {
                eprintln!("usage: singws-analyze [--jobs N] [--envelope] [--timeout S] (--stdin | PATH...)");
                return;
            }
            other => paths.push(PathBuf::from(other)),
        }
    }
    if from_stdin {
        for line in std::io::stdin().lock().lines().map_while(Result::ok) {
            if !line.trim().is_empty() {
                paths.push(PathBuf::from(line));
            }
        }
    }
    let cancel = AtomicBool::new(false);
    if let Some(cache) = compare {
        run_compare(&cache, jobs, limit, &opts, &cancel);
        return;
    }
    let pool = rayon::ThreadPoolBuilder::new().num_threads(jobs).build().expect("thread pool");
    let out = std::sync::Mutex::new(std::io::stdout());
    pool.install(|| {
        paths.par_iter().for_each(|path| {
            let t0 = Instant::now();
            // A panic inside a decoder must cost one file, never the whole batch.
            let outcome = catch_unwind(AssertUnwindSafe(|| analyze_path(path, &opts, &cancel)));
            let ms = t0.elapsed().as_secs_f64() * 1000.0;
            let value = match outcome {
                Ok(Ok(r)) => json!({
                    "path": path, "ok": true, "i": r.integrated_lufs, "peak_db": r.sample_peak_dbfs,
                    "duration": r.duration_s, "start": r.audio_start_s, "end": r.audio_end_s,
                    "rate": r.sample_rate, "channels": r.channels, "skipped_packets": r.skipped_packets,
                    "envelope": r.envelope_100ms_db, "engine_version": r.engine_version, "ms": ms,
                }),
                Ok(Err(e)) => json!({"path": path, "ok": false, "error": e.code(), "detail": e.to_string(), "ms": ms}),
                Err(_) => json!({"path": path, "ok": false, "error": "internal", "detail": "panic", "ms": ms}),
            };
            let mut lock = out.lock().unwrap();
            let _ = writeln!(lock, "{value}");
        });
    });
}

/// Read-only comparison against a SingWS `loudness.json` cache (libmpv results): accuracy and speed on this machine.
/// The cache file is only read. Nothing is written anywhere.
fn run_compare(cache: &std::path::Path, jobs: usize, limit: usize, opts: &AnalysisOptions, cancel: &AtomicBool) {
    let text = match std::fs::read_to_string(cache) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("cannot read {}: {e}", cache.display());
            std::process::exit(2);
        }
    };
    let data: serde_json::Value = serde_json::from_str(&text).unwrap_or_else(|e| {
        eprintln!("not valid JSON: {e}");
        std::process::exit(2)
    });
    let mut oracle: Vec<(PathBuf, f64, Option<f64>)> = Vec::new();
    if let Some(map) = data.as_object() {
        for (path, rec) in map {
            if rec.get("mode").and_then(|m| m.as_str()) != Some("full") {
                continue;
            }
            let Some(i) = rec.get("i").and_then(|v| v.as_f64()) else { continue };
            let p = PathBuf::from(path);
            if p.exists() {
                oracle.push((p, i, rec.get("peak_db").and_then(|v| v.as_f64())));
            }
        }
    }
    oracle.sort_by(|a, b| a.0.cmp(&b.0));
    if limit > 0 {
        oracle.truncate(limit);
    }
    if oracle.is_empty() {
        eprintln!("no usable entries (need mode=full entries whose files still exist)");
        std::process::exit(2);
    }
    println!("machine: {} ({} cores available), jobs={jobs}", std::env::consts::ARCH, std::thread::available_parallelism().map(|n| n.get()).unwrap_or(0));
    println!("entries compared: {}   (cache is read, never written)", oracle.len());

    let pool = rayon::ThreadPoolBuilder::new().num_threads(jobs).build().expect("thread pool");
    let t0 = Instant::now();
    let rows: Vec<_> = pool.install(|| {
        oracle
            .par_iter()
            .map(|(path, ref_i, ref_peak)| {
                let t = Instant::now();
                let r = catch_unwind(AssertUnwindSafe(|| analyze_path(path, opts, cancel)));
                (path, *ref_i, *ref_peak, r, t.elapsed().as_secs_f64() * 1000.0)
            })
            .collect()
    });
    let wall = t0.elapsed().as_secs_f64();

    let (mut lu_ok, mut lu_n, mut pk_ok, mut pk_n, mut legacy_n) = (0usize, 0usize, 0usize, 0usize, 0usize);
    let (mut sum_d, mut max_d, mut max_p) = (0.0f64, 0.0f64, 0.0f64);
    let mut times: Vec<f64> = Vec::new();
    let mut errors: std::collections::BTreeMap<String, usize> = Default::default();
    let mut outliers: Vec<(f64, String)> = Vec::new();
    for (path, ref_i, ref_peak, res, ms) in &rows {
        match res {
            Ok(Ok(r)) => {
                times.push(*ms);
                match r.integrated_lufs {
                    Some(v) => {
                        let d = v - ref_i;
                        lu_n += 1;
                        sum_d += d;
                        max_d = max_d.max(d.abs());
                        if d.abs() <= 0.1001 {
                            lu_ok += 1;
                        } else {
                            outliers.push((d.abs(), format!("rust {v} vs libmpv {ref_i}  {}", path.display())));
                        }
                    }
                    None => *errors.entry("no_loudness".into()).or_default() += 1,
                }
                // Older cache writers stored unrounded loudness and a placeholder peak of 0.0; do not score those.
                let legacy = *ref_peak == Some(0.0) && ((ref_i * 10.0).round() / 10.0 - ref_i).abs() > 1e-9;
                if legacy {
                    legacy_n += 1;
                } else if let Some(rp) = ref_peak {
                    let d = (r.sample_peak_dbfs - rp).abs();
                    pk_n += 1;
                    max_p = max_p.max(d);
                    if d <= 0.1001 {
                        pk_ok += 1;
                    }
                }
            }
            Ok(Err(e)) => *errors.entry(e.code().to_string()).or_default() += 1,
            Err(_) => *errors.entry("internal".into()).or_default() += 1,
        }
    }
    times.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let pct = |n: usize, d: usize| if d == 0 { 0.0 } else { 100.0 * n as f64 / d as f64 };
    println!("loudness: {lu_ok}/{lu_n} within +-0.1 LU ({:.1}%)  mean delta {:+.3}  max |delta| {:.2}", pct(lu_ok, lu_n), if lu_n > 0 { sum_d / lu_n as f64 } else { 0.0 }, max_d);
    println!("sample peak: {pk_ok}/{pk_n} within +-0.1 dB ({:.1}%)  max |delta| {:.2}   ({legacy_n} legacy cache entries with placeholder peak 0.0 not scored)", pct(pk_ok, pk_n), max_p);
    if !times.is_empty() {
        println!("speed: {:.1}s wall, {:.2} files/s;  per file median {:.0} ms, p90 {:.0} ms", wall, rows.len() as f64 / wall, times[times.len() / 2], times[(times.len() * 9 / 10).saturating_sub(1)]);
    }
    if !errors.is_empty() {
        println!("not ok: {errors:?}");
    }
    outliers.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());
    for (_, line) in outliers.iter().take(10) {
        println!("  outlier: {line}");
    }
}
