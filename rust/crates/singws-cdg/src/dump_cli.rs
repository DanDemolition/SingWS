//! `singws-cdg-dump FILE.cdg` writes one raw RGBA 300x216 frame per packet that yields a frame to
//! stdout (what FFmpeg's rawvideo output gives), for the differential test.
//! `singws-cdg-dump FILE.cdg --at SECONDS [--at ...]` writes only those frames.
//! `--clean THRESHOLD[:t]` cleans every frame the way the app does (":t" = transparent black); `--sidefill` prints the side-fill colour (3 bytes) per frame instead.
use singws_cdg::{CdgDecoder, CdgPlayer, HEIGHT, PACKET_BYTES, WIDTH, clean_near_black, side_fill_color};
use std::io::Write;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let path = args.first().expect("usage: singws-cdg-dump FILE.cdg [--at SECONDS]...");
    let data = std::fs::read(path).expect("read cdg");
    let mut ats = Vec::new();
    let mut clean: Option<(i32, bool)> = None;
    let mut sidefill = false;
    let mut i = 1;
    while i < args.len() {
        if args[i] == "--at" {
            ats.push(args[i + 1].parse::<f64>().expect("seconds"));
            i += 2;
        } else if args[i] == "--clean" {
            let v = &args[i + 1];
            clean = Some((v.trim_end_matches(":t").parse().expect("threshold"), v.ends_with(":t")));
            i += 2;
        } else if args[i] == "--sidefill" {
            sidefill = true;
            i += 1;
        } else {
            i += 1;
        }
    }
    let mut out = std::io::BufWriter::new(std::io::stdout().lock());
    let mut buf = Vec::new();
    if ats.is_empty() {
        let mut d = CdgDecoder::new();
        for p in data.chunks_exact(PACKET_BYTES) {
            if d.apply(p) {
                buf.clear();
                d.write_rgba(&mut buf);
                if sidefill {
                    let (r, g, b) = side_fill_color(&buf, WIDTH, HEIGHT);
                    out.write_all(&[r, g, b]).unwrap();
                    continue;
                }
                if let Some((t, tr)) = clean {
                    clean_near_black(&mut buf, t, tr);
                }
                out.write_all(&buf).unwrap();
            }
        }
    } else {
        let mut p = CdgPlayer::new(data);
        for t in ats {
            buf.clear();
            p.render_at(t).write_rgba(&mut buf);
            out.write_all(&buf).unwrap();
        }
    }
}
