//! `singws-chat-cli`: stdin = `{"chat": {...summary...}, "current": "group", "utc_offset_min": 0}`, stdout = the Chat tab view or `{"error": ...}`. Read-only; used by tools/chat_parity.py.
use serde_json::{Value, json};
use singws_uimodel::chat;
use std::io::Read;

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).ok();
    let out = match serde_json::from_str::<Value>(&s) {
        Ok(r) => chat::view(r.get("chat").unwrap_or(&Value::Null), r.get("current").and_then(Value::as_str).unwrap_or("group"), r.get("utc_offset_min").and_then(Value::as_i64).unwrap_or(0)),
        Err(e) => json!({"error": e.to_string()}),
    };
    println!("{}", serde_json::to_string(&out).unwrap());
}
