//! The Show tab's "now playing" hero card text, as a toolkit-free view model. Equal to `KaraokeApp._apply_redesign_hero_text` (the second line "Song · Artist",
//! the disc/brand line and the format badge source), `_hero_media_badge_text` (KJ / MP4 / ZIP / CDG / MP3 / WAV / the extension), `_hero_remaining_text` (the
//! time left, always with its minus sign) and `_fmt_karaoke_time` (`mm:ss`). Sizing and eliding the text to the card (font metrics) stays with the toolkit.
//! Anything Python would do that this module cannot reproduce exactly is declined (`Err("decline: ...")`).

use serde_json::Value;
use singws_dbw::pyval::{R, decline, py_float_from_str, py_lower, py_str_or_empty, py_strip};

/// `os.path.splitext(p)[1]` (POSIX)
fn splitext_ext(p: &str) -> &str {
    let base_start = p.rfind('/').map(|i| i + 1).unwrap_or(0);
    let base = &p[base_start..];
    let lead = base.len() - base.trim_start_matches('.').len();
    match base[lead..].rfind('.') {
        Some(i) => &base[lead + i..],
        None => "",
    }
}

/// `_hero_media_badge_text(song_path)`
pub fn media_badge_text(song_path: &Value) -> R<String> {
    let low = py_lower(py_strip(&py_str_or_empty(song_path)?))?;
    if low.is_empty() {
        return Ok("KJ".into());
    }
    for (suffix, badge) in [(".mp4", "MP4"), (".mkv", "MP4"), (".mov", "MP4"), (".zip", "ZIP"), (".cdg", "CDG"), (".mp3", "MP3"), (".wav", "WAV")] {
        if low.ends_with(suffix) {
            return Ok(badge.into());
        }
    }
    let ext = splitext_ext(&low).replace('.', "");
    if !ext.is_ascii() {
        return decline("non-ASCII upper()");
    }
    let up = ext.to_ascii_uppercase();
    Ok(if up.is_empty() { "KJ".into() } else { up })
}

#[derive(Debug, Clone, PartialEq)]
pub struct HeroText {
    /// the second line: "Song · Artist" (whichever exist), or "Ready to sing"
    pub subtitle: String,
    /// the third line: the disc / brand
    pub meta: String,
    /// the badge: "KaraFun" for a streaming entry, else the media badge for the path, empty without a path
    pub format_text: String,
}

/// `_apply_redesign_hero_text(title, artist, brand, path)` with the redesign on
pub fn hero_text(title: &Value, artist: &Value, brand: &Value, path: &Value) -> R<HeroText> {
    let title = py_strip(&py_str_or_empty(title)?).to_string();
    let artist = py_strip(&py_str_or_empty(artist)?).to_string();
    let joined = [title, artist].into_iter().filter(|p| !p.is_empty()).collect::<Vec<_>>().join(" \u{b7} ");
    let subtitle = if joined.is_empty() { "Ready to sing".to_string() } else { joined };
    let meta = py_strip(&py_str_or_empty(brand)?).to_string();
    let low = py_lower(&py_str_or_empty(path)?)?;
    let format_text = if low.starts_with("karafun_streaming:") {
        "KaraFun".to_string()
    } else if singws_dbw::pyval::truthy(path) {
        media_badge_text(path)?
    } else {
        String::new()
    };
    Ok(HeroText { subtitle, meta, format_text })
}

/// `_hero_remaining_text()` for the time-left label's text
pub fn remaining_text(label: &Value) -> R<String> {
    let txt = py_strip(&py_str_or_empty(label)?).to_string();
    Ok(if txt.is_empty() || txt.starts_with('-') { txt } else { format!("-{txt}") })
}

/// `_fmt_karaoke_time(sec)`: whole seconds (rounded half to even, clamped at zero; anything unusable is zero) as `mm:ss`
pub fn fmt_time(sec: &Value) -> R<String> {
    let f = match sec {
        Value::Bool(b) => Some(*b as u8 as f64),
        Value::Number(n) => n.as_f64(),
        Value::String(s) => py_float_from_str(s)?,
        _ => None,
    };
    let secs: i128 = match f {
        Some(f) if f.is_finite() => {
            if f.abs() > 1e30 {
                return decline("huge duration");
            }
            (f.round_ties_even() as i128).max(0)
        }
        _ => 0,
    };
    Ok(format!("{:02}:{:02}", secs / 60, secs % 60))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn badge_names_the_media_kind_by_extension() {
        for (p, b) in [("/a/Song.MP4", "MP4"), ("x.mkv", "MP4"), ("a.MOV", "MP4"), ("d.zip", "ZIP"), ("d.cdg", "CDG"), ("d.mp3", "MP3"), ("d.wav", "WAV"), ("d.flac", "FLAC"), ("noext", "KJ"), ("", "KJ"), ("  ", "KJ"), ("/a.b/c", "KJ"), ("x.tar.gz", "GZ")] {
            assert_eq!(media_badge_text(&json!(p)).unwrap(), b, "{p}");
        }
        assert_eq!(media_badge_text(&Value::Null).unwrap(), "KJ");
        assert_eq!(media_badge_text(&json!("x.mp3  ")).unwrap(), "MP3", "trailing spaces are stripped first");
    }

    #[test]
    fn subtitle_joins_what_exists_and_falls_back_to_ready_to_sing() {
        let h = hero_text(&json!(" Radio Ga Ga "), &json!("Queen"), &json!(" SC8671 "), &json!("/x/a.zip")).unwrap();
        assert_eq!((h.subtitle.as_str(), h.meta.as_str(), h.format_text.as_str()), ("Radio Ga Ga \u{b7} Queen", "SC8671", "ZIP"));
        assert_eq!(hero_text(&json!(""), &json!("Queen"), &Value::Null, &Value::Null).unwrap(), HeroText { subtitle: "Queen".into(), meta: "".into(), format_text: "".into() });
        assert_eq!(hero_text(&json!(null), &json!("  "), &json!(""), &json!("")).unwrap().subtitle, "Ready to sing");
        assert_eq!(hero_text(&json!("t"), &json!("a"), &json!(""), &json!("KaraFun_Streaming:123")).unwrap().format_text, "KaraFun");
    }

    #[test]
    fn remaining_time_always_carries_its_minus() {
        assert_eq!(remaining_text(&json!("03:21")).unwrap(), "-03:21");
        assert_eq!(remaining_text(&json!("-03:21")).unwrap(), "-03:21");
        assert_eq!(remaining_text(&json!("  ")).unwrap(), "");
        assert_eq!(remaining_text(&json!(" 1:00 ")).unwrap(), "-1:00");
    }

    #[test]
    fn karaoke_time_rounds_half_to_even_and_clamps() {
        for (v, t) in [(json!(0), "00:00"), (json!(59.5), "01:00"), (json!(60.5), "01:00"), (json!(61.5), "01:02"), (json!(-4), "00:00"), (json!(3599), "59:59"), (json!(6000), "100:00"), (json!("125"), "02:05"), (json!(null), "00:00"), (json!("x"), "00:00"), (json!([]), "00:00")] {
            assert_eq!(fmt_time(&v).unwrap(), t, "{v}");
        }
    }
}
