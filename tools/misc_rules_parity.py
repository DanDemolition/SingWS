#!/usr/bin/env python3
"""Parity: small pure rules of the app (the methods are read from 0.2.18.1.py by AST and run unchanged) vs the Rust `singws-misc`: `_local_audio_output_priority`,
`_queue_entry_artist_title_for_tombstone`, `_next_active_entry_after`, `_remote_request_timestamp`. Spellings of times the Rust side does not model (naive times, week dates, ...) are
declined and counted.

  CARGO_TARGET_DIR=... python3 tools/misc_rules_parity.py [--seeds N]
"""
import argparse
import ast
import json
import os
import random
import subprocess
import sys
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-karafun")) + "/release/singws-misc"
WANTED = ("_resolve_karaoke_audio_path", "_track_from_search_row", "_find_mp3_pair_for_cdg", "_local_audio_output_priority", "_queue_entry_artist_title_for_tombstone", "_next_active_entry_after", "_remote_request_timestamp")


def load():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    ns = {"datetime": datetime, "os": os, "Path": Path}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in WANTED:
                    sub.decorator_list = []
                    exec(compile(ast.Module([sub], []), "app", "exec"), ns)
    assert all(w in ns for w in WANTED)
    return ns


NAMES = ["External Headphones", "MacBook Pro Speakers", "Built-in Output", "Apple TV 4K", "Sony BRAVIA", "HDMI Out", "USB Audio", "headset mic", "Internal Speakers", "AirPlay", "Television", "", None, "Display Audio", "LG Monitor", "BUILT IN"]
KINDS = ["display", "speaker", "headphones", "", None, "Display"]
TS = [1700000000, 1700000000.5, 1e9, 1e9 + 1, 999999999, 5, 0, -1, True, False, None, "", "  ", "1700000000", " 1700000000.25 ", "1e12", "abc", "inf", "-inf", "nan", "NaN", "2026-11-08T21:30:00Z", "2026-11-08T21:30:00+00:00",
      "2026-11-08 21:30:00-08:00", "2026-11-08T21:30:00.123456+05:30", "2026-11-08T21:30+01:00", "2026-02-30T10:00:00Z", "2026-13-01T00:00:00Z", "2024-02-29T00:00:00Z", "2023-02-29T00:00:00Z",
      "2026-11-08T25:00:00Z", "2026-11-08T21:30:00", "2026-11-08", "20261108T213000Z", "2026-11-08T21:30:00.5Z", "0000-01-01T00:00:00Z", "0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59Z",
      "2026-11-08T21:30:00+24:00", "yesterday", "1_000", "٣", [], {}, "2026-11-08T21:30:00Z2", "2026-11-08T21:30:00.1234567Z"]
KEYS = ["received_at_server", "server_received_at", "waitlisted_at", "attention_at", "requested_at_client", "requested_at", "created_at", "submitted_at", "received_at", "request_time", "ts", "time", "other"]


PATHS = ["/m/Song.cdg", "/m/song.CDG", "/m/Song.zip", "/m/Song.ZIP", "/m/Song.mp3", "/m/Song", "/m/.cdg", "/m/a.b.cdg", "/m/dir.d/Song.cdg", "..cdg", "/m/x.cdg.", "", "Song.cdg", "/m/Zoë.cdg", "a/./b.cdg", "/m/Song.cdg.bak"]


def case(r):
    kind = r.choice(["audio", "tombstone", "next", "ts", "ts", "audio_path", "audio_path", "track_row", "mp3"])
    if kind == "audio_path":
        info = r.choice([r.choice(PATHS), [r.choice(PATHS)], [r.choice(PATHS), r.choice(PATHS)], [], {"song_info": r.choice(PATHS)}, {"song_info": [r.choice(PATHS), "/a/b.mp3"]}, {"song_info": None}, {}, None, "", False, 0])
        return {"op": "audio_path", "song_info": info}
    if kind == "track_row":
        row = {}
        for k in ("artist", "title", "discid", "disc_id", "disc", "duration_secs", "duration", "duration_estimated", "duration_source", "path", "song_type", "type", "display", "songid", "song_id", "id", "provider",
                  "provider_track_id", "provider_url", "local_reference_path", "authorization_requirement", "availability_status", "karafun_catalog_only"):
            if r.random() < 0.3:
                row[k] = r.choice(["x", "", 0, 5, 2.5, None, True, False, [], [1], {}, {"a": 1}, "KV 123", 263])
        return {"op": "track_row", "row": r.choice([row, row, row, None, [], "x"])}
    if kind == "mp3":
        return {"op": "mp3_pair", "variant": r.randrange(1 << 30)}
    if kind == "audio":
        return {"op": "audio_priority", "name": r.choice(NAMES), "kind": r.choice(KINDS)}
    if kind == "tombstone":
        e = {}
        for k in ("artist", "title", "display_name"):
            if r.random() < 0.6:
                e[k] = r.choice(["", "  ", "Sugarcult", "Memory", "A • B", "A - B - C", "no sep", " x • y ", "•", "-", None, "Zoë • Café", "a•b-c"])
        return {"op": "tombstone", "entry": r.choice([e, e, e, None, "x", []])}
    if kind == "next":
        n = r.randrange(0, 6)
        # songs are distinct objects in the app: the rule finds the entry by identity, so equal-looking shared objects (None, an interned string) are not generated
        songs = [r.choice([{"skipped": True}, {"skipped": False}, {}, {"skipped": 1}, {"skipped": 0, "title": "x"}, "".join(["s", str(i)])]) for i in range(n)]
        return {"op": "next_active", "songs": songs, "index": r.choice([None, *range(n)] if n else [None])}
    req = {k: r.choice(TS) for k in KEYS if r.random() < 0.25}
    return {"op": "request_ts", "req": r.choice([req, req, req, None, {}])}


def build_dir(variant):
    r = random.Random(variant)
    d = tempfile.mkdtemp(prefix="mp3pair_")
    stem = r.choice(["Song", "song", "SONG", "A B", "x.y"])
    for name in r.sample(["Song.mp3", "song.MP3", "SONG.Mp3", "Song.mp3.bak", "other.mp3", "Song.cdg", "song.cdg", "A B.MP3", "x.y.mp3", "song.mp4", "Song.zip"], r.randrange(0, 7)):
        open(os.path.join(d, name), "wb").write(b"x")
    if r.random() < 0.3 and not os.path.exists(os.path.join(d, "song.mp3")):
        os.makedirs(os.path.join(d, "song.mp3"))
    return d, os.path.join(d, stem + r.choice([".cdg", ".CDG", ".Cdg"]))


def run_python(ns, c):
    op = c["op"]
    if op == "audio_path":
        return ns["_resolve_karaoke_audio_path"](None, c["song_info"])
    if op == "track_row":
        return ns["_track_from_search_row"](None, c["row"])
    if op == "mp3_pair":
        d, path = build_dir(c["variant"])
        try:
            c["path"] = path
            return ns["_find_mp3_pair_for_cdg"](None, path).replace(d, "<D>")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    if op == "audio_priority":
        item = {"name": c["name"], "kind": c["kind"]}
        return list(ns["_local_audio_output_priority"](item))
    if op == "tombstone":
        return list(ns["_queue_entry_artist_title_for_tombstone"](None, c["entry"]))
    if op == "next_active":
        songs = c["songs"]
        entry = songs[c["index"]] if c["index"] is not None else object()
        return ns["_next_active_entry_after"](None, {"songs": songs}, entry)   # the song itself (None when there is none, or when the song is None)
    v = ns["_remote_request_timestamp"](None, c["req"])
    return None if v in (float("inf"), float("-inf")) else v          # JSON has no infinity: the Rust side answers null


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=6000)
    a = ap.parse_args(argv)
    ns = load()
    cases = [case(random.Random(i)) for i in range(a.seeds)]
    want = []
    for c in cases:
        try:
            want.append(run_python(ns, c))
        except Exception as exc:  # noqa: BLE001
            want.append(("raised", str(exc)[:40]))
    dirs = {}
    for c in cases:
        if c["op"] == "mp3_pair":
            dirs[id(c)] = build_dir(c["variant"])          # rebuilt for the Rust side (the Python side removed its copy)
            c["path"] = dirs[id(c)][1]
    out = subprocess.run([CLI], input=json.dumps({"batch": cases}), capture_output=True, text=True)
    for d, _ in dirs.values():
        shutil.rmtree(d, ignore_errors=True)
    if out.returncode != 0:
        raise SystemExit("cli failed: " + out.stderr[:300])
    got = json.loads(out.stdout)
    diffs, declined, raised, kinds = [], 0, 0, {}
    for c, w, g in zip(cases, want, got):
        if isinstance(w, tuple):
            raised += 1
            continue
        if "declined" in g:
            declined += 1
            continue
        kinds[c["op"]] = kinds.get(c["op"], 0) + 1
        mine = g["v"]
        if c["op"] == "mp3_pair":
            mine = mine.replace(os.path.dirname(c["path"]), "<D>")
        if c["op"] == "next_active":
            mine = None if mine is None else c["songs"][mine]
        if json.loads(json.dumps(w)) != mine:
            diffs.append((c, w, g["v"]))
    print(f"cases={len(cases)} declined={declined} python-raised={raised} compared: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    for c, w, g in diffs[:5]:
        print(f" - {json.dumps(c)[:260]}\n   py={json.dumps(w)[:200]}\n   rs={json.dumps(g)[:200]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
