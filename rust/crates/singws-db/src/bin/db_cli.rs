//! `singws-db-cli <database>`: reads one JSON request from stdin, writes one JSON response to stdout. Read-only (see the library).
use std::io::Read;

fn main() {
    let path = match std::env::args().nth(1) {
        Some(p) => p,
        None => {
            eprintln!("usage: singws-db-cli <database>  (JSON request on stdin)");
            std::process::exit(2);
        }
    };
    let mut input = String::new();
    if std::io::stdin().read_to_string(&mut input).is_err() {
        eprintln!("could not read stdin");
        std::process::exit(2);
    }
    let response = match serde_json::from_str::<serde_json::Value>(&input) {
        Ok(request) => singws_db::handle(std::path::Path::new(&path), &request),
        Err(e) => serde_json::json!({ "ok": false, "error": format!("request: {e}") }),
    };
    println!("{}", serde_json::to_string(&response).expect("response serializes"));
}
