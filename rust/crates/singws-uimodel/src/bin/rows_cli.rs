//! `singws-rows-cli`: stdin = `{"op": "search"|"library_db"|"queue_song"|"status"|"split"|"normalize"|"rtime"|"badge", ...}`, stdout = the result or `{"error": ...}`. Read-only; used by tools/rows_parity.py.
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
        // the Library tab's rows for a query over a song index: strict search, else the typo-tolerant scan, then the app's row text for each row
        "library_db" => {
            let db = std::path::PathBuf::from(g("db").as_str().ok_or("missing db")?);
            let res = singws_uimodel::search_with_fuzzy(&db, g("query").as_str().unwrap_or(""), 500, None)?;
            let providers = singws_uimodel::providers_for(&db, &res.rows.iter().map(|r| r.id).collect::<Vec<_>>());
            let mut out = Vec::new();
            for r in &res.rows {
                let x = rows::library_row(r, providers.get(&r.id).map(String::as_str))?;
                out.push(json!({"path": r.path, "left": x.left, "right": x.right, "tooltip": x.tooltip}));
            }
            Ok(json!({"rows": out}))
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
