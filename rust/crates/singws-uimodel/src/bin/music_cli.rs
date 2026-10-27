//! `singws-music-cli`: stdin = `{"playlist_text": <bg_playlist.json text>, "query": <search box text>}`, stdout = the playlist view or `{"error": ...}`. Read-only; used by tools/music_parity.py.
use serde_json::{Value, json};
use singws_uimodel::music;
use std::io::Read;

fn handle(r: &Value) -> Result<Value, String> {
    let text = r.get("playlist_text").and_then(Value::as_str).ok_or("missing playlist_text")?;
    let query = r.get("query").and_then(Value::as_str).unwrap_or("");
    let raw: Value = serde_json::from_str(text).map_err(|e| format!("json: {e}"))?;
    let rows = music::parse_playlist(&raw)?;
    let cutoff = r.get("cutoff").and_then(Value::as_i64).unwrap_or(-1);
    let v = music::view_from(&rows, query, cutoff)?;
    Ok(json!({"filenames": v.rows.iter().map(|r| r.filename.clone()).collect::<Vec<_>>(), "shown": v.shown, "total": v.total, "count_label": v.count_label}))
}

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).ok();
    let out = match serde_json::from_str::<Value>(&s).map_err(|e| e.to_string()).and_then(|r| handle(&r)) {
        Ok(v) => v,
        Err(e) => json!({"error": e}),
    };
    println!("{}", serde_json::to_string(&out).unwrap());
}
