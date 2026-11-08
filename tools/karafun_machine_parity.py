#!/usr/bin/env python3
"""Parity: the small decision methods between the KaraFun worker and the machine -- `_apply_verified_duration`, `_karafun_apple_events_preflight`,
`_karafun_target_audio_output_name` / `_ensure_karafun_audio_output` -- read from 0.2.18.1.py by AST and run unchanged against scripted inputs, vs the Rust `machine.rs`.
Compared: the result, the entry (for the duration), the process id (preflight) and every log line.

  python3 tools/karafun_machine_parity.py [--seeds N] [--binary PATH]
"""
import argparse
import ast
import json
import math
import os
import random
import re
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WANTED_METHODS = ("_apply_verified_duration", "_karafun_apple_events_preflight", "_karafun_target_audio_output_name", "_ensure_karafun_audio_output", "_normalized_audio_output_name")


def run_rust(binary, reqs):
    out = subprocess.run([binary], input=json.dumps({"batch": reqs}).encode(), capture_output=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode()[-500:])
    return json.loads(out.stdout)


def load():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    log = []
    ns = {"math": math, "re": re, "_diag": lambda m: log.append(m)}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "normalize_karafun_duration_seconds":
            exec(compile(ast.Module([node], []), "app", "exec"), ns)
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in WANTED_METHODS:
                    sub.decorator_list = []
                    exec(compile(ast.Module([sub], []), "app", "exec"), ns)
    for name in WANTED_METHODS:
        assert name in ns, f"{name} moved"
    return ns, log


def run_python(ns, log, sc):
    log.clear()
    kind = sc["kind"]
    if kind == "apply_duration":
        entry = json.loads(json.dumps(sc["entry"]))
        out = ns["_apply_verified_duration"](entry, sc["value"], source=sc["source"])
        return {"result": out, "entry": entry, "log": list(log)}
    me = types.SimpleNamespace()
    if kind == "preflight":
        me._run_karafun_applescript_sync = lambda lines, timeout=None: (sc["ok"], sc["stdout"], sc["stderr"])
        me._karafun_preflight_script = lambda: ["x"]
        res = ns["_karafun_apple_events_preflight"](me)
        return {"result": list(res), "pid": me._karafun_process_id if sc["ok"] else 0}
    settings = {"karafun_audio_output_name": sc["explicit"]}
    if "follow" in sc:
        settings["karafun_audio_output_follow_singws"] = sc["follow"]
    me.settings = settings
    me._safe_local_audio_output_name = lambda: sc["safe_local"]
    me._normalized_audio_output_name = ns["_normalized_audio_output_name"]
    me._karafun_target_audio_output_name = types.MethodType(ns["_karafun_target_audio_output_name"], me)
    res = ns["_ensure_karafun_audio_output"](me)
    return {"result": list(res), "log": list(log)}


DURATIONS = [None, True, False, 0, -5, 1, 90, 90.4, 90.5, 91.5, 263, 86400, 86401, 1e30, "0", "90", "90.5", " 1:30 ", "1:30", "1:60", "0:00", "1:2:3", "01:02:03", "abc", "", "-1", "9999999",
             "24:00:00", "٣", "1:٣0", "3:41", [], {}, "1:5"]
SOURCES = ["karafun_result", "karafun_result ", " karafun_result", "manual", "Manual override", "", None, "verified_metadata", "catalog", "x" * 5]
TITLES = ["Memory", "", None, "it's", "Zoë", 'say "hi"']
OUTPUT_NAMES = ["", "  ", "External Headphones", "USB Audio", "Apple TV", "AirPlay: Living Room", "Sony BRAVIA", "Television", "Display Audio", "MacBook Pro Speakers", "dis play", " Display ", None, "Zoë", "AIR-PLAY"]


def scenario(r):
    kind = r.choice(["apply_duration"] * 5 + ["preflight"] * 2 + ["audio"] * 3)
    if kind == "apply_duration":
        entry = {}
        if r.random() < 0.7:
            entry["duration"] = r.choice(DURATIONS)
        if r.random() < 0.5:
            entry["duration_estimated"] = r.choice([True, False, 1, 0, None, "x"])
        if r.random() < 0.5:
            entry["duration_source"] = r.choice(SOURCES)
        if r.random() < 0.6:
            entry["title"] = r.choice(TITLES)
        if r.random() < 0.05:
            entry = r.choice([None, [], "x", 5])
        return {"kind": kind, "entry": entry, "value": r.choice(DURATIONS), "source": r.choice(SOURCES)}
    if kind == "preflight":
        return {"kind": kind, "ok": r.random() < 0.7, "stdout": r.choice(["", "AUTHORIZED|123", "ok|0", "ok|abc", "x", "a|b|456", "AUTHORIZED| 7 ", "AUTH|+8", "AUTH|-9", "AUTH|1_0", "AUTH|٣", "|", "12"]),
                "stderr": r.choice(["", "not authorized", "error -1743", "Zoë"])}
    sc = {"kind": kind, "explicit": r.choice(OUTPUT_NAMES[:5] + [""] * 4), "safe_local": r.choice(OUTPUT_NAMES)}
    if r.random() < 0.7:
        sc["follow"] = r.choice([True, False, True, 1, 0, None, "", "x"])
    return sc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=6000)
    ap.add_argument("--binary", default=os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun"))
    args = ap.parse_args(argv)
    ns, log = load()
    cases = []
    for seed in range(args.seeds):
        sc = scenario(random.Random(seed))
        try:
            cases.append((seed, sc, run_python(ns, log, sc)))
        except Exception as exc:  # noqa: BLE001 - shapes Python itself rejects: not compared
            cases.append((seed, sc, {"raised": str(exc)[:60]}))
    reqs = [{"op": "machine", "scenario": sc} for _, sc, _ in cases]
    got = run_rust(args.binary, reqs)
    diffs, declined, raised, kinds = [], 0, 0, {}
    for (seed, sc, py), g in zip(cases, got):
        if "raised" in py:
            raised += 1
            continue
        if g.get("declined"):
            declined += 1
            continue
        kinds[sc["kind"]] = kinds.get(sc["kind"], 0) + 1
        want = json.loads(json.dumps(py))
        mine = {k: g[k] for k in want if k in g}
        if mine != want:
            diffs.append((seed, sc, want, mine))
    print(f"scenarios={len(cases)} declined={declined} python-raised={raised} compared: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    for seed, sc, want, mine in diffs[:4]:
        print(f" - seed {seed}: {json.dumps(sc)[:200]}\n   py={json.dumps(want)[:260]}\n   rs={json.dumps(mine)[:260]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
