//! CD+G decoder. The app never decodes CDG in Python: libmpv does, through
//! FFmpeg's `cdgraphics` decoder. This crate reproduces that decoder's output
//! pixel for pixel, including its quirks:
//! * the fine scroll offsets also shift where later tiles land;
//! * DEFINE_TRANSPARENT only affects palettes loaded after it;
//! * colours never loaded are transparent black (alpha 0);
//! * a scroll-copy strip ignores the other axis' shift;
//! * a tile outside the picture, or a non-CDG packet, yields no frame.
//!
//! The Rust display can therefore draw the picture libmpv draws today.
//! `tools/cdg_diff.py` proves it against FFmpeg itself.
//!
//! One CDG packet is 24 bytes and lasts 1/300 s. FFmpeg emits one frame per
//! packet, so the picture at time `t` is the state after packet
//! `floor(t * 300)` (see [`CdgPlayer::render_at`]).

pub const PACKET_BYTES: usize = 24;
pub const PACKETS_PER_SECOND: f64 = 300.0;
pub const WIDTH: usize = 300;
pub const HEIGHT: usize = 216;
const BORDER_W: usize = 6;
const BORDER_H: usize = 12;
const TILE_W: usize = 6;
const TILE_H: usize = 12;

const COMMAND: u8 = 0x09;
const MASK: u8 = 0x3F;
const MEMORY_PRESET: u8 = 1;
const BORDER_PRESET: u8 = 2;
const TILE_BLOCK: u8 = 6;
const SCROLL_PRESET: u8 = 20;
const SCROLL_COPY: u8 = 24;
const DEFINE_TRANSPARENT: u8 = 28;
const LOAD_LOW: u8 = 30;
const LOAD_HIGH: u8 = 31;
const TILE_XOR: u8 = 38;
// scroll commands, named by the direction the picture moves (measured against
// FFmpeg: command 1 moves it right / down, command 2 left / up)
const H_PLUS: u8 = 1;
const H_MINUS: u8 = 2;
const V_PLUS: u8 = 1;
const V_MINUS: u8 = 2;

/// The decoder state: a 300x216 indexed frame, a 16-entry ARGB palette and the
/// two fine-scroll offsets.
#[derive(Clone)]
pub struct CdgDecoder {
    pixels: Vec<u8>,
    palette: [u32; 16],
    /// DEFINE_TRANSPARENT values (6 bits each); FFmpeg turns them into palette
    /// alpha 255 - 4*v when a palette is next loaded (not retroactively).
    transparency: [u8; 16],
    hscroll: i32,
    vscroll: i32,
    /// Scroll packets that moved nothing (FFmpeg's output for these is
    /// undefined, see `scroll`); worth logging when comparing against a show.
    pub noop_scrolls: u32,
}

impl Default for CdgDecoder {
    fn default() -> Self {
        Self::new()
    }
}

impl CdgDecoder {
    pub fn new() -> Self {
        // FFmpeg zeroes the first frame and its palette: unset colours are
        // transparent black (alpha 0), not opaque black.
        Self { pixels: vec![0; WIDTH * HEIGHT], palette: [0; 16], transparency: [0; 16], hscroll: 0, vscroll: 0, noop_scrolls: 0 }
    }

    pub fn indexed(&self) -> &[u8] {
        &self.pixels
    }

    pub fn palette(&self) -> &[u32; 16] {
        &self.palette
    }

    /// Apply one 24-byte packet. Returns whether FFmpeg would emit a frame for
    /// it: not for a packet that is not a CDG command, nor for a tile placed
    /// outside the picture (FFmpeg treats that as a decode error and drops the
    /// frame). The state is unchanged in both cases.
    pub fn apply(&mut self, packet: &[u8]) -> bool {
        if packet.len() < PACKET_BYTES || (packet[0] & MASK) != COMMAND {
            return false;
        }
        let inst = packet[1] & MASK;
        let d = &packet[4..20];
        match inst {
            MEMORY_PRESET => {
                if d[1] & 0x0F == 0 {
                    let c = d[0] & 0x0F;
                    self.pixels.iter_mut().for_each(|p| *p = c);
                }
            }
            BORDER_PRESET => {
                if d[1] & 0x0F == 0 {
                    self.border(d[0] & 0x0F)
                }
            }
            TILE_BLOCK => return self.tile(d, false),
            TILE_XOR => return self.tile(d, true),
            SCROLL_PRESET => self.scroll(d, false),
            SCROLL_COPY => self.scroll(d, true),
            DEFINE_TRANSPARENT => {
                for i in 0..16 {
                    self.transparency[i] = d[i] & 0x3F;
                }
            }
            LOAD_LOW => self.load_palette(d, 0),
            LOAD_HIGH => self.load_palette(d, 8),
            _ => {}
        }
        true
    }

    fn border(&mut self, c: u8) {
        for y in 0..BORDER_H {
            self.pixels[y * WIDTH..(y + 1) * WIDTH].fill(c);
            let b = HEIGHT - y - 1;
            self.pixels[b * WIDTH..(b + 1) * WIDTH].fill(c);
        }
        for y in BORDER_H..HEIGHT - BORDER_H {
            self.pixels[y * WIDTH..y * WIDTH + BORDER_W].fill(c);
            self.pixels[y * WIDTH + WIDTH - BORDER_W..(y + 1) * WIDTH].fill(c);
        }
    }

    fn tile(&mut self, d: &[u8], xor: bool) -> bool {
        // FFmpeg adds the fine-scroll offsets to the tile position (note the
        // offsets: the horizontal one moves columns, the vertical one rows).
        let ri = (d[2] & 0x1F) as i32 * TILE_H as i32 + self.vscroll;
        let ci = (d[3] & 0x3F) as i32 * TILE_W as i32 + self.hscroll;
        if ri > (HEIGHT - TILE_H) as i32 || ci > (WIDTH - TILE_W) as i32 {
            return false;
        }
        let (ri, ci) = (ri as usize, ci as usize);
        for y in 0..TILE_H {
            for x in 0..TILE_W {
                let mut c = if (d[4 + y] >> (5 - x)) & 1 == 0 { d[0] & 0x0F } else { d[1] & 0x0F };
                let at = ci + x + WIDTH * (ri + y);
                if xor {
                    c ^= self.pixels[at];
                }
                self.pixels[at] = c;
            }
        }
        true
    }

    fn load_palette(&mut self, d: &[u8], offset: usize) {
        for i in 0..8 {
            let color = ((d[2 * i] as u32) << 6) + (d[2 * i + 1] & 0x3F) as u32;
            let r = ((color >> 8) & 0xF) * 17;
            let g = ((color >> 4) & 0xF) * 17;
            let b = (color & 0xF) * 17;
            let a = 255 - 4 * self.transparency[i + offset] as u32;
            self.palette[i + offset] = (a << 24) | (r << 16) | (g << 8) | b;
        }
    }

    fn scroll(&mut self, d: &[u8], roll: bool) {
        let color = d[0] & 0x0F;
        let hscmd = (d[1] & 0x30) >> 4;
        let vscmd = (d[2] & 0x30) >> 4;
        let h_off = ((d[1] & 0x07) as i32).min(BORDER_W as i32 - 1);
        let v_off = ((d[2] & 0x0F) as i32).min(BORDER_H as i32 - 1);
        let mut hinc = h_off - self.hscroll;
        let mut vinc = self.vscroll - v_off;
        self.hscroll = h_off;
        self.vscroll = v_off;
        if vscmd == V_PLUS {
            vinc += 12;
        }
        if vscmd == V_MINUS {
            vinc -= 12;
        }
        if hscmd == H_PLUS {
            hinc += 6;
        }
        if hscmd == H_MINUS {
            hinc -= 6;
        }
        if hinc == 0 && vinc == 0 {
            // FFmpeg swaps in a brand-new frame buffer here and returns before
            // filling it, so what it shows depends on recycled memory (all
            // zeros when fresh, an older frame otherwise). That is not
            // reproducible, so the picture is kept and the event is counted;
            // tools/cdg_diff.py never feeds such a packet to FFmpeg.
            self.noop_scrolls += 1;
            return;
        }
        let (w, h) = (WIDTH as i32, HEIGHT as i32);
        let input = self.pixels.clone();
        let out = &mut self.pixels;
        // body: logical shift of the old frame by (hinc, vinc)
        for y in vinc.max(0)..(h + vinc).min(h) {
            for x in hinc.max(0)..(w + hinc).min(w) {
                out[(y * w + x) as usize] = input[((y - vinc) * w + (x - hinc)) as usize];
            }
        }
        let fill = |out: &mut Vec<u8>, ox: i32, oy: i32, ix: i32, iy: i32, rw: i32, rh: i32| {
            for y in 0..rh {
                for x in 0..rw {
                    let at = ((oy + y) * w + ox + x) as usize;
                    out[at] = if roll { input[((iy + y) * w + ix + x) as usize] } else { color };
                }
            }
        };
        if vinc > 0 {
            fill(out, 0, 0, 0, h - vinc, w, vinc);
        } else if vinc < 0 {
            fill(out, 0, h + vinc, 0, 0, w, -vinc);
        }
        if hinc > 0 {
            fill(out, 0, 0, w - hinc, 0, hinc, h);
        } else if hinc < 0 {
            fill(out, w + hinc, 0, 0, 0, -hinc, h);
        }
    }

    /// RGBA, 300x216, row-major. Straight (non-premultiplied) alpha taken from
    /// the palette: colours never loaded are alpha 0, loaded ones alpha 255.
    pub fn to_rgba(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(WIDTH * HEIGHT * 4);
        self.write_rgba(&mut out);
        out
    }

    pub fn write_rgba(&self, out: &mut Vec<u8>) {
        for &p in &self.pixels {
            let c = self.palette[(p & 0x0F) as usize];
            out.extend_from_slice(&[(c >> 16) as u8, (c >> 8) as u8, c as u8, (c >> 24) as u8]);
        }
    }
}

/// The app's `_clean_cdg_near_black` on an RGBA frame: pixels whose R, G and B are all <= `threshold` (clamped to 0..=32) and whose alpha is
/// non-zero are cleared. With `transparent_black` they become fully transparent (all four bytes 0); otherwise pure-black pixels are left alone
/// and near-black ones lose their RGB but keep their alpha. A threshold of 0 without `transparent_black` changes nothing.
pub fn clean_near_black(rgba: &mut [u8], threshold: i32, transparent_black: bool) {
    let t = threshold.clamp(0, 32) as u8;
    if t == 0 && !transparent_black {
        return;
    }
    for p in rgba.chunks_exact_mut(4) {
        let (r, g, b, a) = (p[0], p[1], p[2], p[3]);
        if a > 0 && r <= t && g <= t && b <= t && (transparent_black || r | g | b != 0) {
            if transparent_black {
                p.fill(0);
            } else {
                p[..3].fill(0);
            }
        }
    }
}

/// The app's `_cdg_side_fill_color`: the most common opaque edge colour, sampled at 13 points along each edge (ties go to the first seen),
/// black when every sample is transparent.
pub fn side_fill_color(rgba: &[u8], width: usize, height: usize) -> (u8, u8, u8) {
    let px = |x: usize, y: usize| {
        let o = (y * width + x) * 4;
        (rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3])
    };
    let mut counts: Vec<((u8, u8, u8), u32)> = Vec::new();
    let steps = 12usize;
    for i in 0..=steps {
        // Python's round() is half-to-even; the products here never land on .5 for 300x216 but keep the rule exact
        let rnd = |n: usize| -> usize {
            let v = n as f64 / steps as f64;
            let f = v.floor();
            let d = v - f;
            (if d > 0.5 || (d == 0.5 && (f as i64) % 2 != 0) { f + 1.0 } else { f }) as usize
        };
        let x = rnd((width - 1) * i).min(width - 1);
        let y = rnd((height - 1) * i).min(height - 1);
        for (r, g, b, a) in [px(x, 0), px(x, height - 1), px(0, y), px(width - 1, y)] {
            if a == 0 {
                continue;
            }
            match counts.iter_mut().find(|(k, _)| *k == (r, g, b)) {
                Some(e) => e.1 += 1,
                None => counts.push(((r, g, b), 1)),
            }
        }
    }
    let mut best: Option<((u8, u8, u8), u32)> = None;
    for e in counts {
        if best.is_none_or(|b| e.1 > b.1) {
            best = Some(e);
        }
    }
    best.map_or((0, 0, 0), |b| b.0)
}

/// How the app's display window lays the 300x216 picture into the window (`cdg_display_mode`): `Fit` on black, `SideFill` on the picture's own
/// edge colour, `Stretch` to the whole window. (`blur` is not ported: it needs Qt's smooth scaling.)
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum DisplayMode {
    Fit,
    SideFill,
    Stretch,
}

/// Qt's `QSize::scaled(KeepAspectRatio)`: integer truncation, width first.
pub fn fit_size(iw: usize, ih: usize, w: usize, h: usize) -> (usize, usize) {
    let rw = h as u64 * iw as u64 / ih as u64;
    if rw <= w as u64 { (rw as usize, h) } else { (w, (w as u64 * ih as u64 / iw as u64) as usize) }
}

/// Qt's `QImage::scaled(.., FastTransformation)` for an upscale: nearest neighbour in 16.16 fixed point, step = floor(65536*src/dst), starting
/// half a step in. Verified identical to Qt on 8 window sizes (see tools/cdg_display_parity.py).
pub fn scale_nearest(src: &[u8], iw: usize, ih: usize, w: usize, h: usize) -> Vec<u8> {
    let axis = |n: usize, d: usize| -> Vec<usize> {
        let step = (65536 * n) / d;
        (0..d).map(|i| (((step / 2) + i * step) >> 16).min(n - 1)).collect()
    };
    let (xs, ys) = (axis(iw, w), axis(ih, h));
    let mut out = Vec::with_capacity(w * h * 4);
    for &sy in &ys {
        for &sx in &xs {
            let o = (sy * iw + sx) * 4;
            out.extend_from_slice(&src[o..o + 4]);
        }
    }
    out
}

/// The picture as the display window draws it: `frame` (300x216 RGBA, already cleaned if the app cleans it) scaled and centred in a
/// `win_w` x `win_h` opaque RGBA window. Source-over compositing; exact for alpha 0 and 255 (the decoder's only values unless
/// DEFINE_TRANSPARENT is used), rounded for in-between alphas.
pub fn compose(frame: &[u8], win_w: usize, win_h: usize, mode: DisplayMode) -> Vec<u8> {
    let (iw, ih) = (WIDTH, HEIGHT);
    let (sw, sh) = if mode == DisplayMode::Stretch { (win_w, win_h) } else { fit_size(iw, ih, win_w, win_h) };
    let bg = if mode == DisplayMode::SideFill { side_fill_color(frame, iw, ih) } else { (0, 0, 0) };
    let scaled = scale_nearest(frame, iw, ih, sw, sh);
    let mut out = Vec::with_capacity(win_w * win_h * 4);
    for _ in 0..win_w * win_h {
        out.extend_from_slice(&[bg.0, bg.1, bg.2, 255]);
    }
    let (x0, y0) = ((win_w - sw) / 2, (win_h - sh) / 2);
    for y in 0..sh {
        for x in 0..sw {
            let s = &scaled[(y * sw + x) * 4..][..4];
            let d = ((y0 + y) * win_w + x0 + x) * 4;
            let a = s[3] as u32;
            if a == 255 {
                out[d..d + 3].copy_from_slice(&s[..3]);
            } else if a > 0 {
                for c in 0..3 {
                    out[d + c] = ((s[c] as u32 * a + out[d + c] as u32 * (255 - a) + 127) / 255) as u8;
                }
            }
        }
    }
    out
}

/// A whole CDG file with seek by time.
pub struct CdgPlayer {
    data: Vec<u8>,
    state: CdgDecoder,
    /// packets applied so far
    applied: usize,
    /// (packets applied, state) every CHECK packets, for cheap backward seeks
    checkpoints: Vec<(usize, CdgDecoder)>,
}

const CHECK: usize = 300;

impl CdgPlayer {
    pub fn new(data: Vec<u8>) -> Self {
        Self { data, state: CdgDecoder::new(), applied: 0, checkpoints: vec![(0, CdgDecoder::new())] }
    }

    pub fn packet_count(&self) -> usize {
        self.data.len() / PACKET_BYTES
    }

    pub fn duration_seconds(&self) -> f64 {
        self.packet_count() as f64 / PACKETS_PER_SECOND
    }

    /// Move to the state after packet `index` (clamped to the last packet);
    /// returns the decoder.
    pub fn seek_packet(&mut self, index: usize) -> &CdgDecoder {
        let n = self.packet_count();
        if n == 0 {
            return &self.state;
        }
        let want = index.min(n - 1) + 1; // packets to have applied
        if want < self.applied {
            let (at, snap) = self.checkpoints.iter().rev().find(|(a, _)| *a <= want).cloned().expect("checkpoint 0");
            self.state = snap;
            self.applied = at;
        }
        while self.applied < want {
            let o = self.applied * PACKET_BYTES;
            self.state.apply(&self.data[o..o + PACKET_BYTES]);
            self.applied += 1;
            if self.applied % CHECK == 0 && !self.checkpoints.iter().any(|(a, _)| *a == self.applied) {
                self.checkpoints.push((self.applied, self.state.clone()));
            }
        }
        &self.state
    }

    /// The picture at `seconds` (negative = start; past the end = last frame).
    pub fn render_at(&mut self, seconds: f64) -> &CdgDecoder {
        let idx = if seconds <= 0.0 { 0 } else { (seconds * PACKETS_PER_SECOND).floor() as usize };
        self.seek_packet(idx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pk(inst: u8, data: &[u8]) -> Vec<u8> {
        let mut p = vec![COMMAND, inst, 0, 0];
        let mut d = data.to_vec();
        d.resize(16, 0);
        p.extend(d);
        p.extend([0; 4]);
        p
    }

    #[test]
    fn clean_and_side_fill_basics() {
        let mut f = vec![0, 0, 0, 255, 5, 5, 5, 200, 40, 40, 40, 255, 3, 3, 3, 0];
        clean_near_black(&mut f, 10, false);
        assert_eq!(f, vec![0, 0, 0, 255, 0, 0, 0, 200, 40, 40, 40, 255, 3, 3, 3, 0]);
        clean_near_black(&mut f, 10, true);
        assert_eq!(&f[..8], &[0; 8]);
        let blank = vec![0u8; 300 * 216 * 4];
        assert_eq!(side_fill_color(&blank, 300, 216), (0, 0, 0));
    }

    #[test]
    fn unset_palette_is_transparent_black() {
        let d = CdgDecoder::new();
        assert_eq!(&d.to_rgba()[..4], &[0, 0, 0, 0]);
    }

    #[test]
    fn palette_and_preset() {
        let mut d = CdgDecoder::new();
        d.apply(&pk(LOAD_LOW, &[0, 0, 0x3F, 0x3F, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]));
        d.apply(&pk(MEMORY_PRESET, &[1]));
        assert_eq!(&d.to_rgba()[..4], &[255, 255, 255, 255]);
    }

    #[test]
    fn xor_tile_and_oob_tile() {
        let mut d = CdgDecoder::new();
        d.apply(&pk(MEMORY_PRESET, &[5]));
        let mut t = vec![0, 3, 0, 0];
        t.extend([0b111111; 12]);
        d.apply(&pk(TILE_XOR, &t));
        assert_eq!(d.indexed()[0], 5 ^ 3);
        let mut t2 = vec![0, 3, 31, 63];
        t2.extend([0b111111; 12]);
        d.apply(&pk(TILE_BLOCK, &t2)); // row 31*12 > 204: dropped
        assert_eq!(d.indexed()[WIDTH * HEIGHT - 1], 5);
    }

    #[test]
    fn player_seeks_backwards_identically() {
        let mut data = Vec::new();
        for i in 0..700u32 {
            let mut t = vec![(i % 16) as u8, ((i + 5) % 16) as u8, (i % 18) as u8, (i % 50) as u8];
            t.extend([(i * 7) as u8 & 0x3F; 12]);
            data.extend(pk(TILE_BLOCK, &t));
        }
        let mut a = CdgPlayer::new(data.clone());
        let mut b = CdgPlayer::new(data);
        let fwd = a.seek_packet(650).indexed().to_vec();
        a.seek_packet(10);
        assert_eq!(a.seek_packet(650).indexed(), &fwd[..]);
        assert_eq!(b.render_at(650.0 / 300.0 + 0.0001).indexed(), &fwd[..]);
        // just before a checkpoint (299 packets applied of a checkpoint at 300)
        let mut fresh = CdgPlayer::new(a.data.clone());
        let want = fresh.seek_packet(298).indexed().to_vec();
        assert_eq!(a.seek_packet(298).indexed(), &want[..]);
    }
}
