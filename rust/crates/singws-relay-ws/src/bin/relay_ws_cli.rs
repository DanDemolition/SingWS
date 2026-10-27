//! `singws-relay-ws-cli <request|host> <ws-url> [--reconnect-ms N] [--seconds S] [--send-after-connect JSON]`
//! Connects, prints one JSON line per output to stdout, stops after S seconds. For tests against a LOCAL server.
use std::io::Write;
use std::time::{Duration, Instant};

use serde_json::json;
use singws_relay::Kind;
use singws_relay_ws::{spawn, Config, Output};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() < 2 {
        eprintln!("usage: singws-relay-ws-cli <request|host> <url> [--reconnect-ms N] [--seconds S] [--send-after-connect JSON]");
        std::process::exit(2);
    }
    let kind = if args[0] == "host" { Kind::Host } else { Kind::Request };
    let mut config = Config::new(kind, args[1].clone());
    let (mut seconds, mut send_after): (f64, Option<serde_json::Value>) = (3.0, None);
    let mut i = 2;
    while i + 1 < args.len() {
        match args[i].as_str() {
            "--reconnect-ms" => config.reconnect_ms = args[i + 1].parse().unwrap_or(5000),
            "--seconds" => seconds = args[i + 1].parse().unwrap_or(3.0),
            "--send-after-connect" => send_after = serde_json::from_str(&args[i + 1]).ok(),
            _ => {}
        }
        i += 2;
    }
    let out = std::sync::Arc::new(std::sync::Mutex::new(std::io::stdout()));
    let sink_out = out.clone();
    let handle = spawn(config, move |o| {
        let line = match o {
            Output::Status(ok, text) => json!({"signal": "status", "ok": ok, "text": text}),
            Output::RequestsAvailable(reason) => json!({"signal": "requests_available", "reason": reason}),
            Output::History(t) => json!({"signal": "history_available", "type": t}),
            Output::Command(c) => json!({"signal": "command", "command": c}),
            Output::Log(t) => json!({"signal": "log", "text": t}),
        };
        let mut o = sink_out.lock().unwrap();
        let _ = writeln!(o, "{line}");
        let _ = o.flush();
    });
    let end = Instant::now() + Duration::from_secs_f64(seconds);
    let mut sent = false;
    while Instant::now() < end {
        if let (false, Some(msg)) = (sent, send_after.as_ref()) {
            if handle.is_connected() && handle.send_json(msg) {
                sent = true;
            }
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    handle.stop();
}
