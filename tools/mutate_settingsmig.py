#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-settingsmig/src/lib.rs (non-test code). Mutants are generated mechanically (comparison operators, connectives,
negations, numeric constants, true/false, min/max, +/-) in a detached COPY of the crate (the repository source is never touched). A mutant is KILLED when it
changes the Rust answers on the tools/settings_migrate_parity.py corpus, where the REAL Python migration block is the oracle (run ONCE). A mutant that does not
compile is reported separately. Survivors must be listed in SURVIVOR_NOTES (equivalent: why, or GAP: what the corpus lacks), otherwise the run fails.

  <python with PyQt6> tools/mutate_settingsmig.py          (MUTATE_ONLY=3,7 runs some; MUTATE_SEEDS=N random cases, default 1500)
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
REAL = ROOT / "rust/crates/singws-settingsmig"
WORK = Path(os.environ.get("MUTATE_WORKDIR") or os.path.expanduser("~/.cache/singws-settingsmig-mut-work"))
TARGET = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-settingsmig-mut")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
ONLY = {int(x) for x in os.environ.get("MUTATE_ONLY", "").split(",") if x}
SEEDS = int(os.environ.get("MUTATE_SEEDS", "1500"))

import mutate_songend as ms  # noqa: E402  (code_spans, PATTERNS)

# "L<line>:<old>-><new>" -> "equivalent: why" | "GAP: what the corpus lacks"
SURVIVOR_NOTES: dict = {
    "L48:false->true": "equivalent: a leading underscore is already rejected by the digit-first check just above, so the initial prev_us never matters",
    "L62: > -> >= ": "equivalent: a value past 2^70 is declined either way and the parity tools skip declined cases (a decline guard against overflow of the i128 accumulator)",
    "L62:70->71": "equivalent: same overflow guard, only the bound moves",
    "L67: < -> <= ": "equivalent: declined at the i64 bound; the parity tools skip declined cases",
    "L67: > -> >= ": "equivalent: declined at the i64 bound; the parity tools skip declined cases",
    "L89:>=->>": "equivalent: a float of exactly 2^63 is declined at the bound; skipped like every decline",
    "L89: < -> <= ": "equivalent: a float of exactly -2^63 is declined at the bound; skipped like every decline",
    "L111:1.0->2.0": "equivalent: float(True) can never equal the legacy thresholds 5.0 / 6.0 that the result is compared with",
    "L111:0.0->1.0": "equivalent: float(False) can never equal the legacy thresholds 5.0 / 6.0 that the result is compared with",
}


def generate(text):
    lines = text.split("\n")
    first = next(i for i, l in enumerate(lines) if l.startswith("pub type Settings"))
    last = next(i for i, l in enumerate(lines) if l.startswith("#[cfg(test)]"))
    out = []
    for ln in range(first, last):
        line = lines[ln]
        if line.strip().startswith(("//", "///")) or ("fn " in line and "{" in line and "(" in line and "if " not in line and "match" not in line):
            continue
        for a, b in ms.code_spans(line):
            seg = line[a:b]
            for pat, repl in ms.PATTERNS:
                for m in pat.finditer(seg):
                    s, e = a + m.start(), a + m.end()
                    tok = line[s:e]
                    if repl is None:
                        if "." in tok:
                            new = repr(float(tok) + 1.0) if float(tok) < 100 else repr(float(tok) - 1.0)
                        else:
                            new = str(int(tok) + 1)
                    else:
                        new = repl
                    if line[:s] + new + line[e:] != line:
                        out.append((ln + 1, s, e, new, line.strip()[:90]))
    return out


def setup():
    (WORK / "src").mkdir(parents=True, exist_ok=True)
    toml = (REAL / "Cargo.toml").read_text().replace('path = "../singws-dbw"', f'path = "{ROOT}/rust/crates/singws-dbw"') + "\n[workspace]\n"
    (WORK / "Cargo.toml").write_text(toml)
    shutil.copy(ROOT / "rust/Cargo.lock", WORK / "Cargo.lock")
    shutil.copy(REAL / "src/main.rs", WORK / "src/main.rs")
    (WORK / "src/lib.rs").write_text((REAL / "src/lib.rs").read_text())


def main():
    home = os.environ.get("SINGWS_HOME", "")
    if not home or Path(home).expanduser().resolve() == (Path.home() / "SingWS").resolve():
        os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-settingsmig-mut-")
    import settings_migrate_parity as par
    import songend_scenarios as ss
    oracle = par.real_migration(ss.load_app())
    cases = par.scripted() + [par.random_case(random.Random(s)) for s in range(SEEDS)]
    cases = [c for c in cases if not par.has_big_int(c)]
    expected = []
    for c in cases:
        py, ch = oracle(c)
        expected.append(json.dumps({"settings": py, "changed": ch}, ensure_ascii=True))
    print(f"oracle ready: {len(cases)} cases", flush=True)
    setup()
    src = WORK / "src/lib.rs"
    original = src.read_text()
    muts = generate(original)
    print(f"mutants: {len(muts)}", flush=True)
    binary = Path(TARGET) / "debug" / "singws-settingsmig"
    payload = json.dumps({"batch": cases}).encode()

    def build():
        return subprocess.run(["cargo", "build"], capture_output=True, text=True, env=ENV, cwd=WORK)

    def answers():
        proc = subprocess.run([str(binary)], input=payload, capture_output=True, timeout=300)
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)

    def agrees(got):
        if got is None:
            return False
        for exp, g in zip(expected, got):
            if "decline" in g:  # a declined case is skipped, exactly like the parity tool
                continue
            if json.dumps(g, ensure_ascii=True) != exp:
                return False
        return True

    base = build()
    assert base.returncode == 0, base.stderr[-600:]
    assert agrees(answers()), "the unmutated crate must agree with Python on the corpus"
    survivors, noncompiling, killed = [], [], 0
    try:
        for n, (ln, s, e, new, snippet) in enumerate(muts, 1):
            if ONLY and n not in ONLY:
                continue
            lines = original.split("\n")
            old = lines[ln - 1][s:e]
            lines[ln - 1] = lines[ln - 1][:s] + new + lines[ln - 1][e:]
            src.write_text("\n".join(lines))
            b = build()
            if b.returncode != 0:
                noncompiling.append(n)
                print(f"#{n} L{ln}: does not compile", flush=True)
                continue
            if agrees(answers()):
                key = f"L{ln}:{old}->{new}"
                note = SURVIVOR_NOTES.get(key)
                survivors.append((n, key, note, snippet))
                print(f"#{n} L{ln} '{old}'->'{new}': SURVIVED {note or '(UNDOCUMENTED)'}   [{snippet}]", flush=True)
            else:
                killed += 1
                print(f"#{n} L{ln} '{old}'->'{new}': killed", flush=True)
    finally:
        src.write_text(original)
    undocumented = [x for x in survivors if not x[2]]
    print(f"mutants={len(muts)} killed={killed} survivors={len(survivors)} undocumented={len(undocumented)} non-compiling={len(noncompiling)}")
    for n, key, note, snippet in undocumented:
        print(f"  UNDOCUMENTED #{n} {key} [{snippet}]")
    return 1 if undocumented else 0


if __name__ == "__main__":
    sys.exit(main())
