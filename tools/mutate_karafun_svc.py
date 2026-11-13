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
import karafun_credentials_parity as cp  # noqa: E402
import karafun_match_parity as mp  # noqa: E402
import karafun_top100_parity as tp  # noqa: E402
import karafun_host_parity as hp  # noqa: E402
import karafun_service_parity as sp  # noqa: E402

CRATE = ROOT / "rust/crates/singws-karafun-svc"
WORK = Path(os.path.expanduser("~/.cache/singws-karafun-svc-mut"))
TARGET = os.path.expanduser("~/.cache/singws-rust-target-karafun-svc-mut2")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET, PATH=os.path.expanduser("~/.cargo/bin") + ":" + os.environ["PATH"])

# "<file>|<snippet[:70]>|<old>-><new>" -> "equivalent: why" | "GAP: what is missing"
NOTES: dict = {
    'top100.rs|let mut buf = [0u16; 2];|2->3': 'equivalent: the UTF-16 scratch buffer is larger than any character needs (2 units suffice)',
    'top100.rs|let force = req.get("force").and_then(|x| x.as_bool()).unwrap_or(false|false->true': 'equivalent: the harness always supplies force; the default is for a malformed request only',
    'top100.rs|let now = req.get("now").and_then(|x| x.as_f64()).unwrap_or(0.0);|0.0->1.0': 'equivalent: the harness always supplies now',
    'top100.rs|let last = req.get("last").and_then(|x| x.as_f64()).unwrap_or(0.0);|0.0->1.0': 'equivalent: the harness always supplies last',
    'top100.rs|Some(a) if a.len() == 2 => (a[0].as_bool().unwrap_or(false), a[1].as_s|false->true': "equivalent: the harness always supplies the sender's bool",
    'top100.rs|let deliveries: Vec<(f64, &Value)> = req.get("deliveries").and_then(|d|0.0->1.0': 'equivalent: the harness always supplies a delivery time',
    'top100.rs|if !(text.contains("still starting") || songs.is_some() || error.is_so|||->&&': 'equivalent: once a chart answer or an error is stored `done` is set and no later message is processed, so only the first term of the condition can matter',
    'top100.rs|while !done && now - 0.0 < timeout {|-->+': "equivalent: subtracting 0.0 (the loop's start time) changes nothing",
    'matching.rs|let mut in_sep = false;|false->true': 'equivalent: a leading separator is dropped by every caller (words trims spaces, terms trims spaces)',
    'matching.rs|same.sort_by_key(|&i| if words(list[i].title) == want_words { 0 } else|1->2': 'equivalent: only the order 0 before non-0 matters in the stable sort, not the second weight',
    'credentials.rs|if stored.iter().any(|&b| b < 0x20 || b >= 0x7f) {|>=->>': 'equivalent: a DEL byte (0x7f) is neither a hex digit nor whitespace, so the hex parse rejects it anyway',
    'credentials.rs|if i + 1 >= raw.len() || !raw[i].is_ascii_hexdigit() || !raw[i + 1].is|||->&&': 'equivalent: a non-hex digit in a pair is also rejected by from_str_radix(...).ok()?, so the explicit digit tests are redundant',
    'host.rs|if position > 0.0 {|>->>=': "equivalent: last_position Some(0.0) and None both resume at 0.0 (recover reads `last or 0.0`)",
    'lib.rs|Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),|true->false': "equivalent: serde_json's as_f64 is always Some for a JSON number, so the default is never used",
    'lib.rs|reload_at: 0.0,|0.0->1.0': 'equivalent: reload_at is written (with reload_after_login) before it is ever read',
    'lib.rs|ready_told: false,|false->true': 'equivalent: the state starts as idle, so the announce test (!ready_told || state != ready) is true either way',
    'lib.rs|awaiting_login: false,|false->true': 'equivalent: awaiting_login is read only by SavedLogin, which the driver sends only after a ReadSavedLogin set it',
    'lib.rs|self.awaiting_login = false;|false->true': 'equivalent: awaiting_login is set again by the next ReadSavedLogin before SavedLogin can read it',
    'lib.rs|self.ready_told = false;|false->true': 'equivalent: the reload sets the state to starting, so the announce test is true regardless of ready_told',
    'lib.rs|self.ready_told = false;|false->true': 'equivalent: the 4th signed-out status sets the state to sign_in, so the announce test is true regardless of ready_told',
    'host.rs|Some(Value::Number(n)) => Ok(n.as_f64().unwrap_or(0.0)),|0.0->1.0': "equivalent: serde_json's as_f64 is always Some for a JSON number",
    'host.rs|gens: 0,|0->1': 'equivalent: the generation counter is only compared for equality, so its origin does not matter',
    'host.rs|self.gens += 1;|1->2': 'equivalent: the generation counter is only compared for equality, so its step does not matter',
    'host.rs|self.key_current = Some(0);|0->1': "equivalent: start's key_current is overwritten by started() before recover (which needs heard) can read it",
    'host.rs|self.tempo_current = Some(100);|100->101': "equivalent: start's tempo_current is overwritten by started() before recover (which needs heard) can read it",
    'host.rs|let ok = self.active(w) && self.asked.as_ref().map(|a| a.heard).unwrap|false->true': 'equivalent: active() already requires asked.is_some(), so the unwrap_or default is never used',
    'host.rs|let ok = self.active(w) && self.asked.as_ref().map(|a| a.heard).unwrap|false->true': 'equivalent: active() already requires asked.is_some(), so the unwrap_or default is never used',
    'host.rs|let resume = self.asked.as_mut().unwrap().resume_at.take().unwrap_or(0|0.0->1.0': 'equivalent: resume_at is None only if the song was never recovered, and a resume of 1.0 is not > 3.0, so no seek either way',
}

PATTERNS = [(re.compile(p), r) for p, r in [
    (r"==", "!="), (r"!=", "=="), (r"<=", "<"), (r">=", ">"), (r"(?<![<=!>\-])<(?![<=])", "<="), (r"(?<![\-=<>])>(?![>=])", ">="),
    (r"&&", "||"), (r"\|\|", "&&"), (r"(?<=\s)\+(?=\s)", "-"), (r"(?<=\s)-(?=\s)", "+"), (r"\btrue\b", "false"), (r"\bfalse\b", "true"),
    (r"(?<![\w.\"\\])\d+(\.\d+)?(?![\w.])", None), (r"(?<=if )!", ""), (r"(?<=&& )!", ""), (r"(?<=\|\| )!", "")]]


def blank_strings(line):
    return re.sub(r'"(\\.|[^"\\])*"', lambda m: '"' + "\x00" * (len(m.group(0)) - 2) + '"', line)


def generate(name, text):
    lines = text.split("\n")
    stop = next((i for i, l in enumerate(lines) if l.startswith(("/// Replay request", "pub fn handle", "#[cfg(target_os"))), len(lines))
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
        sc = hp.storm(rng) if i % 4 == 0 else (hp.zero_clock(rng) if i % 7 == 1 else hp.gen(rng))
        try:
            host.append((sc, hp.norm(hp.python_trace(sc))))
        except Exception:
            pass
    return svc, host


BASE_DECLINED = None


def match_corpus(n):
    rng = random.Random(404)
    reqs = [mp.request(rng) for _ in range(n)]
    out = subprocess.run(["node", str(ROOT / "tools" / "karafun_match_oracle.js")], input="\n".join(json.dumps(r) for r in reqs) + "\n", capture_output=True, text=True, timeout=600).stdout.strip().split("\n")
    return [(dict(r, kind="match"), json.loads(o)) for r, o in zip(reqs, out)]


def top_corpus(n):
    rng = random.Random(505)
    out = []
    for _ in range(n):
        req, kind = tp.gen(rng)
        out.append((dict(req, kind="top100"), tp.python_answer(req, kind)))
    return out


def rust_all(cli, svc, host, brw=(), cred=(), mtc=()):
    """Run the whole corpus through ONE --batch process; returns True when every Rust answer equals the oracle."""
    lines, expect = [], []
    for sc, py in svc:
        events, sizes = sp.to_rust_events(sc)
        lines.append(json.dumps({"has_credentials": sc["has_credentials"], "events": events}))
        expect.append(("svc", sizes, py))
    for sc, py in host:
        lines.append(json.dumps({"kind": "host", "events": sc["events"]}))
        expect.append(("host", None, py))
    for sc, py in brw:
        lines.append(json.dumps(dict(sc, kind="browser", op="ensure")))
        expect.append(("brw", None, py))
    for req, exp in cred:
        lines.append(json.dumps(dict(req, kind="cred")))
        expect.append(("cred", None, exp))
    for req, exp in mtc:
        lines.append(json.dumps(req))
        expect.append(("mtc", None, exp))
    try:
        p = subprocess.run([cli, "--batch"], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False       # a mutant that loops forever is killed
    global BASE_DECLINED
    got = p.stdout.strip().split("\n")
    if len(got) != len(lines):
        return False
    declined = {i for i, a in enumerate(got) if "declined" in json.loads(a)}
    if BASE_DECLINED is None:
        BASE_DECLINED = declined
    elif declined != BASE_DECLINED:
        return False       # a mutant that declines (or stops declining) where the original did not
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
        elif kind == "mtc":
            if r != py:
                return False
        elif kind == "cred":
            if r != py:
                return False
        elif kind == "brw":
            if r != py:
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
    shutil.copytree(CRATE, WORK / "crate", copy_function=shutil.copyfile)  # fresh mtimes: copy2 kept old ones and cargo reused a stale mutant build
    (WORK / "crate" / "Cargo.toml").write_text((CRATE / "Cargo.toml").read_text() + "\n[workspace]\n")
    shutil.copy(ROOT / "rust/Cargo.lock", WORK / "crate" / "Cargo.lock")
    svc, host = corpora(a.svc, a.host)
    brw = []   # the browser model is gone (Chromium is fetched by the display program now)
    cred = cp.oracle_cases() if a.file in ("", "credentials.rs") else []
    mtc = match_corpus(6000) if a.file in ("", "matching.rs") else []
    if a.file in ("", "top100.rs"):
        mtc = mtc + top_corpus(4000)
    print(f"corpus: {len(svc)} service + {len(host)} host scenarios (oracle = the real Python)", flush=True)
    cli = os.path.join(TARGET, "release", "singws-karafun-svc")

    def build():
        return subprocess.run(["cargo", "build", "--release", "--offline", "--quiet", "--manifest-path", str(WORK / "crate" / "Cargo.toml")], env=ENV, capture_output=True, text=True).returncode == 0

    assert build() and rust_all(cli, svc, host, brw, cred, mtc), "the unmutated crate must equal the Python first"
    only = {int(x) for x in a.only.split(",")} if a.only else None
    ORIG = {f: (CRATE / "src" / f).read_text() for f in ("lib.rs", "host.rs", "credentials.rs", "matching.rs", "top100.rs")}  # snapshot: later edits of the repo must not shift the line numbers
    muts = []
    for fname in ("lib.rs", "host.rs", "credentials.rs", "matching.rs", "top100.rs"):
        if a.file and a.file != fname:
            continue
        muts += generate(fname, ORIG[fname])
    print(f"mutants: {len(muts)}", flush=True)
    killed = documented = 0
    undocumented = []
    for n, (fname, ln, s, e, new, snippet) in enumerate(muts, 1):
        if only and n not in only:
            continue
        original = ORIG[fname]
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
            if not rust_all(cli, svc, host, brw, cred, mtc):
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
