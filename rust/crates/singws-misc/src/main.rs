//! Batch driver: stdin {"batch":[{"op":"locfix","events":[["fix",obj,accuracy,age],["error",code,text],..]},..]} -> answers.
use serde_json::{json, Value};
use singws_misc::LocationFixCollector;
use std::io::Read;

fn one(r: &Value) -> Value {
    match r["op"].as_str().unwrap_or("") {
        "locfix" => {
            let mut c = LocationFixCollector::default();
            let mut stops = vec![];
            for e in r["events"].as_array().cloned().unwrap_or_default() {
                let res = match e[0].as_str().unwrap_or("") {
                    "fix" => c.add_fix(&e[1], &e[2], &e[3]),
                    _ => c.add_error(&e[1], e[2].as_str().unwrap_or("")),
                };
                match res {
                    Ok(b) => stops.push(b),
                    Err(why) => return json!({"declined": why}),
                }
            }
            let (fix, err) = c.result();
            json!({"stops": stops, "fix": fix, "error": err})
        }
        "audio_priority" => match singws_misc::local_audio_output_priority(&r["name"], &r["kind"]) {
            Ok((n, t)) => json!({"v": [n, t]}),
            Err(e) => json!({"declined": e}),
        },
        "tombstone" => match singws_misc::tombstone_artist_title(&r["entry"]) {
            Ok((a, t)) => json!({"v": [a, t]}),
            Err(e) => json!({"declined": e}),
        },
        "next_active" => json!({"v": singws_misc::next_active_entry_after(r["songs"].as_array().map(|a| a.as_slice()).unwrap_or(&[]), r["index"].as_u64().map(|i| i as usize))}),
        "request_ts" => match singws_misc::remote_request_timestamp(&r["req"]) {
            Ok(f) => json!({"v": f}),
            Err(e) => json!({"declined": e}),
        },
        "audio_path" => match singws_misc::resolve_karaoke_audio_path(&r["song_info"]) {
            Ok(p) => json!({"v": p}),
            Err(e) => json!({"declined": e}),
        },
        "track_row" => json!({"v": singws_misc::track_from_search_row(&r["row"])}),
        "mp3_pair" => json!({"v": singws_misc::find_mp3_pair_for_cdg(r["path"].as_str().unwrap_or(""))}),
        other => json!({"declined": format!("unknown op {other}")}),
    }
}

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).unwrap();
    let v: Value = serde_json::from_str(&s).unwrap();
    let out: Vec<Value> = v["batch"].as_array().map(|a| a.iter().map(one).collect()).unwrap_or_default();
    println!("{}", Value::Array(out));
}
