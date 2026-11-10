#!/usr/bin/env python3
"""Mutation test of rust/crates/singws-karafun-svc (lib.rs = W1 service machine, src/host.rs = W2 song lifecycle).

The REAL Python (karafun_web.KaraFunWebService, karafun_web_host.KaraFunWebHost) is the oracle, run ONCE over a fixed corpus (tools/karafun_service_parity.py
and tools/karafun_host_parity.py generators); every mutant of the Rust source (operators, comparisons, booleans, integer/float constants; string text is never
touched) must change at least one trace, or carry a note in NOTES (`equivalent: why` / `GAP: what the corpus lacks`). The mutation runs in a SCRATCH COPY of the crate
(~/.cache/singws-karafun-svc-mut), never in the repository.
  SINGWS_HOME=$(mktemp -d) .venv-test-arm64-fresh/bin/python tools/mutate_karafun_svc.py [--svc N] [--host N] [--only 3,7] [--file lib.rs|host.rs]
"""
import argparse
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
if not os.environ.get("SINGWS_HOME") or Path(os.environ["SINGWS_HOME"]).resolve() == (Path.home() / "SingWS").resolve():
    os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="kf_svc_mut_home_")
import karafun_host_parity as hp  # noqa: E402
import karafun_service_parity as sp  # noqa: E402

CRATE = ROOT / "rust/crates/singws-karafun-svc"
WORK = Path(os.path.expanduser("~/.cache/singws-karafun-svc-mut"))
TARGET = os.path.expanduser("~/.cache/singws-rust-target-karafun-svc-mut2")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET, PATH=os.path.expanduser("~/.cargo/bin") + ":" + os.environ["PATH"])

# "<file>|<snippet[:70]>|<old>-><new>" -> "equivalent: why" | "GAP: what is missing"
NOTES: dict = {}

PATTERNS = [(re.compile(p), r) for p, r in [
    (r"==", "!="), (r"!=", "=="), (r"<=", "<"), (r">=", ">"), (r"(?<![<=!>\-])<(?![<=])", "<="), (r"(?<![\-=<>])>(?![>=])", ">="),
    (r"&&", "||"), (r"\|\|", "&&"), (r"(?<=\s)\+(?=\s)", "-"), (r"(?<=\s)-(?=\s)", "+"), (r"\btrue\b", "false"), (r"\bfalse\b", "true"),
    (r"(?<![\w.\"\\])\d+(\.\d+)?(?![\w.])", None), (r"(?<=if )!", ""), (r"(?<=&& )!", ""), (r"(?<=\|\| )!", "")]]


def blank_strings(line):
    return re.sub(r'"(\\.|[^"\\])*"', lambda m: '"' + "\x00" * (len(m.group(0)) - 2) + '"', line)


def generate(name, text):
    lines = text.split("\n")
    stop = next((i for i, l in enumerate(lines) if l.startswith("/// Replay request")), len(lines))
    out = []
    for ln, line in enumerate(lines[:stop]):
        s = line.strip()
        if s.startswith(("//", "///", "use ", "pub mod", "pub const", "const ", "#[")) or re.match(r"(pub )?fn ", s):
            continue
        b = blank_strings(line)
        for pat, repl in PATTERNS:
            for m in pat.finditer(b):
                tok = line[m.start():m.end()]
                if repl is None:
                    new = repr(float(tok) + 1.0) if "." in tok else str(int(tok) + 1)
                    if "." in tok and float(tok) == 0.0:
                        new = "1.0"
                else:
                    new = repl
                if line[:m.start()] + new + line[m.end():] != line:
                    out.append((name, ln + 1, m.start(), m.end(), new, s[:90]))
    return out


def corpora(n_svc, n_host):
    import random as r
    svc = []
    rng = r.Random(101)
    for i in range(n_svc):
        sc = sp.story(rng) if i % 3 == 0 else sp.gen(rng)
        svc.append((sc, sp.python_trace(sc)))
    host = []
    rng = r.Random(202)
    for i in range(n_host):
        sc = hp.gen(rng)
        try:
            host.append((sc, hp.norm(hp.python_trace(sc))))
        except Exception:
            pass
    return svc, host


def rust_all(cli, svc, host):
    """Run the whole corpus through ONE --batch process; returns True when every Rust answer equals the oracle."""
    lines, expect = [], []
    for sc, py in svc:
        events, sizes = sp.to_rust_events(sc)
        lines.append(json.dumps({"has_credentials": sc["has_credentials"], "events": events}))
        expect.append(("svc", sizes, py))
    for sc, py in host:
        lines.append(json.dumps({"kind": "host", "events": sc["events"]}))
        expect.append(("host", None, py))
    p = subprocess.run([cli, "--batch"], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=600)
    got = p.stdout.strip().split("\n")
    if len(got) != len(lines):
        return False
    for ans, (kind, sizes, py) in zip(got, expect):
        r = json.loads(ans)
        if "declined" in r:
            continue
        if kind == "svc":
            merged, i = [], 0
            for n in sizes:
                merged.append([a for part in r["trace"][i:i + n] for a in part])
                i += n
            if {"trace": merged, "state": r["state"]} != py:
                return False
        elif hp.norm(r) != py:
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svc", type=int, default=700)
    ap.add_argument("--host", type=int, default=1200)
    ap.add_argument("--only")
    ap.add_argument("--file", default="")
    a = ap.parse_args()
    if WORK.exists():
        shutil.rmtree(WORK)
    shutil.copytree(CRATE, WORK / "crate")
    (WORK / "crate" / "Cargo.toml").write_text((CRATE / "Cargo.toml").read_text() + "\n[workspace]\n")
    shutil.copy(ROOT / "rust/Cargo.lock", WORK / "crate" / "Cargo.lock")
    svc, host = corpora(a.svc, a.host)
    print(f"corpus: {len(svc)} service + {len(host)} host scenarios (oracle = the real Python)", flush=True)
    cli = os.path.join(TARGET, "release", "singws-karafun-svc")

    def build():
        return subprocess.run(["cargo", "build", "--release", "--offline", "--quiet", "--manifest-path", str(WORK / "crate" / "Cargo.toml")], env=ENV, capture_output=True, text=True).returncode == 0

    assert build() and rust_all(cli, svc, host), "the unmutated crate must equal the Python first"
    only = {int(x) for x in a.only.split(",")} if a.only else None
    muts = []
    for fname in ("lib.rs", "host.rs"):
        if a.file and a.file != fname:
            continue
        muts += generate(fname, (CRATE / "src" / fname).read_text())
    print(f"mutants: {len(muts)}", flush=True)
    killed = documented = 0
    undocumented = []
    for n, (fname, ln, s, e, new, snippet) in enumerate(muts, 1):
        if only and n not in only:
            continue
        original = (CRATE / "src" / fname).read_text()
        lines = original.split("\n")
        line = lines[ln - 1]
        lines[ln - 1] = line[:s] + new + line[e:]
        (WORK / "crate" / "src" / fname).write_text("\n".join(lines))
        key = f"{fname}|{snippet[:70]}|{line[s:e]}->{new}"
        try:
            ok_build = build()
            if not ok_build:
                killed += 1
                print(f"#{n} {fname}:{ln} does not compile", flush=True)
                continue
            if not rust_all(cli, svc, host):
                killed += 1
                print(f"#{n} {fname}:{ln} {line[s:e]!r}->{new!r}: killed", flush=True)
            elif key in NOTES:
                documented += 1
                print(f"#{n} {fname}:{ln} SURVIVED {NOTES[key][:90]}", flush=True)
            else:
                undocumented.append(n)
                print(f"#{n} {fname}:{ln} {line[s:e]!r}->{new!r}: SURVIVED (UNDOCUMENTED) key={key!r}", flush=True)
        finally:
            (WORK / "crate" / "src" / fname).write_text(original)
    print(f"killed={killed} documented={documented} undocumented survivors={len(undocumented)} {undocumented}")
    return 1 if undocumented else 0


if __name__ == "__main__":
    sys.exit(main())
