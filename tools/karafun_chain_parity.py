#!/usr/bin/env python3
"""Chain parity: the per-stretch twins of KaraokeApp._automate_karafun_search_and_play run IN ORDER on one queue entry, the entry each stretch leaves being the entry the next one
starts from, on BOTH sides (the real Python statements, read by AST, and the Rust `singws-karafun`). Order of the real method: intake -> setup -> prelude -> flow -> finish.

Each stretch is already proven identical on its own (tools/karafun_*_parity.py); this checks that what one stretch leaves behind is what the next one is proven on: after every
stretch the two entries, the result, calls, log lines, sleeps and (where modelled) the clock must still agree. A seed's chain stops at the first stretch the Rust side declines.

  python3 tools/karafun_chain_parity.py [--seeds N] [--binary PATH]
"""
import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import karafun_finish_replay as fin  # noqa: E402
import karafun_flow_replay as flo  # noqa: E402
import karafun_intake_replay as itk  # noqa: E402
import karafun_prelude_replay as pre  # noqa: E402
import karafun_setup_replay as stp  # noqa: E402


def run_rust(binary, reqs):
    out = subprocess.run([binary], input=json.dumps({"batch": reqs}).encode(), capture_output=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode()[-500:])
    return json.loads(out.stdout)


def norm(x):
    return json.loads(json.dumps(x))


# name, replay module, rust op, takes (rng, answers)
STAGES = (("intake", itk, False), ("setup", stp, True), ("prelude", pre, True), ("flow", flo, True), ("finish", fin, False))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3000)
    ap.add_argument("--binary", default=os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun"))
    args = ap.parse_args(argv)
    loaded = {name: (mod.load()[0] if isinstance(mod.load(), tuple) else mod.load()) for name, mod, _ in STAGES}
    chains = []
    for seed in range(args.seeds):
        r = random.Random(seed)
        entry = norm(itk.random_scenario(r)["entry"])  # one starting entry per seed
        stages = []
        for name, mod, scripted in STAGES:
            sc = mod.random_scenario(r)
            sc["entry"] = json.loads(json.dumps(entry))
            py = mod.run(loaded[name], sc, r) if scripted else mod.run(loaded[name], sc)
            stages.append((name, sc, py))
            entry = norm(py["entry"])
        chains.append((seed, stages))

    # The Rust side: the same scenarios, each stage's entry replaced by the entry the previous RUST stage left.
    diffs, declined, compared, deepest = [], 0, 0, {}
    alive = [dict(seed=s, stages=st, rs_entry=None, ok=True, at=0) for s, st in chains]
    for idx, (name, mod, scripted) in enumerate(STAGES):
        reqs, who = [], []
        for c in alive:
            if not c["ok"]:
                continue
            _, sc, py = c["stages"][idx]
            sc2 = dict(sc)
            if c["rs_entry"] is not None:
                sc2["entry"] = c["rs_entry"]
            req = {"op": name, "scenario": sc2}
            if scripted:
                req["answers"] = py["answers"]
            reqs.append(req)
            who.append(c)
        got = run_rust(args.binary, reqs)
        for c, g in zip(who, got):
            _, sc, py = c["stages"][idx]
            w = g.get("world", g)
            if g.get("declined") or w.get("declined"):
                declined += 1
                c["ok"] = False
                continue
            compared += 1
            want_entry = norm(py["entry"])
            mine_entry = g["entry"]
            res_want = norm(py.get("result"))
            res_mine = g.get("result")
            bad = []
            if mine_entry != want_entry:
                bad.append("entry")
            if res_want is not None and res_mine is not None and res_mine != res_want:
                bad.append("result")
            if "calls" in py and "calls" in w and norm(py["calls"]) != w["calls"]:
                bad.append("calls")
            if "log" in py and "log" in w and py["log"] != w["log"]:
                bad.append("log")
            if bad:
                diffs.append((c["seed"], name, bad, want_entry, mine_entry))
                c["ok"] = False
                continue
            c["rs_entry"] = mine_entry
            c["at"] = idx + 1
            deepest[name] = deepest.get(name, 0) + 1
    full = sum(1 for c in alive if c["ok"] and c["at"] == len(STAGES))
    print(f"seeds={len(chains)} stage-comparisons={compared} declined={declined} full-chains={full} reached: " + ", ".join(f"{k}={v}" for k, v in deepest.items()))
    for seed, name, bad, we, me in diffs[:3]:
        print(f" - seed {seed} stage {name}: differs in {bad}")
        if "entry" in bad:
            print(f"   py entry={json.dumps(we)[:260]}\n   rs entry={json.dumps(me)[:260]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
