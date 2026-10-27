//! `singws-dbw-cli`: one JSON request on stdin, one JSON response on stdout. Writes only inside the system temp directory (see the library).
use std::io::Read;

fn main() {
    let mut input = String::new();
    if std::io::stdin().read_to_string(&mut input).is_err() {
        eprintln!("could not read stdin");
        std::process::exit(2);
    }
    let response = match serde_json::from_str::<serde_json::Value>(&input) {
        Ok(request) => singws_dbw::handle(&request),
        Err(e) => serde_json::json!({ "ok": false, "error": format!("request: {e}") }),
    };
    println!("{}", serde_json::to_string(&response).expect("response serializes"));
}
