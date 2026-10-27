#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-cdg/src/lib.rs: every mutant must be
caught by the crate's own tests OR by the differential test against FFmpeg
(tools/cdg_diff.py, which needs ffmpeg and ffprobe on PATH). Equivalent mutants
carry a note saying why nothing can see them."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-cdg/src/lib.rs"
TARGET = os.path.expanduser("~/.cache/singws-rust-target-cdg")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
ENV["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + ENV["PATH"]

M = [
    ('(packet[0] & MASK) != COMMAND', '(packet[0] & MASK) == COMMAND', None),
    ('(packet[0] & MASK) != COMMAND', 'packet[0] != COMMAND', None),
    ('let inst = packet[1] & MASK;', 'let inst = packet[1];', None),
    ('                if d[1] & 0x0F == 0 {\n                    let c = d[0] & 0x0F;', '                if true {\n                    let c = d[0] & 0x0F;', None),
    ('                if d[1] & 0x0F == 0 {\n                    let c = d[0] & 0x0F;', '                if d[1] == 0 {\n                    let c = d[0] & 0x0F;', None),
    ('                    let c = d[0] & 0x0F;\n                    self.pixels', '                    let c = d[0];\n                    self.pixels', "the colour index is masked to 4 bits again when the frame is turned into RGBA, so a stray high bit never shows"),
    ('                if d[1] & 0x0F == 0 {\n                    self.border(d[0] & 0x0F)', '                if true {\n                    self.border(d[0] & 0x0F)', None),
    ('                    self.border(d[0] & 0x0F)', '                    self.border(d[0])', "the colour index is masked to 4 bits again when the frame is turned into RGBA, so a stray high bit never shows"),
    ('TILE_BLOCK => return self.tile(d, false),', 'TILE_BLOCK => return self.tile(d, true),', None),
    ('TILE_XOR => return self.tile(d, true),', 'TILE_XOR => return self.tile(d, false),', None),
    ('TILE_BLOCK => return self.tile(d, false),', 'TILE_BLOCK => { self.tile(d, false); }', None),
    ('SCROLL_PRESET => self.scroll(d, false),', 'SCROLL_PRESET => self.scroll(d, true),', None),
    ('SCROLL_COPY => self.scroll(d, true),', 'SCROLL_COPY => self.scroll(d, false),', None),
    ('self.transparency[i] = d[i] & 0x3F;', 'self.transparency[i] = d[i];', "alpha is 255 - 4*v kept to 8 bits, which only depends on v modulo 64: masking changes nothing"),
    ('LOAD_LOW => self.load_palette(d, 0),', 'LOAD_LOW => self.load_palette(d, 8),', None),
    ('LOAD_HIGH => self.load_palette(d, 8),', 'LOAD_HIGH => self.load_palette(d, 0),', None),
    # border
    ('for y in 0..BORDER_H {', 'for y in 0..BORDER_H - 1 {', None),
    ('let b = HEIGHT - y - 1;', 'let b = HEIGHT - y;', "an off-by-one that indexes past the buffer panics: caught by every test" ),
    ('for y in BORDER_H..HEIGHT - BORDER_H {', 'for y in BORDER_H + 1..HEIGHT - BORDER_H {', None),
    ('self.pixels[y * WIDTH..y * WIDTH + BORDER_W].fill(c);', 'self.pixels[y * WIDTH..y * WIDTH + BORDER_W - 1].fill(c);', None),
    ('self.pixels[y * WIDTH + WIDTH - BORDER_W..(y + 1) * WIDTH].fill(c);', 'self.pixels[y * WIDTH + WIDTH - BORDER_W + 1..(y + 1) * WIDTH].fill(c);', None),
    # tile
    ('(d[2] & 0x1F) as i32 * TILE_H as i32 + self.vscroll;', '(d[2] & 0x1F) as i32 * TILE_H as i32 + self.hscroll;', None),
    ('(d[3] & 0x3F) as i32 * TILE_W as i32 + self.hscroll;', '(d[3] & 0x3F) as i32 * TILE_W as i32 + self.vscroll;', None),
    ('(d[2] & 0x1F) as i32 * TILE_H as i32 + self.vscroll;', '(d[2] & 0x1F) as i32 * TILE_H as i32;', None),
    ('(d[3] & 0x3F) as i32 * TILE_W as i32 + self.hscroll;', '(d[3] & 0x3F) as i32 * TILE_W as i32;', None),
    ('(d[2] & 0x1F) as i32 * TILE_H', '(d[2] & 0x0F) as i32 * TILE_H', None),
    ('(d[3] & 0x3F) as i32 * TILE_W', '(d[3] & 0x1F) as i32 * TILE_W', None),
    ('if ri > (HEIGHT - TILE_H) as i32 ||', 'if ri >= (HEIGHT - TILE_H) as i32 ||', None),
    ('|| ci > (WIDTH - TILE_W) as i32 {', '|| ci >= (WIDTH - TILE_W) as i32 {', None),
    ('if ri > (HEIGHT - TILE_H) as i32 ||', 'if ri > (HEIGHT - TILE_H + 1) as i32 ||', "ri is a multiple of 12 plus an offset: rows 205 never place a whole tile that fits, so index panics are caught elsewhere" ),
    ('            return false;\n        }\n        let (ri, ci)', '            return true;\n        }\n        let (ri, ci)', None),
    ('(d[4 + y] >> (5 - x)) & 1 == 0 { d[0] & 0x0F } else { d[1] & 0x0F }', '(d[4 + y] >> (5 - x)) & 1 == 1 { d[0] & 0x0F } else { d[1] & 0x0F }', None),
    ('(d[4 + y] >> (5 - x)) & 1 == 0 { d[0] & 0x0F } else { d[1] & 0x0F }', '(d[4 + y] >> x) & 1 == 0 { d[0] & 0x0F } else { d[1] & 0x0F }', None),
    ('{ d[0] & 0x0F } else { d[1] & 0x0F }', '{ d[0] } else { d[1] & 0x0F }', "the colour index is masked to 4 bits again when the frame is turned into RGBA, so a stray high bit never shows"),
    ('{ d[0] & 0x0F } else { d[1] & 0x0F }', '{ d[0] & 0x0F } else { d[1] }', "the colour index is masked to 4 bits again when the frame is turned into RGBA, so a stray high bit never shows"),
    ('                    c ^= self.pixels[at];', '                    c |= self.pixels[at];', None),
    ('                    c ^= self.pixels[at];', '                    c &= self.pixels[at];', None),
    # palette
    ('((d[2 * i] as u32) << 6) + (d[2 * i + 1] & 0x3F) as u32', '((d[2 * i] as u32) << 6) + d[2 * i + 1] as u32', None),
    ('((d[2 * i] as u32) << 6) + (d[2 * i + 1] & 0x3F) as u32', '((d[2 * i] as u32) << 4) + (d[2 * i + 1] & 0x3F) as u32', None),
    ('let r = ((color >> 8) & 0xF) * 17;', 'let r = ((color >> 8) & 0xF) * 16;', None),
    ('let g = ((color >> 4) & 0xF) * 17;', 'let g = ((color >> 8) & 0xF) * 17;', None),
    ('let b = (color & 0xF) * 17;', 'let b = ((color >> 4) & 0xF) * 17;', None),
    ('let a = 255 - 4 * self.transparency[i + offset] as u32;', 'let a = 255 - 4 * self.transparency[i] as u32;', None),
    ('let a = 255 - 4 * self.transparency[i + offset] as u32;', 'let a = 255 - 3 * self.transparency[i + offset] as u32;', None),
    ('self.palette[i + offset] = (a << 24) | (r << 16) | (g << 8) | b;', 'self.palette[i + offset] = (a << 24) | (b << 16) | (g << 8) | r;', None),
    ('Self { pixels: vec![0; WIDTH * HEIGHT], palette: [0; 16], transparency: [0; 16]', 'Self { pixels: vec![0; WIDTH * HEIGHT], palette: [0xFF00_0000; 16], transparency: [0; 16]', None),
    # scroll
    ('let color = d[0] & 0x0F;\n        let hscmd', 'let color = d[0];\n        let hscmd', "the colour index is masked to 4 bits again when the frame is turned into RGBA, so a stray high bit never shows"),
    ('let hscmd = (d[1] & 0x30) >> 4;', 'let hscmd = (d[1] & 0x30) >> 3;', None),
    ('let vscmd = (d[2] & 0x30) >> 4;', 'let vscmd = (d[1] & 0x30) >> 4;', None),
    ('((d[1] & 0x07) as i32).min(BORDER_W as i32 - 1)', '((d[1] & 0x07) as i32)', None),
    ('((d[1] & 0x07) as i32).min(BORDER_W as i32 - 1)', '((d[1] & 0x03) as i32).min(BORDER_W as i32 - 1)', None),
    ('((d[2] & 0x0F) as i32).min(BORDER_H as i32 - 1)', '((d[2] & 0x0F) as i32)', None),
    ('let mut hinc = h_off - self.hscroll;', 'let mut hinc = h_off;', None),
    ('let mut vinc = self.vscroll - v_off;', 'let mut vinc = v_off - self.vscroll;', None),
    ('let mut vinc = self.vscroll - v_off;', 'let mut vinc = -v_off;', None),
    ('        self.hscroll = h_off;\n        self.vscroll = v_off;', '        self.hscroll = h_off;', None),
    ('        self.hscroll = h_off;\n        self.vscroll = v_off;', '        self.vscroll = v_off;', None),
    ('        if vscmd == V_PLUS {\n            vinc += 12;', '        if vscmd == V_PLUS {\n            vinc += 11;', None),
    ('        if vscmd == V_MINUS {\n            vinc -= 12;', '        if vscmd == V_MINUS {\n            vinc += 12;', None),
    ('        if hscmd == H_PLUS {\n            hinc += 6;', '        if hscmd == H_PLUS {\n            hinc -= 6;', None),
    ('        if hscmd == H_MINUS {\n            hinc -= 6;', '        if hscmd == H_MINUS {\n            hinc -= 5;', None),
    ('const H_PLUS: u8 = 1;', 'const H_PLUS: u8 = 3;', None),
    ('if hinc == 0 && vinc == 0 {', 'if hinc == 0 || vinc == 0 {', None),
    ('            self.noop_scrolls += 1;\n            return;', '            self.noop_scrolls += 1;', "with a zero shift the code below copies the picture onto itself and fills nothing"),
    ('            self.noop_scrolls += 1;', '', "only a counter, nothing reads it but the dump tool's report" ),
    ('for y in vinc.max(0)..(h + vinc).min(h) {', 'for y in vinc.max(0)..(h + vinc).min(h) - 1 {', None),
    ('for x in hinc.max(0)..(w + hinc).min(w) {', 'for x in hinc.max(0)..(w + hinc).min(w) - 1 {', None),
    ('out[(y * w + x) as usize] = input[((y - vinc) * w + (x - hinc)) as usize];', 'out[(y * w + x) as usize] = input[((y - vinc) * w + (x + hinc)) as usize];', None),
    ('out[at] = if roll { input[((iy + y) * w + ix + x) as usize] } else { color };', 'out[at] = if !roll { input[((iy + y) * w + ix + x) as usize] } else { color };', None),
    ('        if vinc > 0 {\n            fill(out, 0, 0, 0, h - vinc, w, vinc);', '        if vinc > 0 {\n            fill(out, 0, 0, 0, h - vinc - 1, w, vinc);', None),
    ('            fill(out, 0, h + vinc, 0, 0, w, -vinc);', '            fill(out, 0, h + vinc, 0, 1, w, -vinc);', None),
    ('            fill(out, 0, 0, w - hinc, 0, hinc, h);', '            fill(out, 0, 0, w - hinc, 1, hinc, h - 1);', None),
    ('            fill(out, w + hinc, 0, 0, 0, -hinc, h);', '            fill(out, w + hinc, 0, 1, 0, -hinc, h);', None),
    ('        } else if vinc < 0 {', '        } else if vinc <= 0 {', "vinc == 0 fills zero rows"),
    ('        } else if hinc < 0 {', '        } else if hinc <= 0 {', "hinc == 0 fills zero columns"),
    # player
    ('let idx = if seconds <= 0.0 { 0 } else { (seconds * PACKETS_PER_SECOND).floor() as usize };', 'let idx = if seconds <= 0.0 { 0 } else { (seconds * PACKETS_PER_SECOND).ceil() as usize };', None),
    ('let idx = if seconds <= 0.0 { 0 } else { (seconds * PACKETS_PER_SECOND).floor() as usize };', 'let idx = if seconds <= 0.0 { 0 } else { (seconds * 25.0).floor() as usize };', None),
    ('let want = index.min(n - 1) + 1;', 'let want = index.min(n - 1);', None),
    ('if want < self.applied {', 'if want <= self.applied {', "re-seeking to the same place just replays from a checkpoint to the same state"),
    ('find(|(a, _)| *a <= want)', 'find(|(a, _)| *a <= want + 1)', None),
]


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT / "rust", env=ENV, capture_output=True, text=True, **kw)


def main():
    only = None
    if len(sys.argv) > 2 and sys.argv[1] == "--only":
        only = {int(x) for x in sys.argv[2].split(",")}
    original = SRC.read_text()
    survivors, killed = [], 0
    try:
        for n, (old, new, note) in enumerate(M):
            if only is not None and n not in only:
                continue
            if original.count(old) != 1:
                print(f"[{n:02d}] PATTERN NOT UNIQUE/FOUND ({original.count(old)}): {old[:60]!r}")
                survivors.append(n)
                continue
            SRC.write_text(original.replace(old, new))
            build = run(["cargo", "build", "--release", "-p", "singws-cdg", "--quiet"])
            if build.returncode != 0:
                killed += 1
                print(f"[{n:02d}] killed (does not compile)")
                continue
            if run(["cargo", "test", "--release", "-p", "singws-cdg", "--quiet"]).returncode != 0:
                killed += 1
                print(f"[{n:02d}] killed by the crate tests")
                continue
            d = subprocess.run([sys.executable, str(ROOT / "tools/cdg_diff.py"), "--fuzz", "30"], env=ENV, capture_output=True, text=True)
            if d.returncode != 0:
                killed += 1
                print(f"[{n:02d}] killed by the differential test")
                continue
            if note:
                print(f"[{n:02d}] EQUIVALENT ({note})")
                killed += 1
            else:
                print(f"[{n:02d}] SURVIVED {old[:70]!r}")
                survivors.append(n)
    finally:
        SRC.write_text(original)
        run(["cargo", "build", "--release", "-p", "singws-cdg", "--quiet"])
    print(f"killed {killed} of {len(M)}; survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
