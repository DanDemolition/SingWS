#!/usr/bin/env python3
"""Parity of the Rust Top 100 rules (rust/crates/singws-karafun-svc, src/top100.rs, milestone W5) against the REAL Python `karafun_top100`.
Offline: a scratch state file, a fake `post`, a fake browser helper, a scripted clock. Compared: reading the state file (`last_success`, every kind of broken file),
`is_due`, the text `_remember` writes, the upload request (URL, user, headers, the exact JSON body text) and how every kind of answer is read (ok, error field, bad
JSON, wrong shapes, network failure class), `refresh` (due / forced / reader failure / sender result / what is remembered) and the chart read loop (ask times, which
message wins, timeouts). The "fewer than 20 songs" refusal is the server's. Needs:  cd rust && cargo build --release -p singws-karafun-svc
  SINGWS_HOME=$(mktemp -d) .venv-test-arm64-fresh/bin/python tools/karafun_top100_parity.py [--cases N] [--seed S]      exit 1 on any difference
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import karafun_top100 as kt  # noqa: E402

BASE = Path(tempfile.mkdtemp(prefix="kf_top100_parity_"))


def find_cli() -> str:
    for c in (os.environ.get("SINGWS_KF_SVC_BIN", ""), os.path.join(os.environ.get("CARGO_TARGET_DIR", ""), "release", "singws-karafun-svc"),
              os.path.expanduser("~/.cache/singws-rust-target-karafun-svc/release/singws-karafun-svc"), str(ROOT / "rust/target/release/singws-karafun-svc")):
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("singws-karafun-svc not built")


def state_file(data: bytes | None) -> Path:
    p = BASE / f"state_{random.random()}.json"
    if data is not None:
        p.write_bytes(data)
    return p


TEXTS = ["bs\x08ff\x0c", "tilde~x", "Memory", "Don't Stop", 'Say "Hi"', "Back\\slash", "café", "日本語", "\U0001f600 party", "tab\there", "new\nline", "ctl\x01\x1f", "del\x7f", "", " ", "x" * 80, "A/B", "<&>"]
STATE_FILES = [None, b"", b"{}", b'{"last_success": 0}', b'{"last_success": 1700000000.5}', b'{"last_success": 1700000000}', b'{"last_success": -5}', b'{"last_success": true}',
               b'{"last_success": false}', b'{"last_success": null}', b'{"last_success": []}', b'{"last_success": {}}', b'[]', b'5', b'null', b'"x"', b"not json", b"\xff\xfe",
               b'{"last_success": 1e3}', b'{"last_success": 12345678901234567890123}', b'{"other": 1}', b'\xef\xbb\xbf{"last_success": 9}', b'{"last_success": 7, "last_success": 8}', b'{"last_success": NaN}', b'{"last_success": Infinity}']


def songs(rng):
    out = []
    for _ in range(rng.choice([0, 1, 3, 20, 100])):
        sec = rng.choice([1e15, 999999999999999.0, 1e-4, 9.9e-5, 5e-5, 0.0001, 0, 1, 212, 212.5, 0.5, 3.0, 1e-5 if False else 200, 99999, -1, 61.25, 1e15 if False else 5])
        out.append({"title": rng.choice(TEXTS), "artist": rng.choice(TEXTS), "seconds": sec})
    return out


def py_upload(cfg, sgs, source, resp):
    seen = {}

    class R:
        def __init__(self, status, body):
            self.status_code, self.body = status, body

        def json(self):
            return json.loads(self.body)

    def post(url, params=None, data=None, headers=None, timeout=None):
        seen.update(url=url, params=params, data=data, headers=headers, timeout=timeout)
        if "raises" in resp:
            raise type(resp["raises"], (Exception,), {})("boom")
        return R(resp["status"], resp["body"] if resp["body"] is not None else "")
    ok, reason = kt.upload(cfg, sgs, source=source, post=post)
    req = None
    if seen:
        req = {"url": seen["url"], "user": seen["params"]["user"], "body": seen["data"], "headers": seen["headers"], "timeout": seen["timeout"]}
    return {"request": req, "result": [ok, reason]}


def gen(rng):
    r = rng.random()
    if r < 0.12:
        data = rng.choice(STATE_FILES)
        return {"op": "last_success", "hex": None if data is None else data.hex()}, "state"
    if r < 0.22:
        return {"op": "is_due", "now": rng.choice([1e9, 1700000000.0, 0.0]) + rng.choice([0, 20 * 3600 - 1, 20 * 3600, 20 * 3600 + 0.5, 5]),
                "last": rng.choice([0.0, 1e9, 1700000000.0, 1700000000.25]), "every": rng.choice([kt.EVERY_SECONDS, 60.0, 0.0])}, "due"
    if r < 0.28:
        return {"op": "remember_text", "when": rng.choice([0.0, 1700000000.123, 1700000000.0, 1.5, 123456789.987654321, 86400.0, 1e15, 999999999999999.0, 1e-4, 9.9e-5, 5e-5, -1e-4, 0.5])}, "remember"
    if r < 0.55:
        cfg = rng.choice([{"base": "https://wskar.com", "user": "wsk", "key": "k3y"}, {"base": "", "user": "wsk", "key": "k"}, {"user": "wsk", "key": "k"}, {"base": "https://x", "key": "k"},
                          {"base": "https://x", "user": "u", "key": ""}, {"base": "https://x", "user": "café u", "key": "k"}, {}])
        sg = songs(rng)
        body = rng.choice(['{"ok": true}', '{"ok": false}', '{"ok": 1}', '{"ok": 0}', '{"ok": true, "n": 3}', '{"error": "need at least 20 songs"}', '{"error": "bad key"}', '{"error": ""}', '{"error": 5}',
                           '{"error": true}', '{"error": -5}', '{"ok": NaN}', '{"ok": Infinity}', '{"error": "x", "n": NaN}', '{}', '[]', '[1]', 'null', '0', '5', '"x"', 'true', 'not json', '', '{"ok": true', None])
        resp = rng.choice([{"status": 200, "body": body}] * 5 + [{"status": 401, "body": body}, {"status": 500, "body": body}, {"status": 404, "body": body}, {"raises": rng.choice(["ConnectionError", "Timeout", "SSLError"])}])
        return {"op": "upload", "config": cfg, "songs": sg, "source": rng.choice(["KaraFun Top US", "x", "café"]), "resp": resp}, "upload"
    if r < 0.72:
        return {"op": "refresh", "force": rng.random() < 0.3, "now": 1700000000.5, "last": rng.choice([0.0, 1700000000.5 - 3600, 1700000000.5 - 20 * 3600, 1700000000.5 - 20 * 3600 + 0.25, 1700000000.5 - 90000]),
                "reader": rng.choice([{"songs": []}, {"songs": [{"title": "a", "artist": "b", "seconds": 1}]}, {"error": "KaraFun's chart could not be read in time"}, {"error": "boom"}, {"error": ""}]),
                "sender": rng.choice([[True, "100 songs sent"], [False, "HTTP 500"], [False, "need at least 20 songs"], [True, "x"]])}, "refresh"
    # the chart read loop
    deliveries = []
    t = 0.0
    for _ in range(rng.choice([0, 1, 2, 3, 5])):
        t += rng.choice([0.0, 0.25, 0.5, 1.0, 3.5, 4.0, 4.25, 8.0, 149.75, 150.0, 151.0])
        m = rng.choice([{"type": "top", "songs": [{"title": "a", "artist": "b", "seconds": 1}], "total": 1}, {"type": "top", "songs": []}, {"type": "top"}, {"type": "top", "songs": None},
                        {"type": "error", "message": "KaraFun is still starting"}, {"type": "error", "message": "boom"}, {"type": "error", "message": ""}, {"type": "error"},
                        {"type": "status"}, {"type": "log"}])
        deliveries.append([t, m])
    return {"op": "read_chart", "timeout": rng.choice([150.0, 10.0, 3.0, 4.0, 4.5]), "deliveries": deliveries}, "read"


def python_answer(req, kind):
    op = req["op"]
    if op == "last_success":
        p = state_file(None if req["hex"] is None else bytes.fromhex(req["hex"]))
        return {"v": kt.last_success(p)}
    if op == "is_due":
        p = state_file(json.dumps({"last_success": req["last"]}).encode())
        return {"v": kt.is_due(req["now"], every=req["every"], path=p)}
    if op == "remember_text":
        p = state_file(None)
        kt._remember(p, req["when"])
        return {"v": p.read_text()}
    if op == "upload":
        return py_upload(req["config"], req["songs"], req["source"], req["resp"])
    if op == "refresh":
        p = state_file(json.dumps({"last_success": req["last"]}).encode())
        calls = {"read": False, "sent": False}

        def reader():
            calls["read"] = True
            if "error" in req["reader"]:
                raise RuntimeError(req["reader"]["error"])
            return req["reader"]["songs"]

        def sender(cfg, sg):
            calls["sent"] = True
            return tuple(req["sender"])
        ok, msg = kt.refresh({}, force=req["force"], now=req["now"], path=p, reader=reader, sender=sender)
        remembered = json.loads(p.read_text())["last_success"] if (ok and calls["sent"]) else None
        return {"result": [ok, msg], "remember": remembered, "read": calls["read"], "sent": calls["sent"]}
    if op == "read_chart":
        return py_read_chart(req)
    raise ValueError(op)


def py_read_chart(req):
    clock = [1000.0]
    pend = sorted(((1000.0 + t, m) for t, m in req["deliveries"]), key=lambda x: x[0])
    holder = {}
    asks = []

    class Web:
        def __init__(self, profile, on_message, on_log, chrome_path):
            holder["cb"] = on_message
        def start(self): pass
        def top(self, cid): asks.append(clock[0] - 1000.0)
        def close(self): pass

    class Event:
        def __init__(self): self.flag = False
        def set(self): self.flag = True
        def is_set(self): return self.flag
        def wait(self, timeout=None):
            limit = clock[0] + timeout
            while not self.flag and pend and pend[0][0] <= limit:
                t, m = pend.pop(0)
                clock[0] = max(clock[0], t)
                holder["cb"](m)
            if not self.flag:
                clock[0] = limit
            return self.flag

    with mock.patch.object(kt.time, "monotonic", lambda: clock[0]), mock.patch.object(kt.threading, "Event", Event):
        try:
            sg = kt.read_chart(make_web=Web, find_browser=lambda cb: Path("/x/chrome"), timeout=req["timeout"])
            result = {"songs": sg}
        except RuntimeError as error:
            result = {"error": str(error)}
    return {"asks": asks, "result": result, "end": clock[0] - 1000.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=6000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    cli = find_cli()
    rng = random.Random(a.seed)
    same = diff = declined = 0
    per: dict = {}
    first = []
    for _ in range(a.cases):
        req, kind = gen(rng)
        py = python_answer(req, kind)
        p = subprocess.run([cli], input=json.dumps(dict(req, kind="top100")), capture_output=True, text=True, timeout=30)
        rs = json.loads(p.stdout)
        if "declined" in rs:
            declined += 1
            continue
        key = req["op"]
        per.setdefault(key, [0, 0])[0] += 1
        if py == rs:
            same += 1
        else:
            diff += 1
            per[key][1] += 1
            if len(first) < 4:
                first.append((req, py, rs))
    print(f"cases {a.cases}: identical {same}, different {diff}, declined {declined}; per op (total, different): {json.dumps(per, sort_keys=True)}")
    for req, py, rs in first:
        print("--- request:", json.dumps(req)[:500], "\n python:", json.dumps(py)[:500], "\n rust:  ", json.dumps(rs)[:500])
    print("IDENTICAL" if diff == 0 else "DIFFERENT")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
