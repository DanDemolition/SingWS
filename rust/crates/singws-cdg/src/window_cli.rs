//! Test driver for `window_rules`: each stdin line is "rx ry rw rh N (sx sy sw sh)*N P [px py pw ph]" (P = 1 when there is a primary screen); the answer line
//! is "x y w h".
use singws_cdg::window_rules::{Rect, rect_on_attached_screen};
use std::io::BufRead;

fn main() {
    for line in std::io::stdin().lock().lines() {
        let v: Vec<i32> = line.unwrap().split_whitespace().map(|t| t.parse().unwrap()).collect();
        if v.len() < 6 {
            continue;
        }
        let rect = Rect::new(v[0], v[1], v[2], v[3]);
        let n = v[4] as usize;
        let screens: Vec<Rect> = (0..n).map(|i| Rect::new(v[5 + 4 * i], v[6 + 4 * i], v[7 + 4 * i], v[8 + 4 * i])).collect();
        let at = 5 + 4 * n;
        let primary = if v[at] == 1 { Some(Rect::new(v[at + 1], v[at + 2], v[at + 3], v[at + 4])) } else { None };
        let r = rect_on_attached_screen(rect, &screens, primary);
        println!("{} {} {} {}", r.x, r.y, r.w, r.h);
    }
}
