//! `singws-ticker-cli`: stdin = `{"queue": [...], "settings": {...}, "now_parts": [...]|null}`, stdout = `{"singers": [...], "message": ..., "text": ...}` or `{"error": ...}`.
//! Read-only; used by tools/ticker_parity.py.
use serde_json::{Value, json};
use singws_uimodel::ticker;
use std::io::Read;

fn handle(r: &Value) -> Result<Value, String> {
    let null = Value::Null;
    let v = ticker::view(r.get("queue").unwrap_or(&null), r.get("settings").unwrap_or(&null), r.get("now_parts").unwrap_or(&null))?;
    Ok(json!({"singers": v.singers, "message": v.message, "text": v.text}))
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
