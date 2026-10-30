#!/usr/bin/env python3
"""Mutation test for the display half of rust/crates/singws-cdg: clean_near_black, side_fill_color, fit_size, scale_nearest, compose,
compose_bridge (lib.rs) and the window placement rule (window_rules.rs). Every mutant must be killed by

  1. the crate's own tests (`cargo test -p singws-cdg`, which include the pinned compose_bridge output and the background-video composite), or
  2. tools/cdg_display_parity.py (the Rust helpers against the app's Python functions and against Qt's own scaling/painting), or
  3. tools/window_rules_parity.py (the placement rules against the app's Python functions and Qt's QRect), or
  4. tools/idle_overlay_parity.py (the idle card layout and meter against the app's own methods).

Parity scripts need PyQt6 + numpy: set PY to the main checkout's .venv/bin/python (default below). The decoder half has its own pass:
tools/mutate_cdg.py. A mutant with a note is an EQUIVALENT mutant: nothing can tell it from the original, and the note says why.

    tools/mutate_display.py [--only 3,7,12]
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRATE = ROOT / "rust/crates/singws-cdg/src"
TARGET = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-display")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET, QT_QPA_PLATFORM="offscreen", CDG_PARITY_QUICK="1")
ENV["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + ENV["PATH"]
PY = os.environ.get("PY", "/Users/daniel/Documents/SingWS/.venv/bin/python")

L, W, I = "lib.rs", "window_rules.rs", "idle_overlay.rs"
EQ_SEARCH = "the sample positions never exceed the last pixel: (width-1)*i/12 <= width-1, so the clamp never acts"
EQ_COUNT = "every count is scaled by the same factor (2n or 2n-1), which keeps both the order and the ties of the counts"

# (file, old, new, note[, nth]): `nth` = which occurrence of `old` to replace (default 0, the first; compose_bridge comes before compose_bridge_background in lib.rs)
M = [
    # ---- clean_near_black
    (L, "threshold.clamp(0, 32) as u8", "threshold.clamp(0, 31) as u8", None),
    (L, "threshold.clamp(0, 32) as u8", "threshold.clamp(0, 33) as u8", None),
    (L, "threshold.clamp(0, 32) as u8", "threshold.clamp(1, 32) as u8", None),
    (L, "if t == 0 && !transparent_black {", "if t == 0 || !transparent_black {", None),
    (L, "if t == 0 && !transparent_black {", "if t == 0 && transparent_black {", None),
    (L, "if a > 0 && r <= t && g <= t && b <= t", "if a >= 0 && r <= t && g <= t && b <= t", None),
    (L, "if a > 0 && r <= t && g <= t && b <= t", "if a > 1 && r <= t && g <= t && b <= t", None),
    (L, "r <= t && g <= t && b <= t &&", "r <= t || g <= t && b <= t &&", None),
    (L, "r <= t && g <= t && b <= t &&", "r <= t && g <= t || b <= t &&", None),
    (L, "r <= t && g <= t && b <= t &&", "r < t && g <= t && b <= t &&", None),
    (L, "r <= t && g <= t && b <= t &&", "r <= t && g < t && b <= t &&", None),
    (L, "r <= t && g <= t && b <= t &&", "r <= t && g <= t && b < t &&", None),
    (L, "(transparent_black || r | g | b != 0)", "(transparent_black && r | g | b != 0)", None),
    (L, "(transparent_black || r | g | b != 0)", "(transparent_black || r & g & b != 0)", None),
    (L, "            if transparent_black {\n                p.fill(0);", "            if !transparent_black {\n                p.fill(0);", None),
    (L, "                p.fill(0);\n            } else {", "                p[..3].fill(0);\n            } else {", None),
    (L, "                p[..3].fill(0);\n            }", "                p.fill(0);\n            }", None),
    (L, "                p[..3].fill(0);\n            }", "                p[..2].fill(0);\n            }", None),
    # ---- side_fill_color
    (L, "let steps = 12usize;", "let steps = 11usize;", None),
    (L, "let steps = 12usize;", "let steps = 13usize;", None),
    (L, "d > 0.5 || (d == 0.5", "d >= 0.5 || (d == 0.5", None),
    (L, "(d == 0.5 && (f as i64) % 2 != 0)", "(d == 0.5 && (f as i64) % 2 == 0)", None),
    (L, "(d == 0.5 && (f as i64) % 2 != 0)", "(d == 0.5 && (f as i64) % 3 != 0)", None),
    (L, "let x = rnd((width - 1) * i).min(width - 1);", "let x = rnd(width * i).min(width - 1);", None),
    (L, "let x = rnd((width - 1) * i).min(width - 1);", "let x = rnd((width - 1) * i);", EQ_SEARCH),
    (L, "let y = rnd((height - 1) * i).min(height - 1);", "let y = rnd(height * i).min(height - 1);", None),
    (L, "let y = rnd((height - 1) * i).min(height - 1);", "let y = rnd((height - 1) * i);", EQ_SEARCH),
    (L, "for i in 0..=steps {", "for i in 0..steps {", None),
    (L, "for (r, g, b, a) in [px(x, 0), px(x, height - 1), px(0, y), px(width - 1, y)] {", "for (r, g, b, a) in [px(x, 1), px(x, height - 1), px(0, y), px(width - 1, y)] {", None),
    (L, "for (r, g, b, a) in [px(x, 0), px(x, height - 1), px(0, y), px(width - 1, y)] {", "for (r, g, b, a) in [px(x, 0), px(x, height - 2), px(0, y), px(width - 1, y)] {", None),
    (L, "for (r, g, b, a) in [px(x, 0), px(x, height - 1), px(0, y), px(width - 1, y)] {", "for (r, g, b, a) in [px(x, 0), px(x, height - 1), px(1, y), px(width - 1, y)] {", None),
    (L, "for (r, g, b, a) in [px(x, 0), px(x, height - 1), px(0, y), px(width - 1, y)] {", "for (r, g, b, a) in [px(x, 0), px(x, height - 1), px(0, y), px(width - 2, y)] {", None),
    (L, "for (r, g, b, a) in [px(x, 0), px(x, height - 1), px(0, y), px(width - 1, y)] {", "for (r, g, b, a) in [px(x, 0), px(x, height - 1), px(0, y)] {", None),
    (L, "            if a == 0 {\n                continue;", "            if a == 255 {\n                continue;", None),
    (L, "            if a == 0 {\n                continue;", "            if a < 255 {\n                continue;", None),
    (L, "Some(e) => e.1 += 1,", "Some(e) => e.1 += 2,", EQ_COUNT),
    (L, "Some(e) => e.1 += 1,", "Some(e) => e.1 += 0,", None),
    (L, "None => counts.push(((r, g, b), 1)),", "None => counts.push(((r, g, b), 2)),", EQ_COUNT),
    (L, "best.is_none_or(|b| e.1 > b.1)", "best.is_none_or(|b| e.1 >= b.1)", None),
    (L, "best.is_none_or(|b| e.1 > b.1)", "best.is_none_or(|b| e.1 < b.1)", None),
    (L, "best.map_or((0, 0, 0), |b| b.0)", "best.map_or((1, 0, 0), |b| b.0)", None),
    (L, "*k == (r, g, b))", "k.0 == r)", None),
    # ---- fit_size
    (L, "let rw = h as u64 * iw as u64 / ih as u64;", "let rw = h as u64 * ih as u64 / iw as u64;", None),
    (L, "let rw = h as u64 * iw as u64 / ih as u64;", "let rw = (h as u64 * iw as u64 + ih as u64 / 2) / ih as u64;", None),
    (L, "if rw <= w as u64 {", "if rw < w as u64 {", None),
    (L, "{ (rw as usize, h) }", "{ (rw as usize, h - 1) }", None),
    (L, "else { (w, (w as u64 * ih as u64 / iw as u64) as usize) }", "else { (w, (w as u64 * iw as u64 / ih as u64) as usize) }", None),
    (L, "else { (w, (w as u64 * ih as u64 / iw as u64) as usize) }", "else { (w, ((w as u64 * ih as u64 + iw as u64 / 2) / iw as u64) as usize) }", None),
    # ---- scale_nearest
    (L, "(((step / 2) + i * step).saturating_sub(1) >> 16).min(n - 1)", "(((step / 2) + i * step) >> 16).min(n - 1)", None),
    (L, "(((step / 2) + i * step).saturating_sub(1) >> 16).min(n - 1)", "(((step / 2) + i * step).saturating_sub(2) >> 16).min(n - 1)", None),
    (L, "let step = (65536 * n) / d;", "let step = (65535 * n) / d;", None),
    (L, "let step = (65536 * n) / d;", "let step = (65536 * n + d / 2) / d;", None),
    (L, "(((step / 2) + i * step).saturating_sub(1) >> 16).min(n - 1)", "(((step / 2) + i * step).saturating_sub(1) >> 15).min(n - 1)", None),
    (L, "(((step / 2) + i * step).saturating_sub(1) >> 16).min(n - 1)", "((step + i * step).saturating_sub(1) >> 16).min(n - 1)", None),
    (L, "(((step / 2) + i * step).saturating_sub(1) >> 16).min(n - 1)", "((i * step).saturating_sub(1) >> 16).min(n - 1)", None),
    (L, "(((step / 2) + i * step).saturating_sub(1) >> 16).min(n - 1)", "(((step / 2) + i * step).saturating_sub(1) >> 16)", "the index never exceeds n-1: step <= 65536*n/d, so (d - 1/2)*step < 65536*n"),
    (L, "let (xs, ys) = (axis(iw, w), axis(ih, h));", "let (xs, ys) = (axis(iw, h), axis(ih, w));", None),
    (L, "let o = (sy * iw + sx) * 4;", "let o = (sx * ih + sy) * 4;", None),
    (L, "out.extend_from_slice(&src[o..o + 4]);", "out.extend_from_slice(&src[o..o + 3]); out.push(255);", None),
    # ---- compose
    (L, "if mode == DisplayMode::Stretch { (win_w, win_h) }", "if mode != DisplayMode::Stretch { (win_w, win_h) }", None),
    (L, "let bg = if mode == DisplayMode::SideFill {", "let bg = if mode != DisplayMode::SideFill {", None),
    (L, "side_fill_color(frame, iw, ih) } else { (0, 0, 0) };", "side_fill_color(frame, iw, ih) } else { (1, 0, 0) };", None),
    (L, "out.extend_from_slice(&[bg.0, bg.1, bg.2, 255]);", "out.extend_from_slice(&[bg.0, bg.1, bg.2, 254]);", None),
    (L, "out.extend_from_slice(&[bg.0, bg.1, bg.2, 255]);", "out.extend_from_slice(&[bg.1, bg.0, bg.2, 255]);", None),
    (L, "let (x0, y0) = ((win_w - sw) / 2, (win_h - sh) / 2);", "let (x0, y0) = ((win_w - sw + 1) / 2, (win_h - sh) / 2);", None),
    (L, "let (x0, y0) = ((win_w - sw) / 2, (win_h - sh) / 2);", "let (x0, y0) = ((win_w - sw) / 2, (win_h - sh + 1) / 2);", None),
    (L, "let (x0, y0) = ((win_w - sw) / 2, (win_h - sh) / 2);", "let (x0, y0) = ((win_w - sw) / 2, (win_h - sh) / 2 + 1);", None),
    (L, "let scaled = scale_nearest(frame, iw, ih, sw, sh);", "let scaled = scale_nearest(frame, iw, ih, sh, sw);", None),
    (L, "let a = s[3] as u32;\n            if a == 255 {", "let a = s[3] as u32;\n            if a >= 254 {", None),
    (L, "let a = s[3] as u32;\n            if a == 255 {", "let a = s[3] as u32;\n            if a == 255 && false {", "at alpha 255 the blend (s*255 + bg*0 + 127)/255 is s: the fast path and the formula agree"),
    (L, "} else if a > 0 {\n                for c in 0..3 {", "} else if a > 1 {\n                for c in 0..3 {", None),
    (L, "} else if a > 0 {\n                for c in 0..3 {", "} else if a >= 0 {\n                for c in 0..3 {", "alpha 0 blends to (bg*255+127)/255 = bg: the blend is the identity there"),
    (L, "} else if a > 0 {\n                for c in 0..3 {", "} else if a > 0 {\n                for c in 0..2 {", None),
    (L, "out[d + c] = ((s[c] as u32 * a + out[d + c] as u32 * (255 - a) + 127) / 255) as u8;", "out[d + c] = ((s[c] as u32 * a + out[d + c] as u32 * (255 - a) + 128) / 255) as u8;", None),
    (L, "out[d + c] = ((s[c] as u32 * a + out[d + c] as u32 * (255 - a) + 127) / 255) as u8;", "out[d + c] = ((s[c] as u32 * a + out[d + c] as u32 * (255 - a)) / 255) as u8;", None),
    (L, "out[d + c] = ((s[c] as u32 * a + out[d + c] as u32 * (255 - a) + 127) / 255) as u8;", "out[d + c] = ((s[c] as u32 * a + out[d + c] as u32 * (256 - a) + 127) / 255) as u8;", None),
    (L, "out[d + c] = ((s[c] as u32 * a + out[d + c] as u32 * (255 - a) + 127) / 255) as u8;", "out[d + c] = ((s[c] as u32 * a + out[d + c] as u32 * a + 127) / 255) as u8;", None),
    # ---- compose_bridge (pinned by compose_bridge_is_pinned and compose_bridge_properties)
    (L, "let sa = WIDTH as f32 / HEIGHT as f32;", "let sa = HEIGHT as f32 / WIDTH as f32;", None),
    (L, "let va = w / h;", "let va = h / w;", None),
    (L, "let (buvx, buvy, buox, buoy) = if va > sa {", "let (buvx, buvy, buox, buoy) = if va < sa {", None),
    (L, "if va > sa { (1.0, sa / va, 0.0, (1.0 - sa / va) * 0.5)", "if va > sa { (1.0, sa / va, 0.0, (1.0 - sa / va) * 0.4)", None),
    (L, "if va > sa { (1.0, sa / va, 0.0, (1.0 - sa / va) * 0.5)", "if va > sa { (1.0, va / sa, 0.0, (1.0 - sa / va) * 0.5)", None),
    (L, "else { (va / sa, 1.0, (1.0 - va / sa) * 0.5, 0.0) };", "else { (va / sa, 1.0, (1.0 - va / sa) * 0.4, 0.0) };", None),
    (L, "else { (va / sa, 1.0, (1.0 - va / sa) * 0.5, 0.0) };", "else { (sa / va, 1.0, (1.0 - va / sa) * 0.5, 0.0) };", None),
    (L, "let (sx, sy) = if va > sa { (sa / va, 1.0) } else { (1.0, va / sa) };", "let (sx, sy) = if va >= sa { (sa / va, 1.0) } else { (1.0, va / sa) };", "at va == sa both branches give (1, 1)"),
    (L, "let (sx, sy) = if va > sa { (sa / va, 1.0) } else { (1.0, va / sa) };", "let (sx, sy) = if va > sa { (1.0, sa / va) } else { (va / sa, 1.0) };", None),
    (L, "corner(3.0 / 300.0, 6.0 / 216.0, 210.0 / 216.0, 297.0 / 300.0)", "corner(4.0 / 300.0, 6.0 / 216.0, 210.0 / 216.0, 297.0 / 300.0)", None),
    (L, "corner(3.0 / 300.0, 6.0 / 216.0, 210.0 / 216.0, 297.0 / 300.0)", "corner(3.0 / 300.0, 7.0 / 216.0, 210.0 / 216.0, 297.0 / 300.0)", None),
    (L, "corner(3.0 / 300.0, 6.0 / 216.0, 210.0 / 216.0, 297.0 / 300.0)", "corner(3.0 / 300.0, 6.0 / 216.0, 209.0 / 216.0, 297.0 / 300.0)", None),
    (L, "corner(3.0 / 300.0, 6.0 / 216.0, 210.0 / 216.0, 297.0 / 300.0)", "corner(3.0 / 300.0, 6.0 / 216.0, 210.0 / 216.0, 296.0 / 300.0)", None),
    (L, "[bilinear(frame, lx, ty), bilinear(frame, rx, ty), bilinear(frame, lx, by), bilinear(frame, rx, by)]", "[bilinear(frame, lx, ty), bilinear(frame, rx, ty), bilinear(frame, lx, by), bilinear(frame, lx, by)]", None),
    (L, "(0..4).filter(|&j| j != i).map(|j| dist(c[i], c[j])).sum()", "(0..4).map(|j| dist(c[i], c[j])).sum()", "the term for j == i is the distance of a colour to itself, 0"),
    (L, "if sums[i] < best {", "if sums[i] <= best {", None),
    (L, "if sums[i] < best {", "if sums[i] > best {", None),
    (L, "let to8 = |v: f32| (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8;", "let to8 = |v: f32| (v.clamp(0.0, 1.0) * 255.0) as u8;", None),
    (L, "let to8 = |v: f32| (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8;", "let to8 = |v: f32| (v.clamp(0.0, 1.0) * 256.0 + 0.5) as u8;", None),
    (L, "let (ux, uy) = ((px as f32 + 0.5) / w, (py as f32 + 0.5) / h);", "let (ux, uy) = (px as f32 / w, (py as f32 + 0.5) / h);", None),
    (L, "let (ux, uy) = ((px as f32 + 0.5) / w, (py as f32 + 0.5) / h);", "let (ux, uy) = ((px as f32 + 0.5) / w, py as f32 / h);", None),
    (L, "let (u, v) = (ux * buvx + buox, uy * buvy + buoy);", "let (u, v) = (ux * buvx + buox, uy * buvy);", None),
    (L, "let (u, v) = (ux * buvx + buox, uy * buvy + buoy);", "let (u, v) = (ux * buvx, uy * buvy + buoy);", None),
    (L, "for i in -2i32..=2 {", "for i in -2i32..2 {", None),
    (L, "for j in -2i32..=2 {", "for j in -2i32..2 {", None),
    (L, "(u + i as f32 * 0.025).clamp(0.005, 0.995)", "(u + i as f32 * 0.03).clamp(0.005, 0.995)", None),
    (L, "(u + i as f32 * 0.025).clamp(0.005, 0.995)", "(u + i as f32 * 0.025).clamp(0.0, 0.995)", None),
    (L, "(u + i as f32 * 0.025).clamp(0.005, 0.995)", "(u + i as f32 * 0.025).clamp(0.005, 1.0)", None),
    (L, "(v + j as f32 * 0.035).clamp(0.0, 1.0)", "(v + j as f32 * 0.03).clamp(0.0, 1.0)", None),
    (L, "(v + j as f32 * 0.035).clamp(0.0, 1.0)", "(v + j as f32 * 0.035).clamp(0.01, 1.0)", None),
    (L, "(v + j as f32 * 0.035).clamp(0.0, 1.0)", "(v + j as f32 * 0.035).clamp(0.0, 0.99)", None),
    (L, "let wt = 1.0 / (1.0 + 0.06 * (i * i + j * j) as f32);", "let wt = 1.0 / (1.0 + 0.05 * (i * i + j * j) as f32);", None),
    (L, "let wt = 1.0 / (1.0 + 0.06 * (i * i + j * j) as f32);", "let wt = 1.0 / (1.0 + 0.06 * (i + j) as f32);", None),
    (L, "[acc[0] / ws * 0.88, acc[1] / ws * 0.88, acc[2] / ws * 0.88]", "[acc[0] / ws * 0.9, acc[1] / ws * 0.88, acc[2] / ws * 0.88]", None),
    (L, "[acc[0] / ws * 0.88, acc[1] / ws * 0.88, acc[2] / ws * 0.88]", "[acc[0] / ws * 0.88, acc[1] / ws * 0.9, acc[2] / ws * 0.88]", None),
    (L, "[acc[0] / ws * 0.88, acc[1] / ws * 0.88, acc[2] / ws * 0.88]", "[acc[0] / ws * 0.88, acc[1] / ws * 0.88, acc[2] / ws * 0.9]", None),
    (L, "let (fu, fv) = ((nx / sx + 1.0) * 0.5, (1.0 - ny / sy) * 0.5);", "let (fu, fv) = ((nx / sx + 1.0) * 0.5, (ny / sy + 1.0) * 0.5);", None),
    (L, "let (tx, ty) = (((fu * WIDTH as f32) as usize).min(WIDTH - 1), ((fv * HEIGHT as f32) as usize).min(HEIGHT - 1));", "let (tx, ty) = (((fu * WIDTH as f32) as usize), ((fv * HEIGHT as f32) as usize).min(HEIGHT - 1));", None),
    (L, "let (tx, ty) = (((fu * WIDTH as f32) as usize).min(WIDTH - 1), ((fv * HEIGHT as f32) as usize).min(HEIGHT - 1));", "let (tx, ty) = (((fu * WIDTH as f32) as usize).min(WIDTH - 1), ((fv * HEIGHT as f32) as usize));", None),
    (L, "let a = if fill == BridgeFill::Off { 1.0 } else {", "let a = if fill != BridgeFill::Off { 1.0 } else {", None),
    (L, "smoothstep(0.0, 6.0 / 300.0, fu) * smoothstep(0.0, 6.0 / 300.0, 1.0 - fu)", "smoothstep(0.0, 8.0 / 300.0, fu) * smoothstep(0.0, 6.0 / 300.0, 1.0 - fu)", None),
    (L, "smoothstep(0.0, 6.0 / 300.0, fu) * smoothstep(0.0, 6.0 / 300.0, 1.0 - fu)", "smoothstep(0.0, 6.0 / 300.0, fu) * smoothstep(0.0, 8.0 / 300.0, 1.0 - fu)", None),
    (L, "smoothstep(0.0, 6.0 / 300.0, fu) * smoothstep(0.0, 6.0 / 300.0, 1.0 - fu)", "smoothstep(0.0, 6.0 / 300.0, fu)", None),
    (L, "rgb[k] = fg[k] * a + rgb[k] * (1.0 - a);", "rgb[k] = fg[k] * a + rgb[k] * a;", None),
    (L, "t * t * (3.0 - 2.0 * t)", "t * t * (3.0 - 1.0 * t)", None),
    (L, "let t = ((x - e0) / (e1 - e0)).clamp(0.0, 1.0);", "let t = ((x - e0) / (e1 - e0)).clamp(0.0, 0.99);", None),
    (L, "(x.clamp(0, w as i32 - 1) as usize, y.clamp(0, h as i32 - 1) as usize)", "(x.clamp(0, w as i32 - 2) as usize, y.clamp(0, h as i32 - 1) as usize)", "texel x = 299 is only read as x0 + 1 with weight (fx - x0); the blur taps are clamped to u <= 0.995, where fx = 298.0 and that weight is 0, and the colour-fill corners sit at u <= 297/300"),
    (L, "(x.clamp(0, w as i32 - 1) as usize, y.clamp(0, h as i32 - 1) as usize)", "(x.clamp(0, w as i32 - 1) as usize, y.clamp(0, h as i32 - 2) as usize)", None),
    (L, "let (fx, fy) = (u * w as f32 - 0.5, v * h as f32 - 0.5);", "let (fx, fy) = (u * w as f32, v * h as f32 - 0.5);", None),
    (L, "let (fx, fy) = (u * w as f32 - 0.5, v * h as f32 - 0.5);", "let (fx, fy) = (u * w as f32 - 0.5, v * h as f32);", None),
    (L, "o[i] = (a[i] * (1.0 - tx) + b[i] * tx) * (1.0 - ty) + (c[i] * (1.0 - tx) + d[i] * tx) * ty;", "o[i] = (a[i] * (1.0 - tx) + b[i] * tx) * (1.0 - ty) + (c[i] * (1.0 - tx) + d[i] * tx) * (1.0 - ty);", None),
    (L, "o[i] = (a[i] * (1.0 - tx) + b[i] * tx) * (1.0 - ty) + (c[i] * (1.0 - tx) + d[i] * tx) * ty;", "o[i] = (a[i] * (1.0 - tx) + b[i] * ty) * (1.0 - ty) + (c[i] * (1.0 - tx) + d[i] * tx) * ty;", None),
    (L, "texel_wh(src, w, h, x0 + 1, y0), texel_wh(src, w, h, x0, y0 + 1), texel_wh(src, w, h, x0 + 1, y0 + 1)", "texel_wh(src, w, h, x0 + 1, y0), texel_wh(src, w, h, x0, y0 + 1), texel_wh(src, w, h, x0, y0 + 1)", None),
    (L, "[src[o] as f32 / 255.0, src[o + 1] as f32 / 255.0, src[o + 2] as f32 / 255.0]\n}\n\n/// GL_LINEAR with clamp-to-edge on any RGBA", "[src[o] as f32 / 255.0, src[o + 1] as f32 / 255.0, src[o + 1] as f32 / 255.0]\n}\n\n/// GL_LINEAR with clamp-to-edge on any RGBA", None),
    (L, "((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt()", "((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2))", "sqrt is monotonic: only the ordering of the sums is used"),
    (L, "((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt()", "((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt()", None),
    # ---- window_rules
    (W, "if screens.iter().any(|s| s.intersects(&rect)) {", "if screens.iter().all(|s| s.intersects(&rect)) {", None),
    (W, "if screens.iter().any(|s| s.intersects(&rect)) {", "if screens.iter().any(|s| s.intersects(&rect)) && false {", None),
    (W, "let Some(avail) = primary else { return rect };", "let Some(avail) = primary else { return Rect::new(0, 0, rect.w, rect.h) };", None),
    (W, "let width = rect.w.min(avail.w);", "let width = rect.w.max(avail.w);", None),
    (W, "let height = rect.h.min(avail.h);", "let height = rect.h.max(avail.h);", None),
    (W, "x: avail.x + 0.max((avail.w - width).div_euclid(2)),", "x: 0.max((avail.w - width).div_euclid(2)),", None),
    (W, "y: avail.y + 0.max((avail.h - height).div_euclid(2)),", "y: 0.max((avail.h - height).div_euclid(2)),", None),
    (W, "x: avail.x + 0.max((avail.w - width).div_euclid(2)),", "x: avail.x + 0.max((avail.w - width).div_euclid(3)),", None),
    (W, "y: avail.y + 0.max((avail.h - height).div_euclid(2)),", "y: avail.y + 0.max((avail.h - height).div_euclid(3)),", None),
    (W, "x: avail.x + 0.max((avail.w - width).div_euclid(2)),", "x: avail.x + 0.max((avail.w - width).div_euclid(2)) + 1,", None),
    (W, "x: avail.x + 0.max((avail.w - width).div_euclid(2)),", "x: avail.x + 0.min((avail.w - width).div_euclid(2)),", None),
    (W, "w: width.max(1),", "w: width,", None),
    (W, "h: height.max(1),", "h: height,", None),
    (W, "w: width.max(1),", "w: width.max(2),", None),
]

# ---- added 2026-10-30: the idle card, Qt's QRect rules, the startup placement, the screen lookup and the background-video composite
NEW = [
 # ---- idle_overlay: visualizer
 (I, "Visualizer { levels: vec![0.08; BAR_COUNT], peaks: vec![0.12; BAR_COUNT], audio_level: 0.0, phase: 0.0 }", "Visualizer { levels: vec![0.08; BAR_COUNT], peaks: vec![0.12; BAR_COUNT - 1], audio_level: 0.0, phase: 0.0 }", None),
 (I, "Visualizer { levels: vec![0.08; BAR_COUNT], peaks: vec![0.12; BAR_COUNT], audio_level: 0.0, phase: 0.0 }", "Visualizer { levels: vec![0.09; BAR_COUNT], peaks: vec![0.12; BAR_COUNT], audio_level: 0.0, phase: 0.0 }", None),
 (I, "Visualizer { levels: vec![0.08; BAR_COUNT], peaks: vec![0.12; BAR_COUNT], audio_level: 0.0, phase: 0.0 }", "Visualizer { levels: vec![0.08; BAR_COUNT], peaks: vec![0.13; BAR_COUNT], audio_level: 0.0, phase: 0.0 }", None),
 (I, "pub const BAR_COUNT: usize = 14;", "pub const BAR_COUNT: usize = 13;", None),
 (I, "self.audio_level = if level.is_nan() { 0.0 } else { level.clamp(0.0, 1.0) };", "self.audio_level = if level.is_nan() { 0.0 } else { level.clamp(0.0, 2.0) };", None),
 (I, "self.audio_level = if level.is_nan() { 0.0 } else { level.clamp(0.0, 1.0) };", "self.audio_level = if level.is_nan() { 1.0 } else { level.clamp(0.0, 1.0) };", None),
 (I, "self.audio_level = if level.is_nan() { 0.0 } else { level.clamp(0.0, 1.0) };", "self.audio_level = if level.is_nan() { 0.0 } else { level.clamp(0.1, 1.0) };", None),
 (I, "let amp = self.audio_level.clamp(0.0, 1.0);", "let amp = self.audio_level.clamp(0.0, 0.9);", "audio_level is already clamped to 0..1 by set_audio_level and only decays afterwards, so the second clamp never acts"),
 (I, "self.audio_level = (self.audio_level * 0.88).max(0.0);", "self.audio_level = (self.audio_level * 0.89).max(0.0);", None),
 (I, "self.audio_level = (self.audio_level * 0.88).max(0.0);", "self.audio_level = (self.audio_level * 0.88).max(0.1);", None),
 (I, "self.phase = (self.phase + (0.12 + amp * 0.55)) % 10000.0;", "self.phase = (self.phase + (0.13 + amp * 0.55)) % 10000.0;", None),
 (I, "self.phase = (self.phase + (0.12 + amp * 0.55)) % 10000.0;", "self.phase = (self.phase + (0.12 + amp * 0.5)) % 10000.0;", None),
 (I, "self.phase = (self.phase + (0.12 + amp * 0.55)) % 10000.0;", "self.phase = (self.phase + (0.12 + amp * 0.55)) % 1000.0;", "the phase would need 10,000 radians of drift to wrap; at most 0.67 a tick that is 15,000 ticks (12 minutes of continuous idle card), longer than the parity runs, and sin is periodic so a wrap at the app's value changes the curve only by the fractional part"),
 (I, "let wave1 = 0.5 + 0.5 * (self.phase + (i as f64 * 0.60)).sin();", "let wave1 = 0.5 + 0.5 * (self.phase + (i as f64 * 0.61)).sin();", None),
 (I, "let wave1 = 0.5 + 0.5 * (self.phase + (i as f64 * 0.60)).sin();", "let wave1 = 0.5 + 0.4 * (self.phase + (i as f64 * 0.60)).sin();", None),
 (I, "let wave2 = 0.5 + 0.5 * ((self.phase * 0.72) + (i as f64 * 1.05)).sin();", "let wave2 = 0.5 + 0.5 * ((self.phase * 0.73) + (i as f64 * 1.05)).sin();", None),
 (I, "let wave2 = 0.5 + 0.5 * ((self.phase * 0.72) + (i as f64 * 1.05)).sin();", "let wave2 = 0.5 + 0.5 * ((self.phase * 0.72) + (i as f64 * 1.06)).sin();", None),
 (I, "let shape = (0.62 * wave1) + (0.38 * wave2);", "let shape = (0.63 * wave1) + (0.38 * wave2);", None),
 (I, "let shape = (0.62 * wave1) + (0.38 * wave2);", "let shape = (0.62 * wave1) + (0.37 * wave2);", None),
 (I, "let target = 0.05 + (0.08 + 0.87 * shape) * amp.max(0.08);", "let target = 0.06 + (0.08 + 0.87 * shape) * amp.max(0.08);", None),
 (I, "let target = 0.05 + (0.08 + 0.87 * shape) * amp.max(0.08);", "let target = 0.05 + (0.09 + 0.87 * shape) * amp.max(0.08);", None),
 (I, "let target = 0.05 + (0.08 + 0.87 * shape) * amp.max(0.08);", "let target = 0.05 + (0.08 + 0.86 * shape) * amp.max(0.08);", None),
 (I, "let target = 0.05 + (0.08 + 0.87 * shape) * amp.max(0.08);", "let target = 0.05 + (0.08 + 0.87 * shape) * amp.max(0.09);", None),
 (I, "let lvl = (old * 0.70) + (target * 0.30);", "let lvl = (old * 0.71) + (target * 0.30);", None),
 (I, "let lvl = (old * 0.70) + (target * 0.30);", "let lvl = (old * 0.70) + (target * 0.31);", None),
 (I, "if lvl >= pk {\n                    pk = lvl.min(1.0);", "if lvl > pk {\n                    pk = lvl.min(1.0);", "a level exactly equal to its peak gives the same value either way (the else branch subtracts 0.018 then max 0.04, but equality of two floats from different formulas essentially never happens)"),
 (I, "pk = lvl.min(1.0);", "pk = lvl;", "a level above 1.0 is not reachable: target is at most 0.05 + 0.95 * 1.0 and the level averages toward it"),
 (I, "pk = (pk - 0.018).max(0.04);", "pk = (pk - 0.019).max(0.04);", None),
 (I, "pk = (pk - 0.018).max(0.04);", "pk = (pk - 0.018).max(0.05);", None),
 (I, "let mut pk = self.peaks.get(i).copied().unwrap_or(lvl);\n                if lvl >= pk {", "let mut pk = self.peaks.get(i).copied().unwrap_or(0.0);\n                if lvl >= pk {", "the meter always has as many peaks as levels (both 14); only a hand-built Visualizer with fewer peaks reaches the fallback, and then the app would raise IndexError"),
 (I, "let lvl = (self.levels[i] * 0.84).max(0.02);", "let lvl = (self.levels[i] * 0.85).max(0.02);", None),
 (I, "let lvl = (self.levels[i] * 0.84).max(0.02);", "let lvl = (self.levels[i] * 0.84).max(0.03);", None),
 (I, "*p = lvl.max(pk * 0.92);", "*p = lvl.max(pk * 0.93);", None),
 (I, "*p = lvl.max(pk * 0.92);", "*p = lvl.min(pk * 0.92);", None),
 (I, "self.audio_level = 0.0;\n            for i in 0..self.levels.len() {\n                let lvl", "for i in 0..self.levels.len() {\n                let lvl", None),
 # ---- idle_overlay: presence, layout
 (I, "idle && bg_exists && bg_playing\n}", "idle && bg_exists || bg_playing\n}", None),
 (I, "idle && bg_exists && bg_playing\n}", "idle || bg_exists && bg_playing\n}", None),
 (I, "if !idle || !bg_exists || !bg_playing {\n        return None;", "if !idle || !bg_exists {\n        return None;", None),
 (I, "if !idle || !bg_exists || !bg_playing {\n        return None;", "if !idle || !bg_playing {\n        return None;", None),
 (I, "if !idle || !bg_exists || !bg_playing {\n        return None;", "if !bg_exists || !bg_playing {\n        return None;", None),
 (I, "if playlist_len == 0 || current_index < 0 || current_index as u64 >= playlist_len as u64 {", "if playlist_len == 0 || current_index < 0 || current_index as u64 > playlist_len as u64 {", None),
 (I, "if playlist_len == 0 || current_index < 0 || current_index as u64 >= playlist_len as u64 {", "if playlist_len == 0 || current_index <= 0 || current_index as u64 >= playlist_len as u64 {", None),
 (I, "if playlist_len == 0 || current_index < 0 || current_index as u64 >= playlist_len as u64 {", "if current_index < 0 || current_index as u64 >= playlist_len as u64 {", "an empty playlist makes every index out of range anyway"),
 (I, "let rw = (box_h as i64) * (w as i64) / (h as i64);\n    if rw >= box_w as i64 {", "let rw = (box_h as i64) * (w as i64) / (h as i64);\n    if rw > box_w as i64 {", "at rw == box_w both branches give the box itself: (rw, box_h) and (box_w, box_w * h / w) coincide when the aspect ratios match"),
 (I, "let rw = (box_h as i64) * (w as i64) / (h as i64);\n    if rw >= box_w as i64 {", "let rw = (box_h as i64) * (w as i64) / (h as i64);\n    if rw <= box_w as i64 {", None),
 (I, "if w <= 0 || h <= 0 {\n        return (w, h);", "if w < 0 || h <= 0 {\n        return (w, h);", "a zero width makes rw 0, which is below any box width, so the second branch divides by w = 0 and the original's guard is only about avoiding that division by zero; the parity run never offers a zero-size picture (QPixmap would be null, which the app handles before scaling)"),
 (I, "if w <= 120 || h <= 120 {", "if w < 120 || h <= 120 {", None),
 (I, "if w <= 120 || h <= 120 {", "if w <= 120 || h < 120 {", None),
 (I, "if w <= 120 || h <= 120 {", "if w <= 120 && h <= 120 {", None),
 (I, "let card_w = ((w as f64 * 0.46) as i32).min(680);", "let card_w = ((w as f64 * 0.47) as i32).min(680);", None),
 (I, "let card_w = ((w as f64 * 0.46) as i32).min(680);", "let card_w = ((w as f64 * 0.46) as i32).min(681);", None),
 (I, "let card_h = ((h as f64 * 0.22) as i32).min(210);\n    let margin", "let card_h = ((h as f64 * 0.23) as i32).min(210);\n    let margin", None),
 (I, "let card_h = ((h as f64 * 0.22) as i32).min(210);\n    let margin", "let card_h = ((h as f64 * 0.22) as i32).min(211);\n    let margin", None),
 (I, "let margin = 18;", "let margin = 19;", None),
 (I, "let card_y = 4.max(h - card_h - 6);", "let card_y = 5.max(h - card_h - 6);", "h > 120 and card_h <= 22 percent of h, so h - card_h - 6 is above 87 and the floor of 4 is never reached"),
 (I, "let card_y = 4.max(h - card_h - 6);", "let card_y = 4.max(h - card_h - 7);", None),
 (I, "let pad = 12;\n    let art_size = 0.max(card_h - pad * 2);", "let pad = 13;\n    let art_size = 0.max(card_h - pad * 2);", None),
 (I, "let art_size = 0.max(card_h - pad * 2);", "let art_size = 0.max(card_h - pad);", None),
 (I, "let art_size = 0.max(card_h - pad * 2);", "let art_size = 1.max(card_h - pad * 2);", "card_h is at least 26 (22 percent of 121), so the floor of 0 is never reached"),
 (I, "text_x = card_x + pad + art_size + 12;", "text_x = card_x + pad + art_size + 13;", None),
 (I, "text_x = card_x + pad + art_size + 12;", "text_x = card_x + pad + art_size;", None),
 (I, "let text_w = (card_x + card_w - pad) - text_x;\n    if text_w <= 40 {", "let text_w = (card_x + card_w - pad) - text_x;\n    if text_w < 40 {", None),
 (I, "let text_w = (card_x + card_w - pad) - text_x;\n    if text_w <= 40 {", "let text_w = (card_x + card_w - pad) - text_x;\n    if text_w <= 41 {", None),
 (I, "let text_w = (card_x + card_w - pad) - text_x;", "let text_w = (card_x + card_w) - text_x;", None),
 (I, "let top_y = card_y + pad + 2;", "let top_y = card_y + pad + 3;", None),
 (I, "let artist_y = title_y + 2.max((card_h as f64 * 0.02) as i32) + artist_ascent;", "let artist_y = title_y + 3.max((card_h as f64 * 0.02) as i32) + artist_ascent;", "card_h <= 210 gives int(card_h * 0.02) <= 4, so the floor of 2 only acts below card_h = 100 where 3 changes it: killed unless the parity set has no small windows"),
 (I, "let artist_y = title_y + 2.max((card_h as f64 * 0.02) as i32) + artist_ascent;", "let artist_y = title_y + 2.max((card_h as f64 * 0.03) as i32) + artist_ascent;", None),
 (I, "artist_baseline: if has_artist { Some(artist_y) } else { None },", "artist_baseline: if !has_artist { Some(artist_y) } else { None },", None),
 (I, "let bar_area_h = 24.max((card_h as f64 * 0.33) as i32);", "let bar_area_h = 25.max((card_h as f64 * 0.33) as i32);", None),
 (I, "let bar_area_h = 24.max((card_h as f64 * 0.33) as i32);", "let bar_area_h = 24.max((card_h as f64 * 0.34) as i32);", None),
 (I, "let bar_area_y = card_y + card_h - pad - bar_area_h;", "let bar_area_y = card_y + card_h - bar_area_h;", None),
 (I, "if bar_area_w > 60 {", "if bar_area_w >= 60 {", None),
 (I, "if bar_area_w > 60 {", "if bar_area_w > 61 {", None),
 (I, "let levels: &[f64] = if levels.is_empty() { &default_levels } else { levels };", "let levels: &[f64] = if levels.is_empty() { &[0.08; 13] } else { levels };", None),
 (I, "let gap = 3;", "let gap = 4;", None),
 (I, "let usable_w = 1.max(bar_area_w - gap * (n - 1) - 4);", "let usable_w = 1.max(bar_area_w - gap * (n - 1) - 5);", None),
 (I, "let usable_w = 1.max(bar_area_w - gap * (n - 1) - 4);", "let usable_w = 1.max(bar_area_w - gap * n - 4);", None),
 (I, "let usable_w = 1.max(bar_area_w - gap * (n - 1) - 4);", "let usable_w = 2.max(bar_area_w - gap * (n - 1) - 4);", "bar_area_w is above 60 and 200 bars at 3 pixels already exceed it, so usable_w reaches 1 only with more than 20 bars in a lane under 100 px; the parity set draws up to 59 bars in lanes of every width and kills this one unless no case reaches it"),
 (I, "let bar_w = 2.max(usable_w.div_euclid(n));", "let bar_w = 3.max(usable_w.div_euclid(n));", None),
 (I, "let bar_w = 2.max(usable_w.div_euclid(n));", "let bar_w = 2.max(usable_w.div_euclid(n) + 1);", None),
 (I, "let max_h = 6.max(bar_area_h - 6);", "let max_h = 7.max(bar_area_h - 6);", "bar_area_h is at least 24, so max_h is at least 18 and the floor of 6 is never reached"),
 (I, "let max_h = 6.max(bar_area_h - 6);", "let max_h = 6.max(bar_area_h - 5);", None),
 (I, "let mut x = bar_area_x + 2;", "let mut x = bar_area_x + 3;", None),
 (I, "let v = lv.clamp(0.03, 1.0);", "let v = lv.clamp(0.04, 1.0);", None),
 (I, "let v = lv.clamp(0.03, 1.0);", "let v = lv.clamp(0.03, 0.99);", None),
 (I, "let h_px = 3.max((max_h as f64 * v) as i32);", "let h_px = 4.max((max_h as f64 * v) as i32);", None),
 (I, "let y = bar_area_y + bar_area_h - h_px - 2;", "let y = bar_area_y + bar_area_h - h_px - 3;", None),
 (I, "Some(p) => v.max(p.min(1.0)),", "Some(p) => v.max(*p),", None),
 (I, "Some(p) => v.max(p.min(1.0)),", "Some(p) => p.min(1.0),", None),
 (I, "let py = bar_area_y + bar_area_h - 2.max((max_h as f64 * pv) as i32) - 2;", "let py = bar_area_y + bar_area_h - 3.max((max_h as f64 * pv) as i32) - 2;", "v is at least 0.03 and max_h at least 18, so int(max_h * pv) is at least 0 and a floor of 2 versus 3 differs only for a pixel; reached by the 0.03-level cases"),
 (I, "bars.push(Bar { bar: Rect::new(x, y, bar_w, h_px), cap: Rect::new(x, py, bar_w, 2) });", "bars.push(Bar { bar: Rect::new(x, y, bar_w, h_px), cap: Rect::new(x, py, bar_w, 3) });", None),
 (I, "x += bar_w + gap;", "x += bar_w + gap + 1;", None),
 (I, "(11.max((card_h as f64 * 0.20) as i32), 9.max((card_h as f64 * 0.14) as i32))", "(12.max((card_h as f64 * 0.20) as i32), 9.max((card_h as f64 * 0.14) as i32))", None),
 (I, "(11.max((card_h as f64 * 0.20) as i32), 9.max((card_h as f64 * 0.14) as i32))", "(11.max((card_h as f64 * 0.21) as i32), 9.max((card_h as f64 * 0.14) as i32))", None),
 (I, "(11.max((card_h as f64 * 0.20) as i32), 9.max((card_h as f64 * 0.14) as i32))", "(11.max((card_h as f64 * 0.20) as i32), 10.max((card_h as f64 * 0.14) as i32))", None),
 (I, "(11.max((card_h as f64 * 0.20) as i32), 9.max((card_h as f64 * 0.14) as i32))", "(11.max((card_h as f64 * 0.20) as i32), 9.max((card_h as f64 * 0.15) as i32))", None),
 (I, "((h as f64 * 0.22) as i32).min(210)\n}", "((h as f64 * 0.23) as i32).min(210)\n}", None),
]

NEW += [
 # ---- window_rules: Qt's intersects
 (W, "if (self.w == 0 && self.h == 0) || (o.w == 0 && o.h == 0) {", "if (self.w == 0 || self.h == 0) || (o.w == 0 && o.h == 0) {", None),
 (W, "if (self.w == 0 && self.h == 0) || (o.w == 0 && o.h == 0) {", "if (self.w == 0 && self.h == 0) || (o.w == 0 || o.h == 0) {", None),
 (W, "if (self.w == 0 && self.h == 0) || (o.w == 0 && o.h == 0) {", "if (self.w == 0 && self.h == 0) && (o.w == 0 && o.h == 0) {", None),
 (W, "if end < p - 1 { (end + 1, p - 1) } else { (p, end) }", "if end < p { (end + 1, p - 1) } else { (p, end) }", None),
 (W, "if end < p - 1 { (end + 1, p - 1) } else { (p, end) }", "if end < p - 2 { (end + 1, p - 1) } else { (p, end) }", None),
 (W, "if end < p - 1 { (end + 1, p - 1) } else { (p, end) }", "if end < p - 1 { (end, p) } else { (p, end) }", None),
 (W, "if end < p - 1 { (end + 1, p - 1) } else { (p, end) }", "if end < p - 1 { (end + 1, p) } else { (p, end) }", None),
 (W, "let end = p + len - 1;", "let end = p + len;", None),
 (W, "l1 <= r2 && l2 <= r1 && t1 <= b2 && t2 <= b1", "l1 < r2 && l2 <= r1 && t1 <= b2 && t2 <= b1", None),
 (W, "l1 <= r2 && l2 <= r1 && t1 <= b2 && t2 <= b1", "l1 <= r2 && l2 < r1 && t1 <= b2 && t2 <= b1", None),
 (W, "l1 <= r2 && l2 <= r1 && t1 <= b2 && t2 <= b1", "l1 <= r2 && l2 <= r1 && t1 < b2 && t2 <= b1", None),
 (W, "l1 <= r2 && l2 <= r1 && t1 <= b2 && t2 <= b1", "l1 <= r2 && l2 <= r1 && t1 <= b2 && t2 < b1", None),
 (W, "l1 <= r2 && l2 <= r1 && t1 <= b2 && t2 <= b1", "l1 <= r2 && l2 <= r1 && t1 <= b2 || t2 <= b1", None),
 (W, "let (l1, r1) = span(self.x, self.w);", "let (l1, r1) = span(self.x, self.h);", None),
 (W, "let (t1, b1) = span(self.y, self.h);", "let (t1, b1) = span(self.y, self.w);", None),
 # ---- window_rules: Val and restore
 (W, "Val::Absent => Some(default),\n            Val::Null => None,", "Val::Absent => None,\n            Val::Null => None,", None),
 (W, "Val::Absent => Some(default),\n            Val::Null => None,", "Val::Absent => Some(default),\n            Val::Null => Some(default),", None),
 (W, "Val::Int(n) if n != 0 => n,", "Val::Int(n) if n > 0 => n,", None),
 (W, "Val::Int(n) if n != 0 => n,", "Val::Int(n) => n,", None),
 (W, "n.clamp(i32::MIN as i64, i32::MAX as i64) as i32", "n as i32", "values beyond i32 are outside what Qt's int() path or a screen can hold; the parity fuzz stays in range and a wrapped value is as wrong as a clamped one"),
 (W, "if [saved.x, saved.y, saved.width, saved.height].iter().all(|v| *v == Val::Absent) {", "if [saved.x, saved.y, saved.width, saved.height].iter().any(|v| *v == Val::Absent) {", None),
 (W, "if [saved.x, saved.y, saved.width, saved.height].iter().all(|v| *v == Val::Absent) {", "if [saved.x, saved.y, saved.width, saved.height].iter().all(|v| *v == Val::Null) {", None),
 (W, "clamp_i32(saved.x.plain(100)?),", "clamp_i32(saved.x.plain(101)?),", None),
 (W, "clamp_i32(saved.y.plain(100)?),", "clamp_i32(saved.y.plain(101)?),", None),
 (W, "clamp_i32(saved.width.plain(640)?),", "clamp_i32(saved.width.plain(641)?),", None),
 (W, "clamp_i32(saved.height.plain(520)?),", "clamp_i32(saved.height.plain(521)?),", None),
 (W, "clamp_i32(saved.x.plain(100)?),", "clamp_i32(saved.x.or_default(100)),", None),
 (W, "clamp_i32(saved.width.plain(640)?),", "clamp_i32(saved.width.or_default(640)),", None),
 (W, "Some(rect_on_attached_screen(rect, screens, primary))\n}", "Some(rect)\n}", None),
 # ---- window_rules: startup placement
 (W, "pub const DEFAULT_SIZE: (i32, i32) = (480, 360);", "pub const DEFAULT_SIZE: (i32, i32) = (480, 361);", None),
 (W, "pub const DEFAULT_SIZE: (i32, i32) = (480, 360);", "pub const DEFAULT_SIZE: (i32, i32) = (481, 360);", None),
 (W, "&& sp.width.or_zero() > 0\n        && sp.height.or_zero() > 0", "&& sp.width.or_zero() >= 0\n        && sp.height.or_zero() > 0", None),
 (W, "&& sp.width.or_zero() > 0\n        && sp.height.or_zero() > 0", "&& sp.width.or_zero() > 0\n        && sp.height.or_zero() >= 0", None),
 (W, "&& sp.width.or_zero() > 0\n        && sp.height.or_zero() > 0", "&& sp.width.or_zero() > 0\n        || sp.height.or_zero() > 0", None),
 (W, "clamp_i32(sp.x.or_default(100)),", "clamp_i32(sp.x.or_default(101)),", None),
 (W, "clamp_i32(sp.y.or_default(100)),", "clamp_i32(sp.y.or_default(101)),", None),
 (W, "clamp_i32(sp.width.or_default(640)),", "clamp_i32(sp.width.or_default(641)),", None),
 (W, "clamp_i32(sp.height.or_default(520)),", "clamp_i32(sp.height.or_default(521)),", "the saved height must be positive to reach this line (or_zero > 0), so the default 520 is never used"),
 (W, "clamp_i32(sp.width.or_default(640)),", "clamp_i32(sp.width.plain(640).unwrap_or(640)),", None),
 (W, "clamp_i32(sp.x.or_default(100)),", "clamp_i32(sp.x.plain(100).unwrap_or(100)),", None),
 (W, "out.geometry = Some(rect_on_attached_screen(rect, screens, primary));\n        out.show = !already_visible;", "out.geometry = Some(rect_on_attached_screen(rect, screens, primary));\n        out.show = true;", None),
 (W, "out.geometry = Some(rect_on_attached_screen(rect, screens, primary));\n        out.show = !already_visible;", "out.geometry = Some(rect);\n        out.show = !already_visible;", None),
 (W, "    if fullscreen_or_maximized {\n        return out;\n    }", "    if !fullscreen_or_maximized {\n        return out;\n    }", None),
 (W, "out.resize = Some((vw, vh));\n    let Some(avail) = cursor_screen.or(primary) else { return out };", "out.resize = Some((vw, vh));\n    let Some(avail) = primary.or(cursor_screen) else { return out };", None),
 (W, "let Some(avail) = cursor_screen.or(primary) else { return out };", "let Some(avail) = cursor_screen else { return out };", None),
 (W, "let (pad_x, pad_y) = (24, 24);", "let (pad_x, pad_y) = (25, 24);", None),
 (W, "let (pad_x, pad_y) = (24, 24);", "let (pad_x, pad_y) = (24, 25);", None),
 (W, "let mut x = sx + sw - vw - pad_x;", "let mut x = sx + sw - pad_x;", None),
 (W, "let mut y = sy + sh - vh - pad_y;", "let mut y = sy + sh - pad_y;", None),
 (W, "x = (sx + 10).max(x.min(sx + sw - vw - 10));", "x = (sx + 11).max(x.min(sx + sw - vw - 10));", None),
 (W, "x = (sx + 10).max(x.min(sx + sw - vw - 10));", "x = (sx + 10).max(x.min(sx + sw - vw - 11));", None),
 (W, "x = (sx + 10).max(x.min(sx + sw - vw - 10));", "x = (sx + 10).max(x.max(sx + sw - vw - 10));", None),
 (W, "x = (sx + 10).max(x.min(sx + sw - vw - 10));", "x = (sx + 10).min(x.min(sx + sw - vw - 10));", None),
 (W, "y = (sy + 10).max(y.min(sy + sh - vh - 10));", "y = (sy + 11).max(y.min(sy + sh - vh - 10));", None),
 (W, "y = (sy + 10).max(y.min(sy + sh - vh - 10));", "y = (sy + 10).max(y.min(sy + sh - vh - 11));", None),
 (W, "y = (sy + 10).max(y.min(sy + sh - vh - 10));", "y = (sy + 10).min(y.min(sy + sh - vh - 10));", None),
 (W, "out.move_to = Some((x, y));\n    out.show = !already_visible;", "out.move_to = Some((x, y));\n    out.show = true;", None),
 # ---- window_rules: screens
 (W, "r.w > 0 && r.h > 0 && px >= r.x && px <= r.x + r.w - 1 && py >= r.y && py <= r.y + r.h - 1", "r.w > 0 && r.h > 0 && px >= r.x && px < r.x + r.w - 1 && py >= r.y && py <= r.y + r.h - 1", None),
 (W, "r.w > 0 && r.h > 0 && px >= r.x && px <= r.x + r.w - 1 && py >= r.y && py <= r.y + r.h - 1", "r.w > 0 && r.h > 0 && px >= r.x && px <= r.x + r.w - 1 && py >= r.y && py < r.y + r.h - 1", None),
 (W, "r.w > 0 && r.h > 0 && px >= r.x && px <= r.x + r.w - 1 && py >= r.y && py <= r.y + r.h - 1", "r.w > 0 && r.h > 0 && px > r.x && px <= r.x + r.w - 1 && py >= r.y && py <= r.y + r.h - 1", None),
 (W, "r.w > 0 && r.h > 0 && px >= r.x && px <= r.x + r.w - 1 && py >= r.y && py <= r.y + r.h - 1", "r.w > 0 && r.h > 0 && px >= r.x && px <= r.x + r.w - 1 && py > r.y && py <= r.y + r.h - 1", None),
 (W, "r.w > 0 && r.h > 0 && px >= r.x", "r.w >= 0 && r.h > 0 && px >= r.x", "QRect.contains of a zero-width rectangle: x2 = x1 - 1 so no point satisfies x >= x1 and x <= x2; the extra w == 0 case is excluded by the arithmetic anyway"),
 (W, "r.w > 0 && r.h > 0 && px >= r.x", "r.w > 0 && r.h >= 0 && px >= r.x", "same: with h == 0 the bottom edge is above the top edge, so nothing is contained"),
 (W, "screens.iter().position(|s| rect_contains_point(s, px, py))", "screens.iter().rposition(|s| rect_contains_point(s, px, py))", None),
 (W, "let cx = ((frame.x as i64 + (frame.x as i64 + frame.w as i64 - 1)) / 2) as i32;", "let cx = ((frame.x as i64 + (frame.x as i64 + frame.w as i64)) / 2) as i32;", None),
 (W, "let cy = ((frame.y as i64 + (frame.y as i64 + frame.h as i64 - 1)) / 2) as i32;", "let cy = ((frame.y as i64 + (frame.y as i64 + frame.h as i64)) / 2) as i32;", None),
 (W, "let cx = ((frame.x as i64 + (frame.x as i64 + frame.w as i64 - 1)) / 2) as i32;", "let cx = (frame.x as i64 + frame.w as i64 / 2) as i32;", None),
 (W, "screen_at(screens, cx, cy).or(window_screen).or(primary)", "screen_at(screens, cx, cy).or(primary).or(window_screen)", None),
 (W, "screen_at(screens, cx, cy).or(window_screen).or(primary)", "window_screen.or(screen_at(screens, cx, cy)).or(primary)", None),
 (W, "screen_at(screens, cx, cy).or(window_screen).or(primary)", "screen_at(screens, cx, cy).or(window_screen)", None),
 (W, "screen_at(screens, cx, cy).or(window_screen).or(primary)", "screen_at(screens, cx, cy).or(primary)", None),
]

NEW += [
 # ---- compose_bridge_background
 (L, "let (lx, rx, ty, by) = (0.0 + 1.0 * (3.0f32 / 300.0), 0.0 + 1.0 * (297.0f32 / 300.0), 6.0f32 / 216.0, 210.0f32 / 216.0);", "let (lx, rx, ty, by) = (0.0 + 1.0 * (4.0f32 / 300.0), 0.0 + 1.0 * (297.0f32 / 300.0), 6.0f32 / 216.0, 210.0f32 / 216.0);", None),
 (L, "let (lx, rx, ty, by) = (0.0 + 1.0 * (3.0f32 / 300.0), 0.0 + 1.0 * (297.0f32 / 300.0), 6.0f32 / 216.0, 210.0f32 / 216.0);", "let (lx, rx, ty, by) = (0.0 + 1.0 * (3.0f32 / 300.0), 0.0 + 1.0 * (296.0f32 / 300.0), 6.0f32 / 216.0, 210.0f32 / 216.0);", None),
 (L, "let (lx, rx, ty, by) = (0.0 + 1.0 * (3.0f32 / 300.0), 0.0 + 1.0 * (297.0f32 / 300.0), 6.0f32 / 216.0, 210.0f32 / 216.0);", "let (lx, rx, ty, by) = (0.0 + 1.0 * (3.0f32 / 300.0), 0.0 + 1.0 * (297.0f32 / 300.0), 7.0f32 / 216.0, 210.0f32 / 216.0);", None),
 (L, "let (lx, rx, ty, by) = (0.0 + 1.0 * (3.0f32 / 300.0), 0.0 + 1.0 * (297.0f32 / 300.0), 6.0f32 / 216.0, 210.0f32 / 216.0);", "let (lx, rx, ty, by) = (0.0 + 1.0 * (3.0f32 / 300.0), 0.0 + 1.0 * (297.0f32 / 300.0), 6.0f32 / 216.0, 209.0f32 / 216.0);", None),
 (L, "let (lx, rx, ty, by) = (0.0 + 1.0 * (12.0f32 / 300.0), 0.0 + 1.0 * (288.0f32 / 300.0), 18.0f32 / 216.0, 198.0f32 / 216.0);", "let (lx, rx, ty, by) = (0.0 + 1.0 * (13.0f32 / 300.0), 0.0 + 1.0 * (288.0f32 / 300.0), 18.0f32 / 216.0, 198.0f32 / 216.0);", None),
 (L, "let (lx, rx, ty, by) = (0.0 + 1.0 * (12.0f32 / 300.0), 0.0 + 1.0 * (288.0f32 / 300.0), 18.0f32 / 216.0, 198.0f32 / 216.0);", "let (lx, rx, ty, by) = (0.0 + 1.0 * (12.0f32 / 300.0), 0.0 + 1.0 * (289.0f32 / 300.0), 18.0f32 / 216.0, 198.0f32 / 216.0);", None),
 (L, "let (lx, rx, ty, by) = (0.0 + 1.0 * (12.0f32 / 300.0), 0.0 + 1.0 * (288.0f32 / 300.0), 18.0f32 / 216.0, 198.0f32 / 216.0);", "let (lx, rx, ty, by) = (0.0 + 1.0 * (12.0f32 / 300.0), 0.0 + 1.0 * (288.0f32 / 300.0), 19.0f32 / 216.0, 198.0f32 / 216.0);", None),
 (L, "let (lx, rx, ty, by) = (0.0 + 1.0 * (12.0f32 / 300.0), 0.0 + 1.0 * (288.0f32 / 300.0), 18.0f32 / 216.0, 198.0f32 / 216.0);", "let (lx, rx, ty, by) = (0.0 + 1.0 * (12.0f32 / 300.0), 0.0 + 1.0 * (288.0f32 / 300.0), 18.0f32 / 216.0, 199.0f32 / 216.0);", None),
 (L, "let panel = if dist(inner[2], inner[3]) < 0.10 { inner[2] } else { tile };", "let panel = if dist(inner[2], inner[3]) <= 0.10 { inner[2] } else { tile };", "a colour distance of exactly 0.10 does not occur between two 8-bit colours (distances are sqrt of sums of squares of multiples of 1/255)"),
 (L, "let panel = if dist(inner[2], inner[3]) < 0.10 { inner[2] } else { tile };", "let panel = if dist(inner[2], inner[3]) < 0.11 { inner[2] } else { tile };", None),
 (L, "let panel = if dist(inner[2], inner[3]) < 0.10 { inner[2] } else { tile };", "let panel = if dist(inner[2], inner[3]) < 0.10 { inner[3] } else { tile };", None),
 (L, "let panel = if dist(inner[2], inner[3]) < 0.10 { inner[2] } else { tile };", "let panel = if dist(inner[1], inner[3]) < 0.10 { inner[2] } else { tile };", None),
 (L, "let new = bilinear_wh(bg.current.rgba, bg.current.w, bg.current.h, ux, uy);", "let new = bilinear_wh(bg.current.rgba, bg.current.w, bg.current.h, uy, ux);", None),
 (L, "Some(p) => bilinear_wh(p.rgba, p.w, p.h, ux, uy),", "Some(p) => bilinear_wh(p.rgba, p.w, p.h, uy, ux),", None),
 (L, "let prev_op = if bg.previous.is_some() { bg.previous_opacity } else { 0.0 };", "let prev_op = bg.previous_opacity;", "without a previous frame `old` is 0.0 anyway, so its opacity cannot show"),
 (L, "rgb[k] = old[k] * prev_op * (1.0 - bg.mix) + new[k] * bg.opacity;", "rgb[k] = old[k] * prev_op * bg.mix + new[k] * bg.opacity;", None),
 (L, "rgb[k] = old[k] * prev_op * (1.0 - bg.mix) + new[k] * bg.opacity;", "rgb[k] = old[k] * (1.0 - bg.mix) + new[k] * bg.opacity;", None),
 (L, "rgb[k] = old[k] * prev_op * (1.0 - bg.mix) + new[k] * bg.opacity;", "rgb[k] = old[k] * prev_op * (1.0 - bg.mix) + new[k];", None),
 (L, "rgb[k] = old[k] * prev_op * (1.0 - bg.mix) + new[k] * bg.opacity;", "rgb[k] = old[k] * prev_op * (1.0 - bg.mix) - new[k] * bg.opacity;", None),
 (L, "if !(delta < 0.16 || (fg[0] < 0.20 && delta < 0.28)) {", "if !(delta <= 0.16 || (fg[0] < 0.20 && delta < 0.28)) {", "a colour distance of exactly 0.16 cannot occur between 8-bit colours either"),
 (L, "if !(delta < 0.16 || (fg[0] < 0.20 && delta < 0.28)) {", "if !(delta < 0.17 || (fg[0] < 0.20 && delta < 0.28)) {", None),
 (L, "if !(delta < 0.16 || (fg[0] < 0.20 && delta < 0.28)) {", "if !(delta < 0.16 || (fg[0] < 0.21 && delta < 0.28)) {", None),
 (L, "if !(delta < 0.16 || (fg[0] < 0.20 && delta < 0.28)) {", "if !(delta < 0.16 || (fg[0] < 0.20 && delta < 0.29)) {", None),
 (L, "if !(delta < 0.16 || (fg[0] < 0.20 && delta < 0.28)) {", "if !(delta < 0.16 && (fg[0] < 0.20 && delta < 0.28)) {", None),
 (L, "if !(delta < 0.16 || (fg[0] < 0.20 && delta < 0.28)) {", "if !(delta < 0.16 || (fg[0] < 0.20 || delta < 0.28)) {", None),
 (L, "if !(delta < 0.16 || (fg[0] < 0.20 && delta < 0.28)) {", "if !(delta < 0.16 || (fg[1] < 0.20 && delta < 0.28)) {", None),
 (L, "let delta = dist(fg, border).min(dist(fg, tile).min(dist(fg, panel)));", "let delta = dist(fg, border).min(dist(fg, tile));", None),
 (L, "let delta = dist(fg, border).min(dist(fg, tile).min(dist(fg, panel)));", "let delta = dist(fg, border).min(dist(fg, panel));", "when the panel colour differs from the tile colour the panel colour is c2, which is also one of the four inner samples the tile colour is picked from; the distance to border/tile alone gives the same decision except where only the panel colour is near, which needs a disc with three different backdrop colours"),
 (L, "let delta = dist(fg, border).min(dist(fg, tile).min(dist(fg, panel)));", "let delta = dist(fg, tile).min(dist(fg, panel));", None),
 (L, "let (sx, sy) = if va > sa { (sa / va, 1.0) } else { (1.0, va / sa) };\n    let nearest", "let (sx, sy) = if va >= sa { (sa / va, 1.0) } else { (1.0, va / sa) };\n    let nearest", "at va == sa both branches give (1.0, 1.0)"),
 (L, "let (sx, sy) = if va > sa { (sa / va, 1.0) } else { (1.0, va / sa) };\n    let nearest", "let (sx, sy) = if va < sa { (sa / va, 1.0) } else { (1.0, va / sa) };\n    let nearest", None),
 (L, "let (sx, sy) = if va > sa { (sa / va, 1.0) } else { (1.0, va / sa) };\n    let nearest", "let (sx, sy) = if va > sa { (va / sa, 1.0) } else { (1.0, va / sa) };\n    let nearest", None),
 (L, "let (tx, ty) = (((u * WIDTH as f32) as usize).min(WIDTH - 1), ((v * HEIGHT as f32) as usize).min(HEIGHT - 1));\n        let o = (ty * WIDTH + tx) * 4;\n        [frame[o] as f32", "let (tx, ty) = (((u * WIDTH as f32) as usize), ((v * HEIGHT as f32) as usize).min(HEIGHT - 1));\n        let o = (ty * WIDTH + tx) * 4;\n        [frame[o] as f32", "u reaches 1.0 only on the last pixel's far edge, which the pixel-centre sampling of the window never produces; the same guard in compose_bridge has an equivalent mutant too"),
 (L, "let (tx, ty) = (((u * WIDTH as f32) as usize).min(WIDTH - 1), ((v * HEIGHT as f32) as usize).min(HEIGHT - 1));\n        let o = (ty * WIDTH + tx) * 4;\n        [frame[o] as f32", "let (tx, ty) = (((u * WIDTH as f32) as usize).min(WIDTH - 1), ((v * HEIGHT as f32) as usize));\n        let o = (ty * WIDTH + tx) * 4;\n        [frame[o] as f32", "same"),
]


NEW2 = [
 # the second copies of shared lines, in compose_bridge_background
 (L, "let to8 = |v: f32| (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8;", "let to8 = |v: f32| (v.clamp(0.0, 1.0) * 255.0) as u8;", None, 1),
 (L, "let to8 = |v: f32| (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8;", "let to8 = |v: f32| (v * 255.0 + 0.5) as u8;", "values above 1 or below 0 only occur with an opacity above 1 or a negative one, which the bridge's setters clamp to 0..1; the sum of the previous and current shares stays in range because mix and opacity are fractions", 1),
 (L, "let (ux, uy) = ((px as f32 + 0.5) / w, (py as f32 + 0.5) / h);", "let (ux, uy) = ((px as f32) / w, (py as f32 + 0.5) / h);", None, 1),
 (L, "let (ux, uy) = ((px as f32 + 0.5) / w, (py as f32 + 0.5) / h);", "let (ux, uy) = ((px as f32 + 0.5) / w, (py as f32) / h);", None, 1),
 (L, "let sa = WIDTH as f32 / HEIGHT as f32;", "let sa = HEIGHT as f32 / WIDTH as f32;", None, 1),
 (L, "let va = w / h;", "let va = h / w;", None, 1),
 (L, "(0..4).filter(|&j| j != i).map(|j| dist(c[i], c[j])).sum()", "(0..4).filter(|&j| j != i).map(|j| dist(c[i], c[j])).fold(0.0, f32::max)", None, 1),
 (L, "if sums[i] < best {", "if sums[i] <= best {", None, 1),
 (L, "if sums[i] < best {", "if sums[i] > best {", None, 1),
]
NEW += NEW2
M += NEW

NEW3 = [
 # the fit quad coverage rule (shared by compose_bridge and compose_bridge_background) and the bg composite's picture coordinates
 (L, "if ph * w > pw * h {\n        // wider", "if ph * w >= pw * h {\n        // wider", "at an exact aspect match the wide branch covers every pixel too (lo = 0 and hi = 2 * ph * w)"),
 (L, "if ph * w > pw * h {\n        // wider", "if ph * w < pw * h {\n        // wider", None),
 (L, "} else if ph * w < pw * h {", "} else if ph * w <= pw * h {", "same: the tall branch at an exact match covers everything"),
 (L, "} else if ph * w < pw * h {", "} else if ph * w > pw * h {", None),
 (L, "let c = 2 * ph * (2 * px + 1);", "let c = 2 * ph * (2 * px);", None),
 (L, "let c = 2 * pw * (2 * py + 1);", "let c = 2 * pw * (2 * py);", None),
 (L, "c >= 2 * ph * w - 2 * pw * h && c < 2 * ph * w + 2 * pw * h", "c > 2 * ph * w - 2 * pw * h && c < 2 * ph * w + 2 * pw * h", None),
 (L, "c >= 2 * ph * w - 2 * pw * h && c < 2 * ph * w + 2 * pw * h", "c >= 2 * ph * w - 2 * pw * h && c <= 2 * ph * w + 2 * pw * h", None),
 (L, "c >= 2 * pw * h - 2 * ph * w && c < 2 * pw * h + 2 * ph * w", "c > 2 * pw * h - 2 * ph * w && c < 2 * pw * h + 2 * ph * w", None),
 (L, "c >= 2 * pw * h - 2 * ph * w && c < 2 * pw * h + 2 * ph * w", "c >= 2 * pw * h - 2 * ph * w && c <= 2 * pw * h + 2 * ph * w", None),
 (L, "c >= 2 * ph * w - 2 * pw * h && c < 2 * ph * w + 2 * pw * h", "c >= 2 * ph * w - 2 * pw * h || c < 2 * ph * w + 2 * pw * h", None),
 (L, "c >= 2 * ph * w - 2 * pw * h && c < 2 * ph * w + 2 * pw * h", "c >= 2 * ph * w - 2 * pw * h && c < 2 * ph * w + 2 * pw * h + 2", None),
 (L, "let (pw, ph) = (WIDTH as i64, HEIGHT as i64);", "let (pw, ph) = (HEIGHT as i64, WIDTH as i64);", None),
 (L, "let (fu, fv) = ((nx / sx + 1.0) * 0.5, (1.0 - ny / sy) * 0.5);", "let (fu, fv) = ((nx / sx + 1.0) * 0.5, (ny / sy + 1.0) * 0.5);", None, 1),
 (L, "let (fu, fv) = ((nx / sx + 1.0) * 0.5, (1.0 - ny / sy) * 0.5);", "let (fu, fv) = ((nx / sx - 1.0) * 0.5, (1.0 - ny / sy) * 0.5);", None, 1),
 (L, "let (nx, ny) = (ux * 2.0 - 1.0, 1.0 - uy * 2.0);", "let (nx, ny) = (ux * 2.0 - 1.0, uy * 2.0 - 1.0);", None, 1),
 (L, "if fit_quad_covers(px, py, win_w, win_h) {", "if fit_quad_covers(py, px, win_w, win_h) {", None, 1),
 (L, "if fit_quad_covers(px, py, win_w, win_h) {", "if fit_quad_covers(px, py, win_h, win_w) {", None, 0),
]
M += NEW3


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT / "rust", env=ENV, capture_output=True, text=True, **kw)


def main():
    only = None
    if len(sys.argv) > 2 and sys.argv[1] == "--only":
        only = {int(x) for x in sys.argv[2].split(",")}
    originals = {f: (CRATE / f).read_text() for f in (L, W, I)}
    survivors, killed, equivalent = [], 0, 0
    try:
        for n, (f, old, new, note, *rest) in enumerate(M):
            nth = rest[0] if rest else 0
            if only is not None and n not in only:
                continue
            src = originals[f]
            if src.count(old) <= nth:
                print(f"[{n:03d}] PATTERN NOT FOUND ({src.count(old)}) in {f}: {old[:70]!r}")
                survivors.append(n)
                continue
            pos = -1
            for _ in range(nth + 1):
                pos = src.index(old, pos + 1)
            (CRATE / f).write_text(src[:pos] + new + src[pos + len(old):])
            try:
                if run(["cargo", "build", "--release", "-p", "singws-cdg", "--quiet"]).returncode != 0:
                    killed += 1
                    print(f"[{n:03d}] killed (does not compile)")
                    continue
                if run(["cargo", "test", "--release", "-p", "singws-cdg", "--quiet"]).returncode != 0:
                    killed += 1
                    print(f"[{n:03d}] killed by the crate tests")
                    continue
                script = {L: "tools/cdg_display_parity.py", W: "tools/window_rules_parity.py", I: "tools/idle_overlay_parity.py"}[f]
                if subprocess.run([PY, str(ROOT / script)], env=ENV, capture_output=True, text=True).returncode != 0:
                    killed += 1
                    print(f"[{n:03d}] killed by {script.split('/')[-1]}")
                    continue
            finally:
                pass
            if note:
                print(f"[{n:03d}] EQUIVALENT ({note})")
                equivalent += 1
            else:
                print(f"[{n:03d}] SURVIVED {f}: {old[:80]!r} -> {new[:60]!r}")
                survivors.append(n)
    finally:
        for f, src in originals.items():
            (CRATE / f).write_text(src)
        run(["cargo", "build", "--release", "-p", "singws-cdg", "--quiet"])
    print(f"killed {killed}, equivalent {equivalent}, of {len(M) if only is None else len(only)}; survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
