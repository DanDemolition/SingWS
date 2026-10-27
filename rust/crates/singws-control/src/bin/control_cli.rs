//! `singws-control-cli <base_url> <user> <pin>`: connects like the shell would and speaks JSON lines for tests.
//! stdin: `{"send":"queue.move_singer","args":{...},"expect_queue_sig":"..."}` or `{"quit":true}`. stdout: one JSON object per line
//! (`{"out":"status",...}`, `{"out":"message",...}`, `{"out":"sent","request_id":..}`, `{"out":"send_error",...}`, `{"out":"no_answer",...}`).
use serde_json::{Value, json};
use singws_control::{Config, Incoming, Output, spawn};
use std::io::{BufRead, Write};

fn emit(v: Value) {
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{}", serde_json::to_string(&v).unwrap());
    let _ = out.flush();
}

fn incoming_json(m: &Incoming) -> Value {
    match m {
        Incoming::Hello { role } => json!({"type": "hello", "role": role}),
        Incoming::State(s) => json!({"type": "state", "connected": s.connected, "app_version": s.app_version, "queue_sig": s.queue_sig,
                                      "rotation": s.rotation, "playback": s.playback, "updated_at": s.updated_at}),
        Incoming::Queue(q) => json!({"type": "queue", "queue_sig": q.queue_sig, "singers": q.singers.iter().map(|s| json!({
            "singer_id": s.singer_id, "name": s.name, "skipped": s.skipped, "marker": s.marker,
            "songs": s.songs.iter().map(|y| json!({"entry_id": y.entry_id, "artist": y.artist, "title": y.title, "skipped": y.skipped})).collect::<Vec<_>>()
        })).collect::<Vec<_>>()}),
        Incoming::Tick { position, duration, remaining, playing } => json!({"type": "tick", "position": position, "duration": duration, "remaining": remaining, "playing": playing}),
        Incoming::Ack(a) => json!({"type": "ack", "request_id": a.request_id, "ok": a.ok, "message": a.message}),
        Incoming::Error { request_id, code, message } => json!({"type": "error", "request_id": request_id, "code": code, "message": message}),
        Incoming::Notice { request_id, ok, code, message } => json!({"type": "notice", "request_id": request_id, "ok": ok, "code": code, "message": message}),
        Incoming::Other(t) => json!({"type": "other", "name": t}),
        Incoming::NotJson => json!({"type": "not_json"}),
    }
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    if a.len() < 4 {
        eprintln!("usage: singws-control-cli <base_url> <user> <pin>");
        std::process::exit(2);
    }
    let mut cfg = Config::new(&a[1], &a[2], &a[3]);
    if let Some(path) = std::env::var_os("CONTROL_ROOTS_PEM") {
        cfg.root_pem = Some(std::fs::read(path).expect("CONTROL_ROOTS_PEM"));
    }
    if let Some(ms) = std::env::var("CONTROL_RECONNECT_MS").ok().and_then(|v| v.parse().ok()) {
        cfg.reconnect_ms = ms;
    }
    if let Some(ms) = std::env::var("CONTROL_ACK_TIMEOUT_MS").ok().and_then(|v| v.parse().ok()) {
        cfg.ack_timeout = std::time::Duration::from_millis(ms);
    }
    let handle = spawn(cfg, |o| match o {
        Output::Status(c, t) => emit(json!({"out": "status", "connected": c, "text": t})),
        Output::LoggedIn => emit(json!({"out": "logged_in"})),
        Output::LoginRefused(t) => emit(json!({"out": "login_refused", "text": t})),
        Output::Message(m) => emit(json!({"out": "message", "msg": incoming_json(&m)})),
        Output::NoAnswer { request_id } => emit(json!({"out": "no_answer", "request_id": request_id})),
        Output::Log(t) => emit(json!({"out": "log", "text": t})),
    });
    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        let Ok(v) = serde_json::from_str::<Value>(&line) else { continue };
        if v.get("quit").is_some() {
            break;
        }
        if let Some(cmd) = v.get("send").and_then(Value::as_str) {
            let args = v.get("args").cloned().unwrap_or(json!({}));
            let sig = v.get("expect_queue_sig").and_then(Value::as_str);
            let sent = match v.get("request_id").and_then(Value::as_str) {
                Some(id) => handle.send_with_id(cmd, &args, sig, id),
                None => handle.send(cmd, &args, sig),
            };
            match sent {
                Ok(id) => emit(json!({"out": "sent", "request_id": id})),
                Err(e) => emit(json!({"out": "send_error", "error": e.to_string()})),
            }
        }
    }
    handle.stop();
}
