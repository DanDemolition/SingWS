//! `singws-wire-cli`: reads a JSON request (or an array of requests) from stdin, writes the prepared wire form to stdout.
//! Pure computation: it never opens a socket.
use std::io::Read;

fn main() {
    let mut input = String::new();
    if std::io::stdin().read_to_string(&mut input).is_err() {
        eprintln!("could not read stdin");
        std::process::exit(2);
    }
    let out = match serde_json::from_str::<serde_json::Value>(&input) {
        Ok(v) => singws_wire::handle(&v),
        Err(e) => serde_json::json!({ "ok": false, "error": format!("request: {e}") }),
    };
    println!("{}", serde_json::to_string(&out).expect("serializes"));
}
