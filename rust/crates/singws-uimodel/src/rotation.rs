//! Panel 3 of the Rust-owned UI: the singer ROTATION as the audience view shows it, read-only, from the persisted `queue.json` and the song library.
//! Reproduces `RotationView.update_rotation` (rows, singer count, title with the rotation-start marker, up-next/on-deck lineup, now/next strips), the
//! queue's load-time migration, and the two display-name lookups (`display_name_for_queue_entry`, the host's `lookup_display_name`).
//!
//! Notes on the PyQt code this follows:
//!  * `RotationView` used to call `self._queue_entry_artist_title`, a method only the main window had; the AttributeError was swallowed, so the table rows never
//!    carried a separate artist. That was fixed in the app (RotationView now delegates to the main window's splitter) and this port follows the fixed code. The visible
//!    NOW SINGING bar keeps using the combined "Artist • Title" text.
//!  * a queue holding two singers whose names are equal ignoring case and spacing is merged by the app on load (`_merge_duplicate_rotation_singers`);
//!    that merge is not ported, so such a queue is DECLINED rather than shown differently from the app.

use crate::history::casefold;
use serde_json::Value;
use singws_dbw::pyval::{R, decline, get, is_py_space, py_lower, py_or, py_str, py_str_or_empty, py_strip, truthy};
use std::path::Path;

#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub number: String,
    pub singer: String,
    pub song: String,
    pub artist: String,
    pub duet: bool,
    pub combined: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct View {
    pub rows: Vec<Row>,
    pub count_label: String,
    pub title_label: String,
    pub next_singer: String,
    pub up_next: (String, String),
    pub on_deck: (String, String),
    pub now_strip: String,
    pub next_strip: String,
}

pub struct Inputs<'a> {
    pub db: Option<&'a Path>,
    /// the in-memory library (`tracks.json`) the host falls back to when a path is not in the index
    pub tracks: &'a [Value],
    pub now_singing: &'a str,
    pub playing: bool,
    pub active_singer_id: &'a str,
    pub live_title: &'a str,
}

fn basename(p: &str) -> &str {
    p.rsplit('/').next().unwrap_or("")
}

/// `re.sub(r"\s+", " ", str(name or "").strip()).casefold()` (`_queue_limit_name_key`)
fn name_key(v: &Value) -> R<String> {
    let s = py_str_or_empty(v)?;
    let collapsed: Vec<&str> = py_strip(&s).split(is_py_space).filter(|p| !p.is_empty()).collect();
    casefold(&collapsed.join(" "))
}

/// The queue after `load_data`'s migration: only dict singers, default flags, entries as dicts with a `song_info`.
pub fn migrate(raw: &Value) -> R<Vec<Value>> {
    let Some(list) = raw.as_array() else {
        return decline("queue.json is not a list");
    };
    let mut out: Vec<Value> = Vec::new();
    for singer in list {
        let Some(map) = singer.as_object() else { continue };
        let mut map = map.clone();
        map.entry("skipped").or_insert(Value::Bool(false));
        let songs = match map.get("songs") {
            None => Vec::new(),
            Some(Value::Array(a)) => a.clone(),
            Some(_) => return decline("a singer's songs are not a list (the app would fail to load the queue)"),
        };
        let mut new_songs: Vec<Value> = Vec::new();
        for entry in songs {
            match &entry {
                Value::Object(m) if m.contains_key("song_info") => {
                    let mut m = m.clone();
                    m.entry("skipped").or_insert(Value::Bool(false));
                    new_songs.push(Value::Object(m));
                }
                Value::Array(a) => {
                    let mut m = serde_json::Map::new();
                    m.insert("song_info".into(), a.first().cloned().unwrap_or(Value::Null));
                    m.insert("key".into(), a.get(1).cloned().unwrap_or(Value::from(0)));
                    m.insert("skipped".into(), Value::Bool(false));
                    if a.len() >= 3 {
                        m.insert("duet_display".into(), a[2].clone());
                    }
                    new_songs.push(Value::Object(m));
                }
                other => {
                    let mut m = serde_json::Map::new();
                    m.insert("song_info".into(), other.clone());
                    m.insert("key".into(), Value::from(0));
                    m.insert("skipped".into(), Value::Bool(false));
                    new_songs.push(Value::Object(m));
                }
            }
        }
        map.insert("songs".into(), Value::Array(new_songs));
        out.push(Value::Object(map));
    }
    let mut seen: Vec<String> = Vec::new();
    for s in &out {
        let key = name_key(get(s, "name"))?;
        if key.is_empty() {
            continue;
        }
        if seen.contains(&key) {
            return decline("duplicate singer names (the app merges them on load; not ported)");
        }
        seen.push(key);
    }
    Ok(out)
}

/// `str(value)` for the scalar types an f-string would show; containers are declined
fn text_of(v: &Value) -> R<String> {
    py_str(v)
}

fn display_from_track_text(disp: &str, artist_title_only: bool) -> String {
    if !artist_title_only {
        return disp.to_string();
    }
    let parts: Vec<&str> = disp.split(" - ").map(py_strip).filter(|p| !p.is_empty()).collect();
    let artist = parts.first().copied().unwrap_or("");
    let title = parts.get(1).copied().unwrap_or("");
    if !artist.is_empty() && !title.is_empty() { format!("{artist} • {title}") } else if !artist.is_empty() { artist.to_string() } else if !title.is_empty() { title.to_string() } else { disp.to_string() }
}

/// The host's `lookup_display_name(path, artist_title_only=True)`: the song index first, then the in-memory library, then the file name.
fn lookup_display_name(path: &str, inp: &Inputs) -> R<String> {
    if let Some(db) = inp.db {
        if db.exists() {
            let con = singws_db::open_readonly(db)?;
            let row = con
                .query_row("SELECT display FROM songs WHERE path = ?", [path], |r| r.get_ref(0).map(|v| match v {
                    rusqlite::types::ValueRef::Text(t) => Some(String::from_utf8_lossy(t).into_owned()),
                    _ => None,
                }))
                .ok();
            // a row whose display is not text makes Python's `.split` raise inside its try/except: fall through to the library
            if let Some(Some(disp)) = row {
                return Ok(display_from_track_text(&disp, true));
            }
        }
    }
    let lowered = py_lower(path)?;
    for t in inp.tracks {
        let Some(obj) = t.as_object() else {
            return decline("a non-object entry in tracks.json (Python would raise)");
        };
        let tp = py_str_or_empty(obj.get("path").unwrap_or(&Value::Null))?;
        if py_lower(&tp)? == lowered {
            return match obj.get("display") {
                None => Ok(basename(path).to_string()).map(|b| display_from_track_text(&b, true)),
                Some(Value::String(d)) => Ok(display_from_track_text(d, true)),
                Some(_) => decline("a library entry whose display is not text (Python would raise)"),
            };
        }
    }
    Ok(basename(path).to_string())
}

/// `RotationView.display_name_for_queue_entry`
fn display_name(entry: &Value, path_value: &Value, inp: &Inputs) -> R<String> {
    let artist = py_strip(&py_str_or_empty(get(entry, "artist"))?).to_string();
    let title = py_strip(&py_str_or_empty(get(entry, "title"))?).to_string();
    if !artist.is_empty() && !title.is_empty() {
        return Ok(format!("{artist} • {title}"));
    }
    if !title.is_empty() {
        return Ok(title);
    }
    let display = py_strip(&py_str_or_empty(get(entry, "display_name"))?).to_string();
    if !display.is_empty() && py_lower(&display)? != "unknown" {
        let mut parts: Vec<String> = display.split(" - ").map(|p| py_strip(p).to_string()).filter(|p| !p.is_empty()).collect();
        if parts.last().is_some_and(|p| py_lower(p).map(|l| l == "karafun").unwrap_or(false)) {
            parts.pop();
        }
        if parts.len() >= 2 {
            return Ok(format!("{} • {}", parts[0], parts[1]));
        }
        if let Some(first) = parts.first() {
            return Ok(first.clone());
        }
    }
    // Python only calls str() on the path when it has to look the song up
    lookup_display_name(&text_of(path_value)?, inp)
}

/// What the host's `lookup_display_name(path, artist_title_only=False)` gives: the full display text, or "Python raised".
enum Full {
    Text(String),
    Raised,
}

/// `str(value or "")` for the display column of a song-index row or a library entry
fn display_text(v: &Value) -> R<String> {
    py_str_or_empty(v)
}

fn lookup_full(path: &str, inp: &Inputs) -> R<Full> {
    if let Some(db) = inp.db {
        if db.exists() {
            let con = singws_db::open_readonly(db)?;
            let row = con
                .query_row("SELECT display FROM songs WHERE path = ?", [path], |r| {
                    r.get_ref(0).map(|v| match v {
                        rusqlite::types::ValueRef::Null => Some(Value::Null),
                        rusqlite::types::ValueRef::Text(t) => Some(Value::String(String::from_utf8_lossy(t).into_owned())),
                        rusqlite::types::ValueRef::Integer(i) => Some(Value::from(i)),
                        rusqlite::types::ValueRef::Real(f) => serde_json::Number::from_f64(f).map(Value::Number),
                        rusqlite::types::ValueRef::Blob(_) => None,
                    })
                })
                .ok();
            match row {
                Some(Some(v)) => return Ok(Full::Text(display_text(&v)?)), // a row that exists answers, even with a NULL display
                Some(None) => return decline("a song-index display of an unsupported type"),
                None => {}
            }
        }
    }
    let lowered = py_lower(path)?;
    for t in inp.tracks {
        let Some(obj) = t.as_object() else { return Ok(Full::Raised) };
        let tp = py_str_or_empty(obj.get("path").unwrap_or(&Value::Null))?;
        if py_lower(&tp)? == lowered {
            return Ok(Full::Text(match obj.get("display") {
                None => basename(path).to_string(),
                Some(v) => display_text(v)?,
            }));
        }
    }
    Ok(Full::Text(basename(path).to_string()))
}

/// `_split_display_artist_title_disc(display)` without the disc
fn split_display(display: &str) -> (String, String) {
    let dn = py_strip(display);
    if dn.is_empty() {
        return (String::new(), String::new());
    }
    let parts: Vec<&str> = dn.split(" - ").map(py_strip).collect();
    if parts.len() >= 3 {
        (parts[0].to_string(), py_strip(&parts[1..parts.len() - 1].join(" - ")).to_string())
    } else if parts.len() == 2 {
        (parts[0].to_string(), parts[1].to_string())
    } else {
        (String::new(), dn.to_string())
    }
}

/// The main window's `_queue_entry_artist_title(entry)`
fn artist_title(entry: &Value, inp: &Inputs) -> R<(String, String)> {
    let mut artist = py_strip(&py_str_or_empty(get(entry, "artist"))?).to_string();
    let mut title = py_strip(&py_str_or_empty(get(entry, "title"))?).to_string();
    if !artist.is_empty() && !title.is_empty() {
        return Ok((artist, title));
    }
    // `_queue_entry_primary_path`: the first element of a list, or the value itself, as text
    let song_info = get(entry, "song_info");
    let path = match song_info {
        Value::Array(a) => match a.first() {
            Some(p) => py_str_or_empty(p)?,
            None => return Ok((artist, title)), // IndexError inside the try: swallowed
        },
        other => py_str_or_empty(other)?,
    };
    if !path.is_empty() {
        match lookup_full(&path, inp)? {
            Full::Text(disp) => {
                let (a2, t2) = split_display(&disp);
                if artist.is_empty() {
                    artist = a2;
                }
                if title.is_empty() {
                    title = t2;
                }
            }
            Full::Raised => {}
        }
    }
    Ok((py_strip(&artist).to_string(), py_strip(&title).to_string()))
}

pub fn view(queue: &[Value], inp: &Inputs) -> R<View> {
    let mut rows: Vec<Row> = Vec::new();
    let mut ready: Vec<String> = Vec::new();
    let mut rotation_start_name = String::new();
    let active_id = if inp.playing { inp.active_singer_id } else { "" };
    let mut num = 1usize;
    for singer in queue {
        if truthy(get(singer, "skipped")) {
            continue;
        }
        if !active_id.is_empty() && py_str_or_empty(get(singer, "singer_id"))? == active_id {
            continue;
        }
        let songs = singer.get("songs").and_then(Value::as_array).cloned().unwrap_or_default();
        let Some(first) = songs.iter().find(|s| !truthy(get(s, "skipped"))) else { continue };
        let song_info = get(first, "song_info");
        let duet_display = get(first, "duet_display");
        let path_value = match song_info {
            Value::Array(a) => match a.first() {
                Some(p) => p.clone(),
                None => return decline("an empty song_info list (Python would raise)"),
            },
            other => other.clone(),
        };
        let mut name = display_name(first, &path_value, inp)?;
        let singer_display_value = if truthy(duet_display) { duet_display } else { get(singer, "name") };
        let singer_display = text_of(singer_display_value)?;
        if truthy(get(singer, "rotation_marker")) {
            let empty = Value::String(String::new());
            let start = py_or(&[singer_display_value, get(singer, "name"), &empty]);
            rotation_start_name = py_strip(&py_str_or_empty(start)?).to_string();
            name = format!("{name}  •  Rotation start");
        }
        let text = format!("{num}. {singer_display}  •  {name}");
        let (row_artist, row_song) = match artist_title(first, inp)? {
            (a, t) if !a.is_empty() && !t.is_empty() => (a, t),
            _ => (String::new(), name.clone()),
        };
        rows.push(Row {
            number: num.to_string(),
            singer: singer_display.clone(),
            song: row_song,
            artist: row_artist,
            duet: truthy(duet_display),
            combined: name,
            text,
        });
        ready.push(singer_display);
        num += 1;
    }
    let count = rows.len();
    let count_label = format!("{count} SINGER{}", if count != 1 { "S" } else { "" });
    let title_label =
        if rotation_start_name.is_empty() { "SINGER ROTATION".to_string() } else { format!("SINGER ROTATION  •  START: {rotation_start_name}") };
    let now_key = casefold(py_strip(inp.now_singing))?;
    let mut next_singer = String::new();
    for n in &ready {
        let cand = py_strip(n);
        if !cand.is_empty() && casefold(cand)? != now_key {
            next_singer = cand.to_string();
            break;
        }
    }
    // the first two rows that are not the singer on stage
    let mut lineup: Vec<(String, String)> = Vec::new();
    for r in &rows {
        if casefold(py_strip(&r.singer))? == now_key {
            continue;
        }
        let detail = if !r.song.is_empty() && !r.artist.is_empty() { format!("{}\n{}", r.song, r.artist) } else if !r.song.is_empty() { r.song.clone() } else { r.artist.clone() };
        lineup.push((r.singer.clone(), detail));
        if lineup.len() >= 2 {
            break;
        }
    }
    let mut now_row: Option<&Row> = None;
    for r in &rows {
        if casefold(py_strip(&r.singer))? == now_key {
            now_row = Some(r);
            break;
        }
    }
    let mut now_strip = String::new();
    if let Some(r) = now_row {
        // the bar names artist and title, so it uses the combined text
        let shown = if !r.combined.is_empty() { &r.combined } else { &r.song };
        now_strip = if shown.is_empty() { r.singer.clone() } else { format!("{} - {}", r.singer, shown) };
    } else if !inp.now_singing.is_empty() && inp.now_singing != "Singer Rotation" {
        now_strip = inp.now_singing.to_string();
    }
    let mut next_strip = String::new();
    if let Some(first) = lineup.first() {
        let next_detail = first.1.split('\n').next().unwrap_or("");
        next_strip = if next_detail.is_empty() { first.0.clone() } else { format!("{} - {}", first.0, next_detail) };
    }
    let live_title = py_strip(inp.live_title);
    if !live_title.is_empty() && inp.playing {
        now_strip = format!("{} — {}", inp.now_singing, live_title);
    }
    Ok(View {
        rows,
        count_label,
        title_label,
        next_singer,
        up_next: lineup.first().cloned().unwrap_or_default(),
        on_deck: lineup.get(1).cloned().unwrap_or_default(),
        now_strip,
        next_strip,
    })
}

/// What the app publishes in `live_state.json` (setting `publish_live_state`): who is on stage right now.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct LiveState {
    pub playing: bool,
    pub singer_id: String,
    pub singer: String,
    pub title: String,
}

pub fn process_alive(pid: i64) -> bool {
    if pid <= 0 || pid > i32::MAX as i64 {
        return false;
    }
    // SAFETY: kill with signal 0 only checks whether the process exists (and may be signalled).
    let r = unsafe { libc::kill(pid as libc::pid_t, 0) };
    r == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

/// `None` when there is no file, it is unreadable, it is from another version, or the app that wrote it is no longer running.
pub fn load_live_state(path: &Path) -> Option<LiveState> {
    let text = std::fs::read_to_string(path).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    if v.get("version").and_then(Value::as_i64) != Some(1) || !process_alive(v.get("pid").and_then(Value::as_i64)?) {
        return None;
    }
    let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    Some(LiveState { playing: v.get("playing").and_then(Value::as_bool).unwrap_or(false), singer_id: s("singer_id"), singer: s("singer"), title: s("title") })
}

/// Read `queue.json` (read-only) and migrate it.
pub fn load_queue(path: &Path) -> R<Vec<Value>> {
    let bytes = std::fs::read(path).map_err(|e| format!("queue unavailable: {e}"))?;
    let text = singws_dbw::index::decode_ignore(&bytes);
    singws_store::reject_big_integers(&text).map_err(|e| format!("decline: {e}"))?;
    let raw: Value = serde_json::from_str(if text.trim().is_empty() { "[]" } else { &text }).map_err(|e| format!("json: {e}"))?;
    migrate(&raw)
}

/// `tracks.json` for the in-memory fallback; a missing file is an empty library, a damaged one is an error.
pub fn load_tracks(path: &Path) -> R<Vec<Value>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let bytes = std::fs::read(path).map_err(|e| format!("tracks unavailable: {e}"))?;
    let text = singws_dbw::index::decode_ignore(&bytes);
    singws_dbw::index::parse_tracks(&text)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn inputs<'a>(tracks: &'a [Value], now: &'a str) -> Inputs<'a> {
        Inputs { db: None, tracks, now_singing: now, playing: false, active_singer_id: "", live_title: "" }
    }

    fn q(v: Value) -> Vec<Value> {
        migrate(&v).unwrap()
    }

    #[test]
    fn migration_normalises_every_entry_shape_like_the_loader() {
        let m = q(json!([
            "not a singer",
            {"name": "A", "songs": [{"song_info": "/a.zip"}, ["/b.zip", 3, "Duo"], "/c.zip", {"artist": "no info"}, ["/d.zip"]]},
            {"name": "B"}
        ]));
        assert_eq!(m.len(), 2, "non-dict singers are dropped");
        assert_eq!(m[0]["skipped"], json!(false));
        let songs = m[0]["songs"].as_array().unwrap();
        assert_eq!(songs[0], json!({"song_info": "/a.zip", "skipped": false}));
        assert_eq!(songs[1], json!({"song_info": "/b.zip", "key": 3, "skipped": false, "duet_display": "Duo"}));
        assert_eq!(songs[2], json!({"song_info": "/c.zip", "key": 0, "skipped": false}));
        assert_eq!(songs[3], json!({"song_info": {"artist": "no info"}, "key": 0, "skipped": false}), "a dict without song_info is wrapped whole");
        assert_eq!(songs[4], json!({"song_info": "/d.zip", "key": 0, "skipped": false}));
        assert_eq!(m[1]["songs"], json!([]));
    }

    #[test]
    fn migration_declines_what_the_app_would_merge_or_fail_on() {
        assert!(migrate(&json!({})).unwrap_err().starts_with("decline:"));
        assert!(migrate(&json!([{"name": "Al", "songs": null}])).unwrap_err().starts_with("decline:"));
        assert!(migrate(&json!([{"name": "Al", "songs": "x"}])).unwrap_err().starts_with("decline:"));
        let dup = migrate(&json!([{"name": "Al  Bo"}, {"name": " al bo "}]));
        assert!(dup.unwrap_err().contains("duplicate singer names"));
        assert!(migrate(&json!([{"name": ""}, {"name": ""}, {}])).is_ok(), "blank names are never grouped");
        assert!(migrate(&json!([{"name": "STRASSE"}, {"name": "straße"}])).is_err(), "grouping uses casefold, so ß equals ss");
    }

    #[test]
    fn rows_skip_skipped_singers_and_songs_and_number_the_rest() {
        let queue = q(json!([
            {"name": "A", "skipped": true, "songs": [{"song_info": "/a", "artist": "X", "title": "Y"}]},
            {"name": "B", "songs": [{"song_info": "/b", "artist": "X", "title": "Y", "skipped": true}, {"song_info": "/b2", "artist": "P", "title": "Q"}]},
            {"name": "C", "songs": []},
            {"name": "D", "songs": [{"song_info": "/d", "title": "Only Title"}]},
        ]));
        let v = view(&queue, &inputs(&[], "Singer Rotation")).unwrap();
        assert_eq!(v.rows.len(), 2);
        assert_eq!(v.rows[0].text, "1. B  •  P • Q");
        assert_eq!(v.rows[1].text, "2. D  •  Only Title");
        assert_eq!(v.count_label, "2 SINGERS");
        assert_eq!(v.rows[0].artist, "P", "the table row carries the artist on its own");
        assert_eq!(v.rows[0].song, "Q", "and the title alone");
        assert_eq!(v.rows[0].combined, "P • Q");
        assert_eq!((v.rows[1].artist.as_str(), v.rows[1].song.as_str()), ("", "Only Title"), "an entry without both parts keeps the combined text");
        assert_eq!(view(&queue[..1], &inputs(&[], "x")).unwrap().count_label, "0 SINGERS");
        assert_eq!(view(&queue[3..], &inputs(&[], "x")).unwrap().count_label, "1 SINGER");
    }

    #[test]
    fn duets_replace_the_singer_name_and_the_rotation_start_is_marked() {
        let queue = q(json!([
            {"name": "Al", "rotation_marker": true, "songs": [{"song_info": "/a", "artist": "X", "title": "Y", "duet_display": "Al + Bo"}]},
            {"name": "Cy", "rotation_marker": true, "songs": [{"song_info": "/c", "artist": "X", "title": "Z"}]},
        ]));
        let v = view(&queue, &inputs(&[], "Singer Rotation")).unwrap();
        assert!(v.rows[0].duet && !v.rows[1].duet);
        assert_eq!(v.rows[0].singer, "Al + Bo");
        assert_eq!(v.rows[0].text, "1. Al + Bo  •  X • Y  •  Rotation start");
        assert_eq!(v.title_label, "SINGER ROTATION  •  START: Cy", "the LAST marked row names the start");
        let none = view(&q(json!([{"name": "Al", "songs": [{"song_info": "/a", "title": "T"}]}])), &inputs(&[], "x")).unwrap();
        assert_eq!(none.title_label, "SINGER ROTATION");
    }

    #[test]
    fn display_names_come_from_the_entry_then_its_display_name_then_the_library() {
        let tracks = vec![json!({"path": "/Lib/Known.zip", "display": "Band - Tune - KV1"}), json!({"path": "/lib/nodisplay.zip"}), json!({"path": "/lib/solo.zip", "display": "Solo"})];
        let inp = inputs(&tracks, "");
        let name = |entry: Value, path: &str| display_name(&entry, &Value::String(path.into()), &inp).unwrap();
        assert_eq!(name(json!({"artist": " A ", "title": " T "}), "/x"), "A • T");
        assert_eq!(name(json!({"title": "Only"}), "/x"), "Only");
        assert_eq!(name(json!({"artist": "A"}), "/x/file name.zip"), "file name.zip", "an artist alone is not enough: falls back to the file name");
        assert_eq!(name(json!({"display_name": "Artist - Title - KaraFun"}), "/x"), "Artist • Title");
        assert_eq!(name(json!({"display_name": "Artist - KARAFUN"}), "/x"), "Artist");
        assert_eq!(name(json!({"display_name": "A - B - C"}), "/x"), "A • B");
        assert_eq!(name(json!({"display_name": "unknown"}), "/x/y.zip"), "y.zip");
        assert_eq!(name(json!({"display_name": " - "}), "/x/y.zip"), "-", "the edge spaces are stripped first, so the separator never splits");
        assert_eq!(name(json!({"display_name": "karafun"}), "/x/y.zip"), "y.zip", "a lone KaraFun marker leaves no parts: falls through to the lookup");
        assert_eq!(name(json!({}), "/lib/known.ZIP"), "Band • Tune", "library paths compare case-insensitively, keeping the artist • title form");
        assert_eq!(name(json!({}), "/lib/solo.zip"), "Solo");
        assert_eq!(name(json!({}), "/lib/nodisplay.zip"), "nodisplay.zip", "a library entry without a display uses the file name");
        assert_eq!(name(json!({}), "/nowhere/else.mp3"), "else.mp3");
    }

    #[test]
    fn the_song_index_wins_over_the_in_memory_library() {
        let dir = std::env::temp_dir().join(format!("singws_rotation_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let db = dir.join("s.db");
        let _ = std::fs::remove_file(&db);
        let con = rusqlite::Connection::open(&db).unwrap();
        con.execute_batch("CREATE TABLE songs (path TEXT, display TEXT); INSERT INTO songs VALUES ('/idx/a.zip', 'Idx - Song'), ('/idx/null.zip', NULL);").unwrap();
        drop(con);
        let tracks = vec![json!({"path": "/idx/a.zip", "display": "Memory - Other"}), json!({"path": "/idx/null.zip", "display": "Mem - Fallback"})];
        let inp = Inputs { db: Some(&db), tracks: &tracks, now_singing: "", playing: false, active_singer_id: "", live_title: "" };
        let name = |p: &str| display_name(&json!({}), &Value::String(p.into()), &inp).unwrap();
        assert_eq!(name("/idx/a.zip"), "Idx • Song");
        assert_eq!(name("/idx/null.zip"), "Mem • Fallback", "a NULL display in the index falls through to memory, as Python's failed .split does");
        assert_eq!(name("/IDX/A.ZIP"), "Memory • Other", "the index matches the path exactly; memory ignores case");
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn a_library_entry_whose_display_is_not_text_is_declined() {
        let tracks = vec![json!({"path": "/p.zip", "display": 5}), json!({"path": "/q.zip", "display": null})];
        let inp = inputs(&tracks, "");
        assert!(display_name(&json!({}), &json!("/p.zip"), &inp).unwrap_err().starts_with("decline:"));
        assert!(display_name(&json!({}), &json!("/q.zip"), &inp).unwrap_err().starts_with("decline:"));
        assert!(display_name(&json!({}), &json!("/other.zip"), &inp).is_ok());
        let bad = vec![json!(5)];
        assert!(display_name(&json!({}), &json!("/p.zip"), &inputs(&bad, "")).unwrap_err().starts_with("decline:"));
    }

    #[test]
    fn the_playing_singer_leaves_the_list_and_the_strips_follow_the_stage() {
        let queue = q(json!([
            {"name": "Al", "singer_id": "a", "songs": [{"song_info": "/1", "artist": "X", "title": "One"}]},
            {"name": "Bo", "singer_id": "b", "songs": [{"song_info": "/2", "artist": "X", "title": "Two"}]},
            {"name": "Cy", "singer_id": "c", "songs": [{"song_info": "/3", "artist": "X", "title": "Three"}]},
            {"name": "Di", "singer_id": "d", "songs": [{"song_info": "/4", "artist": "X", "title": "Four"}]},
        ]));
        let mut inp = inputs(&[], "al");
        let v = view(&queue, &inp).unwrap();
        assert_eq!(v.rows.len(), 4, "not playing: nobody is hidden");
        assert_eq!(v.up_next, ("Bo".into(), "Two\nX".into()), "the singer on stage (compared ignoring case) is not 'up next'; the detail is title then artist");
        assert_eq!(v.on_deck.0, "Cy");
        assert_eq!(v.next_singer, "Bo");
        assert_eq!(v.now_strip, "Al - X • One", "the visible bar names artist and title");
        assert_eq!(v.next_strip, "Bo - Two", "the (hidden) next strip uses the title line only");
        inp.playing = true;
        inp.active_singer_id = "a";
        inp.live_title = "  Live  ";
        let v = view(&queue, &inp).unwrap();
        assert_eq!(v.rows.len(), 3, "the playing singer is removed from the audience list");
        assert_eq!(v.now_strip, "al — Live");
        // the id and the live title are ignored while nothing is playing
        let mut quiet = inputs(&[], "al");
        quiet.active_singer_id = "a";
        quiet.live_title = "Live";
        let q = view(&queue, &quiet).unwrap();
        assert_eq!(q.rows.len(), 4);
        assert_eq!(q.now_strip, "Al - X • One");
        let idle = view(&queue, &inputs(&[], "Someone Else")).unwrap();
        assert_eq!(idle.now_strip, "Someone Else");
        assert_eq!(view(&queue, &inputs(&[], "Singer Rotation")).unwrap().now_strip, "");
        let blank = view(&queue, &inputs(&[], "")).unwrap();
        assert_eq!((blank.up_next.0.as_str(), blank.now_strip.as_str()), ("Al", ""), "with no one on stage the first two rows are up next and on deck");
    }

    #[test]
    fn the_artist_title_split_uses_the_entry_then_the_librarys_display_text() {
        let tracks = vec![
            json!({"path": "/lib/a.zip", "display": "Band - Tune - KV9"}),
            json!({"path": "/lib/b.zip", "display": "Band - Long - Name - Disc"}),
            json!({"path": "/lib/c.zip", "display": "Just Two - Parts"}),
            json!({"path": "/lib/d.zip", "display": "No dashes"}),
            json!({"path": "/lib/e.zip"}),
            json!({"path": "/lib/f.zip", "display": null}),
        ];
        let inp = inputs(&tracks, "");
        let at = |entry: Value| artist_title(&entry, &inp).unwrap();
        assert_eq!(at(json!({"artist": " A ", "title": " T ", "song_info": "/lib/a.zip"})), ("A".into(), "T".into()));
        assert_eq!(at(json!({"song_info": "/lib/a.zip"})), ("Band".into(), "Tune".into()), "three parts: artist, title, disc");
        assert_eq!(at(json!({"song_info": ["/lib/b.zip", "/lib/b.mp3"]})), ("Band".into(), "Long - Name".into()), "the middle parts rejoin");
        assert_eq!(at(json!({"song_info": "/lib/c.zip"})), ("Just Two".into(), "Parts".into()));
        assert_eq!(at(json!({"song_info": "/lib/d.zip"})), ("".into(), "No dashes".into()));
        assert_eq!(at(json!({"song_info": "/lib/e.zip"})), ("".into(), "e.zip".into()), "no display means the file name");
        assert_eq!(at(json!({"song_info": "/lib/f.zip"})), ("".into(), "".into()), "a null display is empty text, not an error");
        assert_eq!(at(json!({"artist": "Given", "song_info": "/lib/a.zip"})), ("Given".into(), "Tune".into()), "a missing half is filled from the library");
        assert_eq!(at(json!({"title": "Given", "song_info": "/lib/a.zip"})), ("Band".into(), "Given".into()));
        assert_eq!(at(json!({"song_info": ""})), ("".into(), "".into()));
        assert_eq!(at(json!({"song_info": []})), ("".into(), "".into()), "an IndexError inside the app's try is swallowed");
        let bad = vec![json!(5)];
        assert_eq!(artist_title(&json!({"song_info": "/x"}), &inputs(&bad, "")).unwrap(), ("".into(), "".into()), "a damaged library entry makes the lookup raise inside the try");
    }

    #[test]
    fn live_state_is_ignored_unless_its_app_is_still_running() {
        let dir = std::env::temp_dir().join(format!("singws_live_state_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("live_state.json");
        let write = |pid: i64, version: i64| {
            std::fs::write(&p, json!({"version": version, "pid": pid, "playing": true, "singer_id": "s1", "singer": "Dana", "title": "Song"}).to_string()).unwrap()
        };
        write(std::process::id() as i64, 1);
        assert_eq!(load_live_state(&p), Some(LiveState { playing: true, singer_id: "s1".into(), singer: "Dana".into(), title: "Song".into() }));
        write(std::process::id() as i64, 2);
        assert_eq!(load_live_state(&p), None, "an unknown version is ignored");
        write(2_000_000_000, 1);
        assert_eq!(load_live_state(&p), None, "the writer is gone (a crash must not leave a singer on stage)");
        write(0, 1);
        assert_eq!(load_live_state(&p), None);
        std::fs::write(&p, "{").unwrap();
        assert_eq!(load_live_state(&p), None);
        std::fs::write(&p, r#"{"version": 1}"#).unwrap();
        assert_eq!(load_live_state(&p), None, "no pid");
        assert_eq!(load_live_state(&dir.join("absent.json")), None);
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn an_empty_song_info_list_makes_python_raise_so_it_is_declined() {
        let queue = q(json!([{"name": "A", "songs": [{"song_info": []}]}]));
        assert!(view(&queue, &inputs(&[], "x")).unwrap_err().starts_with("decline:"));
    }

    #[test]
    fn load_queue_reads_a_file_and_an_empty_file_is_an_empty_queue() {
        let dir = std::env::temp_dir().join(format!("singws_rotation_load_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("queue.json");
        std::fs::write(&p, "").unwrap();
        assert!(load_queue(&p).unwrap().is_empty());
        std::fs::write(&p, "[]").unwrap();
        assert!(load_queue(&p).unwrap().is_empty());
        std::fs::write(&p, "{").unwrap();
        assert!(load_queue(&p).unwrap_err().starts_with("json:"));
        assert!(load_queue(&dir.join("absent.json")).is_err());
        assert!(load_tracks(&dir.join("absent.json")).unwrap().is_empty());
        std::fs::remove_dir_all(dir).ok();
    }
}
