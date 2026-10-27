//! `singws-waitlist-cli`: stdin = `{"op": ..., ...}`, stdout = the result or `{"error": ...}`. Read-only; used by tools/waitlist_parity.py.
//! ops: `sections` {pending_acceptance?: [..], waiting: {..}, show_removed, removed: {..}, local_ids, accepted_ids, first_time_names}
//!      `row` {req, kind, selectable, first_time}   `intake` {req}   `reason` {req}   `isoclock` {text}
use serde_json::{Value, json};
use singws_uimodel::waitlist as w;
use std::collections::HashSet;
use std::io::Read;

fn ids(v: Option<&Value>) -> HashSet<i64> {
    v.and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_i64).collect()).unwrap_or_default()
}

fn handle(r: &Value) -> Result<Value, String> {
    let null = Value::Null;
    let op = r.get("op").and_then(Value::as_str).ok_or("missing op")?;
    match op {
        "sections" => {
            let names: HashSet<String> = r.get("first_time_names").and_then(Value::as_array).map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default();
            let (acc, loc) = (ids(r.get("accepted_ids")), ids(r.get("local_ids")));
            let ctx = w::Context { first_time_names: &names, accepted_ids: &acc, local_ids: &loc };
            let pending = r.get("pending_acceptance").and_then(Value::as_array);
            let secs = w::sections(pending.map(|v| v.as_slice()), r.get("waiting").unwrap_or(&null), r.get("show_removed").and_then(Value::as_bool).unwrap_or(false), r.get("removed").unwrap_or(&null), &ctx)?;
            Ok(Value::Array(
                secs.iter().map(|s| json!({"key": s.key, "title": s.title, "helper": s.helper, "rows": s.rows.iter().map(w::row_json).collect::<Vec<_>>()})).collect(),
            ))
        }
        "row" => {
            let row = w::build_row(r.get("req").unwrap_or(&null), r.get("kind").and_then(Value::as_str).unwrap_or(""), r.get("selectable").and_then(Value::as_bool).unwrap_or(false), r.get("first_time").and_then(Value::as_bool).unwrap_or(false))?;
            Ok(w::row_json(&row))
        }
        "intake" => {
            let req = r.get("req").unwrap_or(&null);
            Ok(json!({"non_actionable": w::non_actionable(req)?, "waiting": w::is_waiting_for_add(req)?, "status_kind": w::status_kind(req)?}))
        }
        "reason" => Ok(json!({"reason": w::reason_text(r.get("req").unwrap_or(&null))?})),
        "isoclock" => {
            let t = r.get("text").and_then(Value::as_str).ok_or("missing text")?;
            Ok(json!({"parsed": w::from_isoformat(t)?.is_some()}))
        }
        _ => Err(format!("unknown op {op}")),
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
