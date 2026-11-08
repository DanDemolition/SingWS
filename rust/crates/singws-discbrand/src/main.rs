//! Batch driver: stdin {"batch":[{"op":..},..]} -> answers. Ops: info, canonical, priority, matches, candidates, effective, choices, preferred, pick, report, segments.
use serde_json::{json, Value};
use singws_discbrand as d;
use std::io::Read;

fn strings(v: &Value) -> Vec<String> {
    v.as_array().map(|a| a.iter().map(|x| x.as_str().unwrap_or("").to_string()).collect()).unwrap_or_default()
}

fn one(r: &Value) -> Value {
    let s = |k: &str| r[k].as_str().unwrap_or("").to_string();
    let res: d::R<Value> = (|| match r["op"].as_str().unwrap_or("") {
        "info" => {
            let i = d::match_info(&s("value"));
            Ok(json!({"raw": i.raw, "normalized": i.normalized, "canonical": i.canonical, "confidence": i.confidence, "matched_alias": i.matched_alias, "match_type": i.match_type}))
        }
        "canonical" => Ok(json!(d::canonical_brand(&s("value"), r["allow_unknown"].as_bool().unwrap_or(true)))),
        "segments" => Ok(json!(d::value_segments(&s("value")))),
        "priority" => {
            let parts = match &r["value"] {
                Value::String(t) => t.split(',').map(str::to_string).collect(),
                other => strings(other),
            };
            Ok(json!(d::normalize_priority(&parts, r["max"].as_u64().unwrap_or(10) as usize)))
        }
        "matches" => Ok(json!(d::disc_value_matches_brand(&s("value"), &s("brand")))),
        "candidates" => Ok(json!(d::candidate_values(r["track"].as_object().ok_or("track")?)?)),
        "effective" => Ok(d::effective_priority(&strings(&r["host"]), &s("raw"), &strings(&r["override"]), r["max"].as_u64().unwrap_or(10) as usize)),
        "choices" => Ok(json!(d::library_choices(r["tracks"].as_array().ok_or("tracks")?)?)),
        "preferred" => Ok(json!(d::request_preferred(r["req"].as_object().ok_or("req")?)?)),
        "pick" => Ok(json!(d::pick_by_priority(r["matches"].as_array().ok_or("matches")?, &strings(&r["priority"]))?)),
        "report" => d::scan_report(r["tracks"].as_array().ok_or("tracks")?, r["max"].as_u64().unwrap_or(50) as usize),
        other => Err(format!("unknown op {other}")),
    })();
    match res {
        Ok(v) => json!({"v": v}),
        Err(e) => json!({"declined": e}),
    }
}

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).unwrap();
    let v: Value = serde_json::from_str(&s).unwrap();
    let out: Vec<Value> = v["batch"].as_array().map(|a| a.iter().map(one).collect()).unwrap_or_default();
    println!("{}", Value::Array(out));
}
