//! `singws-diagnostics-cli`: stdin = the probe facts (see `diagnostics::report`), stdout = the report or `{"error": ...}`. Read-only; used by tools/diagnostics_parity.py.
use serde_json::{Value, json};
use singws_uimodel::diagnostics;
use std::io::Read;

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).ok();
    let out = match serde_json::from_str::<Value>(&s).map_err(|e| e.to_string()).and_then(|f| diagnostics::report(&f)) {
        Ok(r) => json!({
            "checks": r.checks.iter().map(|c| json!([c.ok, c.label, c.detail])).collect::<Vec<_>>(),
            "title": r.title, "lines": r.lines, "log_line": r.log_line, "all_passed": r.all_passed,
        }),
        Err(e) => json!({"error": e}),
    };
    println!("{}", serde_json::to_string(&out).unwrap());
}
