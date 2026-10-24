#!/usr/bin/env python3
"""Stage D4a parity: does the Rust ``singws-wire`` reproduce, byte for byte, what ``requests`` would send?  No network is used anywhere.

The oracle is ``requests.Request(method, url, params=, data=, json=).prepare()`` (the exact preparation ``requests.get/post`` perform before a
socket is opened). For each case the prepared URL, body bytes, ``Content-Type`` and ``Content-Length`` must equal the Rust crate's. Cases Rust
declines (a URL ``requests`` would re-quote, a nested object in a form value ...) are counted separately and must not be cases ``requests``
handles in a way Rust claims to reproduce.

Usage:  python3 tools/wire_parity.py [--cases N] [--seed S]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import struct
import subprocess
import sys
from collections import Counter

import requests

CLI_DEFAULT = os.path.expanduser("~/.cache/singws-rust-target/release/singws-wire-cli")

ENDPOINTS = ["delete_remote_request.php", "complete_remote_request.php", "set_remote_request_order.php", "get_requests_v2.php",
             "host_chat.php", "upsert_host_request.php", "singer_history_sync.php", "set_accepting.php", "snapshot.php"]
HOSTS = ["https://beta.wskar.com", "https://wskar.com", "HTTPS://Beta.WSKAR.com", "http://localhost:8080", "https://192.168.1.20:8443",
         "https://example.test"]
WORDS = ["", "plain", "a b", "é", "日本語", "😀", "a&b=c", "100%", "x+y", "tab\t", "nl\n", "quote\"'", "slash/back\\", "~tilde", "_-.", "?#[]@!$()*,;",
         "Straße", "O'Brien", "ünï", "​", "a" * 120, "\x00\x1f\x7f"]


def oracle(case):
    """requests' own preparation of the case; returns the comparable fields, or an error string if requests itself refuses."""
    try:
        req = requests.Request(case["method"], case["url"], params=case.get("params"), data=case.get("data"), json=case.get("json"))
        p = req.prepare()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}
    body = p.body
    if isinstance(body, str):
        body = body.encode("utf-8")
    return {"ok": True, "method": p.method, "url": p.url, "body": None if body is None else body.decode("utf-8"),
            "content_type": p.headers.get("Content-Type"), "content_length": None if p.headers.get("Content-Length") is None else int(p.headers["Content-Length"])}


def rust_batch(cli, cases):
    out = subprocess.run([cli], input=json.dumps(cases), capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(f"cli failed: {out.stderr[:200]}")
    return json.loads(out.stdout)


def rand_float(rng):
    while True:
        f = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if math.isfinite(f):
            return f


def rand_scalar(rng):
    k = rng.random()
    if k < 0.45:
        return rng.choice(WORDS) + rng.choice(["", "", "x", "7"])
    if k < 0.6:
        return rng.choice([0, 1, -1, 7, 1792783785132, 2**53 + 1, -(2**63), 2**63 - 1, 2**64 - 1, rng.randint(-10**6, 10**6)])
    if k < 0.75:
        return rand_float(rng) if rng.random() < 0.5 else rng.choice([0.1, 1.0, 1e16, 1e-5, 0.30000000000000004, -0.0, 2.5])
    if k < 0.85:
        return rng.random() < 0.5
    return None


def rand_value(rng):
    if rng.random() < 0.8:
        return rand_scalar(rng)
    return [rand_scalar(rng) if rng.random() < 0.85 else [rand_scalar(rng), rand_scalar(rng)] for _ in range(rng.randint(0, 4))]


def rand_mapping(rng, max_items=6):
    return {rng.choice(WORDS) + str(rng.randint(0, 99)) if rng.random() < 0.7 else f"k{i}": rand_value(rng) for i in range(rng.randint(0, max_items))}


def rand_json(rng, depth=0):
    k = rng.random()
    if depth > 2 or k < 0.5:
        return rand_scalar(rng) if rng.random() < 0.9 else rng.choice([[], {}])
    if k < 0.75:
        return [rand_json(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {rng.choice(WORDS): rand_json(rng, depth + 1) for _ in range(rng.randint(0, 4))}


def make_cases(n, seed):
    rng = random.Random(seed)
    cases = []
    for _ in range(n):
        url = rng.choice(HOSTS) + "/api/v1/" + rng.choice(ENDPOINTS)
        if rng.random() < 0.2:
            url += "?tenant=" + rng.choice(["1", "dan", "a-b_c"])
        kind = rng.choice(["form", "form", "get", "json", "json_and_form", "empty_post"])
        case = {"method": "POST", "url": url}
        if kind == "form":
            case["data"] = rand_mapping(rng)
            if rng.random() < 0.3:
                case["params"] = rand_mapping(rng, 3)
        elif kind == "get":
            case["method"] = rng.choice(["GET", "get"])
            case["params"] = rand_mapping(rng, 5)
        elif kind == "json":
            case["json"] = rand_json(rng)
        elif kind == "json_and_form":
            case["json"] = {"ignored": 1}
            case["data"] = rand_mapping(rng, 3)
        else:
            case["data"] = rng.choice([{}, None])
        cases.append(case)
    return cases


def compare(cases, results):
    stats, bad = Counter(), []
    for case, rs in zip(cases, results):
        py = oracle(case)
        if not rs.get("ok"):
            stats["rust_declined"] += 1
            if not py["ok"]:
                stats["both_refused"] += 1
            continue
        if not py["ok"]:
            bad.append((case, py, rs, "requests refused but Rust produced a request"))
            continue
        stats["compared"] += 1
        keys = ("method", "url", "body", "content_type", "content_length")
        if any(py[k] != rs[k] for k in keys):
            bad.append((case, {k: py[k] for k in keys}, {k: rs[k] for k in keys}, "differs"))
    return stats, bad


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--cli", default=os.environ.get("SINGWS_WIRE_CLI") or CLI_DEFAULT)
    args = parser.parse_args(argv)
    if not os.path.exists(args.cli):
        print(f"missing {args.cli}: cargo build --release -p singws-wire (in rust/)", file=sys.stderr)
        return 2
    cases = make_cases(args.cases, args.seed)
    stats, bad = compare(cases, rust_batch(args.cli, cases))
    print(json.dumps(dict(sorted(stats.items()))))
    for case, py, rs, why in bad[:5]:
        print(f"DIFFERENCE ({why}): case={json.dumps(case)[:200]}\n   requests={json.dumps(py)[:300]}\n   rust    ={json.dumps(rs)[:300]}")
    print("WIRE PARITY OK" if not bad else f"WIRE PARITY: {len(bad)} difference(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
