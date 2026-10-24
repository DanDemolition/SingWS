#!/usr/bin/env python3
"""Mutation test for singws-control: each mutant must be killed by the Rust unit tests or, failing that, by the loopback scenarios
(tools/control_loopback.py: real PHP + relay + the app's real command code). Exit 1 when a non-equivalent mutant survives."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-control/src"
ENV = dict(os.environ, CARGO_TARGET_DIR=os.path.expanduser("~/.cache/singws-rust-target"))
PY = os.path.expanduser("~/.venvs/singws/.venv-build-arm64/bin/python")
M = [
    ("protocol.rs", 'v.fract() == 0.0 && v.abs() <= 12.0', 'v.abs() <= 12.0', None),
    ("protocol.rs", 'v.abs() <= 12.0), "value must be a whole', 'v.abs() <= 13.0), "value must be a whole', None),
    ("protocol.rs", '(0.25..=4.0).contains(&v) || (25.0..=400.0).contains(&v)', '(0.25..=4.0).contains(&v)', None),
    ("protocol.rs", '(0.0..=1.0).contains(&v)), "value must be between 0 and 1"', '(0.0..=2.0).contains(&v)), "value must be between 0 and 1"', None),
    ("protocol.rs", 'number(args, "seconds").is_some_and(|v| v >= 0.0)', 'number(args, "seconds").is_some_and(|v| v >= -5.0)', None),
    ("protocol.rs", 'const DIRECTIONS: &[&str] = &["up", "down", "top", "bottom"];', 'const DIRECTIONS: &[&str] = &["up", "down", "top", "bottom", "sideways"];', None),
    ("protocol.rs", 'need(text(args, "singer_id").is_some(), "singer_id is required")?;\n            need(args.get("skipped")', 'need(args.get("skipped")', None),
    ("protocol.rs", 'args.get("skipped").is_some_and(Value::is_boolean)', 'args.get("skipped").is_some()', None),
    ("protocol.rs", 'a.insert("source".into(), json!("shell"));', '', None),
    ("protocol.rs", 'if !command.starts_with("queue.") {\n            return Err("expect_queue_sig only applies to queue commands".into());\n        }', '', None),
    ("protocol.rs", '.filter(|s| !s.is_empty()) {\n        if', '{\n        if', None),
    ("protocol.rs", '"request_id": request_id, "sent_at": sent_at', '"request_id": request_id', None),
    ("protocol.rs", 'let n = COUNTER.fetch_add(1, Ordering::SeqCst) % 10_000;', 'let n = 0;', None),
    ("protocol.rs", 'connected: v.get("connected").and_then(Value::as_bool).unwrap_or(true),', 'connected: v.get("connected").and_then(Value::as_bool).unwrap_or(false),', None),
    ("protocol.rs", '.filter(|x| x.is_object())\n                        .map(|x| SingerRow', '.map(|x| SingerRow', None),
    ("protocol.rs", '"ack" => Incoming::Ack(Ack { request_id: s(&v, "request_id"), ok: b(&v, "ok")', '"ack" => Incoming::Ack(Ack { request_id: s(&v, "request_id"), ok: !b(&v, "ok")', None),
    ("protocol.rs", 'let sep = if ws_url.contains(\'?\') { \'&\' } else { \'?\' };', 'let sep = \'?\';', None),
    ("protocol.rs", '"{ws_url}{sep}role=browser&channel=host', '"{ws_url}{sep}role=app&channel=host', None),
    ("session.rs", 'json!({"action": "login", "user": self.user}), json!({"pin": pin})', 'json!({"action": "login", "user": self.user, "pin": pin}), Value::Null', None),
    ("session.rs", 'if pin.trim().is_empty() {\n            return Err(SessionError::InvalidPin);\n        }', '', None),
    ("session.rs", '"invalid_pin" => Err(SessionError::InvalidPin),', '"invalid_pin" => Err(SessionError::LanOnly),', None),
    ("session.rs", '"unauthenticated" => SessionError::Unauthenticated,', '"unauthenticated" => SessionError::NotConfigured,', None),
    ("session.rs", 'let secure_ok = !base.starts_with("https://") || ws_url.starts_with("wss://");', 'let secure_ok = true;', None),
    ("session.rs", 'if info.token.is_empty() || info.tenant.is_empty() {', 'if info.token.is_empty() {', None),
    ("session.rs", 'Client::with_cookies(Arc::new(Gate::default()))', 'Client::default()', None),
    ("client.rs", 'Err(e @ (SessionError::InvalidPin | SessionError::LanOnly | SessionError::NotConfigured | SessionError::TenantMismatch)) => {', 'Err(e @ (SessionError::NotConfigured | SessionError::TenantMismatch)) if false => {', None),
    ("client.rs", 'Err(SessionError::Unauthenticated) => {\n                // the session expired (eight hours) or the server forgot it: log in again\n                session.forget();\n                logged_in = false;\n                continue;', 'Err(SessionError::Unauthenticated) => {\n                continue;', None),
    ("client.rs", 'while outbound.try_recv().is_ok() {}', '', None),
    ("client.rs", 'now.duration_since(*at) >= config.ack_timeout', 'now.duration_since(*at) >= config.ack_timeout * 1000', None),
    ("client.rs", 'Incoming::Ack(a) => shared.pending.lock().unwrap().retain(|(id, _)| id != &a.request_id),', 'Incoming::Ack(_) => {}', None),
    ("client.rs", 'Incoming::Error { request_id: Some(r), .. } => shared.pending.lock().unwrap().retain(|(id, _)| id != r),', 'Incoming::Error { request_id: Some(_), .. } => {}', None),
    ("client.rs", '        self.shared.pending.lock().unwrap().push((id.clone(), Instant::now()));\n', '', None),
    ("client.rs", 'let lost: Vec<String> = shared.pending.lock().unwrap().drain(..).map(|(id, _)| id).collect();\n        for id in lost {\n            sink(Output::NoAnswer { request_id: id });\n        }', '', None),
    ("client.rs", 'if !self.is_connected() {\n            return Err(SendError::NotConnected);\n        }\n        let id = request_id.to_string();', 'let id = request_id.to_string();', None),
    ("client.rs", 'let url = browser_url(&info.ws_url, &info.tenant, &info.token);', 'let url = browser_url(&info.ws_url, &config.user, &info.token);', "the server's tenant and the configured user are the same string whenever login succeeded (the token endpoint rejects a mismatch), so the relay, which takes the tenant from the token, behaves identically"),
    ("view.rs", 'self.connected && self.app_online', 'self.connected || self.app_online', None),
    ("view.rs", 'if !connected {\n                    self.app_online = false;\n                }', '', None),
    ("view.rs", 'if a.message.trim() == "stale_view" {\n                    self.refresh_requested = true;\n                }', '', None),
    ("view.rs", 'let kind = if a.ok { kind } else if kind == "ok" { "error" } else { kind };', 'let kind = kind;', None),
    ("view.rs", 'None if outcome.1 == "ok" => return,', 'None if false => return,', None),
    ("view.rs", 'self.pending.retain(|(id, _)| id != request_id);', 'self.pending.clear();', None),
    ("view.rs", 'return humanize(rest);', 'return (rest.to_string(), "ok");', None),
    ("view.rs", '"stale_view" => ("The list changed before your edit arrived. It was refreshed; check it and try again".into(), "warn"),', '"stale_view" => ("The list changed".into(), "ok"),', None),
    ("view.rs", '"shell_commands_off" => ("Remote control is switched off in the app (setting shell_commands)".into(), "error"),', '"shell_commands_off" => ("off".into(), "ok"),', None),
    ("view.rs", 'self.login_error.clear();', '', None),
    ("view.rs", 'self.seq += 1;\n        self.result = Some(CommandResult { seq: self.seq, text: text.to_string(), kind: "error" });', 'self.result = Some(CommandResult { seq: self.seq, text: text.to_string(), kind: "error" });', None),
    ("view.rs", 'if code == "app_offline" {\n                    self.app_online = false;\n                }', '', None),
    ("view.rs", 'self.queue_sig = Some(q.queue_sig.clone());\n                self.singers = q.singers.clone();', 'self.singers = q.singers.clone();', None),
    ("view.rs", '"play_pause" | "stop" | "next" | "restart" | "key_up"', '"stop" | "next" | "restart" | "key_up"', None),
]


def run(cmd, timeout=900):
    return subprocess.run(cmd, cwd=ROOT / "rust", env=ENV, capture_output=True, text=True, timeout=timeout)


def main():
    survivors, killed = [], {"unit": 0, "loopback": 0, "build": 0}
    originals = {}
    try:
        only = {int(x) for x in os.environ.get("ONLY", "").split(",") if x.strip()}
        for n, (fname, old, new, reason) in enumerate(M):
            if only and n not in only:
                continue
            path = SRC / fname
            text = originals.setdefault(fname, path.read_text())
            if text.count(old) != 1:
                print(f"[{n:02d}] PATTERN NOT UNIQUE/FOUND ({text.count(old)}) {fname}: {old[:60]!r}")
                survivors.append(n)
                continue
            path.write_text(text.replace(old, new, 1))
            try:
                r = run(["cargo", "test", "-p", "singws-control", "--lib", "--quiet"], timeout=240)
                if r.returncode != 0:
                    kind = "build" if "could not compile" in r.stderr else "unit"
                    killed[kind] += 1
                    print(f"[{n:02d}] killed by {kind:8s} {fname}: {old[:50]!r}")
                    continue
            except subprocess.TimeoutExpired:
                killed["unit"] += 1
                print(f"[{n:02d}] killed by unit (hang)")
                continue
            run(["cargo", "build", "--release", "-p", "singws-control"])
            try:
                p = subprocess.run([PY, str(ROOT / "tools/control_loopback.py")], cwd=ROOT, capture_output=True, text=True, timeout=600,
                                   env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
                failed = p.returncode != 0
            except subprocess.TimeoutExpired:
                failed = True
            if failed:
                killed["loopback"] += 1
                print(f"[{n:02d}] killed by loopback {fname}: {old[:50]!r}")
            elif reason:
                print(f"[{n:02d}] EQUIVALENT ({reason[:80]})")
            else:
                survivors.append(n)
                print(f"[{n:02d}] SURVIVED {fname}: {old[:70]!r}")
    finally:
        for fname, text in originals.items():
            (SRC / fname).write_text(text)
        run(["cargo", "build", "--release", "-p", "singws-control"])
    print(f"killed: {killed}; survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
