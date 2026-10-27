//! `singws-history-cli`: stdin = one request object, stdout = one response object. Pure functions of the request (no files are opened
//! except by the `load` op, read-only); used by tools/history_parity.py to compare with the app's own Python.
use serde_json::{Value, json};
use singws_uimodel::history::{self, Row, Store};
use std::io::Read;

fn rows_json(rows: &[Row], key_name: &str) -> Value {
    Value::Array(rows.iter().map(|r| json!({ "text": r.text, key_name: r.key, "selectable": r.selectable })).collect())
}

fn song_json(s: &history::Song) -> Value {
    json!({
        "key": s.key, "artist": s.artist, "title": s.title, "songid": s.songid, "disc_id": s.disc_id, "song_type": s.song_type,
        "duet_display": s.duet_display, "path": s.path, "provider": s.provider, "provider_track_id": s.provider_track_id,
        "provider_url": s.provider_url, "local_reference_path": s.local_reference_path, "authorization_requirement": s.authorization_requirement,
        "availability_status": s.availability_status, "last_key": s.last_key, "last_tempo_percent": s.last_tempo_percent,
        "play_count": s.play_count, "first_performed_at": s.first_performed_at, "last_performed_at": s.last_performed_at, "updated_at": s.updated_at,
    })
}

fn store_json(st: &Store) -> Value {
    Value::Array(
        st.singers
            .iter()
            .map(|s| {
                json!({
                    "key": s.key, "name": s.name, "preferred_disc_priority": s.preferred_disc_priority, "updated_at": s.updated_at,
                    "last_seen_at": s.last_seen_at, "total_performances": s.total_performances, "unique_song_count": s.unique_song_count,
                    "songs": s.songs.iter().map(song_json).collect::<Vec<_>>(),
                })
            })
            .collect(),
    )
}

fn usize_arg(r: &Value, k: &str, d: usize) -> usize {
    r.get(k).and_then(Value::as_u64).map(|v| v as usize).unwrap_or(d)
}

fn handle(r: &Value) -> Result<Value, String> {
    let op = r.get("op").and_then(Value::as_str).ok_or("missing op")?;
    let store = || history::load_text(r.get("raw_text").and_then(Value::as_str).ok_or("missing raw_text")?);
    let now = r.get("now").and_then(Value::as_i64).unwrap_or(0);
    match op {
        "normalize" => Ok(store_json(&store()?)),
        "directory" => {
            let d = history::directory(&store()?, r.get("search").and_then(Value::as_str).unwrap_or(""), usize_arg(r, "render_limit", 700), usize_arg(r, "song_scan_limit", 500))?;
            Ok(json!({ "rows": rows_json(&d.rows, "singer_key"), "total_matches": d.total_matches, "shown": d.shown }))
        }
        "songs" => {
            let v = history::songs_view(
                &store()?,
                r.get("singer_key").and_then(Value::as_str).unwrap_or(""),
                r.get("query").and_then(Value::as_str).unwrap_or(""),
                usize_arg(r, "render_limit", 500),
                now,
            )?;
            Ok(json!({ "rows": rows_json(&v.rows, "song_key"), "total": v.total, "shown": v.shown, "total_unfiltered": v.total_unfiltered, "filter_active": v.filter_active, "meta": v.meta }))
        }
        "details" => {
            let (name, summary) = history::details(&store()?, r.get("singer_key").and_then(Value::as_str).unwrap_or(""));
            Ok(json!({ "name": name, "summary": summary }))
        }
        "last_sang" => {
            let ts = r.get("timestamps").and_then(Value::as_array).ok_or("missing timestamps")?;
            Ok(Value::Array(ts.iter().map(|t| json!({ "last_sang": history::last_sang_text(t.as_i64().unwrap_or(0), now), "stamp": history::format_timestamp(t.as_i64().unwrap_or(0)) })).collect()))
        }
        "casefold" => {
            let texts = r.get("texts").and_then(Value::as_array).ok_or("missing texts")?;
            Ok(Value::Array(texts.iter().map(|t| match history::casefold(t.as_str().unwrap_or("")) { Ok(s) => json!(s), Err(e) => json!({ "decline": e }) }).collect()))
        }
        other => Err(format!("unknown op `{other}`")),
    }
}

fn main() {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).ok();
    let out = match serde_json::from_str::<Value>(&input) {
        Ok(r) => match handle(&r) {
            Ok(v) => json!({ "ok": true, "result": v }),
            Err(e) => json!({ "ok": false, "error": e }),
        },
        Err(e) => json!({ "ok": false, "error": e.to_string() }),
    };
    println!("{}", serde_json::to_string(&out).unwrap());
}
