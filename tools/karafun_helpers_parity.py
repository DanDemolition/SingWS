#!/usr/bin/env python3
"""Parity: the five nested helpers of the real KaraokeApp._automate_karafun_search_and_play worker (0.2.18.1.py, read by AST, see karafun_helpers_replay.py) vs the Rust helpers.rs.
Both get the same scripted sequence of calls and the same SCRIPTED ANSWERS; compared: the entry, the learned setting, the session dict, every call, every log line, every sleep, the final clock.

  python3 tools/karafun_helpers_parity.py [--seeds N] [--binary PATH]
"""
import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import karafun_helpers_replay as fr  # noqa: E402


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
    helpers = fr.load()
    cases, reqs = [], []
    for seed in range(args.seeds):
        r = random.Random(seed)
        sc = fr.random_scenario(r)
        py = fr.run(helpers, sc, r)
        cases.append((seed, sc, py))
        reqs.append({"op": "helpers", "scenario": sc, "answers": py["answers"]})
    got = run_rust(args.binary, reqs)
    diffs, declined, learned = [], 0, 0
    for (seed, sc, py), g in zip(cases, got):
        w = g["world"]
        if w.get("declined"):
            declined += 1
            continue
        want = {"entry": norm(py["entry"]), "native": py["settings"]["karafun_native_double_click"], "session": norm(py["session"]), "calls": norm(py["calls"]), "log": py["log"],
                "sleeps": norm(py["sleeps"]), "final_clock": py["final_clock"], "consumed": len(py["answers"])}
        mine = {"entry": g["entry"], "native": g["native"], "session": g["session"], "calls": w["calls"], "log": w["log"], "sleeps": w["sleeps"], "final_clock": w["final_clock"], "consumed": w["consumed"]}
        if w.get("order_error"):
            mine["order_error"] = w["order_error"]
        learned += int(any("learned: native" in l for l in py["log"]))
        if mine != want:
            diffs.append((seed, [k for k in want if mine.get(k) != want[k]] + (["order_error"] if "order_error" in mine else []), want, mine))
    print(f"scenarios={len(cases)} declined={declined} compared={len(cases) - declined} learned-native={learned}")
    for seed, bad, want, mine in diffs[:3]:
        print(f" - seed {seed}: differs in {bad}")
        for k in bad[:2]:
            print(f"   {k}: py={json.dumps(want.get(k))[:300]}\n   {k}: rs={json.dumps(mine.get(k))[:300]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
