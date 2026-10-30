//! QObject exposed to QML as `IdleOverlay`: the numbers for the audience window's idle "now playing" card. All rules live in the toolkit-free
//! `singws_cdg::idle_overlay` (proven equal to the app's drawing by `tools/idle_overlay_parity.py`) and `singws_cdg::track_display` (proven equal to
//! `track_display.py`). It draws nothing and reads no show data: the track path, its tags and the meter level are given by the caller (the demo
//! window takes them from the command line).

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, artist)]
        #[qproperty(QString, title)]
        type IdleOverlay = super::IdleOverlayRust;

        /// The text the card shows for a music file: its tags when they are good, the cleaned file name otherwise (`track_display.display_from_path`).
        #[qinvokable]
        #[cxx_name = "setTrack"]
        fn set_track(self: Pin<&mut IdleOverlay>, path: &QString, tag_artist: &QString, tag_title: &QString);
        /// One 50 ms tick of the card's meter: `level` is the background player's meter (0..1), `active` whether the card is drawn.
        #[qinvokable]
        fn step(self: Pin<&mut IdleOverlay>, active: bool, level: f64);
        /// The card for a window of w x h as JSON (`null` when the window is too small), with the font ascents QML measured for the two point sizes.
        #[qinvokable]
        #[cxx_name = "layoutJson"]
        fn layout_json(self: &IdleOverlay, w: i32, h: i32, has_art: bool, title_ascent: i32, artist_ascent: i32) -> QString;
        #[qinvokable]
        #[cxx_name = "titlePointSize"]
        fn title_point_size(self: &IdleOverlay, h: i32) -> i32;
        #[qinvokable]
        #[cxx_name = "artistPointSize"]
        fn artist_point_size(self: &IdleOverlay, h: i32) -> i32;
        /// `Qt.KeepAspectRatioByExpanding` size of an iw x ih picture in a square box, as "w,h".
        #[qinvokable]
        fn expand(self: &IdleOverlay, iw: i32, ih: i32, box_size: i32) -> QString;
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use serde_json::{Value, json};
use singws_cdg::idle_overlay::{self as io, CardLayout, Visualizer};
use singws_cdg::track_display::display_from_path;
use singws_cdg::window_rules::Rect;

#[derive(Default)]
pub struct IdleOverlayRust {
    artist: QString,
    title: QString,
    viz: Visualizer,
}

fn rect(r: &Rect) -> Value {
    json!({"x": r.x, "y": r.y, "w": r.w, "h": r.h})
}

pub fn layout_to_json(l: &Option<CardLayout>) -> Value {
    let Some(l) = l else { return Value::Null };
    json!({
        "card": rect(&l.card),
        "art": l.art.as_ref().map(rect),
        "text": l.text.as_ref().map(|t| json!({
            "x": t.x, "titleBaseline": t.title_baseline, "artistBaseline": t.artist_baseline,
            "titlePointSize": t.title_point_size, "artistPointSize": t.artist_point_size, "width": t.width,
        })),
        "lane": l.lane.as_ref().map(rect),
        "bars": l.bars.iter().map(|b| json!({"bar": rect(&b.bar), "cap": rect(&b.cap)})).collect::<Vec<_>>(),
    })
}

impl qobject::IdleOverlay {
    fn set_track(mut self: Pin<&mut Self>, path: &QString, tag_artist: &QString, tag_title: &QString) {
        let (artist, title) = display_from_path(&path.to_string(), &tag_artist.to_string(), &tag_title.to_string());
        self.as_mut().set_artist(QString::from(artist.as_str()));
        self.as_mut().set_title(QString::from(title.as_str()));
    }

    fn step(mut self: Pin<&mut Self>, active: bool, level: f64) {
        let viz = &mut self.as_mut().rust_mut().viz;
        viz.set_audio_level(level);
        viz.advance(active);
    }

    fn layout_json(&self, w: i32, h: i32, has_art: bool, title_ascent: i32, artist_ascent: i32) -> QString {
        let has_artist = !self.artist.to_string().is_empty();
        let viz = &self.rust().viz;
        let l = io::card_layout(w, h, has_art, has_artist, title_ascent, artist_ascent, &viz.levels, &viz.peaks);
        QString::from(layout_to_json(&l).to_string().as_str())
    }

    fn title_point_size(&self, h: i32) -> i32 {
        io::font_sizes(io::card_height(h)).0
    }

    fn artist_point_size(&self, h: i32) -> i32 {
        io::font_sizes(io::card_height(h)).1
    }

    fn expand(&self, iw: i32, ih: i32, box_size: i32) -> QString {
        let (w, h) = io::expand_size(iw, ih, box_size, box_size);
        QString::from(format!("{w},{h}").as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_carries_every_rectangle() {
        let l = io::card_layout(1280, 720, true, true, 20, 14, &[0.5; 14], &[0.6; 14]);
        let v = layout_to_json(&l);
        assert_eq!(v["card"]["x"], 18);
        assert_eq!(v["art"]["w"], 134);
        assert_eq!(v["bars"].as_array().unwrap().len(), 14);
        assert_eq!(v["text"]["titlePointSize"], 31);
        assert!(layout_to_json(&io::card_layout(100, 100, false, false, 1, 1, &[], &[])).is_null());
    }
}
