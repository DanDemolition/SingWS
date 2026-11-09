//! `analyze_cdg_visual_bytes` (transition_analysis.py): find a provably blank CD+G tail. Tile operations are simulated on the 300x216 indexed screen; a visible, non-uniform
//! screen that is explicitly cleared to one colour and stays that way is the only proof of an early visual end. Palette and scroll operations count as visible mutations and, when
//! they follow the clear, make the tail ambiguous. tools/cdg_visual_parity.py runs the app's own function on the same bytes.
const PACKET: usize = 24;
const PER_SECOND: f64 = 300.0;
const COMMAND: u8 = 0x09;
const MEMORY_PRESET: u8 = 0x01;
const BORDER_PRESET: u8 = 0x02;
const TILE_BLOCK: u8 = 0x06;
const SCROLL_PRESET: u8 = 0x14;
const SCROLL_COPY: u8 = 0x18;
const DEFINE_TRANSPARENT: u8 = 0x1C;
const LOAD_CLUT_LOW: u8 = 0x1E;
const LOAD_CLUT_HIGH: u8 = 0x1F;
const TILE_BLOCK_XOR: u8 = 0x26;
const WIDTH: usize = 300;
const HEIGHT: usize = 216;

#[derive(Debug, Clone, PartialEq)]
pub struct Visual {
    pub visual_start: Option<f64>,
    pub visual_end: Option<f64>,
    pub duration: f64,
    pub confidence: f64,
    pub method: &'static str,
    pub safe: bool,
    pub reason: &'static str,
    pub last_change: Option<f64>,
}

pub fn analyze_cdg_visual_bytes(raw: &[u8], minimum_blank_tail_seconds: f64) -> Visual {
    let packet_count = raw.len() / PACKET;
    let duration = packet_count as f64 / PER_SECOND;
    if packet_count == 0 {
        return Visual { visual_start: None, visual_end: None, duration: 0.0, confidence: 0.0, method: "cdg_packets_v1", safe: false, reason: "empty_cdg", last_change: None };
    }
    let mut pixels = vec![0u8; WIDTH * HEIGHT];
    let total = pixels.len() as i64;
    let mut color_counts = [0i64; 16];
    color_counts[0] = total;
    let mut border_color = 0u8;
    let mut palette = [0u32; 16];
    let mut transparent: Option<u8> = None;
    let mut first_change: Option<f64> = None;
    let mut last_change: Option<f64> = None;
    let mut saw_nonuniform = false;
    let mut final_clear_time: Option<f64> = None;
    let mut ambiguous_after_clear = false;
    let ts = |i: usize| i as f64 / PER_SECOND;

    for index in 0..packet_count {
        let packet = &raw[index * PACKET..(index + 1) * PACKET];
        if packet[0] & 0x3F != COMMAND {
            continue;
        }
        let instruction = packet[1] & 0x3F;
        let data = &packet[4..20];
        let mut changed = || {
            let at = ts(index);
            if first_change.is_none() {
                first_change = Some(at);
            }
            last_change = Some(at);
        };
        match instruction {
            MEMORY_PRESET => {
                let color = (data[0] & 0x0F) as usize;
                if color_counts[color] != total {
                    pixels.fill(color as u8);
                    color_counts = [0; 16];
                    color_counts[color] = total;
                    changed();
                }
                if saw_nonuniform {
                    final_clear_time = Some(ts(index));
                    ambiguous_after_clear = false;
                }
            }
            BORDER_PRESET => {
                let color = data[0] & 0x0F;
                if color != border_color {
                    border_color = color;
                    changed();
                }
            }
            TILE_BLOCK | TILE_BLOCK_XOR => {
                let (color0, color1) = (data[0] & 0x0F, data[1] & 0x0F);
                let (row, column) = ((data[2] & 0x1F) as usize, (data[3] & 0x3F) as usize);
                let (y0, x0) = (row * 12, column * 6);
                let mut tile_changed = false;
                if y0 < HEIGHT && x0 < WIDTH {
                    for tile_y in 0..12 {
                        let y = y0 + tile_y;
                        if y >= HEIGHT {
                            break;
                        }
                        let bits = data[4 + tile_y] & 0x3F;
                        let offset = y * WIDTH + x0;
                        for tile_x in 0..6.min(WIDTH - x0) {
                            let mut color = if bits & (1 << (5 - tile_x)) != 0 { color1 } else { color0 };
                            let pos = offset + tile_x;
                            if instruction == TILE_BLOCK_XOR {
                                color ^= pixels[pos];
                            }
                            if pixels[pos] != color {
                                color_counts[pixels[pos] as usize] -= 1;
                                pixels[pos] = color;
                                color_counts[color as usize] += 1;
                                tile_changed = true;
                            }
                        }
                    }
                    if tile_changed {
                        changed();
                        if *color_counts.iter().max().unwrap() != total {
                            saw_nonuniform = true;
                            final_clear_time = None;
                        } else if saw_nonuniform {
                            final_clear_time = Some(ts(index));
                            ambiguous_after_clear = false;
                        }
                    }
                }
            }
            SCROLL_PRESET | SCROLL_COPY => {
                changed();
                final_clear_time = None;
                ambiguous_after_clear = true;
            }
            LOAD_CLUT_LOW | LOAD_CLUT_HIGH => {
                let base = if instruction == LOAD_CLUT_LOW { 0 } else { 8 };
                let mut palette_changed = false;
                for offset in 0..8 {
                    let value = (((data[offset * 2] & 0x3F) as u32) << 6) | (data[offset * 2 + 1] & 0x3F) as u32;
                    if palette[base + offset] != value {
                        palette[base + offset] = value;
                        palette_changed = true;
                    }
                }
                if palette_changed {
                    changed();
                    if final_clear_time.is_some() {
                        ambiguous_after_clear = true;
                    }
                }
            }
            DEFINE_TRANSPARENT => {
                let color = data[0] & 0x0F;
                if transparent != Some(color) {
                    transparent = Some(color);
                    changed();
                    if final_clear_time.is_some() {
                        ambiguous_after_clear = true;
                    }
                }
            }
            _ => {}
        }
    }

    let blank_tail = match final_clear_time {
        Some(t) => duration - t,
        None => 0.0,
    };
    let safe = saw_nonuniform && final_clear_time.is_some() && !ambiguous_after_clear && blank_tail >= minimum_blank_tail_seconds.max(0.0);
    let (reason, visual_end, confidence) = if safe {
        ("explicit_clear_then_stable_blank_tail", final_clear_time, 0.98)
    } else if saw_nonuniform && final_clear_time.is_none() {
        ("static_or_active_nonblank_final_screen", None, 0.0)
    } else if final_clear_time.is_some() {
        ("blank_tail_too_short_or_ambiguous", None, 0.0)
    } else {
        ("no_provable_visual_content_and_clear", None, 0.0)
    };
    Visual { visual_start: first_change, visual_end, duration, confidence, method: "cdg_packets_v1", safe, reason, last_change }
}

#[derive(Debug, Clone, PartialEq)]
pub struct VideoVisual {
    pub visual_start: Option<f64>,
    pub visual_end: Option<f64>,
    pub confidence: f64,
    pub method: &'static str,
    pub safe: bool,
    pub reason: &'static str,
}

/// `analyze_video_tail_samples`: only a high-confidence static-black MP4 tail after real content counts as dead. Samples are (timestamp, mean luma, difference).
pub fn analyze_video_tail(samples: &[(f64, f64, f64)], duration: f64, black_luma_max: f64, static_difference_max: f64, minimum_black_tail_seconds: f64) -> VideoVisual {
    let total = if duration.is_finite() { duration } else { 0.0 };
    let mut ordered: Vec<(f64, f64, f64)> = samples
        .iter()
        .copied()
        .filter(|(t, l, d)| t.is_finite() && l.is_finite() && d.is_finite())
        .filter(|(t, _, _)| !(*t < 0.0 || (total > 0.0 && *t > total + 0.25)))
        .collect();
    ordered.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
    let method = "mp4_thumbnails_v1";
    if ordered.len() < 2 || total <= 0.0 {
        return VideoVisual { visual_start: None, visual_end: None, confidence: 0.0, method, safe: false, reason: "insufficient_video_samples" };
    }
    let meaningful: Vec<&(f64, f64, f64)> = ordered.iter().filter(|(_, l, d)| *l > black_luma_max || *d > static_difference_max).collect();
    let visual_start = meaningful.first().map(|m| m.0);
    let mut black_tail_start: Option<f64> = None;
    for item in ordered.iter().rev() {
        let is_static_black = item.1 <= black_luma_max && item.2 <= static_difference_max;
        if !is_static_black {
            break;
        }
        black_tail_start = Some(item.0);
    }
    let tail_length = match black_tail_start {
        Some(b) => total - b,
        None => 0.0,
    };
    let has_prior = !meaningful.is_empty() && black_tail_start.is_some_and(|b| meaningful.iter().any(|m| m.0 < b));
    let safe = has_prior && tail_length >= minimum_black_tail_seconds.max(0.0);
    if safe {
        return VideoVisual { visual_start, visual_end: black_tail_start, confidence: 0.97, method, safe: true, reason: "active_video_then_static_black_tail" };
    }
    let final_item = ordered.last().unwrap();
    let reason = if final_item.1 > black_luma_max && final_item.2 <= static_difference_max {
        "static_nonblack_final_screen"
    } else if black_tail_start.is_some() {
        "black_tail_too_short_or_no_prior_content"
    } else {
        "video_active_or_uncertain_through_end"
    };
    VideoVisual { visual_start, visual_end: None, confidence: 0.0, method, safe: false, reason }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn packet(instruction: u8, data: &[u8]) -> Vec<u8> {
        let mut p = vec![COMMAND, instruction, 0, 0];
        let mut d = data.to_vec();
        d.resize(16, 0);
        p.extend(d);
        p.extend([0u8; 4]);
        p
    }

    fn idle(n: usize) -> Vec<u8> {
        vec![0u8; PACKET * n]
    }

    #[test]
    fn a_black_sample_exactly_a_quarter_second_past_the_end_still_counts() {
        let samples = [(0.0, 0.5, 0.3), (90.0, 0.5, 0.3), (180.25, 0.0, 0.0)];
        let kept = analyze_video_tail(&samples, 180.0, 0.03, 0.01, 1.0);
        assert_eq!(kept.reason, "black_tail_too_short_or_no_prior_content");
        let dropped = analyze_video_tail(&samples, 180.0, 0.03, 0.01, 1.0);
        assert!(!dropped.safe);
        let beyond = analyze_video_tail(&[(0.0, 0.5, 0.3), (90.0, 0.5, 0.3), (180.26, 0.0, 0.0)], 180.0, 0.03, 0.01, 1.0);
        assert_eq!(beyond.reason, "video_active_or_uncertain_through_end");
    }

    #[test]
    fn an_empty_file_proves_nothing() {
        assert_eq!(analyze_cdg_visual_bytes(&[], 1.0).reason, "empty_cdg");
    }

    #[test]
    fn graphics_cleared_and_left_blank_for_a_second_is_a_provable_end() {
        let mut bytes = packet(MEMORY_PRESET, &[1]);
        bytes.extend(packet(TILE_BLOCK, &[2, 3, 0, 0, 0x3F, 0x3F]));
        bytes.extend(idle(10));
        bytes.extend(packet(MEMORY_PRESET, &[1]));
        bytes.extend(idle(400));
        let v = analyze_cdg_visual_bytes(&bytes, 1.0);
        assert!(v.safe, "{v:?}");
        assert_eq!(v.reason, "explicit_clear_then_stable_blank_tail");
        assert_eq!(v.visual_end, Some(12.0 / 300.0));
    }

    #[test]
    fn a_palette_change_after_the_clear_makes_the_tail_ambiguous() {
        let mut bytes = packet(MEMORY_PRESET, &[1]);
        bytes.extend(packet(TILE_BLOCK, &[2, 3, 0, 0, 0x3F]));
        bytes.extend(packet(MEMORY_PRESET, &[1]));
        bytes.extend(packet(LOAD_CLUT_LOW, &[1, 2, 3]));
        bytes.extend(idle(400));
        let v = analyze_cdg_visual_bytes(&bytes, 1.0);
        assert!(!v.safe);
        assert_eq!(v.reason, "blank_tail_too_short_or_ambiguous");
    }
}
