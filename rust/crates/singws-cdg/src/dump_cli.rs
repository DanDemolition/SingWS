//! `singws-cdg-dump FILE.cdg` writes one raw RGBA 300x216 frame per packet that yields a frame to
//! stdout (what FFmpeg's rawvideo output gives), for the differential test.
//! `singws-cdg-dump FILE.cdg --at SECONDS [--at ...]` writes only those frames.
use singws_cdg::{CdgDecoder, CdgPlayer, HEIGHT, PACKET_BYTES, WIDTH, BridgeFill, DisplayMode, clean_near_black, compose_bridge, compose, side_fill_color};
use std::io::Write;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let path = args.first().expect("usage: singws-cdg-dump FILE.cdg [--at SECONDS]...");
    let data = std::fs::read(path).expect("read cdg");
    // --clean THRESHOLD[:t] cleans every frame the way the app does (":t" = transparent black); --sidefill prints the side-fill colour per frame instead
    let mut clean: Option<(i32, bool)> = None;
    let mut sidefill = false;
    // --compose WxH:fit|sidefill|stretch composes every frame into a window image
    let mut bridge_fill: Option<(usize, usize, BridgeFill)> = None;
    let mut compose_to: Option<(usize, usize, DisplayMode)> = None;
    // --raw-in: FILE holds raw 300x216 RGBA frames instead of CDG packets (to feed --clean / --sidefill / --compose arbitrary pixels)
    let mut raw_in = false;
    let mut ats = Vec::new();
    let mut i = 1;
    while i < args.len() {
        if args[i] == "--at" {
            ats.push(args[i + 1].parse::<f64>().expect("seconds"));
            i += 2;
        } else if args[i] == "--clean" {
            let v = &args[i + 1];
            clean = Some((v.trim_end_matches(":t").parse().expect("threshold"), v.ends_with(":t")));
            i += 2;
        } else if args[i] == "--compose" {
            let (dim, m) = args[i + 1].split_once(':').expect("WxH:mode");
            let (w, h) = dim.split_once('x').expect("WxH");
            let mode = match m { "fit" => DisplayMode::Fit, "sidefill" => DisplayMode::SideFill, _ => DisplayMode::Stretch };
            compose_to = Some((w.parse().unwrap(), h.parse().unwrap(), mode));
            i += 2;
        } else if args[i] == "--bridge" {
            // --bridge WxH:off|colour|blur composes the way the video bridge's shader does
            let (dim, m) = args[i + 1].split_once(':').expect("WxH:fill");
            let (w, h) = dim.split_once('x').expect("WxH");
            let f = match m { "colour" => BridgeFill::Colour, "blur" => BridgeFill::Blur, _ => BridgeFill::Off };
            bridge_fill = Some((w.parse().unwrap(), h.parse().unwrap(), f));
            i += 2;
        } else if args[i] == "--raw-in" {
            raw_in = true;
            i += 1;
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
        let frame_bytes = WIDTH * HEIGHT * 4;
        let chunk = if raw_in { frame_bytes } else { PACKET_BYTES };
        for p in data.chunks_exact(chunk) {
            if raw_in || d.apply(p) {
                buf.clear();
                if raw_in {
                    buf.extend_from_slice(p);
                } else {
                    d.write_rgba(&mut buf);
                }
                if sidefill {
                    let (r, g, b) = side_fill_color(&buf, WIDTH, HEIGHT);
                    out.write_all(&[r, g, b]).unwrap();
                    continue;
                }
                if let Some((t, tr)) = clean {
                    clean_near_black(&mut buf, t, tr);
                }
                if let Some((w, h, m)) = compose_to {
                    buf = compose(&buf, w, h, m);
                }
                out.write_all(&buf).unwrap();
            }
        }
    } else {
        let mut p = CdgPlayer::new(data);
        for t in ats {
            buf.clear();
            p.render_at(t).write_rgba(&mut buf);
            if let Some((tv, tr)) = clean {
                clean_near_black(&mut buf, tv, tr);
            }
            if let Some((w, h, m)) = compose_to {
                buf = compose(&buf, w, h, m);
            }
            if let Some((w, h, f)) = bridge_fill {
                buf = compose_bridge(&buf, w, h, f);
            }
            out.write_all(&buf).unwrap();
        }
    }
}
