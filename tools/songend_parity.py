#!/usr/bin/env python3
"""Parity: the REAL Python song-end hand-off code (0.2.18.1.py, driven by tools/songend_scenarios.py) vs the Rust ``singws-songend`` crate.

For every scenario the recorded inputs are replayed through the Rust CLI; every command (bg/host calls, single-shot timers, QTimer start/stop,
log lines), the return value and the detector/hand-off state after EVERY event must be identical (strict types: 1000 is not 1000.0).

  .venv/bin/python tools/songend_parity.py [--seeds N] [--binary PATH] [--only NAME]      (needs PyQt6; SINGWS_HOME is a scratch dir)
Build first:  CARGO_TARGET_DIR=~/.cache/singws-rust-target-songend cargo build --release -p singws-songend   (in rust/)
Exit status 0 only when every scenario is identical.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import songend_scenarios as sc  # noqa: E402

DEFAULT_BIN = Path.home() / ".cache/singws-rust-target-songend/release/singws-songend"


def run_python(scenario):
    return sc.run(copy.deepcopy(scenario))


def rust(binary, replay):
    proc = subprocess.run([str(binary)], input=json.dumps(replay).encode(), capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode()[-800:])
    return json.loads(proc.stdout)["results"]


def strict_eq(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def compare(name, out, results):
    exp = out["expected"]
    if len(exp) != len(results):
        return [f"{name}: event count python={len(exp)} rust={len(results)}"]
    for i, (e, r) in enumerate(zip(exp, results)):
        ev = out["replay"]["events"][i]
        if not strict_eq(e["cmds"], r["cmds"]):
            return [f"{name}: event {i} t+{e['t'] - sc.T0_MS}ms {ev['ev']} {ev['a']}\n   python cmds: {json.dumps(e['cmds'])[:1200]}\n   rust   cmds: {json.dumps(r['cmds'])[:1200]}"]
        if not strict_eq(e["ret"], r["ret"]):
            return [f"{name}: event {i} {ev['ev']} return python={e['ret']!r} rust={r['ret']!r}"]
        if not strict_eq(e["state"], r["state"]):
            diff = {k: (e["state"][k], r["state"].get(k)) for k in e["state"] if not strict_eq(e["state"][k], r["state"].get(k))}
            return [f"{name}: event {i} t+{e['t'] - sc.T0_MS}ms {ev['ev']} {ev['a']} state differs {diff}"]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=300)
    ap.add_argument("--binary", default=str(DEFAULT_BIN))
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    all_scen = sc.scripted() + [sc.random_scenario(s) for s in range(args.seeds)]
    if args.only:
        all_scen = [s for s in all_scen if args.only in s["name"]]
    problems, events, cmds = [], 0, 0
    kinds = {}
    for s in all_scen:
        out = run_python(s)
        problems += compare(s["name"], out, rust(args.binary, out["replay"]))
        events += len(out["expected"])
        for e in out["expected"]:
            for c in e["cmds"]:
                cmds += 1
                kinds[c["c"]] = kinds.get(c["c"], 0) + 1
    print(f"scenarios={len(all_scen)} events={events} commands={cmds}")
    print("command kinds:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    if problems:
        print(f"DIFFERENT: {len(problems)}")
        for p in problems[:10]:
            print(" -", p)
        return 1
    print("IDENTICAL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
