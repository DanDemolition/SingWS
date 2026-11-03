#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-songend/src/lib.rs. Mutants are generated mechanically (comparison operators, boolean connectives,
negations, numeric constants, true/false, min/max, +/-) on every code line from ``pub fn event`` onward, outside string literals and comments.
A mutant is KILLED when it changes the Rust answers on the tools/songend_parity.py corpus (the REAL Python code is the oracle, run ONCE).
Survivors must be listed in tools/mutants_songend.py NOTES (equivalent: reason, or GAP: a corpus gap), otherwise the run fails.
  .venv/bin/python tools/mutate_songend.py     (MUTATE_ONLY=3,7 runs some; MUTATE_SEEDS=N random scenarios, default 400; MUTATE_PROFILE=release|dev)
Restores the source afterwards. A mutant that does not compile is reported separately."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
SRC = ROOT / "rust/crates/singws-songend/src/lib.rs"
TARGET = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-songend-mut")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
ONLY = {int(x) for x in os.environ.get("MUTATE_ONLY", "").split(",") if x}
SEEDS = int(os.environ.get("MUTATE_SEEDS", "400"))
PROFILE = os.environ.get("MUTATE_PROFILE", "dev")

from mutants_songend import NOTES  # noqa: E402


def code_spans(line):
    """(start, end) pieces of the line that are code: not inside "..." and not after //."""
    spans, i, start, in_str = [], 0, 0, False
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str, start = False, i + 1
        else:
            if c == '"':
                spans.append((start, i))
                in_str = True
            elif line.startswith("//", i):
                spans.append((start, i))
                return spans
        i += 1
    if not in_str:
        spans.append((start, len(line)))
    return spans


PATTERNS = [
    (re.compile(r"<="), "<"), (re.compile(r">="), ">"),
    (re.compile(r" < "), " <= "), (re.compile(r" > "), " >= "),
    (re.compile(r"&&"), "||"), (re.compile(r"\|\|"), "&&"),
    (re.compile(r"==") , "!="), (re.compile(r"!="), "=="),
    (re.compile(r"(?<![=!<>])!(?=[a-z(])"), ""),
    (re.compile(r"\btrue\b"), "false"), (re.compile(r"\bfalse\b"), "true"),
    (re.compile(r"\.max\("), ".min("), (re.compile(r"\.min\("), ".max("),
    (re.compile(r" \+ "), " - "), (re.compile(r" - "), " + "), (re.compile(r"\+= "), "-= "),
    (re.compile(r"(?<![\w.\"])\d+\.\d+(?![\w\"])"), None), (re.compile(r"(?<![\w.\"])\d+(?![\w.\"])"), None),
]


def generate(text):
    lines = text.split("\n")
    first = next(i for i, l in enumerate(lines) if "pub fn event" in l)
    out = []
    for ln in range(first, len(lines)):
        line = lines[ln]
        if line.strip().startswith(("//", "///")) or "fn " in line and "{" in line and "(" in line and "if " not in line and "match" not in line:
            continue
        for a, b in code_spans(line):
            seg = line[a:b]
            for pat, repl in PATTERNS:
                for m in pat.finditer(seg):
                    s, e = a + m.start(), a + m.end()
                    tok = line[s:e]
                    if repl is None:
                        if "." in tok:
                            new = repr(float(tok) + 1.0) if float(tok) < 100 else repr(float(tok) - 1.0)
                        else:
                            v = int(tok)
                            new = str(v + 1)
                    else:
                        new = repl
                    mutated = line[:s] + new + line[e:]
                    if mutated != line:
                        out.append((ln + 1, s, e, new, line.strip()[:90]))
    return out


def build():
    cmd = ["cargo", "build", "-p", "singws-songend"] + (["--release"] if PROFILE == "release" else [])
    return subprocess.run(cmd, capture_output=True, text=True, env=ENV, cwd=ROOT / "rust")


def main():
    import songend_parity as par
    import songend_scenarios as sc
    corpus = sc.scripted() + [sc.random_scenario(s) for s in range(SEEDS)]
    work = [(s["name"], par.run_python(s)) for s in corpus]
    print(f"oracle ready: {len(work)} scenarios", flush=True)
    original = SRC.read_text()
    muts = generate(original)
    print(f"mutants: {len(muts)}", flush=True)
    binary = Path(TARGET) / ("release" if PROFILE == "release" else "debug") / "singws-songend"
    survivors, noncompiling, killed_n = [], [], 0
    try:
        base = build()
        assert base.returncode == 0, base.stderr[-500:]
        bad = [n for n, o in work if par.compare(n, o, par.rust(binary, o["replay"]))]
        assert not bad, f"baseline already differs: {bad[:3]}"
        lines = original.split("\n")
        for i, (ln, s, e, new, snippet) in enumerate(muts):
            if ONLY and i not in ONLY:
                continue
            mutated_lines = list(lines)
            mutated_lines[ln - 1] = lines[ln - 1][:s] + new + lines[ln - 1][e:]
            SRC.write_text("\n".join(mutated_lines))
            b = build()
            if b.returncode != 0:
                noncompiling.append(i)
                print(f"#{i} L{ln}: does not compile", flush=True)
                continue
            killed = None
            for name, out in work:
                try:
                    diff = par.compare(name, out, par.rust(binary, out["replay"]))
                except Exception as exc:
                    diff = [str(exc)[:100]]
                if diff:
                    killed = name
                    break
            if killed:
                killed_n += 1
                print(f"#{i} L{ln} {lines[ln-1][s:e]!r}->{new!r}: killed by {killed}", flush=True)
            else:
                key = f"L{ln}:{lines[ln-1][s:e]}->{new}"
                note = NOTES.get(key, "")
                print(f"#{i} L{ln} {lines[ln-1][s:e]!r}->{new!r}: SURVIVED {note or '(UNDOCUMENTED)'}   [{snippet}]", flush=True)
                survivors.append((i, key, note, snippet))
    finally:
        SRC.write_text(original)
        build()
    undocumented = [x for x in survivors if not x[2]]
    gaps = [x for x in survivors if x[2].startswith("GAP")]
    print(f"mutants={len(muts)} killed={killed_n} survivors={len(survivors)} documented-equivalent={len(survivors) - len(undocumented) - len(gaps)} "
          f"open-GAPs={len(gaps)} undocumented={len(undocumented)} non-compiling={len(noncompiling)}")
    for i, key, note, snippet in undocumented:
        print(f"  UNDOCUMENTED #{i} {key} [{snippet}]")
    return 1 if undocumented else 0


if __name__ == "__main__":
    sys.exit(main())
