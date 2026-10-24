//! SQLite WRITE compatibility for SingWS (Stage D3b): the write paths of `song_index.py` and `phrase_markers.py`, proven logically equal to
//! the Python-built databases by `tools/dbw_parity.py`. This crate is NOT linked into the app and its CLI only opens databases inside the
//! system temp directory, never the live profile.

pub mod index;
pub mod markers;
pub mod pyval;

use pyval::R;
use rusqlite::types::Value as Sql;
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

/// A database path the writer may open: its (existing) parent directory must resolve inside the system temp directory, and must not be
/// the live profile directory (`~/SingWS` or `$SINGWS_HOME`). Returns the resolved path.
pub fn allowed_db(path: &str) -> R<PathBuf> {
    let p = Path::new(path);
    let name = p.file_name().ok_or("refused: database path has no file name")?;
    let parent = p.parent().filter(|x| !x.as_os_str().is_empty()).unwrap_or(Path::new("."));
    let parent = std::fs::canonicalize(parent).map_err(|x| format!("refused: parent directory unusable: {x}"))?;
    let temp = std::fs::canonicalize(std::env::temp_dir()).map_err(|x| format!("refused: no usable temp dir: {x}"))?;
    if !parent.starts_with(&temp) {
        return Err(format!("refused: {} is outside the system temp directory", parent.display()));
    }
    let mut live: Vec<PathBuf> = Vec::new();
    if let Some(home) = std::env::var_os("HOME") {
        live.push(Path::new(&home).join("SingWS"));
    }
    if let Some(h) = std::env::var_os("SINGWS_HOME") {
        live.push(PathBuf::from(h));
    }
    for l in live {
        if let Ok(l) = std::fs::canonicalize(&l) {
            if parent.starts_with(&l) {
                return Err(format!("refused: {} is inside the live profile {}", parent.display(), l.display()));
            }
        }
    }
    Ok(parent.join(name))
}

fn encode_sql(v: &Sql) -> Value {
    match v {
        Sql::Null => Value::Null,
        Sql::Integer(i) => json!(i),
        Sql::Real(f) => json!({ "$real": singws_store::py_float_repr(*f) }),
        Sql::Text(s) => json!(s),
        Sql::Blob(b) => json!({ "$blob": b.iter().map(|x| format!("{x:02x}")).collect::<String>() }),
    }
}

fn uuid_source(request: &Value) -> impl FnMut() -> String {
    let mut fixed: Option<std::collections::VecDeque<String>> = request
        .get("uuids")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect());
    move || match fixed.as_mut() {
        Some(q) => q.pop_front().unwrap_or_else(|| "00000000000000000000000000000000".into()),
        None => markers::random_uuid4_hex().unwrap_or_else(|_| "00000000000000000000000000000000".into()),
    }
}

fn map_texts(request: &Value, f: fn(&str) -> R<String>) -> R<Value> {
    let texts = request.get("texts").and_then(Value::as_array).ok_or("missing texts")?;
    Ok(Value::Array(
        texts
            .iter()
            .map(|t| match f(t.as_str().unwrap_or("")) {
                Ok(s) => json!(s),
                Err(why) => json!({ "decline": why }),
            })
            .collect(),
    ))
}

fn str_arg<'a>(r: &'a Value, k: &str) -> R<&'a str> {
    r.get(k).and_then(Value::as_str).ok_or_else(|| format!("missing {k}"))
}

fn i64_arg(r: &Value, k: &str) -> R<i64> {
    r.get(k).and_then(Value::as_i64).ok_or_else(|| format!("missing {k}"))
}

fn strings(r: &Value, k: &str) -> Vec<String> {
    r.get(k).and_then(Value::as_array).map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()).unwrap_or_default()
}

fn run_marker_op(con: &rusqlite::Connection, op: &Value, now: i64, uuids: &mut dyn FnMut() -> String) -> R<Value> {
    let kind = str_arg(op, "op")?;
    let f = |k: &str| op.get(k).and_then(Value::as_f64);
    match kind {
        "upsert" => {
            let m = markers::Marker {
                path: str_arg(op, "path")?,
                kind: str_arg(op, "kind")?,
                seconds: f("seconds").ok_or("missing seconds")?,
                bars: op.get("bars").and_then(Value::as_i64),
                bpm: f("bpm"),
                label: op.get("label").and_then(Value::as_str),
                source: op.get("source").and_then(Value::as_str).unwrap_or("manual"),
                song_key: op.get("song_key").and_then(Value::as_str).unwrap_or(""),
                make_default: op.get("make_default").and_then(Value::as_bool).unwrap_or(false),
                marker_id: op.get("marker_id").and_then(Value::as_i64),
            };
            Ok(json!(markers::upsert_marker(con, &m, now, uuids)?))
        }
        "set_default" => {
            markers::set_default(con, str_arg(op, "path")?, i64_arg(op, "marker_id")?)?;
            Ok(Value::Null)
        }
        "clear_default" => {
            markers::clear_default(con, str_arg(op, "path")?)?;
            Ok(Value::Null)
        }
        "delete" => {
            markers::delete_marker(con, i64_arg(op, "marker_id")?, now)?;
            Ok(Value::Null)
        }
        "set_bpm" => Ok(json!(markers::set_song_bpm(con, str_arg(op, "path")?, f("bpm").ok_or("missing bpm")?, now)?)),
        "set_analysis" => Ok(json!(markers::set_song_analysis(
            con,
            str_arg(op, "path")?,
            f("bpm").ok_or("missing bpm")?,
            f("first_beat"),
            f("confidence").unwrap_or(0.0),
            op.get("version").and_then(Value::as_i64).unwrap_or(0),
            now,
        )?)),
        other => Err(format!("unknown marker op `{other}`")),
    }
}

/// One JSON request in, one JSON response out. Database operations are confined by [`allowed_db`]; the `py_*` and `track_rows`
/// operations touch no database at all.
pub fn handle(request: &Value) -> Value {
    let result = (|| -> R<Value> {
        let op = str_arg(request, "op")?;
        match op {
            "py_fuzzy_match_key" => Ok(map_texts(request, pyval::fuzzy_match_key)?),
            "py_normalize_text" => Ok(map_texts(request, pyval::normalize_text)?),
            "py_int_of_float" => {
                // Batch form: one result per value, null when Python would raise and move on, {"decline": why} when Rust refuses.
                let values = request.get("values").and_then(Value::as_array).ok_or("missing values")?;
                Ok(Value::Array(
                    values
                        .iter()
                        .map(|v| match pyval::py_int_of_float(v) {
                            Ok(Some(i)) => json!(i),
                            Ok(None) => Value::Null,
                            Err(why) => json!({ "decline": why }),
                        })
                        .collect(),
                ))
            }
            "py_decode_ignore" => {
                let hex = str_arg(request, "hex")?;
                let bytes: Vec<u8> = (0..hex.len() / 2).filter_map(|i| u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).ok()).collect();
                Ok(json!(index::decode_ignore(&bytes)))
            }
            "track_rows" => {
                let tracks = request.get("tracks").and_then(Value::as_array).ok_or("missing tracks")?;
                let mut out = Vec::new();
                for t in tracks {
                    out.push(match index::track_row(t)? {
                        Some(r) => Value::Array(r.iter().map(encode_sql).collect()),
                        None => Value::Null,
                    });
                }
                Ok(Value::Array(out))
            }
            "index_init" => {
                let db = allowed_db(str_arg(request, "db")?)?;
                let con = index::connect_write(&db)?;
                index::init_schema(&con)?;
                Ok(Value::Null)
            }
            "index_rebuild" => {
                let db = allowed_db(str_arg(request, "db")?)?;
                let rows = index::rebuild(Path::new(str_arg(request, "tracks")?), &db, i64_arg(request, "now")?)?;
                Ok(json!({ "rows": rows }))
            }
            "index_update" => {
                let db = allowed_db(str_arg(request, "db")?)?;
                let (changed, removed) = index::update(
                    Path::new(str_arg(request, "tracks")?),
                    &db,
                    &strings(request, "changed"),
                    &strings(request, "removed"),
                    i64_arg(request, "now")?,
                )?;
                Ok(json!({ "changed": changed, "removed": removed }))
            }
            "markers_init" => {
                let db = allowed_db(str_arg(request, "db")?)?;
                let mut uuids = uuid_source(request);
                markers::connect(&db, &mut uuids)?;
                Ok(Value::Null)
            }
            "markers_ops" => {
                let db = allowed_db(str_arg(request, "db")?)?;
                let now = i64_arg(request, "now")?;
                let mut uuids = uuid_source(request);
                let mut results = Vec::new();
                // Like the app, every operation opens its own connection (and runs init_schema) and commits before the next.
                for op in request.get("ops").and_then(Value::as_array).ok_or("missing ops")? {
                    let con = markers::connect(&db, &mut uuids)?;
                    results.push(run_marker_op(&con, op, now, &mut uuids)?);
                }
                Ok(Value::Array(results))
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
mod tests;
