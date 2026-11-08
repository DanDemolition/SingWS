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
    # --- readback.rs ---
    'readback.rs|return Err(chars.get(ds).map_or(false, |c| !c.is_ascii() && c.is_numer|&&->||': 'equivalent: parse_key_tempo declines any text holding a non-ASCII numeric character before this test can matter, so the inner check never decides',
    'readback.rs|return Err(chars.get(ds).map_or(false, |c| !c.is_ascii() && c.is_numer|!->': 'equivalent: parse_key_tempo declines any text holding a non-ASCII numeric character before this test can matter, so the inner check never decides',
    'readback.rs|let n: i64 = chars[ds..i].iter().collect::<String>().parse().unwrap_or|0->1': 'equivalent: at most 18 ASCII digits always parse as i64, so the unwrap_or default is never used',
    'readback.rs|if chars[s..].len() < key.len() || chars[s..s + key.len()] != key[..] | < -> <= ': 'equivalent: a KEY= at the very end is skipped here or fails to find digits right after (both continue)',
    'readback.rs|if i == ws || chars.len() < i + tempo.len() || chars[i..i + tempo.len(| < -> <= ': 'equivalent: a text that ends exactly where TEMPO= would start either skips here or finds no digits after it (both continue)',
    # --- monitor.rs ---
    "monitor.rs|let mut i = 0;|0->1": "equivalent in the monitor: lines are stripped and empty lines dropped afterwards, so an extra or missing empty line cannot change the result",
    "monitor.rs|if c == '\\r' && i + 1 < b.len() && b[i + 1].1 == '\\n' {|==->!=": "equivalent in the monitor: same reason (empty lines dropped, lines stripped)",
    "monitor.rs|if c == '\\r' && i + 1 < b.len() && b[i + 1].1 == '\\n' {|1->2": "equivalent in the monitor: same reason (empty lines dropped, lines stripped)",
    "monitor.rs|next += 1;|+= ->-= ": "equivalent in the monitor: a leftover separator at the start of a line is removed by the strip",
    "monitor.rs|i += 1;|1->2": "equivalent in the monitor: a skipped separator ends up inside the next line and is removed by the strip",
    "monitor.rs|if start < s.len() {| < -> <= ": "equivalent in the monitor: an extra empty last line is dropped",
    "monitor.rs|let started = if started <= 0.0 || started > now + 5.0 { now } else { |<=-><": "equivalent: a clock start of exactly 0.0 is falsy in the app (replaced by now) and never reaches this guard",
    "monitor.rs|idle_stop_count: 0,|0->1": "equivalent: the first poll cannot reach the idle-after-playback branch, so the initial value is overwritten before it is read",
    "monitor.rs|if total > 0 && 0 <= current && current <= total {|<=-><": "equivalent: a normalised clock is at least 1, so current >= 0 and current > 0 agree",
    "monitor.rs|if total > 0 && 0 <= current && current <= total {| > -> >= ": "equivalent: a normalised clock is at least 1, so total > 0 and total >= 0 agree",
    "monitor.rs|if total > 0 && 0 <= current && current <= total {|0->1": "equivalent: a total of 1 can never show progress (current <= total = 1 cannot exceed an earlier current >= 1)",
    "monitor.rs|if total > 0 && 0 <= current && current <= total {|0->1": "equivalent: a normalised clock is at least 1, so current >= 0 and current >= 1 agree",
    "monitor.rs|let best = next_candidates.get(&total).copied().unwrap_or(-1);|1->2": "equivalent: the default only feeds max() with a clock value of at least 1",
    "monitor.rs|let clock_near_end = self.last_clock_remaining.map_or(false, |r| r <= |false->true": "equivalent: last_clock_remaining and last_clock_at are None together, so a default of true on one side is still gated by the other",
    "monitor.rs|let clock_near_end = self.last_clock_remaining.map_or(false, |r| r <= |false->true": "equivalent: last_clock_remaining and last_clock_at are None together, so a default of true on one side is still gated by the other",
    "monitor.rs|let remaining_kt = p.display_times.map_or(0.0, |(a, b)| b - a);|0.0->1.0": "equivalent: with no display times the remaining is 0 or 1, never above the 35 s threshold",
    # key: "<file>|<first 70 chars of the line>|<old>-><new>"
    "lib.rs|let mut out = String::with_capacity(value.len() + 2);|2->3": "equivalent: only a capacity hint of the output string",
    "lib.rs|let mut groups = 0;|0->1": "equivalent: with the counter starting at 1 the empty suffix at the very end matches, which cuts nothing (the text is stripped, so no earlier suffix is only whitespace)",
    "lib.rs|groups += 1;|1->2": "equivalent: the counter is only ever tested for being above zero",
    "lib.rs|return groups > 0 && chars[j..].is_empty();| > -> >= ": "equivalent: a match with no group at all is the empty suffix at the very end, which cuts nothing",
    "pure.rs|let mut it = text.splitn(2, '.');|2->3": "equivalent: a text with two dots reaches None either way (the number test fails, then the clock test fails)",
    "pure.rs|let values: Vec<u128> = parts.iter().map(|p| if p.len() > 30 { u128::M| > -> >= ": "equivalent: overflow guard for absurdly long digit runs; every such value ends as None",
    "pure.rs|let values: Vec<u128> = parts.iter().map(|p| if p.len() > 30 { u128::M|30->31": "equivalent: same overflow guard, only the bound moves",
    "pure.rs|let values: Vec<u128> = parts.iter().map(|p| if p.len() > 30 { u128::M|4->5": "equivalent: same overflow guard, only the 'huge' value moves; it is far above 86400 either way",
    "pure.rs|Value::Number(n) => n.as_i64().unwrap_or_else(|| n.as_f64().unwrap_or(|0.0->1.0": "equivalent: a JSON number always has an f64 value, the fallback never runs",
    "pure.rs|Value::String(s) => s.trim().parse::<f64>().map(|f| f.trunc() as i64).|0->1": "not modelled: Python int() of an unparsable string raises; the app only passes numbers here",
    "pure.rs|_ => 0,|0->1": "not modelled: Python int() of None or a container raises; the app only passes numbers here",
    "pure.rs|Value::Number(n) => n.as_f64().unwrap_or(0.0),|0.0->1.0": "equivalent: a JSON number always has an f64 value, the fallback never runs",
    "ladder.rs|let mut out = String::with_capacity(s.len() + 2);|2->3": "equivalent: only a capacity hint of the output string",
    "ladder.rs|c if (c as u32) < 0x7f => out.push(c),| < -> <= ": "equivalent: 0x7f is caught by the control-character arm above it",
    "ladder.rs|text.contains(\"-1743\") || text.contains(\"not authorized to send apple |&&->||": "see the corpus: partial phrases are in the pool now (killed); kept for line shifts",
    "ladder.rs|if text.is_empty() || text.contains(\"-1743\") || text.contains(\"-25211\"|||->&&": "equivalent in the ladder: a permission error raises before this check and last_error is never empty",
    "ladder.rs|return false;|false->true": "equivalent in the ladder: last_error is never empty and permission errors raise before this check",
    "ladder.rs|let mut ok = false;|false->true": "equivalent: ok is always assigned by the first try before it is read",
    "ladder.rs|if parts.len() < 3 || ![\"FOUND\", \"TITLE_ONLY\", \"FIRST\"].contains(&part|||->&&": "equivalent: found is either empty or a valid accepted answer, so both halves agree",
    "pure.rs|_ => 0.0,|0.0->1.0": "not modelled: a string seek offset (Python float() would parse it); the app only passes numbers or None",
    "pure.rs|let mut in_space = false;|false->true": "equivalent: the whitespace collapse runs after the regex that already turned every non-alphanumeric run into one space, and the result is stripped",
    "pure.rs|in_space = true;|true->false": "equivalent: same reason, no run of spaces can reach the collapse",
}


FILES = ("lib.rs", "pure.rs", "ladder.rs", "monitor.rs", "readback.rs", "watchdog.rs", "startflow.rs", "flow.rs", "geometry.rs")


def generate(name, text):
    lines = text.split("\n")
    first = next(i for i, l in enumerate(lines) if l.startswith(("fn ", "pub(crate) fn ", "pub fn ", "pub enum ", "/// A value", "use ", "pub enum")))
    last = next((i for i, l in enumerate(lines) if l.startswith("#[cfg(test)]")), len(lines))
    out = []
    for ln in range(first, last):
        line = lines[ln]
        if line.strip().startswith(("//", "///", "use ", "static ")) or ("fn " in line and "{" in line and "(" in line and "if " not in line and "match" not in line):
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
                        out.append((name, ln + 1, s, e, new, line.strip()[:90]))
    return out


def setup():
    (WORK / "src").mkdir(parents=True, exist_ok=True)
    (WORK / "Cargo.toml").write_text(__import__("re").sub(r"# A live runner.*?\[dependencies\]\n", "[dependencies]\n", (REAL / "Cargo.toml").read_text(), flags=__import__("re").S).replace('singws-mac = { path = "../singws-mac", optional = true }\n', "") + "\n[workspace]\n")
    shutil.copy(ROOT / "rust/Cargo.lock", WORK / "Cargo.lock")
    for name in ("main.rs", "script_lines.rs", "casefold_table.rs", "builders.rs", "lib.rs", "pure.rs", "ladder.rs", "monitor.rs", "readback.rs", "watchdog.rs", "startflow.rs", "flow.rs", "geometry.rs"):
        shutil.copy(REAL / "src" / name, WORK / "src" / name)


def main():
    import karafun_script_parity as par
    release = Path(os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun"))
    real_run = par.run_rust
    captured = []

    def spy(binary, batch):
        resp = real_run(binary, batch)
        captured.append((batch, resp))
        return resp
    par.run_rust = spy
    assert par.main(["--seeds", str(SEEDS), "--binary", str(release)]) == 0, "the unmutated crate must equal the Python first"
    import karafun_ladder_parity as lp
    lp.run_rust = spy
    assert lp.main(["--seeds", str(SEEDS * 4), "--binary", str(release)]) == 0, "the ladder must equal the Python first"
    import karafun_monitor_parity as mpar
    mpar.run_rust = spy
    assert mpar.main(["--seeds", str(SEEDS * 5), "--binary", str(release)]) == 0, "the monitor must equal the Python first"
    import karafun_watchdog_parity as wdp
    wdp.mp.run_rust = spy
    assert wdp.main(["--seeds", str(SEEDS * 5), "--binary", str(release)]) == 0, "the watchdog must equal the Python first"
    import karafun_startflow_parity as sfp
    sfp.run_rust = spy
    assert sfp.main(["--binary", str(release)]) == 0, "startflow must equal the Python first"
    import karafun_flow_parity as flp
    flp.run_rust = spy
    assert flp.main(["--seeds", str(SEEDS * 3), "--binary", str(release)]) == 0, "the post-ladder flow must equal the Python first"
    import karafun_geometry_parity as gp
    gp.run_rust = spy
    assert gp.main(["--seeds", str(SEEDS), "--binary", str(release)]) == 0, "the geometry must equal the Python first"
    print(f"oracle ready: {sum(len(b) for b, _ in captured)} requests in {len(captured)} batches", flush=True)
    setup()
    originals = {n: (WORK / "src" / n).read_text() for n in FILES}
    muts = [m for n in FILES for m in generate(n, originals[n])]
    print(f"mutants: {len(muts)}", flush=True)
    binary = Path(TARGET) / "debug" / "singws-karafun"
    payloads = [(json.dumps({"batch": b}).encode(), r) for b, r in captured]

    def build():
        return subprocess.run(["cargo", "build"], capture_output=True, text=True, env=ENV, cwd=WORK)

    def agrees(report=False):
        for payload, resp in payloads:
            try:
                proc = subprocess.run([str(binary)], input=payload, capture_output=True, timeout=60)
            except subprocess.TimeoutExpired:
                return False  # a mutant that hangs is killed
            if proc.returncode != 0 or json.loads(proc.stdout) != resp:
                if report:
                    got = json.loads(proc.stdout) if proc.returncode == 0 else None
                    reqs = json.loads(payload)["batch"]
                    for q, a, b in zip(reqs, resp, got or []):
                        if a != b:
                            print("BASELINE DIFFERENCE:", json.dumps(q)[:300], "expected", json.dumps(a)[:200], "got", json.dumps(b)[:200], flush=True)
                            break
                    else:
                        print("BASELINE: exit", proc.returncode, flush=True)
                return False
        return True

    base = build()
    assert base.returncode == 0, base.stderr[-600:]
    assert agrees(True), "the unmutated crate must agree with its own baseline"
    survivors, noncompiling, killed = [], [], 0
    try:
        for n, (name, ln, s, e, new, snippet) in enumerate(muts, 1):
            if ONLY and n not in ONLY:
                continue
            src = WORK / "src" / name
            lines = originals[name].split("\n")
            old = lines[ln - 1][s:e]
            lines[ln - 1] = lines[ln - 1][:s] + new + lines[ln - 1][e:]
            src.write_text("\n".join(lines))
            b = build()
            if b.returncode != 0:
                noncompiling.append(n)
                print(f"#{n} {name} L{ln}: does not compile", flush=True)
                src.write_text(originals[name])
                continue
            ok = agrees()
            src.write_text(originals[name])
            if ok:
                key = f"{name}|{snippet[:70]}|{old}->{new}"
                note = SURVIVOR_NOTES.get(key)
                survivors.append((n, key, note, snippet))
                print(f"#{n} {name} L{ln} '{old}'->'{new}': SURVIVED {note or '(UNDOCUMENTED)'}   [{snippet}]", flush=True)
            else:
                killed += 1
                print(f"#{n} {name} L{ln} '{old}'->'{new}': killed", flush=True)
    finally:
        for name, text in originals.items():
            (WORK / "src" / name).write_text(text)
    undocumented = [x for x in survivors if not x[2]]
    print(f"mutants={len(muts)} killed={killed} survivors={len(survivors)} undocumented={len(undocumented)} non-compiling={len(noncompiling)}")
    for n, key, note, snippet in undocumented:
        print(f"  UNDOCUMENTED #{n} {key} [{snippet}]")
    return 1 if undocumented else 0


if __name__ == "__main__":
    sys.exit(main())
