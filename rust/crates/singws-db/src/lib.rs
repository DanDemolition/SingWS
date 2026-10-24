//! Read-only SQLite access used to PROVE a Rust reader sees exactly what Python's `sqlite3` sees in the app's databases
//! (`singws.db` song index, `phrase_markers.db`). It cannot modify a database: the file is opened with SQLite's read-only flag, which
//! also refuses to create a missing file, `PRAGMA query_only` is on, and the only free-form statement accepted is a single SELECT.
//!
//! Values are encoded so nothing is lost or conflated in JSON: NULL -> null, INTEGER -> number (exact 64-bit), REAL -> number
//! (non-finite as `{"$float":"inf"|"-inf"|"nan"}`), TEXT -> string (invalid UTF-8 as `{"$invalid_utf8":"<hex>"}`), BLOB -> `{"$blob":"<hex>"}`.

use std::path::Path;
use std::time::Duration;

use rusqlite::types::ValueRef;
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Map, Value};

pub const MAX_ROWS: usize = 200_000;

/// Open `path` read-only. Never creates a file and never writes.
pub fn open_readonly(path: &Path) -> Result<Connection, String> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
        .map_err(|e| format!("open: {e}"))?;
    conn.busy_timeout(Duration::from_millis(5000)).map_err(|e| e.to_string())?;
    conn.execute_batch("PRAGMA query_only=ON; PRAGMA temp_store=MEMORY;").map_err(|e| e.to_string())?;
    Ok(conn)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

pub fn encode(v: ValueRef<'_>) -> Value {
    match v {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(i) => json!(i),
        ValueRef::Real(f) => {
            if f.is_finite() {
                json!(f)
            } else if f.is_nan() {
                json!({"$float": "nan"})
            } else if f > 0.0 {
                json!({"$float": "inf"})
            } else {
                json!({"$float": "-inf"})
            }
        }
        ValueRef::Text(t) => match std::str::from_utf8(t) {
            Ok(s) => Value::String(s.to_string()),
            Err(_) => json!({"$invalid_utf8": hex(t)}),
        },
        ValueRef::Blob(b) => json!({"$blob": hex(b)}),
    }
}

/// The one free-form statement shape allowed: a single SELECT (an optional trailing `;` is fine).
pub fn guard_select(sql: &str) -> Result<&str, String> {
    let s = sql.trim().trim_end_matches(';').trim();
    let head: String = s.chars().take(6).collect::<String>().to_ascii_lowercase();
    if head != "select" {
        return Err("only a single SELECT statement is allowed".into());
    }
    // A ';' anywhere else would start a second statement (this crate has no string literals containing ';' in its own queries).
    if s.contains(';') {
        return Err("multiple statements are not allowed".into());
    }
    Ok(s)
}

fn bind(params: &[Value]) -> Result<Vec<rusqlite::types::Value>, String> {
    params
        .iter()
        .map(|p| match p {
            Value::Null => Ok(rusqlite::types::Value::Null),
            Value::Bool(b) => Ok(rusqlite::types::Value::Integer(*b as i64)),
            Value::Number(n) => {
                if let Some(i) = n.as_i64() {
                    Ok(rusqlite::types::Value::Integer(i))
                } else {
                    n.as_f64().map(rusqlite::types::Value::Real).ok_or_else(|| "number out of range".to_string())
                }
            }
            Value::String(s) => Ok(rusqlite::types::Value::Text(s.clone())),
            other => Err(format!("unsupported parameter {other}")),
        })
        .collect()
}

/// Run a guarded SELECT; returns `{"columns":[..],"rows":[[..]..]}`.
pub fn query(conn: &Connection, sql: &str, params: &[Value]) -> Result<Value, String> {
    let sql = guard_select(sql)?;
    let mut stmt = conn.prepare(sql).map_err(|e| format!("prepare: {e}"))?;
    let columns: Vec<String> = stmt.column_names().iter().map(|c| c.to_string()).collect();
    let bound = bind(params)?;
    let mut rows = stmt.query(rusqlite::params_from_iter(bound.iter())).map_err(|e| format!("query: {e}"))?;
    let mut out = Vec::new();
    while let Some(row) = rows.next().map_err(|e| format!("step: {e}"))? {
        if out.len() >= MAX_ROWS {
            return Err(format!("more than {MAX_ROWS} rows"));
        }
        out.push(Value::Array((0..columns.len()).map(|i| encode(row.get_ref_unwrap(i))).collect()));
    }
    Ok(json!({ "columns": columns, "rows": out }))
}

fn quote_ident(name: &str) -> String {
    format!("\"{}\"", name.replace('"', "\"\""))
}

fn table_names(conn: &Connection) -> Result<Vec<String>, String> {
    let r = query(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name", &[])?;
    Ok(r["rows"].as_array().unwrap().iter().map(|row| row[0].as_str().unwrap_or("").to_string()).collect())
}

/// Schema and header facts: every sqlite_master entry, the pragmas that identify a database, and per-table column and index info.
pub fn schema(conn: &Connection) -> Result<Value, String> {
    let mut pragmas = Map::new();
    // Database-level facts only. `foreign_keys` is deliberately NOT here: it is a per-connection setting whose default differs between
    // engines (rusqlite's bundled SQLite turns it ON, Python's sqlite3 leaves it OFF and the app enables it explicitly on its write
    // connections), so comparing it says nothing about the file. A future Rust WRITER must set it explicitly to match the app.
    for name in ["user_version", "application_id", "journal_mode", "page_size", "encoding", "auto_vacuum"] {
        let v = conn.query_row(&format!("PRAGMA {name}"), [], |r| Ok(encode(r.get_ref_unwrap(0)))).map_err(|e| e.to_string())?;
        pragmas.insert(name.to_string(), v);
    }
    let master = query(conn, "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name", &[])?;
    let mut tables = Map::new();
    for t in table_names(conn)? {
        let info = query(conn, &format!("SELECT cid, name, type, \"notnull\", dflt_value, pk FROM pragma_table_info({})", quote_str(&t)), &[])?;
        let idx = query(conn, &format!("SELECT name, \"unique\", origin, partial FROM pragma_index_list({}) ORDER BY name", quote_str(&t)), &[])?;
        tables.insert(t, json!({ "columns": info["rows"], "indexes": idx["rows"] }));
    }
    Ok(json!({ "pragmas": pragmas, "master": master["rows"], "tables": tables }))
}

fn quote_str(s: &str) -> String {
    format!("'{}'", s.replace('\'', "''"))
}

/// Every row of `table` (which must exist in sqlite_master), by rowid.
pub fn dump(conn: &Connection, table: &str) -> Result<Value, String> {
    if !table_names(conn)?.iter().any(|t| t == table) {
        return Err(format!("no such table: {table}"));
    }
    match query(conn, &format!("SELECT rowid, * FROM {} ORDER BY rowid", quote_ident(table)), &[]) {
        Ok(v) => Ok(v),
        Err(_) => query(conn, &format!("SELECT * FROM {} ORDER BY 1", quote_ident(table)), &[]), // WITHOUT ROWID table
    }
}

/// Request/response bridge for the CLI: `{"op":"schema"}`, `{"op":"dump","table":..}`, `{"op":"query","sql":..,"params":[..]}`.
pub fn handle(path: &Path, request: &Value) -> Value {
    let result = (|| -> Result<Value, String> {
        let conn = open_readonly(path)?;
        match request.get("op").and_then(Value::as_str).ok_or("missing op")? {
            "schema" => schema(&conn),
            "dump" => dump(&conn, request.get("table").and_then(Value::as_str).ok_or("missing table")?),
            "query" => {
                let sql = request.get("sql").and_then(Value::as_str).ok_or("missing sql")?;
                let params = request.get("params").and_then(Value::as_array).cloned().unwrap_or_default();
                query(&conn, sql, &params)
            }
            other => Err(format!("unknown op `{other}`")),
        }
    })();
    match result {
        Ok(v) => json!({ "ok": true, "result": v }),
        Err(e) => json!({ "ok": false, "error": e }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static N: AtomicU64 = AtomicU64::new(0);

    /// A private temp directory with a database built by a throwaway read-write connection; never the live profile.
    struct Fixture(std::path::PathBuf);
    impl Fixture {
        fn new() -> Self {
            let dir = std::env::temp_dir().join(format!("singws-db-test-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
            std::fs::create_dir_all(&dir).unwrap();
            let db = dir.join("t.db");
            let c = Connection::open(&db).unwrap();
            c.execute_batch(
                "CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
                 CREATE TABLE songs (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, artist TEXT, title TEXT,
                                     duration_secs INTEGER, ratio REAL, raw BLOB, searchstring TEXT NOT NULL);
                 CREATE INDEX idx_songs_artist_title ON songs(artist COLLATE NOCASE, title COLLATE NOCASE);
                 INSERT INTO meta VALUES ('schema_version','5');
                 INSERT INTO songs (path, artist, title, duration_secs, ratio, raw, searchstring) VALUES
                   ('/m/a.mp3','Abba','Waterloo',168,0.5,x'00ff10','abba waterloo'),
                   ('/m/b.mp3','ÉLAN','Straße 100%',NULL,1e300,NULL,'élan straße 100% x_y'),
                   ('/m/c.mp3',NULL,'',9223372036854775807,-0.0,x'','c');
                 PRAGMA user_version = 7;",
            )
            .unwrap();
            Fixture(dir)
        }
        fn db(&self) -> std::path::PathBuf {
            self.0.join("t.db")
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn values_are_encoded_without_loss() {
        let f = Fixture::new();
        let conn = open_readonly(&f.db()).unwrap();
        let r = query(&conn, "SELECT id, artist, duration_secs, ratio, raw FROM songs ORDER BY id", &[]).unwrap();
        assert_eq!(r["columns"], json!(["id", "artist", "duration_secs", "ratio", "raw"]));
        assert_eq!(r["rows"][0], json!([1, "Abba", 168, 0.5, {"$blob": "00ff10"}]));
        assert_eq!(r["rows"][1], json!([2, "ÉLAN", null, 1e300, null]));
        assert_eq!(r["rows"][2][2], json!(9223372036854775807i64));
        assert_eq!(r["rows"][2][4], json!({"$blob": ""}));
        assert_eq!(r["rows"][2][1], Value::Null);
    }

    #[test]
    fn like_and_order_follow_sqlite_semantics_with_unescaped_wildcards() {
        let f = Fixture::new();
        let conn = open_readonly(&f.db()).unwrap();
        // the app builds %word% with no escaping, so % and _ typed by the operator are wildcards
        let r = query(&conn, "SELECT id FROM songs WHERE searchstring LIKE ? AND searchstring LIKE ? ORDER BY artist, title LIMIT ?",
                      &[json!("%x_y%"), json!("%100%%"), json!(500)]).unwrap();
        assert_eq!(r["rows"], json!([[2]]));
        let r = query(&conn, "SELECT id FROM songs WHERE searchstring LIKE ? ORDER BY artist, title", &[json!("%ABBA%")]).unwrap();
        assert_eq!(r["rows"], json!([[1]]), "LIKE is case-insensitive for ASCII");
        // SQLite's LIKE ignores case for ASCII letters ONLY: an upper-case accented letter does not match the stored lower-case one
        // (the app lower-cases queries itself via normalize_text, so this never reaches the database in practice)
        let r = query(&conn, "SELECT id FROM songs WHERE searchstring LIKE ?", &[json!("%ÉLAN%")]).unwrap();
        assert_eq!(r["rows"], json!([]));
        let r = query(&conn, "SELECT id FROM songs WHERE searchstring LIKE ?", &[json!("%élan%")]).unwrap();
        assert_eq!(r["rows"], json!([[2]]));
    }

    #[test]
    fn parameters_keep_their_sqlite_type() {
        let f = Fixture::new();
        let conn = open_readonly(&f.db()).unwrap();
        // binding an integer as text would be invisible to most comparisons (SQLite coerces), so assert the stored type directly
        let r = query(&conn, "SELECT typeof(?), typeof(?), typeof(?), typeof(?)", &[json!(7), json!(1.5), json!("7"), Value::Null]).unwrap();
        assert_eq!(r["rows"], json!([["integer", "real", "text", "null"]]));
        let r = query(&conn, "SELECT typeof(?)", &[json!(9223372036854775807i64)]).unwrap();
        assert_eq!(r["rows"], json!([["integer"]]));
    }

    #[test]
    fn the_statement_guard_itself_rejects_everything_but_one_select() {
        // the guard is a first line of defence in front of SQLite's own read-only enforcement, so it is tested on its own
        for ok in ["select 1", "SELECT 1;", "  SeLeCt * from songs  ", "select 'a'"] {
            assert!(guard_select(ok).is_ok(), "{ok}");
        }
        for bad in ["insert into t values (1)", "delete from songs", "update songs set a=1", "pragma user_version=1", "with x as (select 1) select * from x",
                    "select 1; select 2", "select 1;;drop table songs", "", "   ", "sel", "drop table songs", "attach database 'x' as y"] {
            assert!(guard_select(bad).is_err(), "{bad:?} must be refused by the guard");
        }
    }

    #[test]
    fn schema_reports_header_pragmas_columns_and_indexes() {
        let f = Fixture::new();
        let s = schema(&open_readonly(&f.db()).unwrap()).unwrap();
        assert_eq!(s["pragmas"]["user_version"], json!(7));
        assert_eq!(s["pragmas"]["encoding"], json!("UTF-8"));
        let cols: Vec<&str> = s["tables"]["songs"]["columns"].as_array().unwrap().iter().map(|c| c[1].as_str().unwrap()).collect();
        assert_eq!(cols, vec!["id", "path", "artist", "title", "duration_secs", "ratio", "raw", "searchstring"]);
        let idx: Vec<&str> = s["tables"]["songs"]["indexes"].as_array().unwrap().iter().map(|c| c[0].as_str().unwrap()).collect();
        assert!(idx.contains(&"idx_songs_artist_title"));
        assert!(s["master"].as_array().unwrap().iter().any(|m| m[1] == "idx_songs_artist_title"));
    }

    #[test]
    fn dump_returns_every_row_by_rowid_and_rejects_unknown_tables() {
        let f = Fixture::new();
        let conn = open_readonly(&f.db()).unwrap();
        let d = dump(&conn, "songs").unwrap();
        assert_eq!(d["rows"].as_array().unwrap().len(), 3);
        assert_eq!(d["rows"][0][0], json!(1));
        assert!(dump(&conn, "nope").is_err());
        assert!(dump(&conn, "songs\"; DROP TABLE songs; --").is_err(), "table names are checked against sqlite_master");
        assert_eq!(dump(&conn, "meta").unwrap()["rows"][0], json!([1, "schema_version", "5"]));
    }

    #[test]
    fn nothing_can_be_written_or_created() {
        let f = Fixture::new();
        let conn = open_readonly(&f.db()).unwrap();
        for sql in ["INSERT INTO songs (path, searchstring) VALUES ('x','x')", "UPDATE songs SET title='x'", "DELETE FROM songs",
                    "DROP TABLE songs", "PRAGMA user_version = 9", "SELECT 1; DROP TABLE songs", "  select 1 ; select 2",
                    "WITH x AS (SELECT 1) DELETE FROM songs", "ATTACH DATABASE 'x.db' AS x", "VACUUM"] {
            assert!(query(&conn, sql, &[]).is_err(), "{sql} must be refused");
        }
        // even a raw statement through the connection cannot write
        assert!(conn.execute("INSERT INTO songs (path, searchstring) VALUES ('y','y')", []).is_err());
        assert_eq!(query(&conn, "SELECT COUNT(*) FROM songs", &[]).unwrap()["rows"], json!([[3]]));
        // a missing database is an error and is NOT created
        let missing = f.0.join("missing.db");
        assert!(open_readonly(&missing).is_err());
        assert!(!missing.exists());
        // a successful trailing semicolon is fine
        assert!(query(&conn, "SELECT 1;", &[]).is_ok());
    }

    #[test]
    fn the_database_file_is_byte_identical_after_reads() {
        let f = Fixture::new();
        let before = std::fs::read(f.db()).unwrap();
        {
            let conn = open_readonly(&f.db()).unwrap();
            schema(&conn).unwrap();
            dump(&conn, "songs").unwrap();
            query(&conn, "SELECT * FROM songs", &[]).unwrap();
        }
        assert_eq!(std::fs::read(f.db()).unwrap(), before);
    }

    #[test]
    fn bridge_handles_each_op_and_reports_errors_without_panicking() {
        let f = Fixture::new();
        let ok = handle(&f.db(), &json!({"op": "query", "sql": "SELECT title FROM songs WHERE id = ?", "params": [1]}));
        assert_eq!(ok["result"]["rows"], json!([["Waterloo"]]));
        assert_eq!(handle(&f.db(), &json!({"op": "schema"}))["ok"], json!(true));
        assert_eq!(handle(&f.db(), &json!({"op": "dump", "table": "songs"}))["ok"], json!(true));
        assert_eq!(handle(&f.db(), &json!({"op": "nope"}))["ok"], json!(false));
        assert_eq!(handle(&f.db(), &json!({"op": "query", "sql": "DELETE FROM songs"}))["ok"], json!(false));
        assert_eq!(handle(&f.0.join("none.db"), &json!({"op": "schema"}))["ok"], json!(false));
        assert_eq!(handle(&f.db(), &json!({"op": "query", "sql": "SELECT ?", "params": [[1]]}))["ok"], json!(false));
    }
}
