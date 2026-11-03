#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-bgm-policy/src/lib.rs: every mutant must change the Rust answers on the tools/bgm_policy_parity.py
corpus (the REAL Python player is the oracle, run ONCE). A mutant that does not compile is reported separately (not counted as killed).
Equivalent mutants carry a note. Restores the source afterwards.
  .venv/bin/python tools/mutate_bgm_policy.py          (MUTATE_ONLY=3,7 runs some; MUTATE_SEEDS=N random scenarios, default 700)"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
SRC = ROOT / "rust/crates/singws-bgm-policy/src/lib.rs"
TARGET = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-bgm-policy-mut")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
ONLY = {int(x) for x in os.environ.get("MUTATE_ONLY", "").split(",") if x}
SEEDS = int(os.environ.get("MUTATE_SEEDS", "700"))

from mutants_bgm_policy import M, SURVIVOR_NOTES  # noqa: E402


def build():
    return subprocess.run(["cargo", "build", "--release", "-p", "singws-bgm-policy"], capture_output=True, text=True, env=ENV, cwd=ROOT / "rust")


def main():
    import bgm_policy_parity as par
    corpus = par.scripted() + [par.random_scenario(s) for s in range(SEEDS)]
    work = []
    for s in corpus:
        out = par.run_python(s)
        work.append((s["name"], out))
    print(f"oracle ready: {len(work)} scenarios", flush=True)
    original = SRC.read_text()
    binary = Path(TARGET) / "release" / "singws-bgm-policy"
    survivors, noncompiling = [], []
    try:
        base = build()
        assert base.returncode == 0, base.stderr[-500:]
        bad = [n for n, o in work if par.compare(n, o, par.rust(binary, o["replay"]))]
        assert not bad, f"baseline already differs: {bad[:3]}"
        for i, (old, new, note) in enumerate(M):
            if ONLY and i not in ONLY:
                continue
            count = original.count(old)
            if count != 1:
                print(f"#{i}: pattern matches {count} times: {old[:70]!r}", flush=True)
                survivors.append((i, "pattern-count-" + str(count), old))
                continue
            SRC.write_text(original.replace(old, new))
            b = build()
            if b.returncode != 0:
                noncompiling.append(i)
                print(f"#{i}: does not compile", flush=True)
                continue
            killed = None
            for name, out in work:
                try:
                    diff = par.compare(name, out, par.rust(binary, out["replay"]))
                except Exception as exc:     # a panicking mutant is killed too
                    diff = [str(exc)[:100]]
                if diff:
                    killed = name
                    break
            status = f"killed by {killed}" if killed else ("SURVIVED" + (f" (documented equivalent: {note})" if note else (f" ({SURVIVOR_NOTES[i]})" if i in SURVIVOR_NOTES else " (UNDOCUMENTED)")))
            print(f"#{i}: {status}", flush=True)
            if not killed:
                survivors.append((i, note, old))
    finally:
        SRC.write_text(original)
        build()
    undocumented = [s for s in survivors if not s[1] and s[0] not in SURVIVOR_NOTES and not str(s[0]).startswith('pattern')]
    gaps = [i for i, _n, _o in survivors if str(SURVIVOR_NOTES.get(i, '')).startswith('GAP')]
    print(f'open GAPs (not killed, not proven equivalent): {len(gaps)}')
    print(f"mutants={len(M)} survivors={len(survivors)} undocumented={len(undocumented)} non-compiling={noncompiling}")
    for i, note, old in undocumented:
        print("  UNDOCUMENTED #%d: %s" % (i, old[:90].replace("\n", " ")))
    return 1 if undocumented else 0


if __name__ == "__main__":
    sys.exit(main())
