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
