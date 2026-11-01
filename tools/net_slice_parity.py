#!/usr/bin/env python3
"""Parity of the Rust network slices (rust/crates/singws-netslice) against the app's REAL Python.

Oracle = the app module itself. Slice 1: the REAL `_probe_network_sync_status_impl` (the body of `probe_network_sync_status`) runs against a scripted fake
`requests` module (no socket is ever opened; no real server is contacted) that is wrapped by the PRODUCTION recorder (`net_slice_shadow.RecordingRequests`);
the helper's request and the comparison are the PRODUCTION `probe_batch` / `compare` (key order, key set and value TYPES). The Rust side must reproduce both the
result dict and the list of requests (url, params, headers, timeout) Python made. Corpus: handwritten cases + seeded random scenarios (--cases N).
Needs the CLI:  cd rust && CARGO_TARGET_DIR=~/.cache/singws-rust-target-netslice cargo build --release -p singws-netslice
  ~/Documents/SingWS/.venv/bin/python tools/net_slice_parity.py [--cases N] [--seed S]       exit 1 on any difference
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="net_slice_parity_home_")
sys.path.insert(0, str(ROOT))
CLI = os.environ.get("SINGWS_NETSLICE_BIN") or os.path.join(
    os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-netslice")), "release", "singws-netslice")
os.environ["SINGWS_NETSLICE_BIN"] = CLI

failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 30:
        print("DIFF:", msg[:600])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def load_app():
    spec = importlib.util.spec_from_file_location("singws_main_netslice_parity", str(ROOT / "0.2.18.1.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


APP = load_app()
import net_slice_shadow as shadow  # noqa: E402

EX = APP.requests.exceptions


class FakeResponse:
    def __init__(self, status, text, json_ok, json_msg):
        self.status_code, self.text, self._ok, self._msg = status, text, json_ok, json_msg

    def json(self):
        if not self._ok:
            raise ValueError(self._msg)
        return {}


class FakeRequests:
    """Answers each `get` from a script keyed by which endpoint the URL is; never touches the network."""
    exceptions = EX

    def __init__(self, script):
        self.script = script

    def get(self, url, params=None, headers=None, timeout=None):
        for name in ("get_requests.php", "accepting.txt", "host_commands.php", "singer_history_sync.php", "post_rotation.php"):
            if name in url:
                out = self.script[name]
                if isinstance(out, BaseException):
                    raise out
                return out
        raise AssertionError("unexpected url")


BODIES = ["", "0", "1", " 1\n", "1\r\n", "01", "2", "{}", "<html>it's \"down\"</html>", "x" * 400, "tab\there", "ctl\x01\x7f", "back\\slash", "q'q", 'q"q', "'\"", "aé"]
STATUSES = [200, 200, 200, 201, 204, 301, 400, 401, 403, 404, 405, 500, 502, None]
JSON_MSGS = ["Expecting value: line 1 column 1 (char 0)", "x", "", "bad 'quote' \"d\""]
EXC = [lambda: EX.Timeout("slow"), lambda: EX.ConnectTimeout("ct"), lambda: EX.ReadTimeout("rt"), lambda: EX.ConnectionError("refused"),
       lambda: ValueError("bad value"), lambda: RuntimeError(""), lambda: OSError("os trouble"), lambda: EX.SSLError("tls")]
BASES = ["https://example.test", "example.test", " example.test/ ", "http://x.test///", "", "   ", "ftp://x.test", "https://wskar.com", "HTTPS://Mixed.Test/path/", "beta.wskar.com/"]
TENANTS = ["wsk", " wsk ", "", "  ", "a b", "x\ty"]
KEYS = ["", "k", " k ", "  ", "sec ret"]
TIMEOUTS = [0, 0.3, 1, 3.0, 3, 4.5, 6.0, 10.5, 25]


def scenario(rng):
    def outcome():
        if rng.random() < 0.18:
            return rng.choice(EXC)()
        return FakeResponse(rng.choice(STATUSES), rng.choice(BODIES), rng.random() < 0.6, rng.choice(JSON_MSGS))
    script = {n: outcome() for n in ("get_requests.php", "accepting.txt", "host_commands.php", "singer_history_sync.php", "post_rotation.php")}
    if rng.random() < 0.4:  # a healthy server
        script["get_requests.php"] = FakeResponse(200, "{}", True, "")
        script["accepting.txt"] = FakeResponse(200, rng.choice(["0", "1", " 1\n"]), False, "")
        for n in ("host_commands.php", "singer_history_sync.php", "post_rotation.php"):
            script[n] = FakeResponse(rng.choice([200, 400, 405]), "", False, "")
    return (rng.choice(BASES), rng.choice(TENANTS), rng.choice(KEYS), rng.choice(TIMEOUTS)), script


def one(args, script, label):
    base, tenant, key, timeout = args
    rec = shadow.RecordingRequests(FakeRequests(script))
    result = APP._probe_network_sync_status_impl(base, tenant, key, timeout_sec=timeout, requests_module=rec)
    batch = shadow.probe_batch(base, tenant, key, timeout, rec.calls, result)
    if batch is None:
        tally("no_batch")
        fail(f"{label}: probe_batch declined {args!r}")
        return
    name, req, expected = batch
    answer = shadow.run_helper(Path(CLI), [req])[0]
    if "unsupported" in answer:
        tally("unsupported")
        ascii_only = all(s.isascii() for s in json.dumps(req, ensure_ascii=False))
        if ascii_only:
            fail(f"{label}: unsupported on an ASCII case: {answer['unsupported']}")
        return
    d = shadow.compare(name, expected, answer)
    tally("compared")
    tally("connected" if result["ok"] else "failed")
    if result["partial"]:
        tally("partial")
    if d:
        fail(f"{label}: {d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    if not os.path.exists(CLI):
        print("CLI not built:", CLI, file=sys.stderr)
        return 2
    rng = random.Random(a.seed)
    # a healthy server, with and without a key, deterministic
    ok = {"get_requests.php": FakeResponse(200, "{}", True, ""), "accepting.txt": FakeResponse(200, "1", False, ""),
          "host_commands.php": FakeResponse(200, "", False, ""), "singer_history_sync.php": FakeResponse(405, "", False, ""), "post_rotation.php": FakeResponse(200, "", False, "")}
    for key in ("", "k"):
        one(("https://example.test", "wsk", key, 6.0), ok, "handwritten healthy")
    for i in range(a.cases):
        args, script = scenario(rng)
        one(args, script, f"case {i} seed {a.seed}")
    print("counts:", counts)
    print("IDENTICAL" if not failures else f"{len(failures)} DIFFERENCES")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
