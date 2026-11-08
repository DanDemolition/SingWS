#!/usr/bin/env python3
"""Parity: `audit_zip_duplicates` (duplicate_song_audit.py, the app's own module) vs the Rust `singws-scan` op `dupes` on libraries of real temp ZIPs: shared and distinct MP3 / CDG
contents, copy-named duplicates, the same audio with different graphics, a missing CDG, two MP3s, no MP3, directory entries, upper-case extensions, stored and deflated members,
damaged archives (a flipped byte inside a stored member: the central directory reads fine and the CRC fails), files that are not ZIPs. Compared: the whole result, exactly.

  CARGO_TARGET_DIR=... python3 tools/dupes_parity.py [--cases N]
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import duplicate_song_audit as dsa  # noqa: E402

CLI = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-scan")) + "/release/singws-scan-cli"
NAMES = ["Song", "Song copy", "Song (1)", "Song_1", "song duplicate", "Song dup", "SONG", "Other Song", "a", "B", "Track 02", "x.y", "Zed"]
AUDIO = [b"A" * 3000, b"B" * 3000, b"audio-three" * 300, bytes(range(256)) * 20]
GFX = [b"G" * 2400, b"H" * 2400, b"\x09\x01" * 1200]


def build(r, d, i):
    kind = r.choices(["pair", "one_mp3_cdg", "no_cdg", "two_mp3", "no_mp3", "dirs", "stored", "bad", "damaged", "upper"], [6, 4, 2, 1, 1, 1, 2, 1, 1, 1])[0]
    name = r.choice(NAMES) + r.choice(["", "", " 2", "-x"]) + r.choice([".zip", ".zip", ".ZIP", ".Zip"])
    path = os.path.join(d, r.choice(["", "sub1", "sub2"]), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        path = os.path.join(d, f"u{i}_{name}")
    audio, gfx = r.choice(AUDIO), r.choice(GFX)
    if kind == "bad":
        Path(path).write_bytes(b"not a zip at all")
        return path
    method = zipfile.ZIP_STORED if kind in ("stored", "damaged") else r.choice([zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED])
    with zipfile.ZipFile(path, "w", method) as z:
        if kind == "dirs":
            z.writestr("folder/", b"")
        if kind == "two_mp3":
            z.writestr("a.mp3", audio)
            z.writestr("b.mp3", audio)
        elif kind != "no_mp3":
            z.writestr(("A.MP3" if kind == "upper" else "t.mp3"), audio)
        if kind not in ("no_cdg",):
            z.writestr(("A.CDG" if kind == "upper" else "t.cdg"), gfx)
    if kind == "damaged":
        data = bytearray(Path(path).read_bytes())
        pos = data.find(audio[:50]) if audio[:50] in data else -1
        if pos > 0:
            data[pos + 10] ^= 0xFF
            Path(path).write_bytes(bytes(data))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=120)
    a = ap.parse_args(argv)
    diffs, shapes = [], {}
    for case in range(a.cases):
        r = random.Random(case)
        d = os.path.realpath(tempfile.mkdtemp(prefix="dupes_"))
        try:
            paths = [build(r, d, i) for i in range(r.randrange(0, 14))]
            extra = r.choice([[], [os.path.join(d, "note.txt")], [paths[0]] if paths else []])
            allpaths = paths + extra
            want = dsa.audit_zip_duplicates(allpaths)
            resp = json.loads(subprocess.run([CLI], input=json.dumps({"op": "dupes", "paths": allpaths}), capture_output=True, text=True).stdout)
            if not resp.get("ok"):
                shapes["declined"] = shapes.get("declined", 0) + 1
                continue
            got = resp["result"]
            kinds = tuple(sorted({g["kind"] for g in want["groups"]}))
            shapes[kinds] = shapes.get(kinds, 0) + 1
            if want["unreadable"]:
                shapes["with unreadable"] = shapes.get("with unreadable", 0) + 1
            if json.loads(json.dumps(want)) != got:
                diffs.append((case, want, got))
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print(f"cases={a.cases} " + ", ".join(f"{k}={v}" for k, v in sorted(shapes.items(), key=str)))
    for case, want, got in diffs[:3]:
        print(f" - case {case}\n   py={json.dumps(want)[:400]}\n   rs={json.dumps(got)[:400]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
