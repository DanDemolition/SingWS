//! QObject exposed to QML as `DisplayPlacement`: where the audience window opens. All rules are `singws_cdg::window_rules`, proven equal to the app's
//! `_place_video_window_bottom_right`, `toggle_karaoke_window` and `_rect_on_attached_screen` by `tools/window_rules_parity.py`. The screens are the live ones
//! (`cpp/screens.cpp`), or a JSON file (`--display-screens FILE`) so a projector, a second display or an unplugged TV can be simulated. The saved geometry is
//! `karaoke_window_pos` in `settings.json` under SINGWS_HOME, read-only (`--display-saved JSON` overrides it, `none` means nothing saved).
//!
//! Topology JSON: `{"screens":[{"x","y","w","h","ax","ay","aw","ah"}...], "primary": index or -1, "cursor_screen": index or -1}`.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        type DisplayPlacement = super::DisplayPlacementRust;

        /// What to do with the window at startup, as JSON: `{"geometry":{x,y,w,h}|null,"resize":[w,h]|null,"move":[x,y]|null,"show":bool,"screens":n}`.
        /// `topology_file` empty = the live screens; `saved_json` empty = read settings.json, `none` = nothing saved.
        #[qinvokable]
        fn decide(self: &DisplayPlacement, topology_file: &QString, saved_json: &QString, fullscreen: bool, visible: bool) -> QString;
        /// The topology the rules would see, as JSON (live or from the file), for diagnostics.
        #[qinvokable]
        fn topology(self: &DisplayPlacement, topology_file: &QString) -> QString;
    }
}

use core::pin::Pin;
use cxx_qt_lib::QString;
use serde_json::{Value, json};
use singws_cdg::window_rules::{Placement, Rect, SavedPos, Val, place_video_window};
use std::ffi::{CStr, c_char};

unsafe extern "C" {
    fn singws_screens_json_live() -> *mut c_char;
    fn singws_screens_json_free(p: *mut c_char);
}

#[derive(Default)]
pub struct DisplayPlacementRust;

fn live_topology() -> String {
    unsafe {
        let p = singws_screens_json_live();
        let s = CStr::from_ptr(p).to_string_lossy().into_owned();
        singws_screens_json_free(p);
        s
    }
}

fn val(v: &Value, key: &str) -> Val {
    match v.get(key) {
        None => Val::Absent,
        Some(Value::Null) => Val::Null,
        Some(n) => match n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)) {
            Some(i) => Val::Int(i),
            None => Val::Null, // a string or object: the app's int() raises on it, which the rules treat like null
        },
    }
}

/// `settings["karaoke_window_pos"]` as the rules read it: `None` when it is missing or not a dictionary.
pub fn saved_from_json(v: &Value) -> Option<SavedPos> {
    if !v.is_object() {
        return None;
    }
    Some(SavedPos { x: val(v, "x"), y: val(v, "y"), width: val(v, "width"), height: val(v, "height") })
}

fn rect_of(s: &Value, prefix: &str) -> Rect {
    let n = |k: &str| s.get(format!("{prefix}{k}")).and_then(Value::as_i64).unwrap_or(0) as i32;
    Rect::new(n("x"), n("y"), n("w"), n("h"))
}

fn avail_of(s: &Value) -> Rect {
    let n = |k: &str| s.get(k).and_then(Value::as_i64).unwrap_or(0) as i32;
    Rect::new(n("ax"), n("ay"), n("aw"), n("ah"))
}

/// The decision for a topology (JSON text) and a saved geometry.
pub fn decide_from(topology: &str, saved: Option<&SavedPos>, fullscreen: bool, visible: bool) -> Result<(Placement, usize), String> {
    let t: Value = serde_json::from_str(topology).map_err(|e| format!("topology: {e}"))?;
    let list = t.get("screens").and_then(Value::as_array).cloned().unwrap_or_default();
    // the rules use the AVAILABLE geometry (a file may omit it: then the full geometry stands in)
    let avail: Vec<Rect> = list.iter().map(|s| if s.get("aw").is_some() { avail_of(s) } else { rect_of(s, "") }).collect();
    let pick = |key: &str| t.get(key).and_then(Value::as_i64).filter(|i| *i >= 0).and_then(|i| avail.get(i as usize).copied());
    let p = place_video_window(saved, fullscreen, visible, pick("cursor_screen"), pick("primary"), &avail);
    Ok((p, avail.len()))
}

fn placement_json(p: &Placement, screens: usize) -> Value {
    json!({
        "geometry": p.geometry.map(|r| json!({"x": r.x, "y": r.y, "w": r.w, "h": r.h})),
        "resize": p.resize.map(|(w, h)| json!([w, h])),
        "move": p.move_to.map(|(x, y)| json!([x, y])),
        "show": p.show,
        "screens": screens,
    })
}

fn read_topology(file: &str) -> Result<String, String> {
    if file.is_empty() { Ok(live_topology()) } else { std::fs::read_to_string(file).map_err(|e| format!("{file}: {e}")) }
}

fn saved_settings() -> Option<SavedPos> {
    let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS")))?;
    let text = std::fs::read_to_string(format!("{dir}/settings.json")).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    saved_from_json(v.get("karaoke_window_pos")?)
}

impl qobject::DisplayPlacement {
    fn decide(&self, topology_file: &QString, saved_json: &QString, fullscreen: bool, visible: bool) -> QString {
        let saved_text = saved_json.to_string();
        let saved = match saved_text.as_str() {
            "" => saved_settings(),
            "none" => None,
            t => serde_json::from_str::<Value>(t).ok().as_ref().and_then(saved_from_json),
        };
        let out = match read_topology(&topology_file.to_string()).and_then(|t| decide_from(&t, saved.as_ref(), fullscreen, visible)) {
            Ok((p, n)) => placement_json(&p, n),
            Err(e) => json!({"error": e}),
        };
        QString::from(out.to_string().as_str())
    }

    fn topology(&self, topology_file: &QString) -> QString {
        QString::from(read_topology(&topology_file.to_string()).unwrap_or_else(|e| json!({"error": e}).to_string()).as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TWO: &str = r#"{"screens":[{"x":0,"y":0,"w":1440,"h":900,"ax":0,"ay":25,"aw":1440,"ah":875},{"x":1440,"y":0,"w":1920,"h":1080,"ax":1440,"ay":0,"aw":1920,"ah":1080}],"primary":0,"cursor_screen":1}"#;

    #[test]
    fn first_run_lands_on_the_screen_under_the_cursor() {
        let (p, n) = decide_from(TWO, None, false, false).unwrap();
        assert_eq!(n, 2);
        assert_eq!(p.move_to, Some((1440 + 1920 - 480 - 24, 1080 - 360 - 24)));
        assert!(p.show);
    }

    #[test]
    fn a_saved_projector_position_is_pulled_back_when_the_projector_is_gone() {
        let one = r#"{"screens":[{"x":0,"y":0,"w":1680,"h":1050,"aw":1680,"ah":1030,"ax":0,"ay":20}],"primary":0,"cursor_screen":0}"#;
        let saved = saved_from_json(&json!({"x": 1680, "y": 0, "width": 1280, "height": 720})).unwrap();
        let (p, _) = decide_from(one, Some(&saved), false, false).unwrap();
        // 1680..2960 does not touch 0..1679: shrunk to fit and centred on the primary's available area
        assert_eq!(p.geometry, Some(Rect::new(200, 20 + 155, 1280, 720)));
        // with the projector attached it stays where it was saved
        let both = r#"{"screens":[{"x":0,"y":0,"w":1680,"h":1050},{"x":1680,"y":0,"w":1920,"h":1080}],"primary":0,"cursor_screen":0}"#;
        let (p, _) = decide_from(both, Some(&saved), false, false).unwrap();
        assert_eq!(p.geometry, Some(Rect::new(1680, 100, 1280, 720)), "a saved y of 0 comes back as 100 (the app's `or 100`)");
    }

    #[test]
    fn saved_json_forms() {
        assert!(saved_from_json(&json!([1, 2])).is_none());
        let s = saved_from_json(&json!({"x": null, "y": 5.9, "width": "wide"})).unwrap();
        assert_eq!((s.x, s.y, s.width, s.height), (Val::Null, Val::Int(5), Val::Null, Val::Absent));
    }
}
