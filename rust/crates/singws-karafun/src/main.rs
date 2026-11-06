//! stdin: {"op":"search_script","query":..,"safe_title":..,"safe_artist":..,"require_exact_title":bool,"resolve_only":bool}
//!        {"op":"match_title","title":..}   {"op":"literal","value":..}     or  {"batch":[ ...requests... ]}
//! stdout: {"lines":[...]} | {"title":..} | {"literal":..}   (a batch answers with a list)
use serde_json::{json, Value};
use singws_karafun::{applescript_literal, match_title, search_script};
use std::io::Read;

fn answer(v: &Value) -> Value {
    let s = |k: &str| v[k].as_str().unwrap_or("");
    match v["op"].as_str().unwrap_or("search_script") {
        "match_title" => json!({"title": match_title(s("title"))}),
        "literal" => json!({"literal": applescript_literal(s("value"))}),
        _ => json!({"lines": search_script(
            s("query"), s("safe_title"), s("safe_artist"),
            v["require_exact_title"].as_bool().unwrap_or(false), v["resolve_only"].as_bool().unwrap_or(false))}),
    }
}

fn main() {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).expect("stdin");
    let v: Value = serde_json::from_str(&input).expect("json");
    if let Some(batch) = v["batch"].as_array() {
        println!("{}", Value::Array(batch.iter().map(answer).collect()));
    } else {
        println!("{}", answer(&v));
    }
}
