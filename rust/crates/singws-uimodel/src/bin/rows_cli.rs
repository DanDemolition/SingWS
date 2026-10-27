//! `singws-rows-cli`: stdin = `{"op": "search"|"queue_song"|"status"|"split"|"normalize"|"rtime"|"badge", ...}`, stdout = the result or `{"error": ...}`. Read-only; used by tools/rows_parity.py.
use serde_json::{Value, json};
use singws_uimodel::rows;
use std::io::Read;

fn handle(r: &Value) -> Result<Value, String> {
    let null = Value::Null;
    let g = |k: &str| r.get(k).unwrap_or(&null);
    let flag = |k: &str| r.get(k).and_then(Value::as_bool).unwrap_or(false);
    match r.get("op").and_then(Value::as_str).ok_or("missing op")? {
        "search" => {
            let x = rows::search_row(g("track"))?;
            Ok(json!({"left": x.left, "right": x.right, "tooltip": x.tooltip}))
        }
        "queue_song" => {
            let x = rows::queue_song_row(g("entry"))?;
            Ok(json!({"left": x.left, "right": x.right, "tooltip": x.tooltip}))
        }
        "status" => Ok(json!({"text": rows::singer_status_label(g("singer"), flag("start"), flag("current"), flag("next"))?})),
        "split" => {
            let (a, t, d) = rows::split_display(g("display").as_str().unwrap_or(""), g("fallback").as_str().unwrap_or(""));
            Ok(json!({"parts": [a, t, d]}))
        }
        "normalize" => Ok(json!({"seconds": rows::normalize_karafun_duration(g("value"))?})),
        "rtime" => Ok(json!({"text": rows::fmt_right_time(g("value"))})),
        "badge" => Ok(json!({"text": rows::track_media_badge(g("track"))?})),
        op => Err(format!("unknown op {op}")),
    }
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
