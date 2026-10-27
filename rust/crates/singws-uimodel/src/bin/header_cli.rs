//! `singws-header-cli`: stdin = `{"op": "chips"|"status"|"disconnected"|"eta"|"summary", ...}`, stdout = the result or `{"error": ...}`. Read-only; used by tools/header_parity.py.
use serde_json::{Value, json};
use singws_uimodel::header as h;
use std::io::Read;

fn handle(r: &Value) -> Result<Value, String> {
    let null = Value::Null;
    let g = |k: &str| r.get(k).unwrap_or(&null);
    let net = r.get("network_configured").and_then(Value::as_bool).unwrap_or(false);
    match r.get("op").and_then(Value::as_str).ok_or("missing op")? {
        "chips" => Ok(json!({"chips": h::chips(g("settings"), g("connection"), net, g("summary"))?.iter().map(|c| json!([c.text, c.tone])).collect::<Vec<_>>()})),
        "status" => {
            let s = h::status(g("settings"), g("connection"), net, g("last"), g("attention"), g("queue_rev"))?;
            Ok(json!({"dot_token": s.dot_token, "text": s.text, "tooltip": s.tooltip, "location": s.location}))
        }
        "disconnected" => Ok(json!({"state": h::disconnected_state(g("message").as_str().unwrap_or(""))?})),
        "eta" => {
            let v = h::eta_view(g("queue"), g("total_secs"), r.get("now").and_then(Value::as_i64).ok_or("missing now")?, g("queue_mode"), r.get("locked").and_then(Value::as_bool).unwrap_or(false), r.get("looping").and_then(Value::as_bool).unwrap_or(false))?;
            Ok(json!({"singer_count": v.singer_count, "ends": v.ends, "minutes": v.minutes, "html": v.html}))
        }
        "summary" => Ok(json!({"text": h::queue_summary(g("count").as_i64().unwrap_or(0), g("minutes").as_i64().unwrap_or(0), g("locked").as_bool().unwrap_or(false), g("looping").as_bool().unwrap_or(false))})),
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
