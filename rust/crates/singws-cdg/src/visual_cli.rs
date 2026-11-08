//! Test driver for `visual` (`tools/cdg_visual_parity.py`): each stdin line is "MIN_TAIL HEX" (the file's bytes in hex, "-" for none); each answer is one JSON object.
use singws_cdg::visual::analyze_cdg_visual_bytes;
use std::io::BufRead;

fn main() {
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        let mut it = line.split_whitespace();
        let first = it.next().unwrap();
        if first == "video" {
            let mut n = || -> f64 { it.next().unwrap().parse().unwrap() };
            let (min, total, black, stat) = (n(), n(), n(), n());
            let list = it.next().unwrap_or("-");
            let samples: Vec<(f64, f64, f64)> = if list == "-" { vec![] } else { list.split(';').map(|s| { let p: Vec<f64> = s.split(',').map(|x| x.parse().unwrap()).collect(); (p[0], p[1], p[2]) }).collect() };
            let v = singws_cdg::visual::analyze_video_tail(&samples, total, black, stat, min);
            println!("{{\"visual_start\":{},\"visual_end\":{},\"confidence\":{:?},\"method\":\"{}\",\"safe\":{},\"reason\":\"{}\"}}", opt(v.visual_start), opt(v.visual_end), v.confidence, v.method, v.safe, v.reason);
            continue;
        }
        let min: f64 = first.parse().unwrap();
        let hex = it.next().unwrap_or("-");
        let bytes: Vec<u8> = if hex == "-" { vec![] } else { (0..hex.len() / 2).map(|i| u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).unwrap()).collect() };
        let v = analyze_cdg_visual_bytes(&bytes, min);
        println!(
            "{}",
            serde_like(&v)
        );
    }
}

fn opt(v: Option<f64>) -> String {
    match v {
        Some(x) => format!("{x:?}"),
        None => "null".to_string(),
    }
}

fn serde_like(v: &singws_cdg::visual::Visual) -> String {
    format!(
        "{{\"visual_start\":{},\"visual_end\":{},\"duration\":{:?},\"confidence\":{:?},\"method\":\"{}\",\"safe\":{},\"reason\":\"{}\",\"last_change\":{}}}",
        opt(v.visual_start), opt(v.visual_end), v.duration, v.confidence, v.method, v.safe, v.reason, opt(v.last_change)
    )
}
