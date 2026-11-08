//! Test driver for `tail` (`tools/tail_silence_parity.py`): stdin {"batch":[{"op","sr","pcm":[int16...],..}]} -> answers.
use serde_json::{json, Value};
use singws_analysis::tail;
use std::io::Read;

fn one(r: &Value) -> Value {
    let sr = r["sr"].as_u64().unwrap_or(16000) as usize;
    let pcm: Vec<f32> = r["pcm"].as_array().map(|a| a.iter().map(|x| x.as_i64().unwrap_or(0) as f32 / 32768.0).collect()).unwrap_or_default();
    match r["op"].as_str().unwrap_or("") {
        "lead" => json!({"v": tail::lead_silence(&pcm, sr, r["noise_db"].as_f64().unwrap_or(-50.0), r["min_silence"].as_f64().unwrap_or(0.5))}),
        "trailing" => match tail::trailing_silence(&pcm, sr, r["noise_db"].as_f64().unwrap_or(-65.0)) {
            Ok(v) => json!({"v": v}),
            Err(e) => json!({"err": format!("{e:?}")}),
        },
        "audible" => json!({"v": tail::audible_through_end(&pcm, sr, r["window"].as_f64().unwrap_or(0.5), r["threshold"].as_f64().unwrap_or(-40.0))}),
        _ => json!({"err": "unknown op"}),
    }
}

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).unwrap();
    let v: Value = serde_json::from_str(&s).unwrap();
    let out: Vec<Value> = v["batch"].as_array().map(|a| a.iter().map(one).collect()).unwrap_or_default();
    println!("{}", Value::Array(out));
}
