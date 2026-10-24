//! `phrase_markers.py` write paths: `_connect`, `init_schema` (including the uuid backfill and column migrations), marker upsert / default /
//! clear / soft-delete, and the BPM / beat-grid cache. `now` and the uuid source are injected so a differential run is deterministic.
//! Not covered (reads are already proven by singws-db; sync merge `apply_remote` and JSON import/export are not ported): see the plan.

use crate::pyval::R;
use rusqlite::Connection;
use rusqlite::types::Value as Sql;
use std::path::Path;

fn e(err: rusqlite::Error) -> String {
    err.to_string()
}

/// `phrase_markers._connect`: plain connection (no WAL, no synchronous change, no foreign_keys), busy timeout, then `init_schema`.
pub fn connect(path: &Path, uuids: &mut dyn FnMut() -> String) -> R<Connection> {
    let con = Connection::open(path).map_err(e)?;
    // rusqlite's bundled SQLite defaults foreign_keys to ON while Python's system SQLite does not; state the Python behaviour.
    con.execute_batch("PRAGMA busy_timeout=5000; PRAGMA foreign_keys=OFF;").map_err(e)?;
    init_schema(&con, uuids)?;
    Ok(con)
}

fn columns(con: &Connection, table: &str) -> R<Vec<String>> {
    let mut stmt = con.prepare(&format!("PRAGMA table_info({table})")).map_err(e)?;
    let names = stmt.query_map([], |r| r.get::<_, String>(1)).map_err(e)?.collect::<Result<Vec<_>, _>>().map_err(e)?;
    Ok(names)
}

pub fn init_schema(con: &Connection, uuids: &mut dyn FnMut() -> String) -> R<()> {
    con.execute_batch(
        "
        CREATE TABLE IF NOT EXISTS markers (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            song_path  TEXT NOT NULL,
            song_key   TEXT,
            kind       TEXT NOT NULL,          -- 'bar4' | 'bar8' | 'bar16' | 'custom'
            seconds    REAL NOT NULL,
            bars       INTEGER,                -- 4/8/16, NULL for custom
            bpm        REAL,                   -- bpm used for bar markers, NULL for manual
            label      TEXT,
            source     TEXT,                   -- 'bpm' | 'manual'
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER,
            updated_at INTEGER
        )
        ",
    )
    .map_err(e)?;
    let existing = columns(con, "markers")?;
    if !existing.iter().any(|c| c == "uuid") {
        con.execute_batch("ALTER TABLE markers ADD COLUMN uuid TEXT").map_err(e)?;
    }
    if !existing.iter().any(|c| c == "deleted_at") {
        con.execute_batch("ALTER TABLE markers ADD COLUMN deleted_at INTEGER").map_err(e)?;
    }
    let ids: Vec<i64> = {
        let mut stmt = con.prepare("SELECT id FROM markers WHERE uuid IS NULL OR uuid = ''").map_err(e)?;
        stmt.query_map([], |r| r.get::<_, i64>(0)).map_err(e)?.collect::<Result<Vec<_>, _>>().map_err(e)?
    };
    for id in ids {
        con.execute("UPDATE markers SET uuid=? WHERE id=?", rusqlite::params![uuids(), id]).map_err(e)?;
    }
    con.execute_batch("CREATE INDEX IF NOT EXISTS idx_markers_path ON markers(song_path)").map_err(e)?;
    con.execute_batch("CREATE INDEX IF NOT EXISTS idx_markers_key ON markers(song_key)").map_err(e)?;
    con.execute_batch("CREATE UNIQUE INDEX IF NOT EXISTS idx_markers_uuid ON markers(uuid)").map_err(e)?;
    con.execute_batch(
        "
        CREATE TABLE IF NOT EXISTS song_meta (
            song_path  TEXT PRIMARY KEY,
            bpm        REAL,
            updated_at INTEGER
        )
        ",
    )
    .map_err(e)?;
    let sm = columns(con, "song_meta")?;
    for (col, decl) in [("first_beat", "REAL"), ("confidence", "REAL"), ("analyzed_at", "INTEGER"), ("analysis_version", "INTEGER")] {
        if !sm.iter().any(|c| c == col) {
            con.execute_batch(&format!("ALTER TABLE song_meta ADD COLUMN {col} {decl}")).map_err(e)?;
        }
    }
    Ok(())
}

/// A fresh `uuid.uuid4().hex`: 16 random bytes with the version-4 and RFC 4122 variant bits set.
pub fn random_uuid4_hex() -> R<String> {
    use std::io::Read;
    let mut b = [0u8; 16];
    std::fs::File::open("/dev/urandom").and_then(|mut f| f.read_exact(&mut b)).map_err(|x| format!("urandom: {x}"))?;
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    Ok(b.iter().map(|x| format!("{x:02x}")).collect())
}

pub struct Marker<'a> {
    pub path: &'a str,
    pub kind: &'a str,
    pub seconds: f64,
    pub bars: Option<i64>,
    pub bpm: Option<f64>,
    pub label: Option<&'a str>,
    pub source: &'a str,
    pub song_key: &'a str,
    pub make_default: bool,
    pub marker_id: Option<i64>,
}

fn opt<T: Into<Sql>>(v: Option<T>) -> Sql {
    v.map(Into::into).unwrap_or(Sql::Null)
}

/// `upsert_marker`; returns the marker id.
pub fn upsert_marker(con: &Connection, m: &Marker, now: i64, new_uuid: &mut dyn FnMut() -> String) -> R<i64> {
    // `max(0.0, float(seconds))`: NaN and negatives give 0.0 (max keeps its first argument unless the second is strictly greater).
    let seconds = if m.seconds > 0.0 { m.seconds } else { 0.0 };
    let mut existing: Option<i64> = m.marker_id;
    if existing.is_none() && m.kind != "custom" {
        existing = con
            .query_row("SELECT id FROM markers WHERE song_path = ? AND kind = ?", [m.path, m.kind], |r| r.get::<_, i64>(0))
            .ok();
    }
    let id = if let Some(id) = existing {
        con.execute(
            "UPDATE markers SET song_key=?, kind=?, seconds=?, bars=?, bpm=?, label=?,
                          source=?, updated_at=?, deleted_at=NULL WHERE id=?",
            rusqlite::params![m.song_key, m.kind, seconds, opt(m.bars), opt(m.bpm), opt(m.label.map(str::to_string)), m.source, now, id],
        )
        .map_err(e)?;
        id
    } else {
        con.execute(
            "INSERT INTO markers (song_path, song_key, kind, seconds, bars, bpm, label,
                          source, is_default, uuid, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,0,?,?,?)",
            rusqlite::params![m.path, m.song_key, m.kind, seconds, opt(m.bars), opt(m.bpm), opt(m.label.map(str::to_string)), m.source, new_uuid(), now, now],
        )
        .map_err(e)?;
        con.last_insert_rowid()
    };
    if m.make_default {
        set_default(con, m.path, id)?;
    }
    Ok(id)
}

pub fn set_default(con: &Connection, path: &str, marker_id: i64) -> R<()> {
    con.execute("UPDATE markers SET is_default=0 WHERE song_path=?", [path]).map_err(e)?;
    con.execute("UPDATE markers SET is_default=1 WHERE id=?", [marker_id]).map_err(e)?;
    Ok(())
}

pub fn clear_default(con: &Connection, path: &str) -> R<()> {
    con.execute("UPDATE markers SET is_default=0 WHERE song_path=?", [path]).map_err(e)?;
    Ok(())
}

/// Soft delete (tombstone).
pub fn delete_marker(con: &Connection, marker_id: i64, now: i64) -> R<()> {
    con.execute("UPDATE markers SET deleted_at=?, updated_at=?, is_default=0 WHERE id=?", rusqlite::params![now, now, marker_id])
        .map_err(e)?;
    Ok(())
}

/// `set_song_bpm`: silently does nothing for an empty path or a BPM that is zero or negative (NaN passes the guard, like Python).
pub fn set_song_bpm(con: &Connection, path: &str, bpm: f64, now: i64) -> R<bool> {
    if path.is_empty() || bpm <= 0.0 {
        return Ok(false);
    }
    con.execute(
        "INSERT INTO song_meta (song_path, bpm, updated_at) VALUES (?,?,?) \
         ON CONFLICT(song_path) DO UPDATE SET bpm=excluded.bpm, updated_at=excluded.updated_at",
        rusqlite::params![path, bpm, now],
    )
    .map_err(e)?;
    Ok(true)
}

/// `set_song_analysis`. Python's `float(confidence or 0.0)` turns a falsy -0.0 into 0.0, but SQLite drops the sign of a stored -0.0 anyway.
pub fn set_song_analysis(
    con: &Connection,
    path: &str,
    bpm: f64,
    first_beat: Option<f64>,
    confidence: f64,
    version: i64,
    now: i64,
) -> R<bool> {
    if path.is_empty() || bpm <= 0.0 {
        return Ok(false);
    }
    con.execute(
        "INSERT INTO song_meta (song_path, bpm, first_beat, confidence, analysis_version, updated_at, analyzed_at) \
         VALUES (?,?,?,?,?,?,?) \
         ON CONFLICT(song_path) DO UPDATE SET bpm=excluded.bpm, first_beat=excluded.first_beat, \
         confidence=excluded.confidence, analysis_version=excluded.analysis_version, \
         updated_at=excluded.updated_at, analyzed_at=excluded.analyzed_at",
        rusqlite::params![path, bpm, opt(first_beat), confidence, version, now, now],
    )
    .map_err(e)?;
    Ok(true)
}
