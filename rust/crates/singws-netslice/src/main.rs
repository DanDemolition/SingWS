//! `singws-netslice`: stdin JSON request `{"op": "probe"|..., ...}` (or an array of them) -> the answer JSON (or an array),
//! `{"unsupported": reason}` for a case the crate does not model. Reads only stdin; prints only the answer; never opens a socket.
use std::io::{Read, Write};

fn main() {
    let mut buf = Vec::new();
    let _ = std::io::stdin().read_to_end(&mut buf);
    let Ok(v) = serde_json::from_slice::<serde_json::Value>(&buf) else {
        eprintln!("bad json");
        std::process::exit(2);
    };
    let out = match &v {
        serde_json::Value::Array(items) => serde_json::Value::Array(items.iter().map(singws_netslice::build).collect()),
        other => singws_netslice::build(other),
    };
    let _ = writeln!(std::io::stdout().lock(), "{out}");
}
