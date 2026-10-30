//! `singws-status-cli`: stdin = a `status_snapshot.json` document, stdout = the view or `{"error": ...}`. Read-only; used by test_status_snapshot.py.
use serde_json::{Value, json};
use singws_uimodel::status;
use std::io::Read;

fn handle(r: &Value) -> Result<Value, String> {
    let v = status::view(r)?;
    Ok(json!({
        "chips": v.chips.iter().map(|c| json!([c.text, c.tone])).collect::<Vec<_>>(),
        "status": {"dot_token": v.status.dot_token, "text": v.status.text, "tooltip": v.status.tooltip, "location": v.status.location},
        "eta": {"singer_count": v.eta.singer_count, "ends": v.eta.ends, "minutes": v.eta.minutes, "html": v.eta.html},
        "summary": v.summary,
        "report": {"title": v.report.title, "lines": v.report.lines, "log_line": v.report.log_line, "all_passed": v.report.all_passed},
    }))
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
