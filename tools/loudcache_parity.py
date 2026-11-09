#!/usr/bin/env python3
"""Parity: the loudness cache rules of 0.2.18.1.py (`_loudness_gain_from_lufs`, `loudness_info_cached`, `loudness_gain_db_cached`, `loudness_failed_cached`, `_loudness_mark_failed`,
`_loudness_load_cache`), read by AST and run unchanged on real temp files, vs the Rust `singws-loudcache`. Compared: every answer, exactly. A shape Python raises on (int(None)) or the
Rust port declines (numbers given as text, NaN literals) is counted and skipped.

  CARGO_TARGET_DIR=... python3 tools/loudcache_parity.py [--seeds N]
"""
import argparse
import ast
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-karafun"))
CLI = TARGET + "/release/singws-loudcache"
WANTED = {"_loudness_gain_from_lufs", "loudness_info_cached", "loudness_gain_db_cached", "loudness_failed_cached", "_loudness_mark_failed", "_loudness_load_cache", "_loudness_file_sig"}
CONSTS = {"LOUDNESS_TARGET_LUFS", "LOUDNESS_MAX_GAIN_DB", "LOUDNESS_MIN_GAIN_DB", "LOUDNESS_TRUE_PEAK_HEADROOM_DB", "_LOUDNESS_FAILURE_CACHE_VERSION"}


def load(work):
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    ns = {"json": json, "os": os, "time": time, "zipfile": zipfile, "math": math, "_loudness_cache": {}, "_loudness_cache_loaded": False, "_loudness_lock": threading.Lock(),
          "LOUDNESS_CACHE_PATH": Path(work) / "loudness.json", "LOUDNESS_CHECKPOINT_PATH": Path(work) / "loudness.checkpoint.jsonl", "_diag": lambda m: None,
          "_loudness_append_checkpoint": lambda path, entry: None}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in WANTED:
            exec(compile(ast.Module([node], []), "app", "exec"), ns)
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in CONSTS:
            exec(compile(ast.Module([node], []), "app", "exec"), ns)
    missing = (WANTED | CONSTS) - set(ns)
    assert not missing, f"moved: {missing}"
    return ns


def rust(batch):
    out = subprocess.run([CLI], input=json.dumps({"batch": batch}), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("cli failed: " + out.stderr[:300])
    return json.loads(out.stdout)


NUMS = [0, 1, -1, 5, 1_700_000_000, 1.5, 2.9, -0.5, True, False, None, [], {}, 1e30]
LUFS = [-30.0, -16.0, -14.2, -8.0, 0.0, -50.0, -2.25, 7.5, -100.0, 12.0, -34.0, -34.01]
PEAKS = [None, -20.0, -3.0, -1.0, 0.0, 0.3, 1.6, -0.5, 5.0, -30.0]


def entry_for(r, sig):
    e = {}
    if r.random() < 0.93:
        e["i"] = r.choice(LUFS + [r.uniform(-60, 10), None, True, [], {}])
    if r.random() < 0.6:
        e["peak_db"] = r.choice(PEAKS + [None, True, [], {}])
    if r.random() < 0.6:
        e["mode"] = r.choice(["fast", "full", "other", None, 1])
    for k, v in (("mtime", sig[0] if sig else 5), ("size", sig[1] if sig else 5)):
        x = r.random()
        if x < 0.7:
            e[k] = v
        elif x < 0.85:
            e[k] = v + r.choice([1, -1, 100])
        elif x < 0.93:
            e[k] = float(v) + r.choice([0.0, 0.7])
        elif x < 0.97:
            pass
        else:
            e[k] = r.choice(NUMS)
    return e


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=4000)
    a = ap.parse_args(argv)
    work = tempfile.mkdtemp(prefix="loudcache_")
    ns = load(work)
    media = os.path.join(work, "song.mp3")
    Path(media).write_bytes(b"x" * 321)
    os.utime(media, (1_700_000_123, 1_700_000_123))
    real_sig = (1_700_000_123, 321)
    media0 = os.path.join(work, "empty.mp3")
    Path(media0).write_bytes(b"")
    os.utime(media0, (0, 0))
    zips = {}
    for name, members in (("one", ["a.mp3"]), ("two", ["a.mp3", "b.mp3"]), ("none", ["a.cdg"]), ("dirmp3", ["d.mp3/"]), ("upper", ["A.MP3"])):
        p = os.path.join(work, f"{name}.zip")
        with zipfile.ZipFile(p, "w") as z:
            for m in members:
                z.writestr(m, b"" if m.endswith("/") else b"data")
        zips[name] = p
    Path(os.path.join(work, "bad.zip")).write_bytes(b"not a zip")
    zips["bad"] = os.path.join(work, "bad.zip")
    ops, cases = [], []
    for seed in range(a.seeds):
        r = random.Random(seed)
        kind = r.choice(["gain"] * 2 + ["info"] * 3 + ["gain_db"] * 2 + ["failed"] * 4 + ["entry"] + ["replay"] * 3)
        if kind == "gain":
            lufs, peak = r.choice(LUFS + [r.uniform(-70, 20)]), r.choice(PEAKS)
            ns_out = ns["_loudness_gain_from_lufs"](lufs, peak)
            ops.append({"op": "gain", "lufs": lufs, "peak": peak})
            cases.append((kind, seed, ns_out))
        elif kind in ("info", "gain_db"):
            has_file = r.random() < 0.8
            zero = has_file and r.random() < 0.25
            path = (media0 if zero else media) if has_file else os.path.join(work, "gone.mp3")
            sig = ((0, 0) if zero else real_sig) if has_file else None
            e = entry_for(r, sig or real_sig)
            ns["_loudness_cache"].clear()
            ns["_loudness_cache"][path] = e if r.random() < 0.97 else r.choice([None, [], "x"])
            ns["_loudness_cache_loaded"] = True
            try:
                out = ns["loudness_info_cached" if kind == "info" else "loudness_gain_db_cached"](path)
            except Exception as exc:  # noqa: BLE001
                out = ("raised", type(exc).__name__)
            ops.append({"op": kind, "entry": ns["_loudness_cache"][path], "sig": list(sig) if sig else None})
            cases.append((kind, seed, out))
        elif kind == "failed":
            zname = r.choice(list(zips) + [None, None])
            path = zips[zname] if zname else (media if r.random() < 0.6 else media0 if r.random() < 0.5 else os.path.join(work, "gone.mp3"))
            st = os.stat(path) if os.path.exists(path) else None
            sig = (int(st.st_mtime), int(st.st_size)) if st else None
            e = entry_for(r, sig or (1, 1))
            e["failed"] = r.choice([True, True, True, False, 1, 0, None, "x", [], {}, [1], {"a": 1}, 2.5, 0.0]) if r.random() < 0.9 else e.get("failed")
            e["failure_version"] = r.choice([3, 3, 2, 1, 0, 4, None, 2.5]) if r.random() < 0.85 else e.get("failure_version")
            e["reason"] = r.choice(["no measurable loudness", "Turbo helper failed: timeout", "ZIP does not contain exactly one readable MP3", "decode error", "", None, "turbo helper failed: x"])
            e = {k: v for k, v in e.items() if not (r.random() < 0.04)}
            ns["_loudness_cache"].clear()
            ns["_loudness_cache"][path] = e if r.random() < 0.95 else r.choice([None, [], "x", 5])
            e = ns["_loudness_cache"][path]
            ns["_loudness_cache_loaded"] = True
            try:
                out = ns["loudness_failed_cached"](path)
            except Exception as exc:  # noqa: BLE001
                out = ("raised", type(exc).__name__)
            single = None
            if path.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(path, "r") as arch:
                        single = len([i for i in arch.infolist() if not i.is_dir() and i.filename.lower().endswith(".mp3")]) == 1
                except Exception:
                    single = None
            ops.append({"op": "failed", "entry": e, "sig": list(sig) if sig else None, "zip": path.lower().endswith(".zip"), "single_mp3": single})
            cases.append((kind, seed, out))
        elif kind == "entry":
            ns["_loudness_cache"].clear()
            real_time = time.time
            ns["time"] = type("T", (), {"time": staticmethod(lambda: 1_700_000_999.7)})
            reason = r.choice(["", "boom", "x" * 250, "Zoë" * 100, "ZIP does not contain exactly one readable MP3"])
            ns["_loudness_mark_failed"](media, reason)
            ns["time"] = time
            ops.append({"op": "failure_entry", "sig": list(real_sig), "reason": reason, "now": 1_700_000_999})
            cases.append((kind, seed, ns["_loudness_cache"][media]))
        else:
            good = lambda: json.dumps({"path": r.choice(["/a.mp3", "/b.zip", "/c d.mp3", "Zoë.mp3", ""]), "entry": r.choice([{"i": -14.0, "mtime": 1, "size": 2}, {"failed": True}, {}, [], None, 5])})
            lines = []
            for _ in range(r.randrange(0, 8)):
                lines.append(r.choice([good(), good(), "{not json", "", "[1,2]", '{"path": 5, "entry": {"x":1}}', '{"path": true, "entry": {}}', '{"path": null, "entry": {"y":1}}', '{"entry": {}}', '{"path": "/e"}', '"str"']))
            sep = r.choice(["\n", "\r\n", "\r", "\x0b", "\x0c", "\x1c", "\x85", " ", "\n\n"])
            cp = sep.join(lines) + r.choice(["", sep])
            fl = sep.join(lines[:2]) if r.random() < 0.4 else None
            cache = r.choice([None, "", json.dumps({"/a.mp3": {"i": -20.0}, "/z": {"failed": True}}), "[]", "{bad", "   ", json.dumps({"/a.mp3": 5}), '{"/a.mp3": {"i": NaN}}', '{"/a.mp3": {"i": Infinity}}', '{"/a.mp3": {"i": -Infinity, "n": NaN}}'])
            cps = [fl, cp if r.random() < 0.8 else None]
            for p, t in ((ns["LOUDNESS_CACHE_PATH"], cache), (ns["LOUDNESS_CHECKPOINT_PATH"].with_suffix(".jsonl.flushing"), cps[0]), (ns["LOUDNESS_CHECKPOINT_PATH"], cps[1])):
                if p.exists():
                    p.unlink()
                if t is not None:
                    p.write_text(t, encoding="utf-8")
            ns["_loudness_cache"].clear()
            ns["_loudness_cache_loaded"] = False
            ns["_loudness_load_cache"]()
            ops.append({"op": "replay", "cache": cache, "checkpoints": cps})
            cases.append((kind, seed, dict(ns["_loudness_cache"])))
    got = rust(ops)
    diffs, declined, skipped, kinds = [], 0, 0, {}
    for (kind, seed, want), g, op in zip(cases, got, ops):
        if "declined" in g:
            declined += 1
            continue
        if isinstance(want, tuple) and want and want[0] == "raised":
            skipped += 1
            continue
        kinds[kind] = kinds.get(kind, 0) + 1
        v = g["v"]
        w = json.loads(json.dumps(want))
        if v != w:
            diffs.append((kind, seed, op, w, v))
    print(f"cases={len(cases)} declined={declined} python-raised={skipped} compared: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    for kind, seed, op, w, v in diffs[:4]:
        print(f" - {kind} seed {seed}: {json.dumps(op)[:200]}\n   py={json.dumps(w)[:200]}\n   rs={json.dumps(v)[:200]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
