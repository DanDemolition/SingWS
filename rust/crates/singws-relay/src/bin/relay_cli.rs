//! `singws-relay-cli`: JSON request (or array) on stdin, JSON answer on stdout. Pure computation; no sockets.
use std::io::Read;

fn main() {
    let mut input = String::new();
    if std::io::stdin().read_to_string(&mut input).is_err() {
        eprintln!("could not read stdin");
        std::process::exit(2);
    }
    let out = match serde_json::from_str::<serde_json::Value>(&input) {
        Ok(v) => singws_relay::handle_batch(&v),
        Err(e) => serde_json::json!({ "ok": false, "error": format!("request: {e}") }),
    };
    println!("{}", serde_json::to_string(&out).expect("serializes"));
}
