//! `singws-http-cli`: stdin = one request object or an array of them
//! (`{"method","url","params","data","json","timeout"}`), stdout = the matching result objects. Used by tools/http_parity.py against 127.0.0.1.
use serde_json::{Value, json};
use singws_http::{Client, HttpError, Request};
use std::io::Read;

fn run(client: &Client, r: &Value) -> Value {
    let get = |k: &str| r.get(k).cloned().unwrap_or(Value::Null);
    let req = Request {
        method: r.get("method").and_then(Value::as_str).unwrap_or("GET").to_string(),
        url: r.get("url").and_then(Value::as_str).unwrap_or("").to_string(),
        params: get("params"),
        data: get("data"),
        json: get("json"),
        timeout: r.get("timeout").and_then(Value::as_f64).unwrap_or(10.0),
    };
    match client.send(&req) {
        Ok(resp) => json!({
            "ok": true, "status": resp.status, "url": resp.url,
            "headers": resp.headers.iter().map(|(k, v)| json!([k, v])).collect::<Vec<_>>(),
            "body": String::from_utf8_lossy(&resp.body),
        }),
        Err(e) => {
            let kind = match &e {
                HttpError::ShuttingDown => "shutting_down",
                HttpError::Connect(_) => "connect",
                HttpError::Timeout(_) => "timeout",
                HttpError::Decline(_) => "decline",
                HttpError::Other(_) => "other",
            };
            json!({ "ok": false, "kind": kind, "error": e.to_string() })
        }
    }
}

fn main() {
    let mut input = String::new();
    if std::io::stdin().read_to_string(&mut input).is_err() {
        std::process::exit(2);
    }
    // SINGWS_HTTP_ROOTS=<pem file>: trust only that bundle (like requests + certifi); otherwise the system verifier.
    let client = match std::env::var_os("SINGWS_HTTP_ROOTS") {
        Some(p) => {
            let pem = std::fs::read(&p).expect("roots file");
            Client::with_roots(Default::default(), &pem).expect("roots")
        }
        None => Client::default(),
    };
    let out = match serde_json::from_str::<Value>(&input) {
        Ok(Value::Array(items)) => Value::Array(items.iter().map(|r| run(&client, r)).collect()),
        Ok(one) => run(&client, &one),
        Err(e) => json!({ "ok": false, "kind": "request", "error": e.to_string() }),
    };
    println!("{}", serde_json::to_string(&out).expect("serializes"));
}
