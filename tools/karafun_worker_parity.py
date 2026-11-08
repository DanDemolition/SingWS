#!/usr/bin/env python3
"""Parity: the WHOLE real KaraokeApp._automate_karafun_search_and_play (0.2.18.1.py, orchestration and real closures included, see karafun_worker_replay.py) vs the Rust
`singws-karafun` worker.rs, on the same scenario and the same SCRIPTED ANSWERS. Compared: the method's return value (or the exception), the entry, every call (name, arguments),
every log line, every sleep and the final fake clock.

  python3 tools/karafun_worker_parity.py [--seeds N] [--binary PATH]
"""
import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import karafun_worker_replay as wr  # noqa: E402


def run_rust(binary, reqs):
    out = subprocess.run([binary], input=json.dumps({"batch": reqs}).encode(), capture_output=True, timeout=600)
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
    loaded = wr.load()
    cases, reqs = [], []
    for seed in range(args.seeds):
        r = random.Random(seed)
        sc = wr.random_scenario(r)
        py = wr.run(loaded, sc, r)
        cases.append((seed, sc, py))
        reqs.append({"op": "worker", "scenario": sc, "answers": py["answers"]})
    got = run_rust(args.binary, reqs)
    diffs, declined, kinds = [], 0, {}
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
        st = py["entry"].get("karafun_submission_state", "?")
        kinds[st] = kinds.get(st, 0) + 1
    print(f"scenarios={len(cases)} declined={declined} compared={len(cases) - declined} end states: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    for seed, bad, sc, want, mine in diffs[:3]:
        print(f" - seed {seed}: differs in {bad}")
        for k in bad[:3]:
            a, b = json.dumps(want.get(k)), json.dumps(mine.get(k))
            i = next((n for n in range(min(len(a), len(b))) if a[n] != b[n]), min(len(a), len(b)))
            print(f"   {k}: py={a[max(0, i - 80):i + 160]}\n   {k}: rs={b[max(0, i - 80):i + 160]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
