//! stdin: {"settings": {...}}   stdout: {"settings": {...}, "changed": bool}  or  {"decline": "why"}
//!
//! serde_json turns an integer outside 64 bits into a float, which would silently change a value the migration passes through untouched, so
//! such an input is declined before parsing (the same rule as the store crate): a run of 20 or more digits outside a string and not part of a float.
use serde_json::{json, Value};
use std::io::Read;

fn has_huge_integer(text: &str) -> bool {
    let b = text.as_bytes();
    let (mut in_str, mut i) = (false, 0);
    while i < b.len() {
        let c = b[i];
        if in_str {
            if c == b'\\' {
                i += 2;
                continue;
            }
            if c == b'"' {
                in_str = false;
            }
            i += 1;
            continue;
        }
        if c == b'"' {
            in_str = true;
            i += 1;
            continue;
        }
        if c.is_ascii_digit() || c == b'-' {
            let start = i;
            i += 1;
            while i < b.len() && (b[i].is_ascii_digit() || matches!(b[i], b'.' | b'e' | b'E' | b'+' | b'-')) {
                i += 1;
            }
            let tok = &text[start..i];
            let is_float = tok.contains(['.', 'e', 'E']);
            if !is_float && tok.trim_start_matches('-').len() >= 20 {
                return true;
            }
            continue;
        }
        i += 1;
    }
    false
}

fn main() {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).expect("stdin");
    if has_huge_integer(&input) {
        println!("{}", json!({"decline": "decline: integer outside 64 bits in the input"}));
        return;
    }
    let v: Value = serde_json::from_str(&input).expect("json");
    if let Some(batch) = v["batch"].as_array() {
        // many cases in one process (the mutation tool); each entry is answered exactly like a single request
        let out: Vec<Value> = batch
            .iter()
            .map(|c| {
                let mut settings = c.as_object().expect("settings object").clone();
                match singws_settingsmig::migrate(&mut settings) {
                    Ok(changed) => json!({"settings": Value::Object(settings), "changed": changed}),
                    Err(e) => json!({"decline": e}),
                }
            })
            .collect();
        println!("{}", Value::Array(out));
        return;
    }
    let mut settings = v["settings"].as_object().expect("settings object").clone();
    match singws_settingsmig::migrate(&mut settings) {
        Ok(changed) => println!("{}", json!({"settings": Value::Object(settings), "changed": changed})),
        Err(e) => println!("{}", json!({"decline": e})),
    }
}
