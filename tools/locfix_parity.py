#!/usr/bin/env python3
"""Parity: `LocationFixCollector` (0.2.18.1.py, read by AST) vs the Rust `singws-misc`: random event sequences (fixes with odd accuracy / age values, transient and fatal errors, codes
of odd types) replayed on both; compared: what each event answered (stop / final) and the final (fix, error text). Text-given numbers are declined.

  CARGO_TARGET_DIR=... python3 tools/locfix_parity.py [--seeds N]
"""
import argparse
import ast
import json
import os
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-karafun")) + "/release/singws-misc"


def load():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "LocationFixCollector":
            exec(compile(ast.Module([node], []), "app", "exec"), ns)
    return ns["LocationFixCollector"]


NUM = [0, 0.0, 5, 49.9, 100, 100.0, 100.0001, 101, 250, 3000, 3000.0, 3000.5, 3001, 1e9, -1, -0.5, True, False, None, [], {}, 29.9, 30, 30.0, 30.01, 31]
CODES = [0, 1, 2, 3, 2.9, 0.0, True, False, None, [], -1, 10**12]
TEXTS = ["", "unknown", "network down", "denied", "Zoë", "x" * 50]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5000)
    a = ap.parse_args(argv)
    cls = load()
    scenarios, want = [], []
    for seed in range(a.seeds):
        r = random.Random(seed)
        events, c, stops = [], cls(), []
        for _ in range(r.randrange(0, 8)):
            if r.random() < 0.65:
                obj, acc, age = r.randrange(1000), r.choice(NUM), r.choice(NUM)
                events.append(["fix", obj, acc, age])
                stops.append(c.add_fix(obj, acc, age))
            else:
                code, text = r.choice(CODES), r.choice(TEXTS)
                events.append(["error", code, text])
                stops.append(c.add_error(code, text))
        fix, err = c.result()
        scenarios.append({"op": "locfix", "events": events})
        want.append({"stops": stops, "fix": fix, "error": err})
    out = subprocess.run([CLI], input=json.dumps({"batch": scenarios}), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("cli failed: " + out.stderr[:300])
    got = json.loads(out.stdout)
    diffs, declined = [], 0
    for sc, w, g in zip(scenarios, want, got):
        if "declined" in g:
            declined += 1
            continue
        if json.loads(json.dumps(w)) != g:
            diffs.append((sc, w, g))
    print(f"scenarios={len(scenarios)} declined={declined} with a fix={sum(1 for w in want if w['fix'] is not None)} rough={sum(1 for w in want if w['error'].startswith('Only a rough'))} timed out={sum(1 for w in want if w['error'].startswith('Timed out'))}")
    for sc, w, g in diffs[:3]:
        print(f" - {json.dumps(sc)[:240]}\n   py={json.dumps(w)[:200]}\n   rs={json.dumps(g)[:200]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
