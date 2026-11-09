//! Batch driver for tools/phrase_parity.py: stdin {"batch":[{"op","sr","pcm":[f32...],..}]} -> answers.
use serde_json::{json, Value};
use singws_phrase as p;
use std::io::Read;

fn one(r: &Value) -> Value {
    let sr = r["sr"].as_u64().unwrap_or(8000) as usize;
    let pcm: Vec<f32> = if let Some(path) = r["pcm_path"].as_str() {
        std::fs::read(path).map(|b| b.chunks_exact(4).map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect()).unwrap_or_default()
    } else {
        r["pcm"].as_array().map(|a| a.iter().map(|x| x.as_f64().unwrap_or(0.0) as f32).collect()).unwrap_or_default()
    };
    match r["op"].as_str().unwrap_or("") {
        "peaks" => json!({"v": p::peaks(&pcm, r["n_cols"].as_u64().unwrap_or(100) as usize).iter().map(|x| json!([x.0, x.1])).collect::<Vec<_>>()}),
        "suggest_start" => json!({"v": p::suggest_start(&pcm, sr, r["bpm"].as_f64(), r["fraction"].as_f64().unwrap_or(0.35))}),
        "tempo" => match p::estimate_tempo_and_beat(&pcm, sr, r["min"].as_f64().unwrap_or(70.0), r["max"].as_f64().unwrap_or(180.0)) {
            Some(t) => json!({"v": {"bpm": t.bpm, "first_beat": t.first_beat, "confidence": t.confidence}}),
            None => json!({"v": null}),
        },
        "loop" => json!({"v": p::beat_aligned_loop(r["start"].as_f64().unwrap_or(0.0), r["bpm"].as_f64().unwrap_or(0.0), r["first_beat"].as_f64(), r["bars"].as_i64().unwrap_or(0)).map(|x| json!([x.0, x.1]))}),
        "smooth" => {
            let x: Vec<f64> = pcm.iter().map(|&v| v as f64).collect();
            json!({"v": p::smooth(&x, r["w"].as_u64().unwrap_or(2) as usize)})
        }
        "sections" => json!({"v": p::sections::detect_sections(&pcm, sr, r["bpm"].as_f64()).iter().map(|s| json!({"seconds": s.seconds, "label": s.label, "from_type": s.from_type, "to_type": s.to_type, "confidence": s.confidence})).collect::<Vec<_>>()}),
        other => json!({"err": format!("unknown op {other}")}),
    }
}

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).unwrap();
    let v: Value = serde_json::from_str(&s).unwrap();
    let out: Vec<Value> = v["batch"].as_array().map(|a| a.iter().map(one).collect()).unwrap_or_default();
    println!("{}", Value::Array(out));
}
