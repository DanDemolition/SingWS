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

pub mod window_rules;
pub mod track_display;

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
        // Python's round() is half-to-even. For 300x216 the 7th sample lands exactly on 149.5 and 107.5 (both floors odd, so both round up);
        // other sizes hit k.5 with an even k (tests cover 302, 306, 308)
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

/// Qt's `QImage::scaled(.., FastTransformation)`: nearest neighbour in 16.16 fixed point, step = floor(65536*src/dst), starting half a step in
/// and one fixed-point unit short of it, so an exact tie (a downscale by a whole ratio such as 300 -> 150) takes the lower texel like Qt does.
/// Identical to Qt on 598 source/destination length pairs, up and down (checked one axis at a time; tools/cdg_display_parity.py checks pictures).
pub fn scale_nearest(src: &[u8], iw: usize, ih: usize, w: usize, h: usize) -> Vec<u8> {
    let axis = |n: usize, d: usize| -> Vec<usize> {
        let step = (65536 * n) / d;
        (0..d).map(|i| (((step / 2) + i * step).saturating_sub(1) >> 16).min(n - 1)).collect()
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

/// The widescreen fill the video bridge's shader draws behind the picture (`singws_bridge_set_sidefill`; the app's `cdg_display_mode`
/// sidefill = 1, blur = 2). This is what the audience sees by default; it replaces the painter-only `DisplayMode::SideFill` for the live look.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum BridgeFill {
    Off,
    /// 1: the corner sample whose colour is closest to the other three
    Colour,
    /// 2: a 5x5 weighted blur of the picture, cover-cropped, dimmed to 88 percent
    Blur,
}

fn texel(src: &[u8], x: i32, y: i32) -> [f32; 3] {
    let (x, y) = (x.clamp(0, WIDTH as i32 - 1) as usize, y.clamp(0, HEIGHT as i32 - 1) as usize);
    let o = (y * WIDTH + x) * 4;
    [src[o] as f32 / 255.0, src[o + 1] as f32 / 255.0, src[o + 2] as f32 / 255.0]
}

/// GL_LINEAR with clamp-to-edge on the 300x216 picture; `u` across, `v` down (0 = top row).
fn bilinear(src: &[u8], u: f32, v: f32) -> [f32; 3] {
    let (fx, fy) = (u * WIDTH as f32 - 0.5, v * HEIGHT as f32 - 0.5);
    let (x0, y0) = (fx.floor(), fy.floor());
    let (tx, ty) = (fx - x0, fy - y0);
    let (x0, y0) = (x0 as i32, y0 as i32);
    let (a, b, c, d) = (texel(src, x0, y0), texel(src, x0 + 1, y0), texel(src, x0, y0 + 1), texel(src, x0 + 1, y0 + 1));
    let mut o = [0.0; 3];
    for i in 0..3 {
        o[i] = (a[i] * (1.0 - tx) + b[i] * tx) * (1.0 - ty) + (c[i] * (1.0 - tx) + d[i] * tx) * ty;
    }
    o
}

fn dist(a: [f32; 3], b: [f32; 3]) -> f32 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt()
}

fn smoothstep(e0: f32, e1: f32, x: f32) -> f32 {
    let t = ((x - e0) / (e1 - e0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// The output window as the Rust video bridge's shader draws a CDG song (RGB only: the shader ignores the picture's alpha). Mirrors
/// `draw_picture` / the `cdgSidefill` branches in rust/crates/singws-video: a full-window background pass (cover-cropped copy of the picture
/// for the fills), then the fit picture with its left and right 6/300 edges faded into the background.
pub fn compose_bridge(frame: &[u8], win_w: usize, win_h: usize, fill: BridgeFill) -> Vec<u8> {
    let (w, h) = (win_w as f32, win_h as f32);
    let sa = WIDTH as f32 / HEIGHT as f32;
    let va = w / h;
    // background-pass uv window: cover (crop) the window
    let (buvx, buvy, buox, buoy) = if va > sa { (1.0, sa / va, 0.0, (1.0 - sa / va) * 0.5) } else { (va / sa, 1.0, (1.0 - va / sa) * 0.5, 0.0) };
    // fit quad half-sizes in NDC
    let (sx, sy) = if va > sa { (sa / va, 1.0) } else { (1.0, va / sa) };
    let corner = |lx: f32, ty: f32, by: f32, rx: f32| [bilinear(frame, lx, ty), bilinear(frame, rx, ty), bilinear(frame, lx, by), bilinear(frame, rx, by)];
    let colour_fill = {
        let c = corner(3.0 / 300.0, 6.0 / 216.0, 210.0 / 216.0, 297.0 / 300.0);
        let sums: Vec<f32> = (0..4).map(|i| (0..4).filter(|&j| j != i).map(|j| dist(c[i], c[j])).sum()).collect();
        let (mut k, mut best) = (0, sums[0]);
        for i in 1..4 {
            if sums[i] < best {
                k = i;
                best = sums[i];
            }
        }
        c[k]
    };
    let to8 = |v: f32| (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8;
    let mut out = Vec::with_capacity(win_w * win_h * 4);
    for py in 0..win_h {
        for px in 0..win_w {
            // window uv: x right, y DOWN
            let (ux, uy) = ((px as f32 + 0.5) / w, (py as f32 + 0.5) / h);
            let mut rgb = [0.0f32; 3];
            if fill != BridgeFill::Off {
                let (u, v) = (ux * buvx + buox, uy * buvy + buoy);
                rgb = match fill {
                    BridgeFill::Colour => colour_fill,
                    BridgeFill::Blur => {
                        let (mut acc, mut ws) = ([0.0f32; 3], 0.0f32);
                        for i in -2i32..=2 {
                            for j in -2i32..=2 {
                                let c = bilinear(frame, (u + i as f32 * 0.025).clamp(0.005, 0.995), (v + j as f32 * 0.035).clamp(0.0, 1.0));
                                let wt = 1.0 / (1.0 + 0.06 * (i * i + j * j) as f32);
                                for k in 0..3 {
                                    acc[k] += c[k] * wt;
                                }
                                ws += wt;
                            }
                        }
                        [acc[0] / ws * 0.88, acc[1] / ws * 0.88, acc[2] / ws * 0.88]
                    }
                    BridgeFill::Off => unreachable!(),
                };
            }
            // foreground: the fit quad, nearest sampling
            let (nx, ny) = (ux * 2.0 - 1.0, 1.0 - uy * 2.0);
            if nx.abs() <= sx && ny.abs() <= sy {
                let (fu, fv) = ((nx / sx + 1.0) * 0.5, (1.0 - ny / sy) * 0.5);
                let (tx, ty) = (((fu * WIDTH as f32) as usize).min(WIDTH - 1), ((fv * HEIGHT as f32) as usize).min(HEIGHT - 1));
                let o = (ty * WIDTH + tx) * 4;
                let fg = [frame[o] as f32 / 255.0, frame[o + 1] as f32 / 255.0, frame[o + 2] as f32 / 255.0];
                let a = if fill == BridgeFill::Off { 1.0 } else { smoothstep(0.0, 6.0 / 300.0, fu) * smoothstep(0.0, 6.0 / 300.0, 1.0 - fu) };
                for k in 0..3 {
                    rgb[k] = fg[k] * a + rgb[k] * (1.0 - a);
                }
            }
            out.extend_from_slice(&[to8(rgb[0]), to8(rgb[1]), to8(rgb[2]), 255]);
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

    /// The picture for an AUDIBLE position and the CDG timing offset (the app's `_effective_cdg_timing_offset_ms`: base 750 ms plus the
    /// user's fine tuning): the display shows the picture of `position + offset`, a positive offset showing lyrics earlier. Measured against
    /// the real bridge (tools/cdg_clock_probe.py): a paused seek shows the packet at the position whatever the offset or tempo; while playing
    /// the picture runs `offset` ahead of the reported position and about 50 ms behind it (render and grab lag, not modelled); tempo changes
    /// nothing because the position is media time.
    pub fn render_at_position(&mut self, position_ms: f64, offset_ms: f64) -> &CdgDecoder {
        self.render_at((position_ms + offset_ms) / 1000.0)
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
    fn position_plus_offset_picks_the_packet() {
        let mut data = Vec::new();
        for i in 0..900u32 {
            let mut t = vec![0, 1 + (i % 15) as u8, (i % 18) as u8, (i % 50) as u8];
            t.extend([0xFF; 12]);
            data.extend(pk(TILE_XOR, &t));
        }
        let mut a = CdgPlayer::new(data.clone());
        let mut b = CdgPlayer::new(data);
        let by_offset = a.render_at_position(1000.0, 750.0).indexed().to_vec();
        assert_eq!(b.render_at(1.75).indexed(), &by_offset[..]);
        assert_eq!(b.seek_packet(525).indexed(), &by_offset[..]);
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

    /// A deterministic 300x216 test picture with distinct corners, a gradient and hard edges.
    fn sample_frame() -> Vec<u8> {
        let mut f = Vec::with_capacity(WIDTH * HEIGHT * 4);
        for y in 0..HEIGHT {
            for x in 0..WIDTH {
                let corner = match (x < 150, y < 108) {
                    (true, true) => [200, 30, 30],
                    (false, true) => [30, 200, 30],
                    (true, false) => [30, 30, 200],
                    (false, false) => [220, 220, 40],
                };
                let edge = x < 6 || x >= WIDTH - 6 || y < 6 || y >= HEIGHT - 6;
                let rgb = if edge { [corner[2], corner[0], corner[1]] } else { [corner[0] / 2 + (x % 100) as u8, corner[1] / 2 + (y % 100) as u8, corner[2] / 2 + ((x + y) % 60) as u8] };
                f.extend_from_slice(&[rgb[0], rgb[1], rgb[2], 255]);
            }
        }
        f
    }

    fn fnv(b: &[u8]) -> u64 {
        b.iter().fold(0xcbf2_9ce4_8422_2325u64, |h, &c| (h ^ c as u64).wrapping_mul(0x0000_0100_0000_01b3))
    }

    #[test]
    fn fit_size_truncates_like_qt() {
        assert_eq!(fit_size(300, 216, 1280, 720), (1000, 720));
        assert_eq!(fit_size(300, 216, 500, 400), (500, 360));
        assert_eq!(fit_size(300, 216, 901, 333), (462, 333));
        assert_eq!(fit_size(300, 216, 300, 216), (300, 216));
        // exactly as wide as allowed: the height branch is taken (Qt: rw <= w), not the width one (which would give 216)
        assert_eq!(fit_size(300, 216, 301, 217), (301, 217));
        // width-limited: the height is truncated, not rounded
        assert_eq!(fit_size(300, 216, 301, 1000), (301, 216));
    }

    #[test]
    fn scale_nearest_is_the_fixed_point_step() {
        // 4 -> 10: step = floor(65536*4/10) = 26214; sample i is (13107 + i*26214) >> 16
        let src: Vec<u8> = (0..4u8).flat_map(|v| [v, v, v, 255]).collect();
        let out = scale_nearest(&src, 4, 1, 10, 1);
        let got: Vec<u8> = out.chunks(4).map(|p| p[0]).collect();
        assert_eq!(got, vec![0, 0, 0, 1, 1, 2, 2, 2, 3, 3]);
        // the last sample is clamped to the source
        let one = scale_nearest(&src, 4, 1, 4, 1);
        assert_eq!(one, src);
        // rows use the same rule
        let col: Vec<u8> = (0..4u8).flat_map(|v| [v, 0, 0, 255]).collect();
        let tall = scale_nearest(&col, 1, 4, 1, 10);
        assert_eq!(tall.chunks(4).map(|p| p[0]).collect::<Vec<_>>(), vec![0, 0, 0, 1, 1, 2, 2, 2, 3, 3]);
    }

    #[test]
    fn scale_nearest_downscale_ties_take_the_lower_texel_like_qt() {
        // 300 -> 150: sample positions are (i + 0.5) * 2 = 1, 3, 5 ... exactly on a texel boundary; Qt takes 0, 2, 4
        let src: Vec<u8> = (0..300u32).flat_map(|v| [(v % 256) as u8, (v / 256) as u8, 0, 255]).collect();
        let out = scale_nearest(&src, 300, 1, 150, 1);
        let got: Vec<u32> = out.chunks(4).map(|p| p[0] as u32 + 256 * p[1] as u32).collect();
        assert_eq!(&got[..5], &[0, 2, 4, 6, 8]);
        // 216 -> 18: 12 * i + 5 (exact ratio 12, Qt) and the last sample is still inside the picture
        let col: Vec<u8> = (0..216u32).flat_map(|v| [v as u8, 0, 0, 255]).collect();
        let out = scale_nearest(&col, 1, 216, 1, 18);
        let got: Vec<u32> = out.chunks(4).map(|p| p[0] as u32).collect();
        assert_eq!(&got[..4], &[5, 17, 29, 41]);
        assert_eq!(got[17], 209);
        // 300 -> 1 and 1 -> 300 do not read outside the picture
        assert_eq!(scale_nearest(&src, 300, 1, 1, 1).len(), 4);
        assert_eq!(scale_nearest(&src[..4], 1, 1, 300, 1).len(), 300 * 4);
    }

    #[test]
    fn scale_nearest_offset_is_exactly_one_unit() {
        // 300 -> 463: output 302 sits where (step/2 + 302*step) has 1 in the low bits; Qt picks texel 196 (an offset of 2 would give 195)
        let src: Vec<u8> = (0..300u32).flat_map(|v| [(v % 256) as u8, (v / 256) as u8, 0, 255]).collect();
        let out = scale_nearest(&src, 300, 1, 463, 1);
        let p = &out[302 * 4..][..4];
        assert_eq!(p[0] as u32 + 256 * p[1] as u32, 196);
        let out = scale_nearest(&src, 300, 1, 634, 1);
        let p = &out[568 * 4..][..4];
        assert_eq!(p[0] as u32 + 256 * p[1] as u32, 269);
    }

    #[test]
    fn compose_centres_with_floor_on_both_axes() {
        // 305x300: the picture is 305 x 219 (width-limited), so 81 spare rows: 40 above, 41 below (floor); 301x216... see the next window
        let mut f = vec![0u8; WIDTH * HEIGHT * 4];
        for p in f.chunks_exact_mut(4) {
            p.copy_from_slice(&[90, 91, 92, 255]);
        }
        let out = compose(&f, 305, 300, DisplayMode::Fit);
        let row = |y: usize| &out[(y * 305 + 150) * 4..][..4];
        assert_eq!(row(39), &[0, 0, 0, 255]);
        assert_eq!(row(40), &[90, 91, 92, 255]);
        assert_eq!(row(258), &[90, 91, 92, 255]);
        assert_eq!(row(259), &[0, 0, 0, 255]);
        // 901x333: the picture is 462 wide, 439 spare columns: 219 left, 220 right
        let out = compose(&f, 901, 333, DisplayMode::Fit);
        let col = |x: usize| &out[(100 * 901 + x) * 4..][..4];
        assert_eq!(col(218), &[0, 0, 0, 255]);
        assert_eq!(col(219), &[90, 91, 92, 255]);
        assert_eq!(col(680), &[90, 91, 92, 255]);
        assert_eq!(col(681), &[0, 0, 0, 255]);
    }

    #[test]
    fn compose_partial_alpha_on_a_bright_background() {
        // background (200, 220, 240): the (255 - a) weight is large enough here that a wrong weight changes the rounded result
        let mut f = vec![0u8; WIDTH * HEIGHT * 4];
        for p in f.chunks_exact_mut(4) {
            p.copy_from_slice(&[200, 220, 240, 255]);
        }
        for (x, px) in [[10u8, 20, 30, 100], [250, 5, 0, 200], [0, 0, 0, 3], [255, 255, 255, 128]].iter().enumerate() {
            f[(100 * WIDTH + 100 + x) * 4..][..4].copy_from_slice(px);
        }
        let out = compose(&f, 2 * WIDTH, HEIGHT, DisplayMode::SideFill);
        let at = |x: usize| &out[(100 * 2 * WIDTH + WIDTH / 2 + 100 + x) * 4..][..4];
        assert_eq!(at(0), &[125, 142, 158, 255][..]);
        assert_eq!(at(1), &[239, 51, 52, 255][..]);
        assert_eq!(at(2), &[198, 217, 237, 255][..]);
        assert_eq!(at(3), &[228, 238, 248, 255][..]);
    }

    #[test]
    fn compose_blends_partial_alpha_and_centres() {
        // one red pixel with alpha 128 on the black window, then on the side-fill colour
        let mut f = vec![0u8; WIDTH * HEIGHT * 4];
        for p in f.chunks_exact_mut(4) {
            p.copy_from_slice(&[10, 20, 30, 255]);
        }
        for p in f.chunks_exact_mut(4).take(WIDTH) {
            p.copy_from_slice(&[255, 0, 0, 128]);
        }
        let out = compose(&f, WIDTH, HEIGHT, DisplayMode::Fit);
        assert_eq!(&out[..4], &[128, 0, 0, 255]);
        assert_eq!(&out[4 * WIDTH..4 * WIDTH + 4], &[10, 20, 30, 255]);
        let mut g = f.clone();
        for p in g.chunks_exact_mut(4).take(WIDTH) {
            p[3] = 0;
        }
        assert_eq!(&compose(&g, WIDTH, HEIGHT, DisplayMode::Fit)[..4], &[0, 0, 0, 255]);
        // a wide window: the picture is centred, bars are the window background
        let wide = compose(&f, 2 * WIDTH, HEIGHT, DisplayMode::Fit);
        assert_eq!(&wide[..4], &[0, 0, 0, 255]);
        let x0 = (2 * WIDTH - WIDTH) / 2;
        assert_eq!(&wide[(HEIGHT / 2 * 2 * WIDTH + x0) * 4..][..4], &[10, 20, 30, 255]);
        assert_eq!(&wide[(HEIGHT / 2 * 2 * WIDTH + x0 - 1) * 4..][..4], &[0, 0, 0, 255]);
        let sf = compose(&f, 2 * WIDTH, HEIGHT, DisplayMode::SideFill);
        assert_eq!(&sf[..4], &[10, 20, 30, 255]);
        // stretch fills the whole window
        let st = compose(&f, 2 * WIDTH, HEIGHT, DisplayMode::Stretch);
        assert_eq!(&st[(HEIGHT / 2 * 2 * WIDTH) * 4..][..4], &[10, 20, 30, 255]);
    }

    #[test]
    fn side_fill_picks_the_most_common_edge_colour_first_seen_on_ties() {
        let mut f = vec![0u8; WIDTH * HEIGHT * 4];
        for p in f.chunks_exact_mut(4) {
            p.copy_from_slice(&[1, 2, 3, 255]);
        }
        assert_eq!(side_fill_color(&f, WIDTH, HEIGHT), (1, 2, 3));
        // left edge a different colour: top and bottom (2 of 4 edges, 13 samples each) are still the majority
        for y in 0..HEIGHT {
            f[(y * WIDTH) * 4..][..4].copy_from_slice(&[9, 9, 9, 255]);
        }
        assert_eq!(side_fill_color(&f, WIDTH, HEIGHT), (1, 2, 3));
        // transparent edge samples do not count
        for p in f.chunks_exact_mut(4) {
            p[3] = 0;
        }
        assert_eq!(side_fill_color(&f, WIDTH, HEIGHT), (0, 0, 0));
    }

    /// Pinned output of `compose_bridge`. There is no CPU reference for the GL shader, so these hashes pin the Rust port as it was when it was
    /// compared on screen with the real audience window (0 pixels beyond tolerance, fit / colour / blur, landscape and portrait). A change to
    /// the picture maths must come with a fresh on-screen comparison (tools/cdg_window_compare.py) and new hashes.
    #[test]
    fn compose_bridge_is_pinned() {
        let f = sample_frame();
        let mut got = Vec::new();
        for (w, h) in [(640usize, 360usize), (400, 500), (300, 216), (211, 97)] {
            for fill in [BridgeFill::Off, BridgeFill::Colour, BridgeFill::Blur] {
                got.push(fnv(&compose_bridge(&f, w, h, fill)));
            }
        }
        let want: Vec<u64> = vec![
            14048955622114239725, 847979852600862693, 11333714022539983672, 15274494003484929749, 6478389082910589469, 11922220376167693918, 4320324006446734613, 7532078087554515445, 11445142463088536617, 2351742098073275432, 11264101343107794192, 9116975756021995183,
        ];
        assert_eq!(got, want);
    }

    #[test]
    fn compose_bridge_properties() {
        let f = sample_frame();
        // Off: letterbox bars are black, the picture is the nearest-scaled frame
        let off = compose_bridge(&f, 640, 360, BridgeFill::Off);
        assert_eq!(&off[..4], &[0, 0, 0, 255]);
        let mid = (180 * 640 + 320) * 4;
        assert_eq!(&off[mid + 3..mid + 4], &[255]);
        // Colour: the bars take the colour of the corner sample closest to the other three
        let col = compose_bridge(&f, 640, 360, BridgeFill::Colour);
        assert_ne!(&col[..3], &[0, 0, 0]);
        // Blur is dimmed: a flat white picture fills the bars at 88 percent
        let white: Vec<u8> = std::iter::repeat([255u8, 255, 255, 255]).take(WIDTH * HEIGHT).flatten().collect();
        let b = compose_bridge(&white, 640, 360, BridgeFill::Blur);
        assert_eq!(&b[..3], &[224, 224, 224]);
        // and a flat picture's centre stays white
        assert_eq!(&b[mid..mid + 3], &[255, 255, 255]);
    }

    /// All-transparent `w` x `h` frame with the given opaque pixels set (x, y, rgb).
    fn sparse(w: usize, h: usize, px: &[(usize, usize, [u8; 3])]) -> Vec<u8> {
        let mut f = vec![0u8; w * h * 4];
        for &(x, y, c) in px {
            f[(y * w + x) * 4..][..4].copy_from_slice(&[c[0], c[1], c[2], 255]);
        }
        f
    }

    #[test]
    fn side_fill_sample_positions_round_half_to_even_like_python() {
        // The 7th of 13 sample points sits at (size-1)*6/12 = (size-1)/2. For an even size-1... odd size-1 that is k.5 and Python's round()
        // goes to the even neighbour. Values checked against the app's _cdg_side_fill_color on the same images.
        let (a, b) = ([10, 0, 0], [0, 20, 0]);
        // (width, pixel with the lower index, expected colour): 150.5 -> 150, 152.5 -> 152, 153.5 -> 154, 149.5 -> 150
        for (w, lo, want) in [(302usize, 150usize, (10, 0, 0)), (306, 152, (10, 0, 0)), (308, 153, (0, 20, 0)), (300, 149, (0, 20, 0))] {
            let top = sparse(w, 216, &[(lo, 0, a), (lo + 1, 0, b)]);
            assert_eq!(side_fill_color(&top, w, 216), want, "top edge, width {w}");
        }
        for (h, lo, want) in [(302usize, 150usize, (10, 0, 0)), (306, 152, (10, 0, 0)), (308, 153, (0, 20, 0))] {
            let left = sparse(300, h, &[(0, lo, a), (0, lo + 1, b)]);
            assert_eq!(side_fill_color(&left, 300, h), want, "left edge, height {h}");
        }
        // the bottom and right edges use the same positions
        let bottom = sparse(308, 216, &[(153, 215, a), (154, 215, b)]);
        assert_eq!(side_fill_color(&bottom, 308, 216), (0, 20, 0));
        let right = sparse(300, 308, &[(299, 153, a), (299, 154, b)]);
        assert_eq!(side_fill_color(&right, 300, 308), (0, 20, 0));
    }

    #[test]
    fn side_fill_ties_go_to_the_first_colour_seen_and_majorities_win() {
        // top edge colour A at the 1st sample only, B at the 2nd only: A has 2 votes (top and left edge), B 1 -> A (first seen, the top edge comes first at i = 0)
        let w = 300;
        let top = sparse(w, 216, &[(0, 0, [1, 1, 1]), (25, 0, [2, 2, 2])]);
        // x = round(299*i/12): i=1 -> 25 (24.9)
        assert_eq!(side_fill_color(&top, w, 216), (1, 1, 1));
        // A also sits at the left-edge sample (0, 0), so it has 2 votes; B at x = 25 and 50 has 2 as well: a tie goes to A, seen first
        let top2 = sparse(w, 216, &[(0, 0, [1, 1, 1]), (25, 0, [2, 2, 2]), (50, 0, [2, 2, 2])]);
        assert_eq!(side_fill_color(&top2, w, 216), (1, 1, 1));
        // a third B vote (x = 75) wins
        let top3 = sparse(w, 216, &[(0, 0, [1, 1, 1]), (25, 0, [2, 2, 2]), (50, 0, [2, 2, 2]), (75, 0, [2, 2, 2])]);
        assert_eq!(side_fill_color(&top3, w, 216), (2, 2, 2));
        // the colour is compared without alpha handling beyond transparent/opaque: a semi-transparent sample still votes
        let mut semi = sparse(w, 216, &[(0, 0, [3, 3, 3])]);
        semi[3] = 77;
        assert_eq!(side_fill_color(&semi, w, 216), (3, 3, 3));
    }

    #[test]
    fn compose_partial_alpha_is_rounded_to_nearest() {
        // opaque (20,40,60) picture, side-fill background (20,40,60); single interior pixels with their own alpha
        let mut f = sparse(WIDTH, HEIGHT, &[]);
        for p in f.chunks_exact_mut(4) {
            p.copy_from_slice(&[20, 40, 60, 255]);
        }
        let set = |f: &mut Vec<u8>, x: usize, y: usize, px: [u8; 4]| f[(y * WIDTH + x) * 4..][..4].copy_from_slice(&px);
        set(&mut f, 100, 100, [250, 10, 128, 100]);
        set(&mut f, 101, 100, [255, 255, 255, 254]);
        set(&mut f, 102, 100, [255, 255, 255, 1]);
        set(&mut f, 103, 100, [128, 128, 128, 1]);
        set(&mut f, 104, 100, [127, 127, 127, 1]);
        // 1:1 in a window twice as wide, picture at x0 = WIDTH / 2
        let out = compose(&f, 2 * WIDTH, HEIGHT, DisplayMode::SideFill);
        let at = |x: usize| &out[(100 * 2 * WIDTH + WIDTH / 2 + x) * 4..][..4];
        // (s*a + bg*(255-a) + 127) / 255 per channel, worked by hand
        assert_eq!(at(100), &[110, 28, 87, 255][..]);
        assert_eq!(at(101), &[254, 254, 254, 255][..]);
        assert_eq!(at(102), &[21, 41, 61, 255][..]);
        // the rounding term: (128 + 20*254 + 127)/255 = 21 with +127, 21 with +128 -- these two pin the .5 edge: n = 127 -> 0 with +127, 1 with +128
        let mut g = sparse(WIDTH, HEIGHT, &[]);
        for p in g.chunks_exact_mut(4) {
            p.copy_from_slice(&[0, 0, 0, 255]);
        }
        set(&mut g, 7, 7, [127, 127, 127, 1]);
        set(&mut g, 8, 7, [128, 128, 128, 1]);
        let o = compose(&g, WIDTH, HEIGHT, DisplayMode::Fit);
        // (127*1 + 0*254 + 127)/255 = 0 (254/255), but with +128 it would be 1; (128 + 127)/255 = 1, without +127 it would be 0
        assert_eq!(&o[(7 * WIDTH + 7) * 4..][..4], &[0, 0, 0, 255]);
        assert_eq!(&o[(7 * WIDTH + 8) * 4..][..4], &[1, 1, 1, 255]);
        let _ = (at(103), at(104));
    }

    /// A deterministic pseudo-random picture: every texel is different, so a one-texel shift of any sample point changes the output.
    fn noise_frame() -> Vec<u8> {
        let mut s = 0x9E37_79B9u32;
        let mut f = Vec::with_capacity(WIDTH * HEIGHT * 4);
        for _ in 0..WIDTH * HEIGHT {
            for _ in 0..3 {
                s = s.wrapping_mul(1664525).wrapping_add(1013904223);
                f.push((s >> 24) as u8);
            }
            f.push(255);
        }
        f
    }

    #[test]
    fn compose_bridge_is_pinned_on_noise() {
        // see compose_bridge_is_pinned: every sample position and kernel tap matters on this picture
        let f = noise_frame();
        let mut got = Vec::new();
        for (w, h) in [(640usize, 360usize), (400, 500), (300, 216), (211, 97), (100, 18), (1000, 36), (25, 19), (26, 18)] {
            for fill in [BridgeFill::Off, BridgeFill::Colour, BridgeFill::Blur] {
                got.push(fnv(&compose_bridge(&f, w, h, fill)));
            }
        }
        let want: Vec<u64> = vec![
            11874557248417483512, 11902828660258321364, 3955552042324344177, 11346715785299743367, 1366077491637955261, 12798927378604934352, 3574462564336687661, 12331715865879986115, 13747073093272899691, 13904132285520996794, 17295131000763183487, 4502574457107367778, 2222069142446841814, 9637906161902579645, 16157606295003066001, 1002928137741541572, 2246646071310370718, 383309623747252508,
            8909105907851778006, 8909105907851778006, 8909105907851778006, 990943005705645604, 9601055432519064091, 14801219898315228905,
        ];
        assert_eq!(got, want);
    }
}
