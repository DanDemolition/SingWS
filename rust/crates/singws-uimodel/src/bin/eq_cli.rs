//! `singws-eq-cli`: stdin = `{"settings_text": <settings.json text>}`, stdout = both equalisers as the app would read them, or `{"error": ...}`. Read-only; used by tools/eq_parity.py.
use serde_json::{Value, json};
use singws_uimodel::eq_view::{self, Eq};
use std::io::Read;

fn eq(e: &Eq) -> Value {
    json!({"gains": e.gains.to_vec(), "enabled": e.enabled, "flat": e.flat, "auto_enabled": e.auto_enabled})
}

fn handle(r: &Value) -> Result<Value, String> {
    if r.get("presets").and_then(Value::as_bool).unwrap_or(false) {
        return Ok(json!({"presets": eq_view::PRESETS.iter().map(|(n, g)| json!({"name": n, "gains": g.to_vec()})).collect::<Vec<_>>()}));
    }
    let text = r.get("settings_text").and_then(Value::as_str).ok_or("missing settings_text")?;
    let v: Value = serde_json::from_str(text).map_err(|e| format!("json: {e}"))?;
    let (bgm, karaoke) = eq_view::read(&v)?;
    Ok(json!({"bgm": eq(&bgm), "karaoke": eq(&karaoke)}))
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
