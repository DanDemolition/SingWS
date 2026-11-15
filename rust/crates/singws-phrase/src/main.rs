//! Batch driver for tools/phrase_parity.py: stdin {"batch":[{"op","sr","pcm":[f32...],..}]} -> answers.
use serde_json::Value;
use singws_phrase as p;
use std::io::Read;

fn one(r: &Value) -> Value {
    let pcm: Vec<f32> = if let Some(path) = r["pcm_path"].as_str() {
        std::fs::read(path).map(|b| b.chunks_exact(4).map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect()).unwrap_or_default()
    } else {
        r["pcm"].as_array().map(|a| a.iter().map(|x| x.as_f64().unwrap_or(0.0) as f32).collect()).unwrap_or_default()
    };
    p::answer(r, &pcm)
}

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).unwrap();
    let v: Value = serde_json::from_str(&s).unwrap();
    let out: Vec<Value> = v["batch"].as_array().map(|a| a.iter().map(one).collect()).unwrap_or_default();
    println!("{}", Value::Array(out));
}
