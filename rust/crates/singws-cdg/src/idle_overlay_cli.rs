//! Test driver for `idle_overlay` (`tools/idle_overlay_parity.py`). Lines on stdin:
//!   `card W H has_art has_artist title_ascent artist_ascent N level*N M peak*M`  -> `none` or the layout as integers:
//!       `card x y w h | art 0 or x y w h | text 0 or x title_baseline artist_baseline(-1 none) title_pt artist_pt width | lane 0 or x y w h | nbars (x y w h cx cy cw ch)*`
//!   `viz OP...` where OP is `L<level>`, `A` or `I`: prints levels, peaks, audio level and phase after every op (17 significant digits).
//!   `index idle bg_exists bg_playing playlist_len current_index` -> the track index or `none`.
//!   `expand W H BW BH` -> the expanded size.
use singws_cdg::idle_overlay::*;
use std::io::BufRead;

fn main() {
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        let t: Vec<&str> = line.split_whitespace().collect();
        match t.first().copied() {
            Some("card") => {
                let i = |k: usize| t[k].parse::<i32>().unwrap();
                let (w, h, has_art, has_artist, ta, aa) = (i(1), i(2), i(3) != 0, i(4) != 0, i(5), i(6));
                let n = i(7) as usize;
                let levels: Vec<f64> = (0..n).map(|k| t[8 + k].parse().unwrap()).collect();
                let m = t[8 + n].parse::<usize>().unwrap();
                let peaks: Vec<f64> = (0..m).map(|k| t[9 + n + k].parse().unwrap()).collect();
                match card_layout(w, h, has_art, has_artist, ta, aa, &levels, &peaks) {
                    None => println!("none"),
                    Some(l) => {
                        let r = |r: &singws_cdg::window_rules::Rect| format!("{} {} {} {}", r.x, r.y, r.w, r.h);
                        let mut out = format!("card {}", r(&l.card));
                        match &l.art { Some(a) => out += &format!(" | art {}", r(a)), None => out += " | art 0" }
                        match &l.text {
                            Some(x) => out += &format!(" | text {} {} {} {} {} {}", x.x, x.title_baseline, x.artist_baseline.unwrap_or(-1), x.title_point_size, x.artist_point_size, x.width),
                            None => out += " | text 0",
                        }
                        match &l.lane { Some(a) => out += &format!(" | lane {}", r(a)), None => out += " | lane 0" }
                        out += &format!(" | {}", l.bars.len());
                        for b in &l.bars { out += &format!(" {} {}", r(&b.bar), r(&b.cap)); }
                        println!("{out}");
                    }
                }
            }
            Some("viz") => {
                let mut v = Visualizer::default();
                for op in &t[1..] {
                    match &op[..1] {
                        "L" => v.set_audio_level(op[1..].parse().unwrap()),
                        "A" => v.advance(true),
                        _ => v.advance(false),
                    }
                    let f = |xs: &[f64]| xs.iter().map(|x| format!("{x:.17e}")).collect::<Vec<_>>().join(",");
                    println!("{} {} {:.17e} {:.17e}", f(&v.levels), f(&v.peaks), v.audio_level, v.phase);
                }
            }
            Some("index") => {
                let b = |k: usize| t[k] != "0";
                match card_track_index(b(1), b(2), b(3), t[4].parse().unwrap(), t[5].parse().unwrap()) {
                    Some(k) => println!("{k}"),
                    None => println!("none"),
                }
            }
            Some("expand") => {
                let i = |k: usize| t[k].parse::<i32>().unwrap();
                let (a, b) = expand_size(i(1), i(2), i(3), i(4));
                println!("{a} {b}");
            }
            _ => {}
        }
    }
}
