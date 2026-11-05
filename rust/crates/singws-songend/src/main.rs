//! `singws-songend REPLAY.json` -> {"results":[{"t","cmds","state","ret"}...]} (stdin when no file is given).
use serde_json::{json, Value};
use std::io::Read;

fn main() {
    let mut text = String::new();
    match std::env::args().nth(1) {
        Some(p) => text = std::fs::read_to_string(p).expect("read replay"),
        None => {
            std::io::stdin().read_to_string(&mut text).expect("read stdin");
        }
    }
    let replay: Value = serde_json::from_str(&text).expect("replay json");
    let mut m = singws_songend::Machine::new();
    let mut results = Vec::new();
    for e in replay["events"].as_array().expect("events") {
        let t = e["t"].as_i64().unwrap_or(0);
        let (cmds, ret) = m.event(t, e["ev"].as_str().unwrap_or(""), &e["a"], &e["snap"]);
        results.push(json!({"t": t, "cmds": cmds, "state": m.state(), "ret": ret}));
    }
    println!("{}", json!({"results": results}));
}
