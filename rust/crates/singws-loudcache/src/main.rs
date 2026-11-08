//! Batch driver: stdin {"batch":[{"op":..},..]} -> stdout the answers. Ops: gain, info, gain_db, failed, failure_entry, replay.
use serde_json::{json, Value};
use singws_loudcache as lc;
use std::io::Read;

fn sig(v: &Value) -> Option<(i64, i64)> {
    let a = v.as_array()?;
    Some((a.first()?.as_i64()?, a.get(1)?.as_i64()?))
}

fn one(r: &Value) -> Value {
    let wrap = |x: lc::R<Value>| match x {
        Ok(v) => json!({"v": v}),
        Err(e) => json!({"declined": e}),
    };
    match r["op"].as_str().unwrap_or("") {
        "gain" => wrap(lc::gain_from_lufs(r["lufs"].as_f64().unwrap_or(0.0), r["peak"].as_f64()).map(|g| json!(g))),
        "info" => wrap(lc::info_cached(&r["entry"], sig(&r["sig"])).map(|i| match i {
            None => Value::Null,
            Some(i) => json!({"lufs": i.lufs, "peak_db": i.peak_db, "gain_db": i.gain_db, "gain_linear": i.gain_linear, "mode": i.mode}),
        })),
        "gain_db" => wrap(lc::gain_db_cached(&r["entry"], sig(&r["sig"])).map(|g| json!(g))),
        "failed" => wrap(lc::failed_cached(&r["entry"], sig(&r["sig"]), r["zip"].as_bool().unwrap_or(false), r["single_mp3"].as_bool()).map(|b| json!(b))),
        "failure_entry" => json!({"v": lc::failure_entry(sig(&r["sig"]).unwrap_or((0, 0)), r["reason"].as_str().unwrap_or(""), r["now"].as_i64().unwrap_or(0))}),
        "replay" => {
            let cps: Vec<Option<&str>> = r["checkpoints"].as_array().map(|a| a.iter().map(|x| x.as_str()).collect()).unwrap_or_default();
            wrap(lc::replay(r["cache"].as_str(), &cps).map(Value::Object))
        }
        other => json!({"declined": format!("unknown op {other}")}),
    }
}

fn main() {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).unwrap();
    let v: Value = serde_json::from_str(&input).unwrap();
    let out: Vec<Value> = v["batch"].as_array().map(|a| a.iter().map(one).collect()).unwrap_or_default();
    println!("{}", Value::Array(out));
}
