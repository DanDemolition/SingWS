#!/usr/bin/env python3
"""Stage D4c parity: the pure logic of the Rust ``singws-relay`` crate versus the app's REAL relay code. No sockets, no network.

* URL: the app's own ``RelayRequestWorker.relay_url`` / ``HostControlRelayWorker.relay_url`` (extracted from the source and run on real PyQt6
  ``QUrl``/``QUrlQuery``) is the oracle; ``toEncoded()`` is what Qt puts on the wire.
* Messages: the real ``_on_text_message`` methods run against recording signal stubs.
* Lifecycle: the REAL worker classes (``start``, ``stop``, ``_open_socket``, ``_on_connected``, ``_on_disconnected``, ``_on_error``, the single-shot
  reconnect timer) are compiled from the source with fake QObject/QTimer/QWebSocket/pyqtSignal, driven through random event sequences, and their
  traces (sockets opened/aborted/closed, timer starts/stops, signals emitted) must equal the Rust state machine's command traces.

Usage:  python3 tools/relay_parity.py [--cases N] [--seed S]       (exit 1 on any difference)
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import random
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QUrl, QUrlQuery  # noqa: E402  (pure URL classes; no event loop or widgets needed)

ROOT = Path(__file__).resolve().parent.parent
CLI_DEFAULT = os.path.expanduser("~/.cache/singws-rust-target/release/singws-relay-cli")
SRC = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8")
_TREE = ast.parse(SRC)


# ---- compile the app's real classes with fake Qt objects -------------------------------------------------------------------------------

class _BoundSignal:
    def __init__(self, owner, name):
        self.owner, self.name, self.slots = owner, name, []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        self.owner._trace.append((f"emit:{self.name}", args))


class _SignalDescriptor:
    def __init__(self, *types):
        self.name = None

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        bound = obj.__dict__.setdefault("_signals", {})
        if self.name not in bound:
            bound[self.name] = _BoundSignal(obj, self.name)
        return bound[self.name]


class FakeQObject:
    def __init__(self, parent=None):
        self._trace = []
        self._parent = parent


class FakeTimer:
    def __init__(self, parent=None):
        self.parent, self.active, self.interval, self.single = parent, False, None, False
        self.timeout = type("S", (), {"connect": lambda s, slot: setattr(self, "slot", slot)})()

    def setSingleShot(self, v):
        self.single = v

    def setInterval(self, ms):
        self.interval = ms

    def start(self):
        self.active = True
        self.parent._trace.append(("timer_start", self.interval))

    def stop(self):
        if self.active:
            self.parent._trace.append(("timer_stop", None))
        self.active = False

    def isActive(self):
        return self.active


class FakeSocket:
    instances = []

    def __init__(self):
        FakeSocket.instances.append(self)
        self.parent = None
        self.connected = _Sig()
        self.disconnected = _Sig()
        self.textMessageReceived = _Sig()
        self.errorOccurred = _Sig()
        self.state = []

    def setParent(self, p):
        self.parent = p

    def blockSignals(self, v):
        pass

    def open(self, url):
        self.parent._trace.append(("open", url.toEncoded().data().decode()))

    def abort(self):
        self.state.append("abort")
        if self.parent is not None:
            self.parent._trace.append(("abort", None))

    def close(self):
        self.state.append("close")

    def deleteLater(self):
        pass

    def errorString(self):
        return "err"


class _Sig:
    def connect(self, slot):
        pass


def _class_namespace(names):
    ns = {"QObject": FakeQObject, "pyqtSignal": _SignalDescriptor, "QTimer": FakeTimer, "QWebSocket": FakeSocket, "QUrl": QUrl,
          "QUrlQuery": QUrlQuery, "json": json, "print": lambda *a, **k: None}
    for node in _TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_network_normalize_base_url":
            exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "app", "exec"), ns)
    for node in _TREE.body:
        if isinstance(node, ast.ClassDef) and node.name in names:
            exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "app", "exec"), ns)
    return ns


NS = _class_namespace({"RelayRequestWorker", "HostControlRelayWorker"})


def make_worker(kind, base="https://wskar.com", user="dan", key="k", version="1.0.1.4"):
    FakeSocket.instances.clear()
    cls = NS["RelayRequestWorker" if kind == "request" else "HostControlRelayWorker"]
    w = cls(base, user, key, version)
    w._reconnect_timer.parent = w
    return w


def drive(w, event):
    """Apply one event to the REAL worker; return the canonical command tokens it produced."""
    w._trace.clear()
    sock = w._socket
    if event == "start":
        w.start()
    elif event == "stop":
        w.stop()
    elif event == "timer":
        if not w._reconnect_timer.active:
            return []
        w._reconnect_timer.active = False
        w._open_socket()
    elif event == "connected":
        w._on_connected()
    elif event == "disconnected":
        w._on_disconnected()
    elif event == "error":
        w._on_error(None)
    tokens = []
    for kind, arg in w._trace:
        if kind == "open":
            tokens.append("open")
        elif kind == "abort":
            tokens.append("abort_old")
        elif kind == "timer_start":
            tokens.append(f"timer_start({arg})")
        elif kind == "timer_stop":
            tokens.append("timer_stop")
        elif kind == "emit:connection_status_changed":
            tokens.append(f"status({arg[0]},{arg[1]})")
        elif kind == "emit:requests_available":
            tokens.append(f"requests_available({arg[0]})")
    # stop() closes+aborts the live socket without going through the trace; recover it from the socket's own record
    if event == "stop" and sock is not None and "close" in sock.state:
        tokens.insert(1 if "timer_stop" in tokens else 0, "close")
    # `abort` recorded by stop() is the live-socket abort, not an "old socket" abort: drop it (it is part of "close")
    if event == "stop":
        tokens = [t for t in tokens if t != "abort_old"]
    return tokens


# ---- Rust side ----------------------------------------------------------------------------------------------------------------------

def rust_batch(cli, requests):
    out = subprocess.run([cli], input=json.dumps(requests), capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:200])
    return json.loads(out.stdout)


# ---- generators ---------------------------------------------------------------------------------------------------------------------

WORDS = ["", "dan", "Dan & Co", "a b+c%d", "é日😀", "~._-/?#=&", "x" * 80, "UPPER", "tab\t", "q\"'", "100%", " pad "]
BASES = ["https://beta.wskar.com", "https://wskar.com", "wskar.com", "HTTPS://Wskar.COM:8443/", "http://localhost:8080", "https://192.168.1.20:8443",
         "http://example.test/path/", "https://wskar.com///", "  https://wskar.com  ", "https://https:", "ws://x.test", "https://a.b-c.test:1"]


def py_url(kind, base, user, key, version):
    w = make_worker(kind, base, user, key, version)
    return w.relay_url().toEncoded().data().decode("utf-8")


def url_cases(n, rng):
    return [(rng.choice(["request", "host"]), rng.choice(BASES), rng.choice(WORDS), rng.choice(WORDS), rng.choice(["1.0.1.4", "", "v 1", "9.9"]))
            for _ in range(n)]


MESSAGES = [
    '{"type":"hello","user":"dan"}', '{"type":"requests_available"}', '{"type":" REQUESTS_AVAILABLE "}', '{"type":"history_added"}',
    '{"type":"History_Updated"}', '{"type":"history_deleted","id":3}', '{"type":"history_reordered"}', '{"type":"history_preferred_brand_changed"}',
    '{"type":"history_favorite_changed"}', '{"type":"history_bulk_sync"}', '{"type":"command","id":9,"name":"pause"}', '{"type":" COMMAND "}',
    '{"type":"ack"}', '{"type":null}', '{"type":0}', '{"type":7}', '{"type":true}', '{"type":false}', '{"type":[]}', '{"type":["command"]}',
    '{"type":{}}', '{"type":"x"}', "{}", "[]", "[1]", "null", "0", '"requests_available"', "not json", "", "{", '{"type": "requests_available"} trailing',
    '{"type":"hello"}\n', "﻿{}", '{"TYPE":"hello"}', '{"type":"história"}', '{"type":"ßx"}', '{"type":"ǅ"}', '{"type":1.5}', '{"type":"hello\u0000"}',
]


def drive_request_message(text):
    w = make_worker("request")
    w._trace.clear()
    w._on_text_message(text)
    sig = [t for t in w._trace if t[0].startswith("emit")]
    if not sig:
        return {"action": "none"}
    name, args = sig[0]
    return {"action": "requests_available"} if name == "emit:requests_available" else {"action": "history", "type": args[0]}


def drive_host_message(text):
    w = make_worker("host")
    w._trace.clear()
    w._on_text_message(text)
    cmds = [t for t in w._trace if t[0] == "emit:command_received"]
    return cmds[0][1][0] if cmds else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--cli", default=os.environ.get("SINGWS_RELAY_CLI") or CLI_DEFAULT)
    args = parser.parse_args(argv)
    if not os.path.exists(args.cli):
        print(f"missing {args.cli}: cargo build --release -p singws-relay (in rust/)", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    bad, stats = [], {}

    # URLs
    cases = url_cases(args.cases, rng)
    res = rust_batch(args.cli, [{"op": "url", "base_url": b, "user": u, "key": k, "version": v, "host": kind == "host"} for kind, b, u, k, v in cases])
    ok = declined = 0
    for (kind, b, u, k, v), r in zip(cases, res):
        if not r["ok"]:
            declined += 1
            continue
        ok += 1
        if py_url(kind, b, u, k, v) != r["url"]:
            bad.append(("url", (kind, b, u, k, v), py_url(kind, b, u, k, v), r["url"]))
    stats["urls_compared"], stats["urls_declined"] = ok, declined

    # messages
    texts = MESSAGES + [json.dumps({"type": rng.choice(["hello", "command", "requests_available", "history_added", "x", "", None, 3])}) for _ in range(300)]
    rq = rust_batch(args.cli, [{"op": "request_message", "text": t} for t in texts])
    hq = rust_batch(args.cli, [{"op": "host_message", "text": t} for t in texts])
    for t, r, h in zip(texts, rq, hq):
        want = drive_request_message(t)
        got = {"requests_available": {"action": "requests_available"}, "history": {"action": "history", "type": r.get("type")}}.get(r["action"], {"action": "none"})
        if want != got:
            bad.append(("request_message", t, want, got))
        if drive_host_message(t) != h["command"]:
            bad.append(("host_message", t, drive_host_message(t), h["command"]))
    stats["messages"] = len(texts)

    # lifecycle: random event sequences through the REAL classes
    events = ["start", "stop", "timer", "connected", "disconnected", "error"]
    seqs, expected = [], []
    for _ in range(args.cases // 3):
        kind = rng.choice(["request", "host"])
        w = make_worker(kind)
        seq, trace = [], []
        for _ in range(rng.randint(1, 14)):
            e = rng.choice(events)
            if e == "timer" and not w._reconnect_timer.active:
                continue
            trace.append(drive(w, e))
            seq.append(e)
        seqs.append({"op": "lifecycle", "kind": kind, "events": seq})
        expected.append((kind, seq, trace, w._connected, w._reconnect_timer.active))
    for (kind, seq, trace, connected, timer), r in zip(expected, rust_batch(args.cli, seqs)):
        if not r["ok"] or r["trace"] != trace or r["connected"] != connected or r["timer_active"] != timer:
            bad.append(("lifecycle", (kind, seq), trace, r))
    stats["lifecycle_sequences"] = len(seqs)

    print(json.dumps(stats))
    for what, case, py, rs in bad[:5]:
        print(f"DIFFERENCE {what}: case={str(case)[:160]}\n   app ={str(py)[:200]}\n   rust={str(rs)[:200]}")
    print("RELAY PARITY OK" if not bad else f"RELAY PARITY: {len(bad)} difference(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
