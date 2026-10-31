//! Row text for the song lists, as a toolkit-free view model. Equal to `KaraokeApp._build_search_row_text` (library result rows), `_build_queue_song_row_text`
//! (queue song rows), `_queue_singer_status_label` (the singer's state line), `_split_display_artist_title_disc`, `_track_media_badge` / `_compact_media_badge`,
//! `_fmt_right_time` / `_fmt_mmss` and `normalize_karafun_duration_seconds`. Each row yields the left chunk, the right chunk and the tooltip; fitting them into one
//! line of a given pixel width (`_two_col_text`) needs font metrics and stays with the toolkit. Python errors the app does not catch (`.strip()` on a non-text value)
//! are `Err("raise: ...")`; anything not reproducible is declined.

use serde_json::Value;
use singws_dbw::pyval::{R, decline, get, py_float_from_str, py_lower, py_str, py_strip, truthy};

fn raise<T>(why: &str) -> R<T> {
    Err(format!("raise: {why}"))
}

/// `(v or "").strip()`: a truthy value that is not text makes Python raise
fn strip_text(v: &Value) -> R<String> {
    if !truthy(v) {
        return Ok(String::new());
    }
    match v {
        Value::String(s) => Ok(py_strip(s).to_string()),
        _ => raise("strip() of a non-text value"),
    }
}

/// `KaraokeApp._fmt_mmss(secs)` inside `_fmt_right_time`'s try: "" where Python raises
pub fn fmt_right_time(v: &Value) -> String {
    if !truthy(v) {
        return "--:--".into();
    }
    let f = match v {
        Value::Bool(_) => 1.0,
        Value::Number(n) => match n.as_f64() {
            Some(f) => f,
            None => return String::new(),
        },
        _ => return String::new(), // `str <= 0` is a TypeError
    };
    if f <= 0.0 {
        return "--:--".into();
    }
    if !f.is_finite() {
        return String::new();
    }
    let s = f.trunc() as i128;
    format!("{:02}:{:02}", s / 60, s % 60)
}

/// `KaraokeApp._fmt_m_ss(secs)` for a whole-second count (or None)
fn fmt_m_ss_int(secs: Option<i64>) -> String {
    match secs {
        Some(s) if s > 0 => format!("{}:{:02}", s / 60, s % 60),
        _ => "0:00".into(),
    }
}

/// `normalize_karafun_duration_seconds(value)`
pub fn normalize_karafun_duration(v: &Value) -> R<Option<i64>> {
    match v {
        Value::Null | Value::Bool(_) => return Ok(None),
        Value::Number(n) => {
            let f = n.as_f64().unwrap_or(f64::NAN);
            if !f.is_finite() || f <= 0.0 || f > 86400.0 {
                return Ok(None);
            }
            return Ok(Some(f.round_ties_even() as i64));
        }
        Value::String(_) => {}
        _ => return Ok(None), // str() of a list / dict is never a clock
    }
    let text = py_strip(v.as_str().unwrap_or(""));
    if text.is_empty() {
        return Ok(None);
    }
    if !text.is_ascii() {
        return decline("non-ASCII clock text");
    }
    let plain = text.bytes().all(|b| b.is_ascii_digit() || b == b'.') && {
        let mut it = text.splitn(2, '.');
        let a = it.next().unwrap_or("");
        let b = it.next();
        !a.is_empty() && a.bytes().all(|c| c.is_ascii_digit()) && b.is_none_or(|b| !b.is_empty() && b.bytes().all(|c| c.is_ascii_digit()))
    };
    if plain {
        let Some(f) = py_float_from_str(text)? else { return Ok(None) };
        if !f.is_finite() || f <= 0.0 || f > 86400.0 {
            return Ok(None);
        }
        return Ok(Some(f.round_ties_even() as i64));
    }
    let parts: Vec<&str> = text.split(':').collect();
    if !(parts.len() == 2 || parts.len() == 3) || !parts.iter().all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit())) {
        return Ok(None);
    }
    let mut vals = Vec::new();
    for p in &parts {
        match p.parse::<i64>() {
            Ok(n) => vals.push(n),
            Err(_) => return Ok(None),
        }
    }
    if *vals.last().unwrap() >= 60 || (vals.len() == 3 && vals[1] >= 60) {
        return Ok(None);
    }
    let total = vals.iter().fold(0i64, |a, p| a * 60 + p);
    Ok(if total <= 0 || total > 86400 { None } else { Some(total) })
}

/// `_compact_media_badge(value)`
pub fn compact_media_badge(v: &Value) -> R<String> {
    let s = if truthy(v) { py_str(v)? } else { String::new() };
    let t = py_strip(&s);
    if !t.is_ascii() {
        return decline("non-ASCII upper()");
    }
    let badge = t.to_ascii_uppercase().replace('.', "");
    if badge.is_empty() {
        return Ok(String::new());
    }
    Ok(match badge.as_str() {
        "MP3G" | "MP3+G" => "MP3+G".into(),
        "VIDEO" | "MOVIE" => "VIDEO".into(),
        "KARAOKE" | "KJ" => "KARAOKE".into(),
        "ZIP" | "CDG" | "MP3" | "MP4" | "M4V" | "MOV" | "MKV" | "WAV" => badge,
        _ => badge.chars().take(8).collect(),
    })
}

/// `_track_media_badge(data)`
pub fn track_media_badge(data: &Value) -> R<String> {
    let kind = if truthy(get(data, "type")) { get(data, "type") } else { get(data, "song_type") };
    let badge = compact_media_badge(kind)?;
    if !badge.is_empty() {
        return Ok(badge);
    }
    let path = get(data, "path");
    if !truthy(path) || py_strip(&py_str(path)?).is_empty() {
        return Ok(String::new());
    }
    compact_media_badge(&Value::String(crate::hero::media_badge_text(path)?))
}

/// `str.title()` for ASCII text
fn ascii_title(s: &str) -> R<String> {
    if !s.is_ascii() {
        return decline("non-ASCII title()");
    }
    let mut prev_letter = false;
    Ok(s.chars()
        .map(|c| {
            let out = if c.is_ascii_alphabetic() { if prev_letter { c.to_ascii_lowercase() } else { c.to_ascii_uppercase() } } else { c };
            prev_letter = c.is_ascii_alphabetic();
            out
        })
        .collect())
}

/// `Path(p).name`
fn path_name(p: &str) -> &str {
    p.split('/').rfind(|c| !c.is_empty() && *c != ".").unwrap_or("")
}

#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub left: String,
    pub right: String,
    pub tooltip: String,
}

fn join_nonempty(parts: &[String], sep: &str) -> String {
    parts.iter().filter(|p| !p.is_empty()).cloned().collect::<Vec<_>>().join(sep)
}

/// `_build_search_row_text(track)`
pub fn search_row(track: &Value) -> R<Row> {
    let artist = strip_text(get(track, "artist"))?;
    let title = strip_text(get(track, "title"))?;
    let disc_src = if truthy(get(track, "discid")) { get(track, "discid") } else { get(track, "disc_id") };
    let disc_id = strip_text(disc_src)?;
    let dur = if truthy(get(track, "duration")) { get(track, "duration") } else { get(track, "duration_secs") };
    let catalog_only = truthy(get(track, "karafun_catalog_only"));
    let left = if !artist.is_empty() {
        format!("{artist} \u{2022} {title}")
    } else if !title.is_empty() {
        title.clone()
    } else {
        strip_text(get(track, "display"))?
    };
    let media_badge = if catalog_only { String::new() } else { track_media_badge(track)? };
    let provider_v = get(track, "provider");
    let provider = py_lower(py_strip(&if truthy(provider_v) { py_str(provider_v)? } else { "local".into() }))?;
    let provider_badge = if !provider.is_empty() && provider != "local" {
        if provider.contains("karafun") { "KaraFun ONLINE".to_string() } else { ascii_title(&provider.replace('_', " "))? }
    } else {
        String::new()
    };
    let show_disc = !disc_id.is_empty() && !catalog_only;
    let mut right_parts = Vec::new();
    if show_disc {
        right_parts.push(disc_id.clone());
    }
    right_parts.push(provider_badge.clone());
    right_parts.push(media_badge.clone());
    let dur_txt = if truthy(dur) { fmt_right_time(dur) } else { String::new() };
    right_parts.push(dur_txt.clone());
    let right = join_nonempty(&right_parts, "  ");
    let mut tooltip_parts = vec![left.clone()];
    let mut meta = Vec::new();
    if !media_badge.is_empty() {
        meta.push(format!("Format: {media_badge}"));
    }
    if !provider_badge.is_empty() {
        meta.push(format!("Provider: {provider_badge}"));
    }
    if show_disc {
        meta.push(format!("Disc: {disc_id}"));
    }
    if truthy(dur) {
        meta.push(format!("Duration: {dur_txt}"));
    }
    if !meta.is_empty() {
        tooltip_parts.push(meta.join("  "));
    }
    let path_v = get(track, "path");
    let path = if truthy(path_v) { py_strip(&py_str(path_v)?).to_string() } else { String::new() };
    if !path.is_empty() {
        tooltip_parts.push(format!("File: {}", path_name(&path)));
    }
    Ok(Row { left, right, tooltip: join_nonempty(&tooltip_parts, "\n") })
}

/// The row the shell's Library tab shows for a song-index row: the track dict `song_index` returns (`SELECT *`), reduced to the columns the shell reads, run through
/// `_build_search_row_text`. `provider` is `songs.provider` (None: the column is missing, which reads as a local song).
pub fn library_row(r: &crate::SongRow, provider: Option<&str>) -> R<Row> {
    let track = serde_json::json!({"artist": r.artist, "title": r.title, "discid": r.discid, "duration_secs": r.duration_secs, "song_type": r.song_type, "path": r.path,
        "display": r.display, "provider": provider});
    search_row(&track)
}

/// `_split_display_artist_title_disc(display_name, fallback_disc)`
pub fn split_display(display_name: &str, fallback_disc: &str) -> (String, String, String) {
    let dn = py_strip(display_name);
    let fb = py_strip(fallback_disc).to_string();
    if dn.is_empty() {
        return (String::new(), String::new(), fb);
    }
    let parts: Vec<&str> = dn.split(" - ").map(py_strip).collect();
    match parts.len() {
        n if n >= 3 => (parts[0].to_string(), py_strip(&parts[1..n - 1].join(" - ")).to_string(), parts[n - 1].to_string()),
        2 => (parts[0].to_string(), parts[1].to_string(), fb),
        _ => (String::new(), dn.to_string(), fb),
    }
}

/// `_build_queue_song_row_text(entry)`
pub fn queue_song_row(entry: &Value) -> R<Row> {
    let display_name = strip_text(get(entry, "display_name"))?;
    let mut artist = strip_text(get(entry, "artist"))?;
    let mut title = strip_text(get(entry, "title"))?;
    let mut disc_id = strip_text(get(entry, "disc_id"))?;
    if artist.is_empty() || title.is_empty() {
        let (a2, t2, d2) = split_display(&display_name, &disc_id);
        if artist.is_empty() {
            artist = a2;
        }
        if title.is_empty() {
            title = t2;
        }
        if disc_id.is_empty() {
            disc_id = d2;
        }
    }
    let left_core = if !artist.is_empty() {
        format!("{artist} \u{2022} {title}")
    } else if !title.is_empty() {
        title.clone()
    } else {
        display_name.clone()
    };
    let left = format!("\u{2003}\u{2003}\u{2022} {left_core}"); // two EM SPACES, not ASCII spaces
    let duet = strip_text(get(entry, "duet_display"))?;
    let dur = get(entry, "duration");
    let estimated = truthy(get(entry, "duration_estimated"));
    let mut dur_txt = if truthy(dur) { fmt_right_time(dur) } else { String::new() };
    if !dur_txt.is_empty() && estimated {
        dur_txt = format!("~{}", fmt_m_ss_int(normalize_karafun_duration(dur)?));
    }
    let key = crate::waitlist::py_int(&if truthy(get(entry, "key")) { get(entry, "key").clone() } else { Value::from(0) })?.unwrap_or(0);
    let tempo = match get(entry, "tempo_percent") {
        Value::Null => None,
        v => crate::waitlist::py_int(v)?,
    };
    let mut right_parts = Vec::new();
    if !duet.is_empty() {
        right_parts.push("DUET".to_string());
    }
    right_parts.push(disc_id.clone());
    right_parts.push(dur_txt.clone());
    if key != 0 {
        right_parts.push(format!("KEY {key:+}"));
    }
    let off_speed = tempo.filter(|t| *t != 100);
    if let Some(t) = off_speed {
        right_parts.push(format!("SPD {t}%"));
    }
    let right = join_nonempty(&right_parts, "  ");
    let mut tooltip_parts = vec![left_core];
    if !duet.is_empty() {
        tooltip_parts.push(format!("Duet: {duet}"));
    }
    let mut detail = Vec::new();
    if !disc_id.is_empty() {
        detail.push(format!("Disc: {disc_id}"));
    }
    if !dur_txt.is_empty() {
        detail.push(if estimated { format!("Estimated duration: {dur_txt}") } else { format!("Duration: {dur_txt}") });
    }
    if key != 0 {
        detail.push(format!("Key: {key:+}"));
    }
    if let Some(t) = off_speed {
        detail.push(format!("Speed: {t}%"));
    }
    if !detail.is_empty() {
        tooltip_parts.push(detail.join("  "));
    }
    Ok(Row { left, right, tooltip: join_nonempty(&tooltip_parts, "\n") })
}

/// `_queue_singer_status_label(singer, is_rotation_start=, is_current=, is_next=)`
pub fn singer_status_label(singer: &Value, is_rotation_start: bool, is_current: bool, is_next: bool) -> R<String> {
    let none = Value::Null;
    let singer = if singer.is_object() { singer } else { &none };
    if truthy(get(singer, "skipped")) {
        return Ok("SKIPPED".into());
    }
    let songs_v = get(singer, "songs");
    let songs: &[Value] = match songs_v {
        v if !truthy(v) => &[],
        Value::Array(a) => a,
        _ => return decline("songs is not a list"),
    };
    let has_active = songs.iter().any(|s| if s.is_object() { !truthy(get(s, "skipped")) } else { true });
    let mut labels = Vec::new();
    if is_rotation_start {
        labels.push("ROTATION START");
    }
    if is_current {
        labels.push("CURRENTLY SINGING");
    } else if is_next && has_active {
        labels.push("NEXT UP");
    } else if !has_active {
        labels.push(if songs.is_empty() { "WAITING FOR SONG" } else { "NO READY SONG" });
    }
    Ok(labels.join(" \u{2022} "))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn right_side_times() {
        for (v, t) in [(json!(245), "04:05"), (json!(0), "--:--"), (json!(null), "--:--"), (json!(-3), "--:--"), (json!("245"), ""), (json!(59.9), "00:59"), (json!(true), "00:01"), (json!(3725), "62:05")] {
            assert_eq!(fmt_right_time(&v), t, "{v}");
        }
    }

    #[test]
    fn karafun_clocks_are_read_strictly() {
        for (v, t) in [(json!("3:45"), Some(225)), (json!("1:02:03"), Some(3723)), (json!("3:60"), None), (json!("0:00"), None), (json!("225"), Some(225)), (json!("225.5"), Some(226)), (json!("224.5"), Some(224)),
                       (json!(225.4), Some(225)), (json!(100000), None), (json!(true), None), (json!("x"), None), (json!(" 3:45 "), Some(225)), (json!("3:"), None), (json!("1:2:3:4"), None)] {
            assert_eq!(normalize_karafun_duration(&v).unwrap(), t, "{v}");
        }
    }

    #[test]
    fn badges_are_compact_and_the_type_beats_the_extension() {
        assert_eq!(compact_media_badge(&json!(".mp3g")).unwrap(), "MP3+G");
        assert_eq!(compact_media_badge(&json!("kj")).unwrap(), "KARAOKE");
        assert_eq!(compact_media_badge(&json!("flacfilexx")).unwrap(), "FLACFILE");
        assert_eq!(track_media_badge(&json!({"type": "movie", "path": "a.zip"})).unwrap(), "VIDEO");
        assert_eq!(track_media_badge(&json!({"path": "a.zip"})).unwrap(), "ZIP");
        assert_eq!(track_media_badge(&json!({"path": "  "})).unwrap(), "");
    }

    #[test]
    fn a_search_row_puts_disc_provider_format_and_length_on_the_right() {
        let t = json!({"artist": " Queen ", "title": "Radio Ga Ga", "discid": "SC8671", "path": "/lib/Queen/SC8671.zip", "duration_secs": 346, "provider": "karafun_online"});
        let r = search_row(&t).unwrap();
        assert_eq!(r.left, "Queen \u{2022} Radio Ga Ga");
        assert_eq!(r.right, "SC8671  KaraFun ONLINE  ZIP  05:46");
        assert_eq!(r.tooltip, "Queen \u{2022} Radio Ga Ga\nFormat: ZIP  Provider: KaraFun ONLINE  Disc: SC8671  Duration: 05:46\nFile: SC8671.zip");
        let c = search_row(&json!({"title": "T", "karafun_catalog_only": true, "discid": "X", "path": "a.zip", "provider": "my_stream"})).unwrap();
        assert_eq!((c.left.as_str(), c.right.as_str()), ("T", "My Stream"));
        assert_eq!(search_row(&json!({"display": " D "})).unwrap().left, "D");
        assert!(search_row(&json!({"artist": 5})).is_err());
    }

    #[test]
    fn a_queue_song_row_shows_duet_disc_length_key_and_speed() {
        let e = json!({"display_name": "Queen - Radio Ga Ga - SC1", "duet_display": "Ann & Bo", "duration": 200, "key": 2, "tempo_percent": 90});
        let r = queue_song_row(&e).unwrap();
        assert_eq!(r.left, "\u{2003}\u{2003}\u{2022} Queen \u{2022} Radio Ga Ga");
        assert_eq!(r.right, "DUET  SC1  03:20  KEY +2  SPD 90%");
        assert_eq!(r.tooltip, "Queen \u{2022} Radio Ga Ga\nDuet: Ann & Bo\nDisc: SC1  Duration: 03:20  Key: +2  Speed: 90%");
        let est = queue_song_row(&json!({"artist": "A", "title": "T", "duration": "3:45", "duration_estimated": true})).unwrap();
        assert_eq!(est.right, "", "a text duration has no right-hand time in the app");
        let est2 = queue_song_row(&json!({"artist": "A", "title": "T", "duration": 225, "duration_estimated": true, "key": -1, "tempo_percent": 100})).unwrap();
        assert_eq!(est2.right, "~3:45  KEY -1");
        assert!(est2.tooltip.contains("Estimated duration: ~3:45"));
    }

    #[test]
    fn display_names_split_on_spaced_dashes() {
        assert_eq!(split_display("A - B - C - D", ""), ("A".into(), "B - C".into(), "D".into()));
        assert_eq!(split_display("A - B", " X "), ("A".into(), "B".into(), "X".into()));
        assert_eq!(split_display("Just a title", "X"), ("".into(), "Just a title".into(), "X".into()));
        assert_eq!(split_display("  ", " X "), ("".into(), "".into(), "X".into()));
    }

    #[test]
    fn the_singer_state_line() {
        let s = json!({"songs": [{"skipped": true}]});
        assert_eq!(singer_status_label(&s, true, false, true).unwrap(), "ROTATION START \u{2022} NO READY SONG");
        assert_eq!(singer_status_label(&json!({"songs": []}), false, false, false).unwrap(), "WAITING FOR SONG");
        assert_eq!(singer_status_label(&json!({"songs": [{}]}), false, false, true).unwrap(), "NEXT UP");
        assert_eq!(singer_status_label(&json!({"songs": [{}]}), false, true, true).unwrap(), "CURRENTLY SINGING");
        assert_eq!(singer_status_label(&json!({"skipped": 1}), true, true, true).unwrap(), "SKIPPED");
        assert_eq!(singer_status_label(&json!("x"), false, false, false).unwrap(), "WAITING FOR SONG");
    }

    #[test]
    fn path_names_follow_pathlib() {
        for (p, n) in [("/a/b.zip", "b.zip"), ("a/b/", "b"), ("/", ""), (".", ""), ("./x", "x"), ("a//b", "b"), ("..", "..")] {
            assert_eq!(path_name(p), n, "{p}");
        }
    }

    #[test]
    fn clock_boundaries_and_out_of_range_minutes_are_refused() {
        for (v, t) in [(json!(86400), Some(86400)), (json!(86400.4), None), (json!("86400"), Some(86400)), (json!("24:00:00"), Some(86400)), (json!("1:59:59"), Some(7199)),
                       (json!("1:75:10"), None), (json!("1:60:00"), None), (json!("0:75:10"), None)] {
            assert_eq!(normalize_karafun_duration(&v).unwrap(), t, "{v}");
        }
    }

    #[test]
    fn an_old_format_song_entry_counts_as_a_ready_song() {
        // a list entry (not a dict) is never skipped: the singer is NEXT UP, not "NO READY SONG"
        let old = json!({"name": "Ann", "songs": [["/a/b.mp3", 1, "Duet"]]});
        assert_eq!(singer_status_label(&old, false, false, true).unwrap(), "NEXT UP");
        let skipped = json!({"name": "Ann", "songs": [{"skipped": true}]});
        assert_eq!(singer_status_label(&skipped, false, false, true).unwrap(), "NO READY SONG");
    }
}
