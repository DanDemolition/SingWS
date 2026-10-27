//! `singws-hero-cli`: stdin = `{"op": "text"|"badge"|"remaining"|"time", ...}`, stdout = the result or `{"error": ...}`. Read-only; used by tools/hero_parity.py.
use serde_json::{Value, json};
use singws_uimodel::hero;
use std::io::Read;

fn handle(r: &Value) -> Result<Value, String> {
    let null = Value::Null;
    let g = |k: &str| r.get(k).unwrap_or(&null).clone();
    match r.get("op").and_then(Value::as_str).ok_or("missing op")? {
        "text" => {
            let h = hero::hero_text(&g("title"), &g("artist"), &g("brand"), &g("path"))?;
            Ok(json!({"subtitle": h.subtitle, "meta": h.meta, "format_text": h.format_text}))
        }
        "badge" => Ok(json!({"badge": hero::media_badge_text(&g("path"))?})),
        "remaining" => Ok(json!({"text": hero::remaining_text(&g("label"))?})),
        "time" => Ok(json!({"text": hero::fmt_time(&g("sec"))?})),
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
