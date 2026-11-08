//! SingWS library scan, proven against the app's own Python (`tools/scan_parity.py`). Pure Rust, no Qt, nothing linked into the app:
//! the app's only use is the optional log-only shadow (`scan_engine = shadow`) that runs `singws-scan-cli` after its own scan.
//!
//! Requests (one JSON object on stdin of the CLI):
//! * `{"op":"scan", ...}`            the full-mode `_build_library_scan_result`; optional `"index_db"` also writes the song index of the
//!                                    scanned tracks into a TEMPORARY database through `singws-dbw` (refused outside the system temp dir)
//! * `{"op":"stem","stems":[..],"fmt":..}`   `parse_filename_stem`
//! * `{"op":"zip_duration","path":..}`        `fast_mp3_duration_from_zip`
//! * `{"op":"mp3_duration","hex":..,"size_hint":..}`  `fast_mp3_duration_from_stream`
//! * `{"op":"normalize","paths":[..]}`        the two path normalisers and `os.path.realpath`

pub mod dupes;
pub mod duration;
pub mod locations;
pub mod names;
pub mod pyfs;
pub mod scan;
mod quick;

use pyfs::R;
use serde_json::{Value, json};

fn hex_bytes(s: &str) -> R<Vec<u8>> {
    if s.len() % 2 != 0 {
        return Err("odd hex".into());
    }
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).map_err(|e| e.to_string())).collect()
}

fn run(req: &Value) -> R<Value> {
    let op = req.get("op").and_then(Value::as_str).ok_or("missing op")?;
    match op {
        "scan" => {
            let mut result = scan::build_scan_result(req)?;
            if let Some(db) = req.get("index_db").and_then(Value::as_str) {
                let db_path = singws_dbw::allowed_db(db)?;
                let tracks_file = db_path.with_extension("tracks.json");
                let tracks = serde_json::to_string(&result["tracks"]).map_err(|e| e.to_string())?;
                std::fs::write(&tracks_file, tracks).map_err(|e| e.to_string())?;
                let now = req.get("now").and_then(Value::as_i64).unwrap_or(0);
                let rows = singws_dbw::index::rebuild(&tracks_file, &db_path, now)?;
                result["index_rows"] = json!(rows);
            }
            Ok(result)
        }
        "stem" => {
            let fmt = req.get("fmt").and_then(Value::as_str).unwrap_or(names::DEFAULT_FILENAME_FORMAT);
            let stems = req.get("stems").and_then(Value::as_array).ok_or("missing stems")?;
            Ok(Value::Array(
                stems
                    .iter()
                    .map(|s| {
                        let (a, t, d) = names::parse_filename_stem(s.as_str().unwrap_or(""), fmt);
                        json!([a, t, d])
                    })
                    .collect(),
            ))
        }
        "zip_duration" => {
            let cwd = req.get("cwd").and_then(Value::as_str).unwrap_or("/");
            duration::duration_from_zip(req.get("path").and_then(Value::as_str).ok_or("missing path")?, cwd)
        }
        "mp3_duration" => {
            let data = hex_bytes(req.get("hex").and_then(Value::as_str).ok_or("missing hex")?)?;
            let hint = req.get("size_hint").and_then(Value::as_u64);
            Ok(json!(duration::mp3_duration_from_bytes(&data, hint)))
        }
        "dupes" => {
            let paths: Vec<String> = req.get("paths").and_then(Value::as_array).ok_or("missing paths")?.iter().map(|p| p.as_str().unwrap_or("").to_string()).collect();
            dupes::audit(&paths)
        }
        "normalize" => {
            let env = locations::Env { cwd: req.get("cwd").and_then(Value::as_str).unwrap_or("/").to_string() };
            let paths = req.get("paths").and_then(Value::as_array).ok_or("missing paths")?;
            Ok(Value::Array(
                paths
                    .iter()
                    .map(|p| {
                        let p = p.as_str().unwrap_or("");
                        match (locations::normalize_location_path(p, &env), locations::normalize_scanned_path(p, &env)) {
                            (Ok(a), Ok(b)) => json!([a, b]),
                            (Err(e), _) | (_, Err(e)) => json!({ "decline": e }),
                        }
                    })
                    .collect(),
            ))
        }
        other => Err(format!("unknown op `{other}`")),
    }
}

/// One request in, one `{"ok":true,"result":..}` or `{"ok":false,"error":..}` out. An error that starts with `decline:` means the port
/// refuses the input rather than risk a different answer.
pub fn handle(req: &Value) -> Value {
    match run(req) {
        Ok(v) => json!({ "ok": true, "result": v }),
        Err(e) => json!({ "ok": false, "error": e }),
    }
}
