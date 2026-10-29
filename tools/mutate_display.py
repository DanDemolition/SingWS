#!/usr/bin/env python3
"""Mutation test for the display half of rust/crates/singws-cdg: clean_near_black, side_fill_color, fit_size, scale_nearest, compose,
compose_bridge (lib.rs) and the window placement rule (window_rules.rs). Every mutant must be killed by

  1. the crate's own tests (`cargo test -p singws-cdg`, which include the pinned compose_bridge output), or
  2. tools/cdg_display_parity.py (the Rust helpers against the app's Python functions and against Qt's own scaling/painting), or
  3. tools/window_rules_parity.py (the placement rule against the app's Python function).

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
TARGET = os.path.expanduser("~/.cache/singws-rust-target-cdg")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET, QT_QPA_PLATFORM="offscreen", CDG_PARITY_QUICK="1")
ENV["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + ENV["PATH"]
PY = os.environ.get("PY", "/Users/daniel/Documents/SingWS/.venv/bin/python")

L, W = "lib.rs", "window_rules.rs"
EQ_SEARCH = "the sample positions never exceed the last pixel: (width-1)*i/12 <= width-1, so the clamp never acts"
EQ_COUNT = "every count is scaled by the same factor (2n or 2n-1), which keeps both the order and the ties of the counts"

# (file, old, new, note)
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
    (L, "if nx.abs() <= sx && ny.abs() <= sy {", "if nx.abs() < sx && ny.abs() <= sy {", None),
    (L, "if nx.abs() <= sx && ny.abs() <= sy {", "if nx.abs() <= sx || ny.abs() <= sy {", None),
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
    (L, "(x.clamp(0, WIDTH as i32 - 1) as usize, y.clamp(0, HEIGHT as i32 - 1) as usize)", "(x.clamp(0, WIDTH as i32 - 2) as usize, y.clamp(0, HEIGHT as i32 - 1) as usize)", "texel x = 299 is only read as x0 + 1 with weight (fx - x0); the blur taps are clamped to u <= 0.995, where fx = 298.0 and that weight is 0, and the colour-fill corners sit at u <= 297/300"),
    (L, "(x.clamp(0, WIDTH as i32 - 1) as usize, y.clamp(0, HEIGHT as i32 - 1) as usize)", "(x.clamp(0, WIDTH as i32 - 1) as usize, y.clamp(0, HEIGHT as i32 - 2) as usize)", None),
    (L, "let (fx, fy) = (u * WIDTH as f32 - 0.5, v * HEIGHT as f32 - 0.5);", "let (fx, fy) = (u * WIDTH as f32, v * HEIGHT as f32 - 0.5);", None),
    (L, "let (fx, fy) = (u * WIDTH as f32 - 0.5, v * HEIGHT as f32 - 0.5);", "let (fx, fy) = (u * WIDTH as f32 - 0.5, v * HEIGHT as f32);", None),
    (L, "o[i] = (a[i] * (1.0 - tx) + b[i] * tx) * (1.0 - ty) + (c[i] * (1.0 - tx) + d[i] * tx) * ty;", "o[i] = (a[i] * (1.0 - tx) + b[i] * tx) * (1.0 - ty) + (c[i] * (1.0 - tx) + d[i] * tx) * (1.0 - ty);", None),
    (L, "o[i] = (a[i] * (1.0 - tx) + b[i] * tx) * (1.0 - ty) + (c[i] * (1.0 - tx) + d[i] * tx) * ty;", "o[i] = (a[i] * (1.0 - tx) + b[i] * ty) * (1.0 - ty) + (c[i] * (1.0 - tx) + d[i] * tx) * ty;", None),
    (L, "texel(src, x0 + 1, y0), texel(src, x0, y0 + 1), texel(src, x0 + 1, y0 + 1)", "texel(src, x0 + 1, y0), texel(src, x0, y0 + 1), texel(src, x0, y0 + 1)", None),
    (L, "[src[o] as f32 / 255.0, src[o + 1] as f32 / 255.0, src[o + 2] as f32 / 255.0]\n}", "[src[o] as f32 / 255.0, src[o + 1] as f32 / 255.0, src[o + 1] as f32 / 255.0]\n}", None),
    (L, "((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt()", "((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2))", "sqrt is monotonic: only the ordering of the sums is used"),
    (L, "((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt()", "((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt()", None),
    # ---- window_rules
    (W, "if self.w <= 0 || self.h <= 0 || o.w <= 0 || o.h <= 0 {", "if self.w < 0 || self.h <= 0 || o.w <= 0 || o.h <= 0 {", None),
    (W, "if self.w <= 0 || self.h <= 0 || o.w <= 0 || o.h <= 0 {", "if self.w <= 0 || self.h < 0 || o.w <= 0 || o.h <= 0 {", None),
    (W, "if self.w <= 0 || self.h <= 0 || o.w <= 0 || o.h <= 0 {", "if self.w <= 0 || self.h <= 0 || o.w < 0 || o.h <= 0 {", None),
    (W, "if self.w <= 0 || self.h <= 0 || o.w <= 0 || o.h <= 0 {", "if self.w <= 0 || self.h <= 0 || o.w <= 0 || o.h < 0 {", None),
    (W, "if self.w <= 0 || self.h <= 0 || o.w <= 0 || o.h <= 0 {", "if self.w <= 0 && self.h <= 0 || o.w <= 0 || o.h <= 0 {", None),
    (W, "let (r1, b1, r2, b2) = (self.x + self.w - 1, self.y + self.h - 1, o.x + o.w - 1, o.y + o.h - 1);", "let (r1, b1, r2, b2) = (self.x + self.w, self.y + self.h - 1, o.x + o.w - 1, o.y + o.h - 1);", None),
    (W, "let (r1, b1, r2, b2) = (self.x + self.w - 1, self.y + self.h - 1, o.x + o.w - 1, o.y + o.h - 1);", "let (r1, b1, r2, b2) = (self.x + self.w - 1, self.y + self.h, o.x + o.w - 1, o.y + o.h - 1);", None),
    (W, "let (r1, b1, r2, b2) = (self.x + self.w - 1, self.y + self.h - 1, o.x + o.w - 1, o.y + o.h - 1);", "let (r1, b1, r2, b2) = (self.x + self.w - 1, self.y + self.h - 1, o.x + o.w, o.y + o.h - 1);", None),
    (W, "let (r1, b1, r2, b2) = (self.x + self.w - 1, self.y + self.h - 1, o.x + o.w - 1, o.y + o.h - 1);", "let (r1, b1, r2, b2) = (self.x + self.w - 1, self.y + self.h - 1, o.x + o.w - 1, o.y + o.h);", None),
    (W, "self.x <= r2 && o.x <= r1 && self.y <= b2 && o.y <= b1", "self.x < r2 && o.x <= r1 && self.y <= b2 && o.y <= b1", None),
    (W, "self.x <= r2 && o.x <= r1 && self.y <= b2 && o.y <= b1", "self.x <= r2 && o.x < r1 && self.y <= b2 && o.y <= b1", None),
    (W, "self.x <= r2 && o.x <= r1 && self.y <= b2 && o.y <= b1", "self.x <= r2 && o.x <= r1 && self.y < b2 && o.y <= b1", None),
    (W, "self.x <= r2 && o.x <= r1 && self.y <= b2 && o.y <= b1", "self.x <= r2 && o.x <= r1 && self.y <= b2 && o.y < b1", None),
    (W, "self.x <= r2 && o.x <= r1 && self.y <= b2 && o.y <= b1", "self.x <= r2 && o.x <= r1 && self.y <= b2 || o.y <= b1", None),
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


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT / "rust", env=ENV, capture_output=True, text=True, **kw)


def main():
    only = None
    if len(sys.argv) > 2 and sys.argv[1] == "--only":
        only = {int(x) for x in sys.argv[2].split(",")}
    originals = {f: (CRATE / f).read_text() for f in (L, W)}
    survivors, killed, equivalent = [], 0, 0
    try:
        for n, (f, old, new, note) in enumerate(M):
            if only is not None and n not in only:
                continue
            src = originals[f]
            if src.count(old) != 1:
                print(f"[{n:03d}] PATTERN NOT UNIQUE/FOUND ({src.count(old)}) in {f}: {old[:70]!r}")
                survivors.append(n)
                continue
            (CRATE / f).write_text(src.replace(old, new))
            try:
                if run(["cargo", "build", "--release", "-p", "singws-cdg", "--quiet"]).returncode != 0:
                    killed += 1
                    print(f"[{n:03d}] killed (does not compile)")
                    continue
                if run(["cargo", "test", "--release", "-p", "singws-cdg", "--quiet"]).returncode != 0:
                    killed += 1
                    print(f"[{n:03d}] killed by the crate tests")
                    continue
                script = "tools/cdg_display_parity.py" if f == L else "tools/window_rules_parity.py"
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
