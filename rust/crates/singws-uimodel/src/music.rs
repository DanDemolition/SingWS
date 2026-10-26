//! Panel 4 of the Rust-owned UI: the background-music playlist, READ-ONLY, from the persisted `bg_playlist.json`. The row label is the entry's
//! `filename`; the search box and the "N tracks" / "K of N tracks" counter follow `KaraokeApp._apply_playlist_filter` (every whitespace-separated
//! word of `str.lower()` of the query must be a substring of the row's `.lower()` text). The app additionally hides rows at or above the track the player
//! is on; that is LIVE player state the shell does not have, so the shell shows every row and says so.

use serde_json::Value;
use singws_dbw::pyval::py_lower;

#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub filename: String,
    /// the entry's `path` when it is a string (shown as a tooltip), otherwise empty
    pub path: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct View {
    pub rows: Vec<Row>,
    pub shown: usize,
    pub total: usize,
    pub count_label: String,
}

/// Python's `str.isspace` for one char (Unicode White_Space plus the four ASCII separators U+001C..U+001F that Python adds).
fn py_space(c: char) -> bool {
    matches!(c, '\u{9}'..='\u{d}' | '\u{1c}'..='\u{20}' | '\u{85}' | '\u{a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}')
}

/// `str.split()` with no argument.
pub fn py_split(s: &str) -> Vec<&str> {
    s.split(py_space).filter(|t| !t.is_empty()).collect()
}

/// The playlist as `load_data` reads it: a JSON list whose every item has `filename` and `path` (a missing key, or a `filename` that is not text, makes the app
/// fail the whole load, so the playlist is empty). Any other shape is an error here too.
pub fn parse_playlist(v: &Value) -> Result<Vec<Row>, String> {
    let list = v.as_array().ok_or("the playlist file is not a list")?;
    let mut rows = Vec::with_capacity(list.len());
    for item in list {
        let o = item.as_object().ok_or("a playlist entry is not an object")?;
        // `QListWidgetItem(None)` is accepted by PyQt and shows an empty row; any other non-text filename raises, which fails the whole load.
        let fv = o.get("filename").ok_or("a playlist entry has no filename")?;
        let filename = if fv.is_null() { "" } else { fv.as_str().ok_or("a playlist filename is not text")? };
        let path = o.get("path").ok_or("a playlist entry has no path")?;
        rows.push(Row { filename: filename.to_string(), path: path.as_str().unwrap_or("").to_string() });
    }
    Ok(rows)
}

pub fn view(rows: &[Row], query: &str) -> Result<View, String> {
    view_from(rows, query, -1)
}

/// The app's list with the player's position applied: rows at or above `cutoff` (the track the player is on, `bg_index` from the app's state; -1 when
/// there is no player or no playlist) are hidden, exactly as `_apply_playlist_filter` hides them. The counter still reports the whole saved playlist.
pub fn view_from(rows: &[Row], query: &str, cutoff: i64) -> Result<View, String> {
    let lowered = py_lower(query)?;
    let terms = py_split(&lowered);
    let total = rows.len();
    let mut shown_rows = Vec::new();
    for (i, r) in rows.iter().enumerate() {
        if (i as i64) <= cutoff {
            continue;
        }
        if terms.is_empty() {
            shown_rows.push(r.clone());
        } else {
            let text = py_lower(&r.filename)?;
            if terms.iter().all(|t| text.contains(t)) {
                shown_rows.push(r.clone());
            }
        }
    }
    let shown = shown_rows.len();
    let count_label = if terms.is_empty() { format!("{total} tracks") } else { format!("{shown} of {total} tracks") };
    Ok(View { rows: shown_rows, shown, total, count_label })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn rows(names: &[&str]) -> Vec<Row> {
        names.iter().map(|n| Row { filename: n.to_string(), path: format!("/m/{n}") }).collect()
    }

    #[test]
    fn every_word_must_match_and_the_label_counts_them() {
        let r = rows(&["Zara Larsson - Lush Life.flac", "Adele - Hello.mp3", "Adele - Skyfall.mp3"]);
        let v = view(&r, "adele  SKY").unwrap();
        assert_eq!(v.rows.len(), 1);
        assert_eq!(v.count_label, "1 of 3 tracks");
        assert_eq!(view(&r, "").unwrap().count_label, "3 tracks");
        assert_eq!(view(&r, "").unwrap().rows.len(), 3, "with no player position every saved track is shown, the first one included");
        assert_eq!(view(&r, "   ").unwrap().count_label, "3 tracks", "a query of only spaces is no filter");
        assert_eq!(view(&r, "nothing").unwrap().count_label, "0 of 3 tracks");
    }

    #[test]
    fn rows_up_to_the_current_track_are_hidden_but_the_counter_keeps_the_whole_playlist() {
        let r = rows(&["a one", "b two", "c three", "d four"]);
        let v = view_from(&r, "", 1).unwrap();
        assert_eq!(v.rows.iter().map(|x| x.filename.as_str()).collect::<Vec<_>>(), ["c three", "d four"]);
        assert_eq!(v.count_label, "4 tracks");
        let q = view_from(&r, "t", 1).unwrap();
        assert_eq!(q.rows.iter().map(|x| x.filename.as_str()).collect::<Vec<_>>(), ["c three"], "a played row stays hidden even when it matches the search");
        assert_eq!(q.count_label, "1 of 4 tracks");
        assert_eq!(view_from(&r, "", -1).unwrap().rows.len(), 4);
        assert_eq!(view_from(&r, "", 3).unwrap().rows.len(), 0, "on the last track nothing is left to show");
        assert_eq!(view_from(&r, "", 99).unwrap().rows.len(), 0);
    }

    #[test]
    fn python_whitespace_splits_words_including_the_ascii_separators() {
        assert_eq!(py_split("a\u{1c}b\u{a0}c\u{3000}d"), vec!["a", "b", "c", "d"]);
        assert_eq!(py_split(" \t\n"), Vec::<&str>::new());
    }

    #[test]
    fn the_playlist_must_be_a_list_of_entries_with_filename_and_path() {
        assert!(parse_playlist(&json!([{"filename": "a.mp3", "path": "/x/a.mp3"}])).is_ok());
        assert!(parse_playlist(&json!({"filename": "a"})).is_err());
        assert!(parse_playlist(&json!([{"filename": "a.mp3"}])).is_err());
        assert!(parse_playlist(&json!([{"filename": 7, "path": "/x"}])).is_err());
        assert_eq!(parse_playlist(&json!([{"filename": null, "path": "/x"}])).unwrap()[0].filename, "", "a null filename is an empty row, as in the app");
        assert!(parse_playlist(&json!(["a.mp3"])).is_err());
        assert_eq!(parse_playlist(&json!([{"filename": "a", "path": 5}])).unwrap()[0].path, "");
    }

    #[test]
    fn text_from_blocks_with_skewed_case_tables_is_declined() {
        assert!(view(&rows(&["a"]), "\u{a7c0}").is_err());
    }
}
