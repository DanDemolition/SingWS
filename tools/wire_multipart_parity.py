#!/usr/bin/env python3
"""Parity: multipart/form-data requests (the app's uploads: songbook CSV, support logs, chat pictures) -- the Rust `singws-wire` against `requests.Request(..., data=, files=).prepare()`
with urllib3's boundary fixed so the bytes are comparable. Compared: URL, body bytes, Content-Type and Content-Length, exactly. Names or file names urllib3 would escape are declined.

  python3 tools/wire_multipart_parity.py [--cases N] [--cli PATH]
"""
import argparse
import json
import os
import random
import subprocess
import sys

import requests
import urllib3.filepost

CLI = os.path.expanduser("~/.cache/singws-rust-target/release/singws-wire-cli")
BOUNDARY = "SingWSBoundary0123456789abcdef"
FIELDS = ["user", "action", "logs", "image", "songbook_csv", "label", "a-b", "x_y", "A1"]
FILENAMES = ["songbook.csv", "singws_logs.zip", "photo.png", "a b.txt", "x", "noext", "UPPER.CSV", "dots.in.name.zip"]
TYPES = ["text/csv", "application/zip", "image/png", "application/octet-stream", None]
TEXTS = ["wsk", "upload", "a b", "é", "日本語", "line\nbreak", "100%", "7", "", "x" * 300]
BAD_NAMES = ['q"uote', "back\\slash", "new\nline", "é.csv", "日本.zip"]


def content(r):
    kind = r.randrange(5)
    if kind == 0:
        return b""
    if kind == 1:
        return bytes(r.randrange(256) for _ in range(r.randrange(1, 300)))
    if kind == 2:
        return b"a,b,c\r\n1,2,3\r\n" * r.randrange(1, 20)
    if kind == 3:
        return ("--" + BOUNDARY + "\r\n").encode() + b"fake part"                     # looks like a boundary line
    return "Zoë ünï 日本語\n".encode() * r.randrange(1, 5)


def scalar(r):
    return r.choice([r.choice(TEXTS), r.randrange(-5, 100000), r.random() < 0.5, 2.5, 0.1, None, 10**18])


def case(r):
    data = {}
    for _ in range(r.randrange(0, 4)):
        v = scalar(r)
        if r.random() < 0.2:
            v = [scalar(r) for _ in range(r.randrange(0, 3))]
        data[r.choice(FIELDS)] = v
    files = {}
    for _ in range(r.randrange(1, 3)):
        name = r.choice(BAD_NAMES) if r.random() < 0.05 else r.choice(FIELDS)
        fname = r.choice(BAD_NAMES) if r.random() < 0.05 else r.choice(FILENAMES)
        files[name] = {"filename": fname, "content": content(r), "content_type": r.choice(TYPES)}
    return {"method": r.choice(["POST", "post", "PUT"]), "url": r.choice(["https://wskar.com/api/v1/support_logs.php", "https://beta.wskar.com/chat_media.php"]),
            "params": r.choice([None, None, {"user": "wsk"}, {"a": "b c", "n": 3}]), "data": data, "files": files}


def oracle(c):
    urllib3.filepost.choose_boundary = lambda: BOUNDARY
    files = {}
    for k, f in c["files"].items():
        files[k] = (f["filename"], f["content"], f["content_type"]) if f["content_type"] else (f["filename"], f["content"])
    p = requests.Request(c["method"], c["url"], params=c["params"], data=c["data"], files=files).prepare()
    return {"url": p.url, "body": p.body, "content_type": p.headers["Content-Type"], "content_length": int(p.headers["Content-Length"])}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--cli", default=CLI)
    a = ap.parse_args(argv)
    cases = [case(random.Random(i)) for i in range(a.cases)]
    wire = []
    for c in cases:
        wire.append({"method": c["method"], "url": c["url"], "params": c["params"], "data": c["data"], "boundary": BOUNDARY,
                     "files": {k: ({"filename": f["filename"], "content_hex": f["content"].hex(), "content_type": f["content_type"]} if f["content_type"] else {"filename": f["filename"], "content_hex": f["content"].hex()}) for k, f in c["files"].items()}})
    out = subprocess.run([a.cli], input=json.dumps(wire), capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise SystemExit("cli failed: " + out.stderr[:300])
    got = json.loads(out.stdout)
    diffs, declined, compared = [], 0, 0
    for i, (c, g) in enumerate(zip(cases, got)):
        if not g["ok"]:
            declined += 1
            continue
        want = oracle(c)
        compared += 1
        mine = {"url": g["url"], "body": bytes.fromhex(g["body_hex"]), "content_type": g["content_type"], "content_length": g["content_length"]}
        if want != mine:
            diffs.append((i, want, mine))
    print(f"cases={len(cases)} compared={compared} declined={declined}")
    for i, want, mine in diffs[:3]:
        print(f" - case {i}\n   py={str(want)[:400]}\n   rs={str(mine)[:400]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
