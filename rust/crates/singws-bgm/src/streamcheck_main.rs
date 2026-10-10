//! Real-file check of the streaming deck: for each file, read it end to end through `StreamingDeck` and compare with the whole-file
//! decode (`decode_file`), then seek to several positions and compare again.
//!   singws-bgm-streamcheck [--rate 48000] FILE...

use std::path::PathBuf;
use std::time::{Duration, Instant};

use singws_bgm::decode::decode_file;
use singws_bgm::engine::DeckSource;
use singws_bgm::stream::StreamingDeck;

fn worst(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(x, y)| (x - y).abs()).fold(0.0, f32::max)
}

fn read_exact_frames(deck: &mut StreamingDeck, frames: usize) -> Vec<f32> {
    let mut out = vec![0.0f32; frames * 2];
    let mut got = 0usize;
    let t0 = Instant::now();
    while got < frames {
        let n = (frames - got).min(1024);
        // wait until the worker has the audio, so an underrun's silence is not counted as data
        let w0 = Instant::now();
        while deck.buffered_seconds() * 48_000.0 < n as f64 && w0.elapsed() < Duration::from_secs(3) {
            std::thread::sleep(Duration::from_micros(300));
        }
        let r = deck.read(&mut out[got * 2..(got + n) * 2]);
        got += r;
        if r < n || t0.elapsed() > Duration::from_secs(60) {
            break;
        }
    }
    out.truncate(got * 2);
    out
}

fn main() {
    let mut rate = 48_000u32;
    let mut files: Vec<PathBuf> = vec![];
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        if a == "--rate" {
            rate = it.next().and_then(|v| v.parse().ok()).unwrap_or(48_000);
        } else {
            files.push(PathBuf::from(a));
        }
    }
    let mut bad = 0;
    for f in &files {
        let name = f.file_name().unwrap().to_string_lossy().to_string();
        let whole = match decode_file(f, rate) {
            Ok(w) => w,
            Err(e) => {
                println!("{name}: SKIP whole-file decode failed: {e}");
                continue;
            }
        };
        let mut deck = match StreamingDeck::open(f, rate) {
            Ok(d) => d,
            Err(e) => {
                println!("{name}: FAIL cannot open: {e}");
                bad += 1;
                continue;
            }
        };
        let total = whole.frames() as usize;
        let full = read_exact_frames(&mut deck, total + 100);
        let common = full.len().min(whole.samples.len());
        let tail_diff = (full.len() as i64 - whole.samples.len() as i64) / 2;
        let len_ok = tail_diff.abs() <= (rate as i64 / 500); // 2 ms
        let w_full = worst(&full[..common], &whole.samples[..common]);
        let mut line = format!("{name}: frames {} vs {} (tail differs by {} frames), full-read worst diff over the common part {:.2e}", full.len() / 2, total, tail_diff, w_full);
        let mut ok = len_ok && w_full < 1e-4;
        if w_full >= 1e-4 {
            let first = (0..common / 2).find(|&i| (full[2 * i] - whole.samples[2 * i]).abs() > 1e-3);
            line += &format!(" [first divergence at frame {:?}]", first);
        }
        let est = deck.len_frames();
        line += &format!(", len_frames {:?}", est);
        let mut seek_worst = 0.0f32;
        for frac in [0.0f64, 0.001, 0.1, 0.37, 0.5, 0.83, 0.97] {
            let target = (total as f64 * frac) as u64;
            if !deck.seek(target) {
                line += &format!(" seek {target} FAILED");
                ok = false;
                continue;
            }
            let want_n = 4800usize.min(total - target as usize);
            let got = read_exact_frames(&mut deck, want_n);
            let want = &whole.samples[target as usize * 2..(target as usize + want_n) * 2];
            if got.len() != want.len() {
                line += &format!(" seek {target}: got {} frames want {}", got.len() / 2, want_n);
                ok = false;
                continue;
            }
            seek_worst = seek_worst.max(worst(&got, want));
        }
        line += &format!(", seek worst diff {:.2e}, underruns {}", seek_worst, deck.underruns());
        if seek_worst > 1e-3 {
            ok = false;
        }
        println!("{} {line}", if ok { "PASS" } else { "FAIL" });
        if !ok {
            bad += 1;
        }
    }
    println!("{} file(s), {} failing", files.len(), bad);
    std::process::exit(if bad == 0 { 0 } else { 1 });
}
