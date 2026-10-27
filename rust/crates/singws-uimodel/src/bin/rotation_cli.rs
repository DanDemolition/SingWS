//! `singws-rotation-cli`: stdin = one request, stdout = one response. `migrate` returns the queue after the app's load-time migration;
//! `view` returns what the audience rotation view shows. Read-only; used by tools/rotation_parity.py.
use serde_json::{Value, json};
use singws_uimodel::rotation::{self, Inputs};
use std::io::Read;
use std::path::PathBuf;

fn view_json(v: &rotation::View) -> Value {
    json!({
        "rows": v.rows.iter().map(|r| json!({"number": r.number, "singer": r.singer, "song": r.song, "artist": r.artist, "duet": r.duet, "combined": r.combined, "text": r.text})).collect::<Vec<_>>(),
        "count_label": v.count_label, "title_label": v.title_label, "next_singer": v.next_singer,
        "up_next": [v.up_next.0, v.up_next.1], "on_deck": [v.on_deck.0, v.on_deck.1],
        "now_strip": v.now_strip, "next_strip": v.next_strip,
    })
}

fn handle(r: &Value) -> Result<Value, String> {
    let text = r.get("queue_text").and_then(Value::as_str).ok_or("missing queue_text")?;
    singws_store::reject_big_integers(text).map_err(|e| format!("decline: {e}"))?;
    let raw: Value = serde_json::from_str(text).map_err(|e| format!("json: {e}"))?;
    let queue = rotation::migrate(&raw)?;
    match r.get("op").and_then(Value::as_str).ok_or("missing op")? {
        "migrate" => Ok(Value::Array(queue)),
        "view" => {
            let db = r.get("db").and_then(Value::as_str).map(PathBuf::from);
            let tracks: Vec<Value> = r.get("tracks").and_then(Value::as_array).cloned().unwrap_or_default();
            let inp = Inputs {
                db: db.as_deref(),
                tracks: &tracks,
                now_singing: r.get("now_singing").and_then(Value::as_str).unwrap_or("Singer Rotation"),
                playing: r.get("playing").and_then(Value::as_bool).unwrap_or(false),
                active_singer_id: r.get("active_singer_id").and_then(Value::as_str).unwrap_or(""),
                live_title: r.get("live_title").and_then(Value::as_str).unwrap_or(""),
            };
            Ok(view_json(&rotation::view(&queue, &inp)?))
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
