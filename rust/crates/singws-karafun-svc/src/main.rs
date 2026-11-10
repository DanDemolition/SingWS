//! `singws-karafun-svc`: one JSON replay request on stdin -> one JSON answer (see `singws_karafun_svc::handle`).
//! `--batch`: one request per line, one answer per line (for the parity and mutation tools).
use std::io::{BufRead, Read};

fn answer(text: &str) -> serde_json::Value {
    match serde_json::from_str::<serde_json::Value>(text) {
        Ok(v) => singws_karafun_svc::handle(&v),
        Err(e) => serde_json::json!({"error": format!("bad request: {e}")}),
    }
}

fn main() {
    if std::env::args().any(|a| a == "--batch") {
        for line in std::io::stdin().lock().lines().map_while(Result::ok) {
            if !line.trim().is_empty() {
                println!("{}", answer(&line));
            }
        }
        return;
    }
    let mut text = String::new();
    let _ = std::io::stdin().read_to_string(&mut text);
    println!("{}", answer(&text));
}
