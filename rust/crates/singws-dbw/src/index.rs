//! `song_index.py` write paths: `_connect(read_only=False)`, `init_schema`, `rebuild_from_tracks_json`, `update_from_tracks_json`.
//! The SQL text is copied verbatim (including the DDL's inner whitespace, which SQLite keeps in `sqlite_master.sql`).

use crate::pyval::*;
use rusqlite::functions::FunctionFlags;
use rusqlite::types::Value as Sql;
use rusqlite::{Connection, params_from_iter};
use serde_json::Value;
use std::collections::BTreeSet;
use std::path::Path;

fn e(err: rusqlite::Error) -> String {
    err.to_string()
}

/// `song_index._connect(dbfile, read_only=False)`
pub fn connect_write(path: &Path) -> R<Connection> {
    let con = Connection::open(path).map_err(e)?;
    // PRAGMA journal_mode returns a row, so it is read rather than executed.
    con.query_row("PRAGMA journal_mode=WAL;", [], |_| Ok(())).map_err(e)?;
    con.execute_batch("PRAGMA synchronous=NORMAL; PRAGMA temp_store=MEMORY; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;")
        .map_err(e)?;
    Ok(con)
}

fn register_fuzzykey(con: &Connection) -> R<()> {
    con.create_scalar_function("fuzzykey", 1, FunctionFlags::SQLITE_UTF8 | FunctionFlags::SQLITE_DETERMINISTIC, |ctx| {
        use rusqlite::types::ValueRef;
        match ctx.get_raw(0) {
            ValueRef::Null => Ok(String::new()),
            ValueRef::Text(t) => fuzzy_match_key(&String::from_utf8_lossy(t)).map_err(|m| rusqlite::Error::UserFunctionError(m.into())),
            // Python's `(s or "").lower()` raises for numbers and blobs, which SQLite reports as a failed statement.
            _ => Err(rusqlite::Error::UserFunctionError("fuzzykey: non-text argument".into())),
        }
    })
    .map_err(e)
}

pub fn init_schema(con: &Connection) -> R<()> {
    con.execute_batch(
        "
        CREATE TABLE IF NOT EXISTS meta (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        );
    ",
    )
    .map_err(e)?;
    con.execute_batch(
        "
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            artist TEXT,
            title TEXT,
            discid TEXT,
            duration_secs INTEGER,
            mtime INTEGER,
            size_bytes INTEGER,
            song_type TEXT,
            display TEXT,
            provider TEXT DEFAULT 'local',
            provider_track_id TEXT,
            provider_url TEXT,
            local_reference_path TEXT,
            authorization_requirement TEXT,
            availability_status TEXT,
            searchstring TEXT NOT NULL
        );
    ",
    )
    .map_err(e)?;
    for sql in [
        "CREATE INDEX IF NOT EXISTS idx_songs_discid ON songs(discid);",
        "CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs(artist);",
        "CREATE INDEX IF NOT EXISTS idx_songs_title  ON songs(title);",
        "CREATE INDEX IF NOT EXISTS idx_songs_mtime  ON songs(mtime);",
        "CREATE INDEX IF NOT EXISTS idx_songs_artist_title ON songs(artist COLLATE NOCASE, title COLLATE NOCASE);",
        "CREATE INDEX IF NOT EXISTS idx_songs_path ON songs(path);",
    ] {
        con.execute_batch(sql).map_err(e)?;
    }

    let existing = columns(con, "songs")?;
    if !existing.iter().any(|c| c == "artist_norm") {
        con.execute_batch("ALTER TABLE songs ADD COLUMN artist_norm TEXT;").map_err(e)?;
    }
    if !existing.iter().any(|c| c == "title_norm") {
        con.execute_batch("ALTER TABLE songs ADD COLUMN title_norm TEXT;").map_err(e)?;
    }
    for (col, decl) in [
        ("provider", "TEXT DEFAULT 'local'"),
        ("provider_track_id", "TEXT"),
        ("provider_url", "TEXT"),
        ("local_reference_path", "TEXT"),
        ("authorization_requirement", "TEXT"),
        ("availability_status", "TEXT"),
    ] {
        if !existing.iter().any(|c| c == col) {
            con.execute_batch(&format!("ALTER TABLE songs ADD COLUMN {col} {decl};")).map_err(e)?;
        }
    }
    for sql in [
        "CREATE INDEX IF NOT EXISTS idx_songs_fuzzy ON songs(artist_norm, title_norm);",
        "CREATE INDEX IF NOT EXISTS idx_songs_title_norm ON songs(title_norm);",
        "CREATE INDEX IF NOT EXISTS idx_songs_provider ON songs(provider, provider_track_id);",
    ] {
        con.execute_batch(sql).map_err(e)?;
    }

    let cur_ver: Option<String> = con
        .query_row("SELECT v FROM meta WHERE k='schema_version'", [], |r| r.get::<_, String>(0))
        .ok();
    if cur_ver.as_deref() != Some("5") {
        register_fuzzykey(con)?;
        con.execute_batch("BEGIN;").map_err(e)?;
        let backfill = (|| -> R<()> {
            con.execute_batch("UPDATE songs SET artist_norm = fuzzykey(artist), title_norm = fuzzykey(title);").map_err(e)?;
            con.execute_batch("UPDATE songs SET provider = COALESCE(NULLIF(provider, ''), 'local');").map_err(e)?;
            con.execute_batch("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version','5');").map_err(e)
        })();
        match backfill {
            Ok(()) => con.execute_batch("COMMIT;").map_err(e)?,
            Err(err) => {
                let _ = con.execute_batch("ROLLBACK;");
                return Err(err);
            }
        }
    }
    Ok(())
}

fn columns(con: &Connection, table: &str) -> R<Vec<String>> {
    let mut stmt = con.prepare(&format!("PRAGMA table_info({table})")).map_err(e)?;
    let names = stmt.query_map([], |r| r.get::<_, String>(1)).map_err(e)?.collect::<Result<Vec<_>, _>>().map_err(e)?;
    Ok(names)
}

// ---- tracks.json -> rows -----------------------------------------------------------------------------------------------------------

/// `path.read_text(encoding="utf-8", errors="ignore")`: invalid byte sequences are dropped, not replaced.
pub fn decode_ignore(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len());
    let mut rest = bytes;
    loop {
        match std::str::from_utf8(rest) {
            Ok(s) => {
                out.push_str(s);
                return out;
            }
            Err(err) => {
                let (good, bad) = rest.split_at(err.valid_up_to());
                out.push_str(std::str::from_utf8(good).expect("validated prefix"));
                match err.error_len() {
                    Some(n) => rest = &bad[n..],
                    None => return out, // truncated sequence at the end of input is dropped
                }
            }
        }
    }
}

/// `json.loads(text or "[]") or []` followed by iteration: the list of values to look at. Non-list truthy containers iterate to
/// non-dict items (skipped); a truthy number or `true` is a TypeError in Python and is declined here.
pub fn parse_tracks(text: &str) -> R<Vec<Value>> {
    let text = if text.is_empty() { "[]" } else { text };
    singws_store::reject_big_integers(text).map_err(|x| format!("decline: {x}"))?;
    let v: Value = serde_json::from_str(text).map_err(|x| format!("json: {x}"))?;
    match v {
        Value::Array(a) => Ok(a),
        Value::Null | Value::Object(_) | Value::String(_) | Value::Bool(false) => Ok(Vec::new()),
        number @ Value::Number(_) if !truthy(&number) => Ok(Vec::new()),
        _ => decline("top-level JSON value is not iterable in Python"),
    }
}

fn track_path(t: &Value) -> R<String> {
    py_str_or_empty(py_or(&[get(t, "path"), get(t, "file"), &Value::String(String::new())]))
}

fn key_int(t: &Value, keys: &[&str], need_non_negative: bool) -> R<Option<i64>> {
    for k in keys {
        let v = get(t, k);
        if v.is_null() {
            continue;
        }
        if let Some(i) = py_int_of_float(v)? {
            if !need_non_negative || i >= 0 {
                return Ok(Some(i));
            }
        }
    }
    Ok(None)
}

/// `os.path.getmtime` as `int()` sees it: CPython builds the float as `sec + nsec * 1e-9` in double arithmetic.
fn stat_mtime(path: &str) -> Option<i64> {
    use std::os::unix::fs::MetadataExt;
    let m = std::fs::metadata(path).ok()?;
    let f = m.mtime() as f64 + m.mtime_nsec() as f64 * 1e-9;
    Some(f.trunc() as i64)
}

fn stat_size(path: &str) -> Option<i64> {
    std::fs::metadata(path).ok().map(|m| m.len() as i64)
}

fn bindable(v: &Value) -> R<Sql> {
    Ok(match v {
        Value::Null => Sql::Null,
        Value::Bool(b) => Sql::Integer(*b as i64),
        Value::String(s) => Sql::Text(s.clone()),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Sql::Integer(i)
            } else if n.is_u64() {
                return decline("integer outside SQLite's 64-bit range");
            } else {
                match n.as_f64() {
                    Some(f) if f.is_finite() => Sql::Real(f),
                    _ => return decline("non-finite number"),
                }
            }
        }
        Value::Array(_) | Value::Object(_) => return decline("JSON container cannot be bound"),
    })
}

fn opt_int(v: Option<i64>) -> Sql {
    v.map(Sql::Integer).unwrap_or(Sql::Null)
}

/// The 18 bound values of one songs row, in the column order of the app's INSERT. `None` when the track has no path.
pub fn track_row(t: &Value) -> R<Option<Vec<Sql>>> {
    if !t.is_object() {
        return Ok(None);
    }
    let path = track_path(t)?;
    if path.is_empty() {
        return Ok(None);
    }
    let artist = py_or(&[get(t, "artist"), get(t, "Artist")]);
    let title = py_or(&[get(t, "title"), get(t, "Title")]);
    let discid = py_or(&[get(t, "discid"), get(t, "disc_id"), get(t, "DiscID"), get(t, "Vendor")]);
    let display = get(t, "display");
    let song_type = py_or(&[get(t, "type"), get(t, "song_type")]);

    let provider = {
        let s = py_str(py_or(&[get(t, "provider"), &Value::String("local".into())]))?;
        if let Some(c) = s.chars().find(|c| unicode_skew_risk(*c)) {
            return decline(format!("Unicode table skew risk: U+{:04X}", c as u32));
        }
        let low = py_strip(&s).to_lowercase();
        if low.is_empty() { "local".to_string() } else { low }
    };
    let strip_field = |key: &str| -> R<String> { Ok(py_strip(&py_str_or_empty(get(t, key))?).to_string()) };
    let provider_track_id = strip_field("provider_track_id")?;
    let provider_url = strip_field("provider_url")?;
    let local_reference_path = strip_field("local_reference_path")?;
    let authorization_requirement = strip_field("authorization_requirement")?;
    let availability_status = strip_field("availability_status")?;

    let dur = key_int(t, &["duration_secs", "duration", "dur", "length_secs", "len_secs"], true)?;
    let mtime = match key_int(t, &["scan_mtime", "mtime", "modified", "last_modified"], false)? {
        Some(v) => Some(v),
        None => stat_mtime(&path),
    };
    let size = match key_int(t, &["scan_size", "size", "bytes"], false)? {
        Some(v) => Some(v),
        None => stat_size(&path),
    };

    let artist_s = py_str_or_empty(py_or(&[get(t, "artist"), get(t, "Artist"), &Value::String(String::new())]))?;
    let title_s = py_str_or_empty(py_or(&[get(t, "title"), get(t, "Title"), &Value::String(String::new())]))?;
    let discid_s = py_str_or_empty(py_or(&[
        get(t, "discid"),
        get(t, "disc_id"),
        get(t, "DiscID"),
        get(t, "Vendor"),
        &Value::String(String::new()),
    ]))?;
    let display_s = py_str_or_empty(get(t, "display"))?;
    let blob = format!(
        "{artist_s} {title_s} {discid_s} {display_s} {provider} {provider_track_id} {provider_url} {} {}",
        basename(&path),
        basename(&local_reference_path)
    );
    let searchstring = normalize_text(&blob)?;

    // `fuzzy_match_key(str(artist or ""))`: str() of a raw (possibly non-string) value.
    let an = fuzzy_match_key(&py_str_or_empty(artist)?)?;
    let tn = fuzzy_match_key(&py_str_or_empty(title)?)?;

    Ok(Some(vec![
        Sql::Text(path),
        bindable(artist)?,
        bindable(title)?,
        bindable(discid)?,
        opt_int(dur),
        opt_int(mtime),
        opt_int(size),
        bindable(song_type)?,
        bindable(display)?,
        Sql::Text(provider),
        Sql::Text(provider_track_id),
        Sql::Text(provider_url),
        Sql::Text(local_reference_path),
        Sql::Text(authorization_requirement),
        Sql::Text(availability_status),
        Sql::Text(searchstring),
        Sql::Text(an),
        Sql::Text(tn),
    ]))
}

const COLUMNS: &str = "(path, artist, title, discid, duration_secs, mtime, size_bytes, song_type, display,
                 provider, provider_track_id, provider_url, local_reference_path, authorization_requirement,
                 availability_status, searchstring, artist_norm, title_norm)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)";

fn read_tracks_file(path: &Path) -> R<Vec<Value>> {
    let bytes = std::fs::read(path).map_err(|x| format!("tracks.json not found at: {} ({x})", path.display()))?;
    parse_tracks(&decode_ignore(&bytes))
}

/// All rows first, so a declined track leaves the database completely untouched.
fn rows_for(tracks: &[Value], only: Option<&BTreeSet<String>>) -> R<Vec<Vec<Sql>>> {
    let mut rows = Vec::new();
    for t in tracks {
        if !t.is_object() {
            continue;
        }
        if let Some(set) = only {
            if !set.contains(&track_path(t)?) {
                continue;
            }
        }
        if let Some(r) = track_row(t)? {
            rows.push(r);
        }
    }
    Ok(rows)
}

/// `rebuild_from_tracks_json`; `now` replaces `time.time()` for the `last_rebuild_epoch` row. Returns rows indexed.
pub fn rebuild(tracks_path: &Path, dbfile: &Path, now: i64) -> R<usize> {
    let tracks = read_tracks_file(tracks_path)?;
    let rows = rows_for(&tracks, None)?;
    let con = connect_write(dbfile)?;
    init_schema(&con)?;
    con.execute_batch("BEGIN;").map_err(e)?;
    let work = (|| -> R<()> {
        con.execute_batch("DELETE FROM songs;").map_err(e)?;
        let sql = format!("INSERT OR REPLACE INTO songs\n            {COLUMNS}");
        let mut stmt = con.prepare(&sql).map_err(e)?;
        for r in &rows {
            stmt.execute(params_from_iter(r.iter())).map_err(e)?;
        }
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('last_rebuild_epoch', ?);", [now.to_string()]).map_err(e)?;
        Ok(())
    })();
    match work {
        Ok(()) => con.execute_batch("COMMIT;").map_err(e)?,
        Err(err) => {
            let _ = con.execute_batch("ROLLBACK;");
            return Err(err);
        }
    }
    Ok(rows.len())
}

/// `update_from_tracks_json`. Returns (changed rows applied, removed paths, ...). A missing database falls back to a rebuild.
pub fn update(tracks_path: &Path, dbfile: &Path, changed: &[String], removed: &[String], now: i64) -> R<(usize, usize)> {
    if !dbfile.exists() {
        let rows = rebuild(tracks_path, dbfile, now)?;
        return Ok((rows, 0));
    }
    let changed: BTreeSet<String> = changed.iter().filter(|p| !p.is_empty()).cloned().collect();
    let removed: BTreeSet<String> = removed.iter().filter(|p| !p.is_empty()).cloned().collect();
    if changed.is_empty() && removed.is_empty() {
        return Ok((0, 0));
    }
    let tracks = read_tracks_file(tracks_path)?;
    let rows = rows_for(&tracks, Some(&changed))?;
    let con = connect_write(dbfile)?;
    init_schema(&con)?;
    con.execute_batch("BEGIN;").map_err(e)?;
    let work = (|| -> R<()> {
        for p in &removed {
            con.execute("DELETE FROM songs WHERE path = ?", [p]).map_err(e)?;
        }
        if !rows.is_empty() {
            let sql = format!(
                "INSERT INTO songs
            {COLUMNS}
            ON CONFLICT(path) DO UPDATE SET
                artist=excluded.artist, title=excluded.title, discid=excluded.discid,
                duration_secs=excluded.duration_secs, mtime=excluded.mtime, size_bytes=excluded.size_bytes,
                song_type=excluded.song_type, display=excluded.display, provider=excluded.provider,
                provider_track_id=excluded.provider_track_id, provider_url=excluded.provider_url,
                local_reference_path=excluded.local_reference_path,
                authorization_requirement=excluded.authorization_requirement,
                availability_status=excluded.availability_status, searchstring=excluded.searchstring,
                artist_norm=excluded.artist_norm, title_norm=excluded.title_norm"
            );
            let mut stmt = con.prepare(&sql).map_err(e)?;
            for r in &rows {
                stmt.execute(params_from_iter(r.iter())).map_err(e)?;
            }
        }
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('last_incremental_epoch', ?);", [now.to_string()]).map_err(e)?;
        Ok(())
    })();
    match work {
        Ok(()) => con.execute_batch("COMMIT;").map_err(e)?,
        Err(err) => {
            let _ = con.execute_batch("ROLLBACK;");
            return Err(err);
        }
    }
    Ok((rows.len(), removed.len()))
}
