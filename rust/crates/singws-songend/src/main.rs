//! `singws-songend REPLAY.json` -> {"results":[{"t","cmds","state","ret"}...]} (stdin when no file is given).
use serde_json::{json, Value};
use std::io::Read;

/// `singws-songend --lines`: one event per stdin line ({"t","ev","a","snap"}), one result line per event ({"t","cmds","state","ret"}), flushed at once,
/// so a driver can answer the machine's own commands (a delivery it asked for) before sending the next event.
fn run_lines() {
    use std::io::{BufRead, Write};
    let mut m = singws_songend::Machine::new();
    let stdin = std::io::stdin();
    let mut out = std::io::stdout().lock();
    for line in stdin.lock().lines() {
        let line = line.expect("stdin line");
        if line.trim().is_empty() {
            continue;
        }
        let e: Value = serde_json::from_str(&line).expect("event json");
        let t = e["t"].as_i64().unwrap_or(0);
        let (cmds, ret) = m.event(t, e["ev"].as_str().unwrap_or(""), &e["a"], &e["snap"]);
        writeln!(out, "{}", json!({"t": t, "cmds": cmds, "state": m.state(), "ret": ret})).expect("stdout");
        out.flush().expect("flush");
    }
}

fn main() {
    if std::env::args().nth(1).as_deref() == Some("--lines") {
        run_lines();
        return;
    }
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
