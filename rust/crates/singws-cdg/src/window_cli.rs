//! Test driver for `window_rules` (`tools/window_rules_parity.py`). Each stdin line is one of
//!   "rx ry rw rh N (sx sy sw sh)*N P [px py pw ph]"  (P = 1 when there is a primary screen)  ->  "x y w h"            (`rect_on_attached_screen`)
//!   "restore X Y W H N (sx sy sw sh)*N P [px py pw ph]" with X Y W H each `a` (absent), `n` (null) or an integer  ->  "x y w h" | "none"
//!   "place S [X Y W H] FS VIS C [cx cy cw ch] P [px py pw ph] N (sx sy sw sh)*N"  ->  "geometry x y w h|- resize w h|- move x y|- show 0|1"
//!   "screenfor fx fy fw fh WIN PRIM N (sx sy sw sh)*N" (WIN, PRIM = screen index or -1)  ->  index | "none"
//!   "contains x y w h px py"  ->  0 | 1
use singws_cdg::window_rules::*;
use std::io::BufRead;

fn val(t: &str) -> Val {
    match t {
        "a" => Val::Absent,
        "n" => Val::Null,
        n => Val::Int(n.parse().unwrap()),
    }
}

fn rect_at(t: &[&str], at: usize) -> Rect {
    let n = |k: usize| t[at + k].parse::<i32>().unwrap();
    Rect::new(n(0), n(1), n(2), n(3))
}

fn opt_rect(t: &[&str], at: &mut usize) -> Option<Rect> {
    let present = t[*at] == "1";
    *at += 1;
    if present {
        let r = rect_at(t, *at);
        *at += 4;
        Some(r)
    } else {
        None
    }
}

fn screens(t: &[&str], at: &mut usize) -> Vec<Rect> {
    let n: usize = t[*at].parse().unwrap();
    *at += 1;
    let v = (0..n).map(|i| rect_at(t, *at + 4 * i)).collect();
    *at += 4 * n;
    v
}

fn main() {
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        let t: Vec<&str> = line.split_whitespace().collect();
        match t.first().copied() {
            Some("restore") => {
                let saved = SavedPos { x: val(t[1]), y: val(t[2]), width: val(t[3]), height: val(t[4]) };
                let mut at = 5;
                let scr = screens(&t, &mut at);
                let primary = opt_rect(&t, &mut at);
                match restore_rect(&saved, &scr, primary) {
                    Some(r) => println!("{} {} {} {}", r.x, r.y, r.w, r.h),
                    None => println!("none"),
                }
            }
            Some("place") => {
                let mut at = 1;
                let has_saved = t[at] == "1";
                at += 1;
                let saved = if has_saved {
                    let s = SavedPos { x: val(t[at]), y: val(t[at + 1]), width: val(t[at + 2]), height: val(t[at + 3]) };
                    at += 4;
                    Some(s)
                } else {
                    None
                };
                let fs = t[at] == "1";
                let vis = t[at + 1] == "1";
                at += 2;
                let cursor = opt_rect(&t, &mut at);
                let primary = opt_rect(&t, &mut at);
                let scr = screens(&t, &mut at);
                let p = place_video_window(saved.as_ref(), fs, vis, cursor, primary, &scr);
                let g = p.geometry.map(|r| format!("{} {} {} {}", r.x, r.y, r.w, r.h)).unwrap_or("-".into());
                let rs = p.resize.map(|(w, h)| format!("{w} {h}")).unwrap_or("-".into());
                let mv = p.move_to.map(|(x, y)| format!("{x} {y}")).unwrap_or("-".into());
                println!("geometry {g} | resize {rs} | move {mv} | show {}", p.show as i32);
            }
            Some("screenfor") => {
                let frame = rect_at(&t, 1);
                let win: i64 = t[5].parse().unwrap();
                let prim: i64 = t[6].parse().unwrap();
                let mut at = 7;
                let scr = screens(&t, &mut at);
                let idx = |v: i64| if v < 0 { None } else { Some(v as usize) };
                match screen_for_window(&scr, frame, idx(win), idx(prim)) {
                    Some(i) => println!("{i}"),
                    None => println!("none"),
                }
            }
            Some("contains") => {
                let r = rect_at(&t, 1);
                let (px, py) = (t[5].parse().unwrap(), t[6].parse().unwrap());
                println!("{}", rect_contains_point(&r, px, py) as i32);
            }
            _ => {
                let v: Vec<i32> = t.iter().map(|s| s.parse().unwrap()).collect();
                if v.len() < 6 {
                    continue;
                }
                let rect = Rect::new(v[0], v[1], v[2], v[3]);
                let n = v[4] as usize;
                let scr: Vec<Rect> = (0..n).map(|i| Rect::new(v[5 + 4 * i], v[6 + 4 * i], v[7 + 4 * i], v[8 + 4 * i])).collect();
                let at = 5 + 4 * n;
                let primary = if v[at] == 1 { Some(Rect::new(v[at + 1], v[at + 2], v[at + 3], v[at + 4])) } else { None };
                let r = rect_on_attached_screen(rect, &scr, primary);
                println!("{} {} {} {}", r.x, r.y, r.w, r.h);
            }
        }
    }
}
