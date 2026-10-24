use super::*;
use crate::index::{connect_write, decode_ignore, parse_tracks, rebuild, update};
use crate::markers::{
    Marker, clear_default, connect, delete_marker, random_uuid4_hex, set_default, set_song_analysis, set_song_bpm, upsert_marker,
};
use crate::pyval::*;
use crate::{index, markers};
use rusqlite::Connection;
use std::sync::atomic::{AtomicU64, Ordering};

static N: AtomicU64 = AtomicU64::new(0);

/// A private directory inside the system temp dir; removed on drop.
struct Dir(PathBuf);
impl Dir {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!("singws_dbw_test_{}_{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
        std::fs::create_dir_all(&p).unwrap();
        Dir(p)
    }
    fn file(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }
    fn tracks(&self, json: &str) -> PathBuf {
        let p = self.file("tracks.json");
        std::fs::write(&p, json).unwrap();
        p
    }
}
impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn rows(db: &Path, sql: &str) -> Vec<Vec<String>> {
    let con = Connection::open(db).unwrap();
    let mut stmt = con.prepare(sql).unwrap();
    let n = stmt.column_count();
    stmt.query_map([], |r| {
        Ok((0..n)
            .map(|i| match r.get_ref(i).unwrap() {
                rusqlite::types::ValueRef::Null => "NULL".to_string(),
                rusqlite::types::ValueRef::Integer(v) => v.to_string(),
                rusqlite::types::ValueRef::Real(v) => format!("{v:?}"),
                rusqlite::types::ValueRef::Text(t) => String::from_utf8_lossy(t).into_owned(),
                rusqlite::types::ValueRef::Blob(_) => "BLOB".into(),
            })
            .collect())
    })
    .unwrap()
    .map(|x| x.unwrap())
    .collect()
}

fn one(db: &Path, sql: &str) -> String {
    rows(db, sql).remove(0).remove(0)
}

fn fixed_uuids() -> impl FnMut() -> String {
    fixed_uuids_from(0)
}

fn fixed_uuids_from(start: u32) -> impl FnMut() -> String {
    let mut n = start;
    move || {
        n += 1;
        format!("{n:032x}")
    }
}

// ---- Python semantics -------------------------------------------------------------------------------------------------------------

#[test]
fn fuzzy_keys_match_the_docstring_examples_of_song_index() {
    assert_eq!(fuzzy_match_key("B.O.B. (Bombs Over Baghdad)").unwrap(), "bob");
    assert_eq!(fuzzy_match_key("Matchbox Twenty").unwrap(), "matchbox20");
    assert_eq!(fuzzy_match_key("3 AM").unwrap(), "3am");
    assert_eq!(fuzzy_match_key("3AM").unwrap(), "3am");
    assert_eq!(fuzzy_match_key("The Beatles").unwrap(), "beatles");
    assert_eq!(fuzzy_match_key("The").unwrap(), "the", "a lone 'the' is kept");
    assert_eq!(fuzzy_match_key("Rock & Roll").unwrap(), "rockandroll");
    assert_eq!(fuzzy_match_key("Gonna Fly").unwrap(), "goingtofly");
    assert_eq!(fuzzy_match_key("Wanna").unwrap(), "wantto");
    assert_eq!(fuzzy_match_key("").unwrap(), "");
}

#[test]
fn bracket_stripping_follows_the_regex_not_balanced_parsing() {
    assert_eq!(strip_brackets_for_test("a (b) c"), "a   c");
    assert_eq!(strip_brackets_for_test("a (b c"), "a (b c", "an opener without a closer is left alone");
    assert_eq!(strip_brackets_for_test("x ((a) b)"), "x   b)", "the first closer ends the match, nesting is not tracked");
    assert_eq!(strip_brackets_for_test("[a} b"), "  b", "any opener may pair with any closer");
    assert_eq!(strip_brackets_for_test("a (b (c d"), "a (b (c d");
    assert_eq!(strip_brackets_for_test("()"), " ");
}

fn strip_brackets_for_test(s: &str) -> String {
    crate::pyval::strip_brackets_pub(s)
}

#[test]
fn python_whitespace_includes_the_four_ascii_separators() {
    for c in ['\u{1c}', '\u{1d}', '\u{1e}', '\u{1f}', ' ', '\t', '\u{85}', '\u{a0}', '\u{2003}', '\u{3000}'] {
        assert!(is_py_space(c), "{c:?}");
    }
    for c in ['a', '\u{200b}', '\u{180e}', '0'] {
        assert!(!is_py_space(c), "{c:?}");
    }
    assert_eq!(normalize_text("  A\u{a0}\u{1f}B \n C ").unwrap(), "a b c");
}

#[test]
fn truthiness_and_or_chains_follow_python() {
    use serde_json::json;
    for v in [json!(null), json!(false), json!(0), json!(0.0), json!(-0.0), json!(""), json!([]), json!({})] {
        assert!(!truthy(&v), "{v}");
    }
    for v in [json!(true), json!(1), json!(-1), json!(0.5), json!("0"), json!(" "), json!([0]), json!({"a": 0}), json!(18446744073709551615u64)] {
        assert!(truthy(&v), "{v}");
    }
    let (a, b, c) = (json!(0), json!(""), json!(null));
    assert_eq!(py_or(&[&a, &b, &c]), &json!(null), "all falsy: the LAST operand is the value");
    let d = json!("x");
    assert_eq!(py_or(&[&a, &d, &b]), &json!("x"));
}

#[test]
fn str_of_values() {
    use serde_json::json;
    assert_eq!(py_str(&json!(true)).unwrap(), "True");
    assert_eq!(py_str(&json!(3.0)).unwrap(), "3.0");
    assert_eq!(py_str(&json!(1e22)).unwrap(), "1e+22");
    assert_eq!(py_str(&json!(-7)).unwrap(), "-7");
    assert_eq!(py_str(&json!(18446744073709551615u64)).unwrap(), "18446744073709551615");
    assert!(py_str(&json!([1])).unwrap_err().starts_with("decline:"));
    assert_eq!(py_str_or_empty(&json!(0)).unwrap(), "");
    assert_eq!(py_str_or_empty(&json!([])).unwrap(), "", "falsy containers vanish before str()");
}

#[test]
fn int_of_float_matches_the_apps_try_except() {
    use serde_json::json;
    let ok = |v: serde_json::Value| py_int_of_float(&v).unwrap();
    assert_eq!(ok(json!(12.9)), Some(12));
    assert_eq!(ok(json!(-12.9)), Some(-12), "truncation toward zero");
    assert_eq!(ok(json!("12.9")), Some(12));
    assert_eq!(ok(json!(" 7\n")), Some(7));
    assert_eq!(ok(json!("1_000")), Some(1000));
    assert_eq!(ok(json!("1__0")), None);
    assert_eq!(ok(json!("_1")), None);
    assert_eq!(ok(json!("1_")), None);
    assert_eq!(ok(json!("1_.5")), None);
    assert_eq!(ok(json!("1e1_0")), Some(10_000_000_000));
    assert_eq!(ok(json!("\u{1c}5")), None, "ASCII text is only stripped of the six ASCII spaces");
    assert_eq!(ok(json!(true)), Some(1));
    assert_eq!(ok(json!(null)), None);
    assert_eq!(ok(json!([1])), None);
    assert_eq!(ok(json!("nan")), None);
    assert_eq!(ok(json!("inf")), None);
    assert_eq!(ok(json!("")), None);
    assert_eq!(ok(json!("0x10")), None);
    assert!(py_int_of_float(&json!(9_223_372_036_854_775_807i64)).unwrap_err().starts_with("decline:"), "float(2**63-1) is 2**63, which no i64 holds");
    assert_eq!(ok(json!(4_611_686_018_427_387_904i64)), Some(4_611_686_018_427_387_904));
    // 2^63 and just below
    assert!(py_int_of_float(&json!(9.223372036854775808e18)).unwrap_err().starts_with("decline:"));
    assert_eq!(ok(json!(-9.223372036854775808e18)), Some(i64::MIN));
    assert!(py_int_of_float(&json!(-9.3e18)).unwrap_err().starts_with("decline:"));
    assert!(py_int_of_float(&json!("١٢")).unwrap_err().starts_with("decline:"), "non-ASCII digits are declined, not guessed");
    assert!(py_int_of_float(&json!("1 ")).is_ok());
}

#[test]
fn decode_ignore_drops_invalid_sequences_like_python() {
    assert_eq!(decode_ignore(b"a\xffb"), "ab");
    assert_eq!(decode_ignore(b"a\xc3"), "a", "a truncated sequence at the end is dropped");
    assert_eq!(decode_ignore(b"\xe2\x82(x"), "(x");
    assert_eq!(decode_ignore("é€".as_bytes()), "é€");
    assert_eq!(decode_ignore(b"\xed\xa0\x80z"), "z", "an encoded surrogate is invalid UTF-8");
    assert_eq!(decode_ignore(b""), "");
}

#[test]
fn unicode_table_skew_blocks_are_declined() {
    assert!(unicode_skew_risk('\u{A7CE}'));
    assert!(unicode_skew_risk('\u{10D50}'));
    assert!(unicode_skew_risk('\u{16EA0}'));
    assert!(!unicode_skew_risk('é'));
    assert!(!unicode_skew_risk('\u{A7BF}'));
    assert!(!unicode_skew_risk('\u{A800}'));
    assert!(normalize_text("a\u{A7CE}").unwrap_err().starts_with("decline:"));
    assert!(fuzzy_match_key("\u{16EB8}").unwrap_err().starts_with("decline:"));
}

// ---- guard ------------------------------------------------------------------------------------------------------------------------

#[test]
fn only_paths_inside_the_temp_dir_and_outside_the_live_profile_are_allowed() {
    let d = Dir::new();
    assert!(allowed_db(d.file("x.db").to_str().unwrap()).is_ok());
    assert!(allowed_db("/usr/local/x.db").unwrap_err().starts_with("refused:"));
    assert!(allowed_db("x.db").is_err() || std::env::current_dir().unwrap().starts_with(std::fs::canonicalize(std::env::temp_dir()).unwrap()));
    assert!(allowed_db("/").unwrap_err().starts_with("refused:"), "no file name");
    let home = std::env::var("HOME").unwrap();
    assert!(allowed_db(&format!("{home}/SingWS/singws.db")).unwrap_err().starts_with("refused:"));
    // a ../ escape out of the temp dir resolves before the check
    let escape = format!("{}/../../x.db", d.0.display());
    assert!(allowed_db(&escape).is_err());
    // a missing parent directory is refused rather than created
    assert!(allowed_db(d.file("nope/x.db").to_str().unwrap()).is_err());
    assert!(!d.file("nope").exists());
}

#[test]
fn a_profile_directory_under_temp_is_refused_even_though_temp_is_allowed() {
    let d = Dir::new();
    let profile = d.file("profile");
    std::fs::create_dir_all(&profile).unwrap();
    // SAFETY: tests that read SINGWS_HOME run in this one test; the variable is restored before returning.
    let before = std::env::var_os("SINGWS_HOME");
    unsafe { std::env::set_var("SINGWS_HOME", &profile) };
    let r = allowed_db(profile.join("singws.db").to_str().unwrap());
    let sibling = allowed_db(d.file("other.db").to_str().unwrap());
    match before {
        Some(v) => unsafe { std::env::set_var("SINGWS_HOME", v) },
        None => unsafe { std::env::remove_var("SINGWS_HOME") },
    }
    assert!(r.unwrap_err().contains("live profile"));
    assert!(sibling.is_ok(), "only the profile itself is protected");
}

// ---- song_index ------------------------------------------------------------------------------------------------------------------

const TRACKS: &str = r#"[
 {"path": "/lib/a.kfn", "artist": "The Who", "title": "3 AM", "discid": "D1", "duration": 215.9, "mtime": 100, "size": 7, "type": "kfn", "display": "x"},
 {"file": "/lib/b.kfn", "Artist": "Beyoncé", "Title": "Halo (Live)", "Vendor": "V", "provider": " Spotify ", "provider_track_id": " id1 "},
 {"artist": "no path"},
 5,
 {"path": "/lib/a.kfn", "artist": "replaced", "duration": -3, "dur": 9}
]"#;

#[test]
fn rebuild_writes_the_app_schema_rows_and_meta() {
    let d = Dir::new();
    let db = d.file("s.db");
    let n = rebuild(&d.tracks(TRACKS), &db, 1234).unwrap();
    assert_eq!(n, 3, "the path-less entry and the non-object are skipped; the duplicate path still counts as a row written");
    assert_eq!(one(&db, "SELECT COUNT(*) FROM songs"), "2", "INSERT OR REPLACE keeps one row per path");
    assert_eq!(
        rows(&db, "SELECT path, artist, duration_secs, artist_norm FROM songs ORDER BY path"),
        vec![vec!["/lib/a.kfn", "replaced", "9", "replaced"], vec!["/lib/b.kfn", "Beyoncé", "NULL", "beyonc"]],
        "later duplicates win; the key keeps only [a-z0-9] after lower-casing, so the accented letter splits the word"
    );
    assert_eq!(one(&db, "SELECT v FROM meta WHERE k='schema_version'"), "5");
    assert_eq!(one(&db, "SELECT v FROM meta WHERE k='last_rebuild_epoch'"), "1234");
    assert_eq!(one(&db, "PRAGMA journal_mode"), "wal");
    assert_eq!(one(&db, "SELECT provider FROM songs WHERE path='/lib/b.kfn'"), "spotify", "stripped and lower-cased");
    assert_eq!(one(&db, "SELECT provider_track_id FROM songs WHERE path='/lib/b.kfn'"), "id1");
    assert_eq!(one(&db, "SELECT searchstring FROM songs WHERE path='/lib/b.kfn'"), "beyoncé halo (live) v spotify id1 b.kfn");
}

#[test]
fn rebuild_twice_replaces_rows_and_keeps_counting_ids() {
    let d = Dir::new();
    let db = d.file("s.db");
    rebuild(&d.tracks(r#"[{"path":"/a"},{"path":"/b"}]"#), &db, 1).unwrap();
    rebuild(&d.tracks(r#"[{"path":"/c"}]"#), &db, 2).unwrap();
    assert_eq!(rows(&db, "SELECT id, path FROM songs"), vec![vec!["3".to_string(), "/c".to_string()]], "AUTOINCREMENT does not reuse ids");
    assert_eq!(one(&db, "SELECT v FROM meta WHERE k='last_rebuild_epoch'"), "2");
}

#[test]
fn top_level_json_shapes_iterate_like_python() {
    assert_eq!(parse_tracks("").unwrap().len(), 0, "an empty file reads as []");
    assert_eq!(parse_tracks("[]").unwrap().len(), 0);
    assert_eq!(parse_tracks("{}").unwrap().len(), 0);
    assert_eq!(parse_tracks(r#"{"a": {"path": "/x"}}"#).unwrap().len(), 0, "iterating a dict yields its keys, which are not tracks");
    assert_eq!(parse_tracks(r#""text""#).unwrap().len(), 0);
    assert_eq!(parse_tracks("null").unwrap().len(), 0);
    assert_eq!(parse_tracks("false").unwrap().len(), 0);
    assert_eq!(parse_tracks("0").unwrap().len(), 0);
    assert_eq!(parse_tracks("0.0").unwrap().len(), 0);
    for bad in ["5", "-1", "true", "2.5"] {
        assert!(parse_tracks(bad).unwrap_err().starts_with("decline:"), "{bad}: not iterable in Python");
    }
    assert!(parse_tracks("[{").unwrap_err().starts_with("json:"));
    assert_eq!(parse_tracks(r#"[{"path": "/a"}, 3]"#).unwrap().len(), 2);
}

#[test]
fn a_declined_track_leaves_the_database_untouched() {
    let d = Dir::new();
    let db = d.file("s.db");
    let r = rebuild(&d.tracks(r#"[{"path":"/a"},{"path":"/b","artist":["list"]}]"#), &db, 1);
    assert!(r.unwrap_err().starts_with("decline:"));
    assert!(!db.exists(), "nothing is created when the input cannot be reproduced");
    let r = rebuild(&d.tracks(r#"[{"path":"/a","size":18446744073709551616}]"#), &db, 1);
    assert!(r.unwrap_err().starts_with("decline:"), "an integer beyond 64 bits");
    let r = rebuild(&d.tracks("5"), &db, 1);
    assert!(r.unwrap_err().starts_with("decline:"), "a number is not iterable");
    assert!(!db.exists());
    assert!(rebuild(&d.file("missing.json"), &db, 1).unwrap_err().contains("not found"));
}

#[test]
fn stat_fallbacks_read_the_real_file_when_the_scan_gave_nothing() {
    let d = Dir::new();
    let f = d.file("real.mp3");
    std::fs::write(&f, vec![0u8; 321]).unwrap();
    let json = format!(r#"[{{"path": "{}"}}, {{"path": "/does/not/exist.kfn"}}, {{"path": "{}", "scan_size": "55", "scan_mtime": 9}}]"#, f.display(), f.display());
    let db = d.file("s.db");
    rebuild(&d.tracks(&json), &db, 1).unwrap();
    assert_eq!(rows(&db, "SELECT size_bytes, mtime FROM songs WHERE path='/does/not/exist.kfn'"), vec![vec!["NULL", "NULL"]]);
    // the duplicate (scan values given) replaced the first row for the real path
    assert_eq!(rows(&db, &format!("SELECT size_bytes, mtime FROM songs WHERE path='{}'", f.display())), vec![vec!["55", "9"]]);
    let db2 = d.file("s2.db");
    rebuild(&d.tracks(&format!(r#"[{{"path": "{}"}}]"#, f.display())), &db2, 1).unwrap();
    assert_eq!(one(&db2, "SELECT size_bytes FROM songs"), "321");
    assert!(one(&db2, "SELECT mtime FROM songs").parse::<i64>().unwrap() > 1_000_000_000);
}

#[test]
fn update_applies_a_delta_and_an_empty_delta_does_not_touch_the_file() {
    let d = Dir::new();
    let db = d.file("s.db");
    rebuild(&d.tracks(r#"[{"path":"/a","artist":"A"},{"path":"/b","artist":"B"},{"path":"/c","artist":"C"}]"#), &db, 1).unwrap();
    let tp = d.tracks(r#"[{"path":"/a","artist":"A2"},{"path":"/b","artist":"B2"},{"path":"/c","artist":"C"},{"path":"/new","artist":"N"}]"#);
    let (c, r) = update(&tp, &db, &["/a".into(), "/new".into(), "/never".into(), "".into()], &["/c".into(), "/ghost".into()], 77).unwrap();
    assert_eq!((c, r), (2, 2), "a changed path absent from tracks.json simply matches nothing; removed paths are counted as given");
    assert_eq!(rows(&db, "SELECT path, artist FROM songs ORDER BY id"), vec![vec!["/a", "A2"], vec!["/b", "B"], vec!["/new", "N"]]);
    assert_eq!(one(&db, "SELECT v FROM meta WHERE k='last_incremental_epoch'"), "77");
    let before = std::fs::read(&db).unwrap();
    assert_eq!(update(&tp, &db, &[], &[], 99).unwrap(), (0, 0));
    assert_eq!(update(&tp, &db, &["".into()], &[], 99).unwrap(), (0, 0), "empty strings are filtered out first");
    assert_eq!(std::fs::read(&db).unwrap(), before);
}

#[test]
fn update_on_a_missing_database_rebuilds() {
    let d = Dir::new();
    let db = d.file("s.db");
    let tp = d.tracks(r#"[{"path":"/a"},{"path":"/b"}]"#);
    assert_eq!(update(&tp, &db, &["/a".into()], &[], 5).unwrap(), (2, 0), "the whole index is built, not just the delta");
    assert_eq!(one(&db, "SELECT v FROM meta WHERE k='last_rebuild_epoch'"), "5");
    assert_eq!(rows(&db, "SELECT v FROM meta WHERE k='last_incremental_epoch'").len(), 0);
}

#[test]
fn init_schema_upgrades_an_old_database_and_leaves_a_current_one_alone() {
    let d = Dir::new();
    let db = d.file("old.db");
    {
        let con = Connection::open(&db).unwrap();
        con.execute_batch(
            "CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
             CREATE TABLE songs (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, artist TEXT, title TEXT, discid TEXT,
               duration_secs INTEGER, mtime INTEGER, size_bytes INTEGER, song_type TEXT, display TEXT, searchstring TEXT NOT NULL);
             INSERT INTO songs (path, artist, title, searchstring) VALUES ('/o', 'The Who', NULL, 'x');",
        )
        .unwrap();
    }
    let con = connect_write(&db).unwrap();
    index::init_schema(&con).unwrap();
    drop(con);
    assert_eq!(rows(&db, "SELECT artist_norm, title_norm, provider FROM songs"), vec![vec!["who", "", "local"]]);
    assert_eq!(one(&db, "SELECT v FROM meta WHERE k='schema_version'"), "5");
    // schema_version 5: derived columns are NOT recomputed
    let con = Connection::open(&db).unwrap();
    con.execute_batch("UPDATE songs SET artist_norm='stale';").unwrap();
    drop(con);
    let con = connect_write(&db).unwrap();
    index::init_schema(&con).unwrap();
    drop(con);
    assert_eq!(one(&db, "SELECT artist_norm FROM songs"), "stale");
}

#[test]
fn the_backfill_fails_like_python_for_a_non_text_artist_and_rolls_back() {
    let d = Dir::new();
    let db = d.file("bad.db");
    {
        let con = Connection::open(&db).unwrap();
        con.execute_batch(
            "CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
             CREATE TABLE songs (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, artist, title TEXT, discid TEXT,
               duration_secs INTEGER, mtime INTEGER, size_bytes INTEGER, song_type TEXT, display TEXT, searchstring TEXT NOT NULL);
             INSERT INTO songs (path, artist, searchstring) VALUES ('/o', 12345, 'x');",
        )
        .unwrap();
    }
    let con = connect_write(&db).unwrap();
    assert!(index::init_schema(&con).is_err());
    assert!(con.is_autocommit(), "the failed backfill transaction was rolled back");
    drop(con);
    assert_eq!(rows(&db, "SELECT v FROM meta WHERE k='schema_version'").len(), 0, "the version marker is only written after a complete backfill");
}

#[test]
fn schema_text_is_the_apps_text_so_sqlite_master_matches() {
    let d = Dir::new();
    let db = d.file("s.db");
    let con = connect_write(&db).unwrap();
    index::init_schema(&con).unwrap();
    drop(con);
    let sql = one(&db, "SELECT sql FROM sqlite_master WHERE name='songs'");
    assert!(sql.starts_with("CREATE TABLE songs (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,"), "{sql}");
    assert!(sql.ends_with(", artist_norm TEXT, title_norm TEXT)"), "the two derived columns are appended by ALTER like the app does: {sql}");
    assert_eq!(rows(&db, "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%' ORDER BY name").len(), 9);
    // rusqlite's bundled SQLite compiles foreign_keys ON by default; Python's system SQLite does not, so both writers state it explicitly.
    let con = connect_write(&db).unwrap();
    assert_eq!(con.query_row("PRAGMA foreign_keys", [], |r| r.get::<_, i64>(0)).unwrap(), 1);
    assert_eq!(con.query_row("PRAGMA synchronous", [], |r| r.get::<_, i64>(0)).unwrap(), 1, "NORMAL");
    assert_eq!(con.query_row("PRAGMA busy_timeout", [], |r| r.get::<_, i64>(0)).unwrap(), 5000);
    assert_eq!(con.query_row("PRAGMA temp_store", [], |r| r.get::<_, i64>(0)).unwrap(), 2, "MEMORY");
}

// ---- phrase_markers --------------------------------------------------------------------------------------------------------------

fn marker<'a>(path: &'a str, kind: &'a str, seconds: f64) -> Marker<'a> {
    Marker { path, kind, seconds, bars: None, bpm: None, label: None, source: "manual", song_key: "", make_default: false, marker_id: None }
}

#[test]
fn markers_schema_backfills_missing_uuids_in_row_order() {
    let d = Dir::new();
    let db = d.file("m.db");
    {
        let con = Connection::open(&db).unwrap();
        con.execute_batch(
            "CREATE TABLE markers (id INTEGER PRIMARY KEY AUTOINCREMENT, song_path TEXT NOT NULL, song_key TEXT, kind TEXT NOT NULL,
               seconds REAL NOT NULL, bars INTEGER, bpm REAL, label TEXT, source TEXT, is_default INTEGER NOT NULL DEFAULT 0,
               created_at INTEGER, updated_at INTEGER);
             INSERT INTO markers (song_path, kind, seconds) VALUES ('/a','bar4',1),('/a','custom',2),('/b','bar8',3);
             CREATE TABLE song_meta (song_path TEXT PRIMARY KEY, bpm REAL, updated_at INTEGER);",
        )
        .unwrap();
    }
    let mut u = fixed_uuids();
    connect(&db, &mut u).unwrap();
    assert_eq!(rows(&db, "SELECT id, uuid FROM markers ORDER BY id"), vec![
        vec!["1".to_string(), format!("{:032x}", 1)],
        vec!["2".to_string(), format!("{:032x}", 2)],
        vec!["3".to_string(), format!("{:032x}", 3)],
    ]);
    assert_eq!(rows(&db, "SELECT name FROM pragma_table_info('markers') ORDER BY cid").len(), 14);
    assert_eq!(rows(&db, "SELECT name FROM pragma_table_info('song_meta') ORDER BY cid").len(), 7);
    // a second connection backfills nothing (no uuid is consumed)
    let mut spare = fixed_uuids_from(50);
    connect(&db, &mut spare).unwrap();
    assert_eq!(spare(), format!("{:032x}", 51), "no uuid was consumed by the second connection");
    assert_eq!(one(&db, "SELECT uuid FROM markers WHERE id=1"), format!("{:032x}", 1));
    // an EMPTY-string uuid is also treated as missing
    let con = Connection::open(&db).unwrap();
    con.execute("UPDATE markers SET uuid='' WHERE id=2", []).unwrap();
    drop(con);
    let mut again = fixed_uuids_from(100);
    connect(&db, &mut again).unwrap();
    assert_eq!(one(&db, "SELECT uuid FROM markers WHERE id=2"), format!("{:032x}", 101));
    assert_eq!(one(&db, "PRAGMA journal_mode"), "delete", "phrase_markers does not switch the database to WAL");
    let con = connect(&db, &mut fixed_uuids_from(900)).unwrap();
    assert_eq!(con.query_row("PRAGMA foreign_keys", [], |r| r.get::<_, i64>(0)).unwrap(), 0, "Python's phrase_markers never enables foreign keys");
    assert_eq!(con.query_row("PRAGMA busy_timeout", [], |r| r.get::<_, i64>(0)).unwrap(), 5000);
}

#[test]
fn upsert_inserts_updates_bar_kinds_in_place_and_reactivates_tombstones() {
    let d = Dir::new();
    let db = d.file("m.db");
    let mut u = fixed_uuids();
    let con = connect(&db, &mut u).unwrap();
    let a = upsert_marker(&con, &marker("/a", "bar4", 10.0), 100, &mut u).unwrap();
    let b = upsert_marker(&con, &marker("/a", "bar4", 20.0), 200, &mut u).unwrap();
    assert_eq!(a, b, "bar markers are unique per (path, kind)");
    let c1 = upsert_marker(&con, &marker("/a", "custom", 5.0), 300, &mut u).unwrap();
    let c2 = upsert_marker(&con, &marker("/a", "custom", 6.0), 300, &mut u).unwrap();
    assert_ne!(c1, c2, "custom markers always insert");
    assert_eq!(rows(&db, "SELECT seconds, created_at, updated_at, is_default FROM markers WHERE id=1"), vec![vec!["20.0", "100", "200", "0"]]);
    set_default(&con, "/a", a).unwrap();
    assert_eq!(one(&db, "SELECT is_default FROM markers WHERE id=1"), "1");
    delete_marker(&con, a, 400).unwrap();
    assert_eq!(rows(&db, "SELECT deleted_at, updated_at, is_default FROM markers WHERE id=1"), vec![vec!["400", "400", "0"]], "a tombstone cannot stay the default");
    upsert_marker(&con, &marker("/a", "bar4", 30.0), 500, &mut u).unwrap();
    assert_eq!(rows(&db, "SELECT deleted_at, seconds FROM markers WHERE id=1"), vec![vec!["NULL", "30.0"]], "overwriting a tombstone re-activates it");
    let edit = Marker { marker_id: Some(c1), label: Some("Edited"), ..marker("/a", "custom", 7.0) };
    assert_eq!(upsert_marker(&con, &edit, 600, &mut u).unwrap(), c1);
    assert_eq!(rows(&db, &format!("SELECT label, seconds, created_at FROM markers WHERE id={c1}")), vec![vec!["Edited", "7.0", "300"]]);
}

#[test]
fn seconds_are_clamped_to_zero_like_max_0_0_x() {
    let d = Dir::new();
    let db = d.file("m.db");
    let mut u = fixed_uuids();
    let con = connect(&db, &mut u).unwrap();
    for (i, s) in [-5.0, -0.0, f64::NAN, 0.25, f64::INFINITY].into_iter().enumerate() {
        upsert_marker(&con, &marker("/a", "custom", s), 1, &mut u).unwrap();
        let got = one(&db, &format!("SELECT seconds FROM markers WHERE id={}", i + 1));
        let want = ["0.0", "0.0", "0.0", "0.25", "inf"][i];
        assert_eq!(got.replace("Inf", "inf"), want, "input #{i}");
    }
}

#[test]
fn make_default_moves_the_flag_within_one_song_only() {
    let d = Dir::new();
    let db = d.file("m.db");
    let mut u = fixed_uuids();
    let con = connect(&db, &mut u).unwrap();
    let a1 = upsert_marker(&con, &Marker { make_default: true, ..marker("/a", "bar4", 1.0) }, 1, &mut u).unwrap();
    let b1 = upsert_marker(&con, &Marker { make_default: true, ..marker("/b", "bar4", 1.0) }, 1, &mut u).unwrap();
    let a2 = upsert_marker(&con, &Marker { make_default: true, ..marker("/a", "bar8", 2.0) }, 1, &mut u).unwrap();
    assert_eq!(rows(&db, "SELECT id FROM markers WHERE is_default=1 ORDER BY id"), vec![vec![b1.to_string()], vec![a2.to_string()]]);
    clear_default(&con, "/a").unwrap();
    assert_eq!(rows(&db, "SELECT id FROM markers WHERE is_default=1"), vec![vec![b1.to_string()]]);
    set_default(&con, "/a", a1).unwrap();
    assert_eq!(rows(&db, "SELECT id FROM markers WHERE is_default=1 ORDER BY id"), vec![vec![a1.to_string()], vec![b1.to_string()]]);
}

#[test]
fn bpm_cache_guards_and_upserts() {
    let d = Dir::new();
    let db = d.file("m.db");
    let mut u = fixed_uuids();
    let con = connect(&db, &mut u).unwrap();
    assert!(!set_song_bpm(&con, "", 120.0, 1).unwrap());
    assert!(!set_song_bpm(&con, "/a", 0.0, 1).unwrap());
    assert!(!set_song_bpm(&con, "/a", -1.0, 1).unwrap());
    assert!(!set_song_analysis(&con, "/a", 0.0, None, 0.0, 0, 1).unwrap());
    assert_eq!(rows(&db, "SELECT * FROM song_meta").len(), 0);
    assert!(set_song_bpm(&con, "/a", 120.5, 10).unwrap());
    assert!(set_song_bpm(&con, "/a", 121.0, 20).unwrap());
    assert_eq!(rows(&db, "SELECT bpm, updated_at FROM song_meta"), vec![vec!["121.0", "20"]]);
    assert!(set_song_analysis(&con, "/a", 100.0, Some(0.25), 0.9, 3, 30).unwrap());
    assert_eq!(
        rows(&db, "SELECT bpm, first_beat, confidence, analysis_version, updated_at, analyzed_at FROM song_meta"),
        vec![vec!["100.0", "0.25", "0.9", "3", "30", "30"]]
    );
    assert!(set_song_analysis(&con, "/neg", 90.0, None, -0.0, 0, 40).unwrap());
    assert_eq!(rows(&db, "SELECT confidence FROM song_meta WHERE song_path='/neg'"), vec![vec!["0.0"]], "SQLite keeps no sign on a stored zero");
    assert!(set_song_analysis(&con, "/b", 90.0, None, 0.0, 0, 40).unwrap());
    assert_eq!(rows(&db, "SELECT first_beat, confidence, analysis_version FROM song_meta WHERE song_path='/b'"), vec![vec!["NULL", "0.0", "0"]]);
    // NaN passes Python's guards and SQLite stores it as NULL
    assert!(set_song_bpm(&con, "/nan", f64::NAN, 50).unwrap());
    assert_eq!(rows(&db, "SELECT bpm FROM song_meta WHERE song_path='/nan'"), vec![vec!["NULL"]]);
}

#[test]
fn random_uuids_are_version_4_hex_and_distinct() {
    let a = random_uuid4_hex().unwrap();
    let b = random_uuid4_hex().unwrap();
    assert_ne!(a, b);
    assert_eq!(a.len(), 32);
    assert!(a.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
    assert_eq!(&a[12..13], "4");
    assert!("89ab".contains(&a[16..17]));
}

// ---- the JSON bridge --------------------------------------------------------------------------------------------------------------

#[test]
fn bridge_refuses_unsafe_paths_and_reports_errors_without_panicking() {
    use serde_json::json;
    let r = handle(&json!({"op": "index_init", "db": "/usr/local/x.db"}));
    assert_eq!(r["ok"], false);
    assert!(r["error"].as_str().unwrap().starts_with("refused:"));
    assert_eq!(handle(&json!({"op": "nope"}))["ok"], false);
    assert_eq!(handle(&json!({}))["ok"], false);
    assert_eq!(handle(&json!({"op": "index_rebuild"}))["ok"], false);
    let d = Dir::new();
    let ok = handle(&json!({"op": "index_rebuild", "db": d.file("s.db").to_str().unwrap(), "tracks": d.tracks(r#"[{"path":"/a"}]"#).to_str().unwrap(), "now": 1}));
    assert_eq!(ok["result"]["rows"], 1);
    let rows = handle(&json!({"op": "track_rows", "tracks": [{"path": "/a", "duration": 3.5}, {"x": 1}]}));
    assert_eq!(rows["result"][1], serde_json::Value::Null);
    assert_eq!(rows["result"][0][4], 3);
    assert_eq!(handle(&json!({"op": "markers_ops", "db": d.file("m.db").to_str().unwrap(), "now": 1, "ops": [{"op": "bogus"}]}))["ok"], false);
    let m = handle(&json!({"op": "markers_ops", "db": d.file("m2.db").to_str().unwrap(), "now": 5, "uuids": ["aa"],
        "ops": [{"op": "upsert", "path": "/p", "kind": "custom", "seconds": 1.5}, {"op": "set_bpm", "path": "/p", "bpm": 120}]}));
    assert_eq!(m["result"], json!([1, true]));
}

#[test]
fn the_write_crate_is_not_part_of_the_apps_native_library() {
    let ffi = include_str!("../../singws-dsp-ffi/Cargo.toml");
    assert!(!ffi.contains("singws-dbw"), "the SQLite writer must stay out of the shipped dylib until a drive decision is made");
    assert!(!ffi.contains("singws-db"));
}
