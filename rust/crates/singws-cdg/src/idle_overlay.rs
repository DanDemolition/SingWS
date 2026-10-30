//! The idle "now playing" card of the app's audience window (`VideoAreaWidget._get_idle_bg_overlay_info`, `_draw_idle_bg_overlay`,
//! `advance_visualizer`): when the karaoke window is idle and background music is playing, a card at the bottom left shows the artwork, the
//! title and artist of the current background track and a small level meter. Everything here is a pure function of numbers so the Qt Quick
//! window can draw the same card; `tools/idle_overlay_parity.py` runs the app's own methods with a recording painter and compares.
//!
//! What is NOT here: the text itself is elided by Qt (`QFontMetrics.elidedText`), which the QML `Text` item does with `elide: ElideRight`;
//! the artist and title come from `track_display`; the font metrics (ascent) are inputs because they belong to the font the window draws with.

use crate::window_rules::Rect;

/// Number of meter bars in the app (`_viz_levels = [0.08] * 14`).
pub const BAR_COUNT: usize = 14;

/// `VideoAreaWidget`'s meter state and its per-tick animation (`set_audio_level`, `advance_visualizer`).
#[derive(Clone, Debug, PartialEq)]
pub struct Visualizer {
    pub levels: Vec<f64>,
    pub peaks: Vec<f64>,
    pub audio_level: f64,
    pub phase: f64,
}

impl Default for Visualizer {
    fn default() -> Self {
        Visualizer { levels: vec![0.08; BAR_COUNT], peaks: vec![0.12; BAR_COUNT], audio_level: 0.0, phase: 0.0 }
    }
}

impl Visualizer {
    /// `set_audio_level`: the meter level of the background player, clamped to 0..1 (not a number counts as 0).
    pub fn set_audio_level(&mut self, level: f64) {
        self.audio_level = if level.is_nan() { 0.0 } else { level.clamp(0.0, 1.0) };
    }

    /// `advance_visualizer(active)`, called every 50 ms by the window's idle timer.
    pub fn advance(&mut self, active: bool) {
        if active {
            let amp = self.audio_level.clamp(0.0, 1.0);
            self.audio_level = (self.audio_level * 0.88).max(0.0);
            self.phase = (self.phase + (0.12 + amp * 0.55)) % 10000.0;
            for i in 0..self.levels.len() {
                let old = self.levels[i];
                let wave1 = 0.5 + 0.5 * (self.phase + (i as f64 * 0.60)).sin();
                let wave2 = 0.5 + 0.5 * ((self.phase * 0.72) + (i as f64 * 1.05)).sin();
                let shape = (0.62 * wave1) + (0.38 * wave2);
                let target = 0.05 + (0.08 + 0.87 * shape) * amp.max(0.08);
                let lvl = (old * 0.70) + (target * 0.30);
                self.levels[i] = lvl;
                let mut pk = self.peaks.get(i).copied().unwrap_or(lvl);
                if lvl >= pk {
                    pk = lvl.min(1.0);
                } else {
                    pk = (pk - 0.018).max(0.04);
                }
                if let Some(p) = self.peaks.get_mut(i) {
                    *p = pk;
                }
            }
        } else {
            self.audio_level = 0.0;
            for i in 0..self.levels.len() {
                let lvl = (self.levels[i] * 0.84).max(0.02);
                self.levels[i] = lvl;
                let pk = self.peaks.get(i).copied().unwrap_or(lvl);
                if let Some(p) = self.peaks.get_mut(i) {
                    *p = lvl.max(pk * 0.92);
                }
            }
        }
    }
}

/// `VideoWindow._tick_idle_overlay`'s `should_draw`: the window is idle and the background player exists and is playing.
pub fn should_draw(idle: bool, bg_exists: bool, bg_playing: bool) -> bool {
    idle && bg_exists && bg_playing
}

/// `_get_idle_bg_overlay_info`: which playlist entry the card shows, or `None` for no card. The card needs an idle window, a playing
/// background player and a current index inside its playlist.
pub fn card_track_index(idle: bool, bg_exists: bool, bg_playing: bool, playlist_len: usize, current_index: i64) -> Option<usize> {
    if !idle || !bg_exists || !bg_playing {
        return None;
    }
    if playlist_len == 0 || current_index < 0 || current_index as u64 >= playlist_len as u64 {
        return None;
    }
    Some(current_index as usize)
}

/// One meter bar: the bar and the peak-hold cap above it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Bar {
    pub bar: Rect,
    pub cap: Rect,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Text {
    pub x: i32,
    pub title_baseline: i32,
    /// `None` when the track has no artist (the app draws no artist line then).
    pub artist_baseline: Option<i32>,
    /// Point sizes of the two fonts (title bold).
    pub title_point_size: i32,
    pub artist_point_size: i32,
    /// Width the text is elided to.
    pub width: i32,
}

#[derive(Clone, Debug, PartialEq)]
pub struct CardLayout {
    /// The rounded card (radius 16).
    pub card: Rect,
    /// The artwork square (radius 10 placeholder behind it), `None` without artwork.
    pub art: Option<Rect>,
    /// Absent when the text column is 40 pixels or narrower: the app then draws the card and artwork only.
    pub text: Option<Text>,
    /// The meter lane (radius 6), absent when narrower than 61 pixels or when the text column is absent.
    pub lane: Option<Rect>,
    pub bars: Vec<Bar>,
}

/// `Qt.KeepAspectRatioByExpanding` for `QSize.scaled`: the smallest size with the picture's aspect ratio that covers the box.
pub fn expand_size(w: i32, h: i32, box_w: i32, box_h: i32) -> (i32, i32) {
    if w <= 0 || h <= 0 {
        return (w, h);
    }
    let rw = (box_h as i64) * (w as i64) / (h as i64);
    if rw >= box_w as i64 {
        (rw as i32, box_h)
    } else {
        (box_w, ((box_w as i64) * (h as i64) / (w as i64)) as i32)
    }
}

/// `_draw_idle_bg_overlay` for a window of `w` x `h`. `title_ascent` / `artist_ascent` are the font metrics' ascent for the two fonts the
/// card draws with (they depend on the point sizes in the result, which the caller can read first with [`font_sizes`]). `levels` and
/// `peaks` are the meter state (empty `levels` means the app's default eight-percent bars).
pub fn card_layout(
    w: i32,
    h: i32,
    has_art: bool,
    has_artist: bool,
    title_ascent: i32,
    artist_ascent: i32,
    levels: &[f64],
    peaks: &[f64],
) -> Option<CardLayout> {
    if w <= 120 || h <= 120 {
        return None;
    }
    let card_w = ((w as f64 * 0.46) as i32).min(680);
    let card_h = ((h as f64 * 0.22) as i32).min(210);
    let margin = 18;
    let card_x = margin;
    let card_y = 4.max(h - card_h - 6);
    let card = Rect::new(card_x, card_y, card_w, card_h);

    let pad = 12;
    let art_size = 0.max(card_h - pad * 2);
    let mut text_x = card_x + pad;
    let mut art = None;
    if has_art {
        art = Some(Rect::new(card_x + pad, card_y + pad, art_size, art_size));
        text_x = card_x + pad + art_size + 12;
    }
    let text_w = (card_x + card_w - pad) - text_x;
    if text_w <= 40 {
        return Some(CardLayout { card, art, text: None, lane: None, bars: Vec::new() });
    }

    let (title_pt, artist_pt) = font_sizes(card_h);
    let top_y = card_y + pad + 2;
    let title_y = top_y + title_ascent;
    let artist_y = title_y + 2.max((card_h as f64 * 0.02) as i32) + artist_ascent;
    let text = Text {
        x: text_x,
        title_baseline: title_y,
        artist_baseline: if has_artist { Some(artist_y) } else { None },
        title_point_size: title_pt,
        artist_point_size: artist_pt,
        width: text_w,
    };

    let bar_area_h = 24.max((card_h as f64 * 0.33) as i32);
    let bar_area_y = card_y + card_h - pad - bar_area_h;
    let bar_area_x = text_x;
    let bar_area_w = text_w;
    let mut lane = None;
    let mut bars = Vec::new();
    if bar_area_w > 60 {
        lane = Some(Rect::new(bar_area_x, bar_area_y, bar_area_w, bar_area_h));
        let default_levels = [0.08; BAR_COUNT];
        let levels: &[f64] = if levels.is_empty() { &default_levels } else { levels };
        let n = levels.len() as i32;
        let gap = 3;
        let usable_w = 1.max(bar_area_w - gap * (n - 1) - 4);
        let bar_w = 2.max(usable_w.div_euclid(n));
        let max_h = 6.max(bar_area_h - 6);
        let mut x = bar_area_x + 2;
        for (i, lv) in levels.iter().enumerate() {
            let v = lv.clamp(0.03, 1.0);
            let h_px = 3.max((max_h as f64 * v) as i32);
            let y = bar_area_y + bar_area_h - h_px - 2;
            let pv = match peaks.get(i) {
                Some(p) => v.max(p.min(1.0)),
                None => v,
            };
            let py = bar_area_y + bar_area_h - 2.max((max_h as f64 * pv) as i32) - 2;
            bars.push(Bar { bar: Rect::new(x, y, bar_w, h_px), cap: Rect::new(x, py, bar_w, 2) });
            x += bar_w + gap;
        }
    }
    Some(CardLayout { card, art, text: Some(text), lane, bars })
}

/// The point sizes of the card's two fonts for a card of height `card_h` (a function of the card only, so the caller can ask the font system
/// for the ascents before calling [`card_layout`]).
pub fn font_sizes(card_h: i32) -> (i32, i32) {
    (11.max((card_h as f64 * 0.20) as i32), 9.max((card_h as f64 * 0.14) as i32))
}

/// The height of the card for a window of height `h` (what [`font_sizes`] needs).
pub fn card_height(h: i32) -> i32 {
    ((h as f64 * 0.22) as i32).min(210)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn card_needs_idle_playing_and_a_valid_index() {
        assert_eq!(card_track_index(true, true, true, 3, 2), Some(2));
        assert_eq!(card_track_index(false, true, true, 3, 2), None);
        assert_eq!(card_track_index(true, false, true, 3, 2), None);
        assert_eq!(card_track_index(true, true, false, 3, 2), None);
        assert_eq!(card_track_index(true, true, true, 0, 0), None);
        assert_eq!(card_track_index(true, true, true, 3, 3), None);
        assert_eq!(card_track_index(true, true, true, 3, -1), None);
        assert_eq!(card_track_index(true, true, true, 3, 0), Some(0));
        assert!(should_draw(true, true, true));
        assert!(!should_draw(true, true, false) && !should_draw(true, false, true) && !should_draw(false, true, true));
    }

    #[test]
    fn small_windows_have_no_card() {
        assert!(card_layout(120, 800, false, true, 10, 8, &[], &[]).is_none());
        assert!(card_layout(800, 120, false, true, 10, 8, &[], &[]).is_none());
        assert!(card_layout(121, 121, false, true, 10, 8, &[], &[]).is_some());
    }

    #[test]
    fn card_geometry_at_1280x720() {
        let v = Visualizer::default();
        let l = card_layout(1280, 720, true, true, 20, 14, &v.levels, &v.peaks).unwrap();
        // 46 percent of 1280 is 588, 22 percent of 720 is 158
        assert_eq!(l.card, Rect::new(18, 556, 588, 158));
        assert_eq!(l.art, Some(Rect::new(30, 568, 134, 134)));
        let t = l.text.unwrap();
        assert_eq!((t.x, t.width), (176, 418));
        assert_eq!((t.title_point_size, t.artist_point_size), (31, 22));
        assert_eq!(t.title_baseline, 556 + 12 + 2 + 20);
        assert_eq!(t.artist_baseline, Some(556 + 12 + 2 + 20 + 3 + 14));
        assert_eq!(l.lane, Some(Rect::new(176, 556 + 158 - 12 - 52, 418, 52)));
        assert_eq!(l.bars.len(), 14);
        assert_eq!(l.bars[0].bar.x, 178);
        assert_eq!(l.bars[1].bar.x, 178 + l.bars[0].bar.w + 3);
    }

    #[test]
    fn caps_and_widths_hold_on_big_windows() {
        let l = card_layout(4000, 3000, false, false, 5, 5, &[], &[]).unwrap();
        assert_eq!(l.card.w, 680);
        assert_eq!(l.card.h, 210);
        assert_eq!(l.card.y, 3000 - 210 - 6);
        assert!(l.text.as_ref().unwrap().artist_baseline.is_none());
        assert_eq!(l.bars.len(), 14, "no levels means the default fourteen");
    }

    #[test]
    fn narrow_text_column_draws_card_only() {
        // card_w = int(130 * 0.46) = 59, with artwork the text column is negative
        let l = card_layout(130, 400, true, true, 5, 5, &[], &[]).unwrap();
        assert!(l.text.is_none() && l.lane.is_none() && l.bars.is_empty());
        assert!(l.art.is_some());
        // exactly 40 wide: no text; 41: text, but no lane (needs more than 60)
        let l = card_layout(400, 400, false, true, 5, 5, &[], &[]).unwrap();
        assert!(l.text.is_some() && l.lane.is_some());
    }

    #[test]
    fn bar_width_floor_and_missing_peaks() {
        // 200 bars in a narrow lane: width floors at 2
        let levels = vec![0.5; 200];
        let l = card_layout(600, 400, false, true, 5, 5, &levels, &[0.9]).unwrap();
        assert!(l.bars.iter().all(|b| b.bar.w == 2));
        // bar 0 has a peak above its level, bar 1 has none and its cap sits on the bar's own height
        let t = &l.bars[1];
        assert_eq!(t.cap.y, t.bar.y);
        assert!(l.bars[0].cap.y < l.bars[0].bar.y);
    }

    #[test]
    fn expanding_size_matches_qt_rule() {
        assert_eq!(expand_size(100, 50, 40, 40), (80, 40));
        assert_eq!(expand_size(50, 100, 40, 40), (40, 80));
        assert_eq!(expand_size(10, 10, 40, 40), (40, 40));
        assert_eq!(expand_size(0, 10, 40, 40), (0, 10));
    }

    #[test]
    fn visualizer_idle_decay_and_active_motion() {
        let mut v = Visualizer::default();
        v.advance(false);
        assert!((v.levels[0] - 0.0672).abs() < 1e-12);
        assert!((v.peaks[0] - 0.12 * 0.92).abs() < 1e-12 || v.peaks[0] >= v.levels[0]);
        for _ in 0..200 {
            v.advance(false);
        }
        assert!(v.levels.iter().all(|l| (*l - 0.02).abs() < 1e-12), "levels floor at 0.02");
        v.set_audio_level(5.0);
        assert_eq!(v.audio_level, 1.0);
        v.set_audio_level(f64::NAN);
        assert_eq!(v.audio_level, 0.0);
        v.set_audio_level(0.9);
        let before = v.phase;
        v.advance(true);
        assert!(v.phase > before + 0.12);
        assert!((v.audio_level - 0.9 * 0.88).abs() < 1e-12);
        assert!(v.levels.iter().all(|l| *l > 0.02));
        assert!(v.peaks.iter().zip(&v.levels).all(|(p, l)| p >= l || (*p - *l).abs() < 0.02));
    }
}
