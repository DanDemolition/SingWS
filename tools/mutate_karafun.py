#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-karafun/src/lib.rs (non-test code; script_lines.rs is generated and checked by gen_karafun_script_rs.py --check). Mutants are generated mechanically (comparison operators, connectives,
negations, numeric constants, true/false, min/max, +/-) in a detached COPY of the crate (the repository source is never touched). A mutant is KILLED when it
changes the Rust answers on the tools/karafun_script_parity.py corpus, where the REAL Python builder is the oracle (run ONCE). A mutant that does not
compile is reported separately. Survivors must be listed in SURVIVOR_NOTES (equivalent: why, or GAP: what the corpus lacks), otherwise the run fails.

  python3 tools/mutate_karafun.py          (MUTATE_ONLY=3,7 runs some; MUTATE_SEEDS=N random cases, default 1500)
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
REAL = ROOT / "rust/crates/singws-karafun"
WORK = Path(os.environ.get("MUTATE_WORKDIR") or os.path.expanduser("~/.cache/singws-karafun-mut-work"))
TARGET = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-karafun-mut")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
ONLY = {int(x) for x in os.environ.get("MUTATE_ONLY", "").split(",") if x}
SEEDS = int(os.environ.get("MUTATE_SEEDS", "1500"))

import mutate_songend as ms  # noqa: E402  (code_spans, PATTERNS)

# "L<line>:<old>-><new>" -> "equivalent: why" | "GAP: what the corpus lacks"
SURVIVOR_NOTES: dict = {
    "L19:2->3": "equivalent: only a capacity hint of the output string",
    "L36:0->1": "equivalent: with the counter starting at 1 the empty suffix at the very end matches, which cuts nothing (the text is stripped, so no earlier suffix is only whitespace)",
    "L50:1->2": "equivalent: the counter is only ever tested for being above zero",
    "L55: > -> >= ": "equivalent: a match with no group at all is the empty suffix at the very end, which cuts nothing",
}


def generate(text):
    lines = text.split("\n")
    first = next(i for i, l in enumerate(lines) if l.startswith("fn py_space"))
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
    (WORK / "Cargo.toml").write_text((REAL / "Cargo.toml").read_text() + "\n[workspace]\n")
    shutil.copy(ROOT / "rust/Cargo.lock", WORK / "Cargo.lock")
    for name in ("main.rs", "script_lines.rs"):
        shutil.copy(REAL / "src" / name, WORK / "src" / name)
    (WORK / "src/lib.rs").write_text((REAL / "src/lib.rs").read_text())


def main():
    import karafun_script_parity as par
    match_title, literal, search = par.load_python()
    cs = par.cases(SEEDS)
    requests, expected = [], []
    for c in cs:
        requests.append(dict(c, op="search_script"))
        expected.append({"lines": search(query=c["query"], safe_title=c["safe_title"], safe_artist=c["safe_artist"],
                                          require_exact_title=c["require_exact_title"], resolve_only=c["resolve_only"])})
    for s in sorted({c["safe_title"] for c in cs} | set(par.TITLES)):
        requests.append({"op": "match_title", "title": s})
        expected.append({"title": match_title(s)})
    for s in sorted({c["query"] for c in cs} | {c["safe_artist"] for c in cs}):
        requests.append({"op": "literal", "value": s})
        expected.append({"literal": literal(s)})
    print(f"oracle ready: {len(requests)} requests", flush=True)
    setup()
    src = WORK / "src/lib.rs"
    original = src.read_text()
    muts = generate(original)
    print(f"mutants: {len(muts)}", flush=True)
    binary = Path(TARGET) / "debug" / "singws-karafun"
    payload = json.dumps({"batch": requests}).encode()

    def build():
        return subprocess.run(["cargo", "build"], capture_output=True, text=True, env=ENV, cwd=WORK)

    def answers():
        proc = subprocess.run([str(binary)], input=payload, capture_output=True, timeout=300)
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)

    def agrees(got):
        return got is not None and got == expected

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
