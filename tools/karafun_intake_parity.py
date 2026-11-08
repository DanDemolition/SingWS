#!/usr/bin/env python3
"""Parity: the `_intake` closure of the real KaraokeApp._automate_karafun_search_and_play (0.2.18.1.py, read by AST, see karafun_intake_replay.py) vs the Rust intake.rs.
Compared: the entry's final fields, every call (name, arguments), every log line.

  python3 tools/karafun_intake_parity.py [--seeds N] [--binary PATH]
"""
import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import karafun_intake_replay as fr  # noqa: E402


def run_rust(binary, reqs):
    out = subprocess.run([binary], input=json.dumps({"batch": reqs}).encode(), capture_output=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode()[-500:])
    return json.loads(out.stdout)


def norm(x):
    return json.loads(json.dumps(x))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3000)
    ap.add_argument("--binary", default=os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun"))
    args = ap.parse_args(argv)
    intake, _n = fr.load()
    cases, reqs = [], []
    for seed in range(args.seeds):
        sc = fr.random_scenario(random.Random(seed))
        cases.append((seed, sc, fr.run(intake, sc)))
        reqs.append({"op": "intake", "scenario": sc})
    got = run_rust(args.binary, reqs)
    diffs, declined, queued = [], 0, 0
    for (seed, sc, py), g in zip(cases, got):
        if g.get("declined"):
            declined += 1
            continue
        want = {"result": norm(py["result"]), "entry": norm(py["entry"]), "calls": norm(py["calls"]), "log": py["log"]}
        mine = {"result": g["result"], "entry": g["entry"], "calls": g["calls"], "log": g["log"]}
        queued += int(py["result"].get("returned", 0) is None)
        if mine != want:
            diffs.append((seed, [k for k in want if mine[k] != want[k]], want, mine))
    print(f"scenarios={len(cases)} declined={declined} compared={len(cases) - declined} queued={queued}")
    for seed, bad, want, mine in diffs[:3]:
        print(f" - seed {seed}: differs in {bad}")
        for k in bad[:2]:
            print(f"   {k}: py={json.dumps(want[k])[:300]}\n   {k}: rs={json.dumps(mine[k])[:300]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
