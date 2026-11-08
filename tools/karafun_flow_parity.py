#!/usr/bin/env python3
"""Parity: the post-ladder flow of the real KaraokeApp._automate_karafun_search_and_play (0.2.18.1.py, read by AST, see karafun_flow_replay.py) vs the Rust
`singws-karafun` flow.rs. Both get the same scenario and the same SCRIPTED ANSWERS; compared: the result dict, the entry's final fields, every call (name, arguments),
every log line, every sleep and the final fake clock.

  python3 tools/karafun_flow_parity.py [--seeds N] [--binary PATH]
"""
import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import karafun_flow_replay as fr  # noqa: E402


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
    flow, n = fr.load()
    cases, reqs = [], []
    for seed in range(args.seeds):
        r = random.Random(seed)
        sc = fr.random_scenario(r)
        py = fr.run(flow, sc, r)
        cases.append((seed, sc, py))
        reqs.append({"op": "flow", "scenario": sc, "answers": py["answers"]})
    got = run_rust(args.binary, reqs)
    diffs, declined, branches = [], 0, {"ok": 0, "failed": 0}
    for (seed, sc, py), g in zip(cases, got):
        w = g["world"]
        if w.get("declined"):
            declined += 1
            continue
        want = {"result": norm(py["result"]), "entry": norm(py["entry"]), "calls": norm(py["calls"]), "log": py["log"], "sleeps": norm(py["sleeps"]), "final_clock": py["final_clock"],
                "consumed": len(py["answers"])}
        mine = {"result": g["result"], "entry": g["entry"], "calls": w["calls"], "log": w["log"], "sleeps": w["sleeps"], "final_clock": w["final_clock"], "consumed": w["consumed"]}
        if w.get("order_error"):
            mine["order_error"] = w["order_error"]
        if mine != want:
            bad = [k for k in want if mine.get(k) != want[k]] + (["order_error"] if "order_error" in mine else [])
            diffs.append((seed, bad, sc, want, mine))
        branches["ok" if py["result"]["ok"] else "failed"] += 1
    print(f"scenarios={len(cases)} declined={declined} compared={len(cases) - declined} ok-results={branches['ok']} failed-results={branches['failed']}")
    for seed, bad, sc, want, mine in diffs[:3]:
        print(f" - seed {seed}: differs in {bad}")
        for k in bad[:2]:
            print(f"   {k}: py={json.dumps(want.get(k))[:260]}\n   {k}: rs={json.dumps(mine.get(k))[:260]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
