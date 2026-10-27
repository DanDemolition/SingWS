//! `singws-uimodel-cli <db>`: stdin `{"query":..,"limit":..}` (or an array of them), stdout the rows. Read-only.
use serde_json::{Value, json};
use std::io::Read;

fn one(db: &std::path::Path, r: &Value) -> Value {
    let q = r.get("query").and_then(Value::as_str).unwrap_or("");
    let limit = r.get("limit").and_then(Value::as_i64).unwrap_or(500);
    let res = if r.get("fuzzy").and_then(Value::as_bool).unwrap_or(false) { singws_uimodel::search_with_fuzzy(db, q, limit, None) } else { singws_uimodel::search(db, q, limit) };
    match res {
        Ok(res) => json!({ "ok": true, "fuzzy_fallback_needed": res.fuzzy_fallback_needed, "rows": res.rows.iter().map(singws_uimodel::row_json).collect::<Vec<_>>() }),
        Err(e) => json!({ "ok": false, "error": e }),
    }
}

fn main() {
    let Some(db) = std::env::args().nth(1) else {
        eprintln!("usage: singws-uimodel-cli <database>");
        std::process::exit(2);
    };
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).ok();
    let db = std::path::PathBuf::from(db);
    let out = match serde_json::from_str::<Value>(&input) {
        Ok(Value::Array(items)) => Value::Array(items.iter().map(|r| one(&db, r)).collect()),
        Ok(v) => one(&db, &v),
        Err(e) => json!({ "ok": false, "error": e.to_string() }),
    };
    println!("{}", serde_json::to_string(&out).unwrap());
}
