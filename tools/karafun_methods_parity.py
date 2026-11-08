#!/usr/bin/env python3
"""Parity: four KaraFun automation methods of the real 0.2.18.1.py (run unchanged, see karafun_methods_replay.py) vs the Rust methods.rs, with the same scenario and the same
SCRIPTED ANSWERS. Compared: the return value or raised error, every call (name, arguments), every log line, every sleep, the final fake clock, the entry's final fields and how
many answers were consumed.

  python3 tools/karafun_methods_parity.py [--seeds N] [--binary PATH]
"""
import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import karafun_methods_replay as mr  # noqa: E402


def run_rust(binary, reqs):
    out = subprocess.run([binary], input=json.dumps({"batch": reqs}).encode(), capture_output=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode()[-400:])
    return json.loads(out.stdout)


def norm(x):
    return json.loads(json.dumps(x))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2000)
    ap.add_argument("--binary", default=os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun"))
    args = ap.parse_args(argv)
    loaded = mr.load()
    cases, reqs = [], []
    for method in mr.METHODS:
        for seed in range(args.seeds):
            r = random.Random(seed * 7 + 3)
            sc = mr.scenario_for(method, r)
            py = mr.run(method, sc, r, loaded=loaded)
            cases.append((method, seed, sc, py))
            reqs.append({"op": "method", "method": method, "scenario": sc, "answers": py["answers"]})
    got = run_rust(args.binary, reqs)
    diffs, declined, per = [], 0, {}
    for (method, seed, sc, py), g in zip(cases, got):
        if g.get("declined"):
            declined += 1
            continue
        want = {"out": norm(py["out"]), "calls": norm(py["calls"]), "log": py["log"], "sleeps": norm(py["sleeps"]), "final_clock": py["final_clock"], "entry": py["entry"],
                "consumed": py["consumed"]}
        mine = {"out": g["out"], "calls": g["calls"], "log": g["log"], "sleeps": g["sleeps"], "final_clock": g["final_clock"], "entry": g["entry"], "consumed": g["consumed"]}
        if g.get("order_error"):
            mine["order_error"] = g["order_error"]
        d = per.setdefault(method, [0, 0])
        d[0] += 1
        if mine != want:
            d[1] += 1
            bad = [k for k in want if mine.get(k) != want[k]] + (["order_error"] if "order_error" in mine else [])
            diffs.append((method, seed, bad, sc, want, mine))
    print(f"cases={len(cases)} declined={declined} compared/different per method: " + ", ".join(f"{m[len('_karafun_'):]}={c}/{d}" for m, (c, d) in per.items()))
    for method, seed, bad, sc, want, mine in diffs[:3]:
        print(f" - {method} seed {seed}: differs in {bad}")
        for k in bad[:2]:
            print(f"   {k}: py={json.dumps(want.get(k))[:240]}\n   {k}: rs={json.dumps(mine.get(k))[:240]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
