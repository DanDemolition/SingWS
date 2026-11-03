//! `singws-karaoke-proto-cli CASES.json` -> `{case name: [[ms, text], ...]}` on stdout. See `tools/karaoke_transport_parity.py`.
use serde_json::{Map, Value};

fn main() {
    let path = std::env::args().nth(1).expect("usage: singws-karaoke-proto-cli CASES.json");
    let cases: Value = serde_json::from_str(&std::fs::read_to_string(path).expect("read cases")).expect("cases json");
    let mut out = Map::new();
    for c in cases.as_array().expect("a list of cases") {
        let name = c["name"].as_str().unwrap_or("?").to_string();
        out.insert(name, singws_karaoke_proto::scenario::run(c));
    }
    println!("{}", Value::Object(out));
}
