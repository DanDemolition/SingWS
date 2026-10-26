//! Panel 1 of the Rust-owned UI: the song library list. The panel is READ-ONLY: it opens `singws.db` with SQLite's read-only flag through
//! `singws-db` and never writes. Search is the strict path of `song_index.search_songs` (every whitespace-separated word must be a substring
//! of the search string, ordered by artist/title, then ranked exact < prefix < word-prefix < contains). The typo-tolerant fallback that Python runs when the strict
//! search finds nothing is NOT ported yet; `SearchResult::fuzzy_fallback_needed` says when Python would have used it.

pub mod history;
pub mod music;
pub mod rotation;
pub mod settings_view;
mod casefold_table;

use serde_json::{Value, json};
use singws_dbw::pyval::normalize_text;
use std::path::Path;

#[derive(Debug, Clone, PartialEq)]
pub struct SongRow {
    pub id: i64,
    pub path: String,
    pub artist: String,
    pub title: String,
    pub discid: String,
    pub duration_secs: Option<i64>,
    pub song_type: String,
    pub display: String,
}

#[derive(Debug)]
pub struct SearchResult {
    pub rows: Vec<SongRow>,
    /// the strict search found nothing, so `song_index` would now run its typo-tolerant scan (not ported)
    pub fuzzy_fallback_needed: bool,
}

/// `mm:ss` (or `h:mm:ss` from one hour), `--:--` when unknown.
pub fn format_duration(secs: Option<i64>) -> String {
    match secs {
        Some(s) if s >= 0 => {
            if s >= 3600 { format!("{}:{:02}:{:02}", s / 3600, (s % 3600) / 60, s % 60) } else { format!("{:02}:{:02}", s / 60, s % 60) }
        }
        _ => "--:--".into(),
    }
}

fn rank_key(row: &SongRow, q: &str) -> Result<(u8, String, String), String> {
    let a = normalize_text(&row.artist)?;
    let t = normalize_text(&row.title)?;
    let combined = format!("{a} {t}");
    let combined = combined.trim().to_string();
    let tier = if q == a || q == t || q == combined {
        0
    } else if a.starts_with(q) || t.starts_with(q) || combined.starts_with(q) {
        1
    } else if format!(" {combined}").contains(&format!(" {q}")) {
        2
    } else {
        3
    };
    Ok((tier, a, t))
}

/// Strict library search. An empty (or whitespace-only) query returns no rows, like Python.
pub fn search(db: &Path, query: &str, limit: i64) -> Result<SearchResult, String> {
    let q = normalize_text(query)?;
    if q.is_empty() || !db.exists() {
        return Ok(SearchResult { rows: Vec::new(), fuzzy_fallback_needed: false });
    }
    let words: Vec<&str> = q.split(' ').filter(|w| !w.is_empty()).collect();
    let con = singws_db::open_readonly(db)?;
    let where_clause = vec!["searchstring LIKE ?"; words.len()].join(" AND ");
    let sql = format!(
        "SELECT id, path, artist, title, discid, duration_secs, song_type, display FROM songs WHERE {where_clause} ORDER BY artist, title LIMIT ?"
    );
    let mut params: Vec<rusqlite::types::Value> = words.iter().map(|w| rusqlite::types::Value::Text(format!("%{w}%"))).collect();
    params.push(rusqlite::types::Value::Integer(limit));
    let mut stmt = con.prepare(&sql).map_err(|e| e.to_string())?;
    let text = |r: &rusqlite::Row, i: usize| -> rusqlite::Result<String> {
        Ok(match r.get_ref(i)? {
            rusqlite::types::ValueRef::Null => String::new(),
            rusqlite::types::ValueRef::Text(t) => String::from_utf8_lossy(t).into_owned(),
            rusqlite::types::ValueRef::Integer(v) => v.to_string(),
            rusqlite::types::ValueRef::Real(v) => v.to_string(),
            rusqlite::types::ValueRef::Blob(_) => String::new(),
        })
    };
    let mapped = stmt
        .query_map(rusqlite::params_from_iter(params.iter()), |r| {
            Ok(SongRow {
                id: r.get(0)?,
                path: text(r, 1)?,
                artist: text(r, 2)?,
                title: text(r, 3)?,
                discid: text(r, 4)?,
                duration_secs: r.get::<_, Option<i64>>(5).unwrap_or(None),
                song_type: text(r, 6)?,
                display: text(r, 7)?,
            })
        })
        .map_err(|e| e.to_string())?;
    let mut rows = mapped.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?;
    let mut keyed = Vec::with_capacity(rows.len());
    for r in rows.drain(..) {
        let k = rank_key(&r, &q)?;
        keyed.push((k, r));
    }
    keyed.sort_by(|a, b| a.0.cmp(&b.0)); // stable, like Python's list.sort
    let rows: Vec<SongRow> = keyed.into_iter().map(|(_, r)| r).collect();
    let fuzzy_fallback_needed = rows.is_empty();
    Ok(SearchResult { rows, fuzzy_fallback_needed })
}

pub fn row_json(r: &SongRow) -> Value {
    json!({ "id": r.id, "path": r.path, "artist": r.artist, "title": r.title, "discid": r.discid,
            "duration_secs": r.duration_secs, "song_type": r.song_type, "display": r.display })
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn fixture(rows: &[(&str, &str, &str, Option<i64>)]) -> (std::path::PathBuf, std::path::PathBuf) {
        static N: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let dir = std::env::temp_dir().join(format!("singws_uimodel_test_{}_{}", std::process::id(), N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)));
        std::fs::create_dir_all(&dir).unwrap();
        let db = dir.join("s.db");
        let _ = std::fs::remove_file(&db);
        let con = Connection::open(&db).unwrap();
        con.execute_batch(
            "CREATE TABLE songs (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, artist TEXT, title TEXT, discid TEXT,
               duration_secs INTEGER, song_type TEXT, display TEXT, searchstring TEXT NOT NULL);",
        )
        .unwrap();
        for (i, (artist, title, ss, dur)) in rows.iter().enumerate() {
            con.execute(
                "INSERT INTO songs (path, artist, title, duration_secs, searchstring) VALUES (?,?,?,?,?)",
                rusqlite::params![format!("/p{i}"), artist, title, dur, ss],
            )
            .unwrap();
        }
        (dir, db)
    }

    #[test]
    fn durations_format_like_the_app_list() {
        assert_eq!(format_duration(Some(247)), "04:07");
        assert_eq!(format_duration(Some(0)), "00:00");
        assert_eq!(format_duration(Some(3600)), "1:00:00");
        assert_eq!(format_duration(Some(3725)), "1:02:05");
        assert_eq!(format_duration(None), "--:--");
        assert_eq!(format_duration(Some(-5)), "--:--");
    }

    #[test]
    fn every_word_must_match_and_results_are_ranked_exact_prefix_word_contains() {
        let (dir, db) = fixture(&[
            ("Zed", "xx alice yy", "zed xx alice yy", Some(10)),
            ("Alice", "Other", "alice other", Some(20)),
            ("Band", "Alice", "band alice", Some(30)),
            ("Alicia", "Song", "alicia song", None),
            ("Cat", "Dog", "cat dog", Some(5)),
        ]);
        let r = search(&db, "  ALICE ", 500).unwrap();
        let titles: Vec<_> = r.rows.iter().map(|x| format!("{}/{}", x.artist, x.title)).collect();
        assert_eq!(
            titles,
            ["Alice/Other", "Band/Alice", "Zed/xx alice yy"],
            "artist OR title equal to the query is tier 0 (ties by artist, then title); 'Alicia' does not contain 'alice'"
        );
        assert!(!r.fuzzy_fallback_needed);
        let two = search(&db, "alice other", 500).unwrap();
        assert_eq!(two.rows.len(), 1);
        let none = search(&db, "alice cat", 500).unwrap();
        assert!(none.rows.is_empty() && none.fuzzy_fallback_needed);
        assert_eq!(search(&db, "alic", 500).unwrap().rows.iter().map(|x| x.duration_secs).collect::<Vec<_>>().len(), 4);
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn empty_queries_and_missing_databases_return_nothing_without_error() {
        let (dir, db) = fixture(&[("A", "B", "a b", None)]);
        for q in ["", "   ", "\t\n"] {
            let r = search(&db, q, 10).unwrap();
            assert!(r.rows.is_empty() && !r.fuzzy_fallback_needed, "{q:?}");
        }
        assert!(search(&dir.join("absent.db"), "a", 10).unwrap().rows.is_empty());
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn limit_applies_before_ranking_like_python() {
        let (dir, db) = fixture(&[("B", "x", "b x", None), ("A", "x", "a x", None), ("C", "x", "c x", None)]);
        let r = search(&db, "x", 2).unwrap();
        assert_eq!(r.rows.iter().map(|x| x.artist.as_str()).collect::<Vec<_>>(), ["A", "B"], "LIMIT cuts the artist-ordered list, then ranking reorders only those");
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn like_wildcards_in_the_query_are_not_escaped_exactly_like_the_app() {
        let (dir, db) = fixture(&[("A", "100% pure", "a 100% pure", None), ("B", "plain", "b plain", None)]);
        assert_eq!(search(&db, "%", 10).unwrap().rows.len(), 2, "the app does not escape %, so it matches everything");
        assert_eq!(search(&db, "p_a", 10).unwrap().rows.len(), 1);
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn searching_never_changes_the_database_file() {
        let (dir, db) = fixture(&[("A", "B", "a b", Some(1))]);
        let before = std::fs::read(&db).unwrap();
        for q in ["a", "zzz", "%"] {
            search(&db, q, 10).unwrap();
        }
        assert_eq!(std::fs::read(&db).unwrap(), before);
        std::fs::remove_dir_all(dir).ok();
    }
}
