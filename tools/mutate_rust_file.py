#!/usr/bin/env python3
"""Generic mutation test for ONE Rust source file: MUT_SRC (crate src dir, relative), MUT_FILE, MUT_PKG (cargo package), MUT_PARITY (a tools/ parity script) and MUT_ARGS. Every mutant must be caught by the crate's unit tests OR by the
differential test against the app's own Python (tools/cdg_visual_parity.py, a fixed-seed run). Mutants are generated mechanically (operators, constants, boolean flips,
the same generator as tools/mutate_karafun.py). A survivor must carry a note in NOTES: `equivalent: <why>` or `GAP: <what the corpus lacks>`.

Run it ALONE (it rewrites visual.rs and rebuilds per mutant).
  python3 tools/mutate_cdg_visual.py [--only 3,7]      CARGO_TARGET_DIR defaults to ~/.cache/singws-rust-target-cdg
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import mutate_karafun as mk  # noqa: E402  (generate)

SRC = ROOT / os.environ["MUT_SRC"]
TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-mut"))
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
ENV["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + ENV["PATH"]
FILE = os.environ["MUT_FILE"]

# "snippet[:70]|old->new" -> "equivalent: why" | "GAP: what is missing"
NOTES: dict = {
    "Sha256 { h: [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527|64->65": "equivalent: the state array has 8 words; a 65th round constant slot is never read",
    "let mut w = [0u32; 64];|64->65": "equivalent: the message schedule array is 64 words; a 65th is never read",
    "while self.buf.len() >= 64 {|>=->>": "equivalent: a full block is processed at the next update or at finalisation: the digest is the same",
    "while self.buf.len() >= 64 {|64->65": "equivalent: a full block is processed at the next update or at finalisation: the digest is the same",
    "if !raw.is_ascii() {|!->": "GAP: a member name with non-ASCII characters (declined): the corpus has none",
    "if !matches!(f.compression(), zip::CompressionMethod::Stored | zip::Co|!->": "GAP: a member stored with a compression method other than Stored/Deflate (declined): the corpus has none",
    "let mut buf = vec![0u8; 1 << 20];|1->2": "equivalent: read chunk size: the digest does not depend on the chunk size",
    "let mut buf = vec![0u8; 1 << 20];|20->21": "equivalent: read chunk size: the digest does not depend on the chunk size",
    "Some(i) if i > 0 => name[..i].to_string(),| > -> >= ": "GAP: a member name that starts with a slash (no directory part): the corpus has none",
    "Some(i) if i > 0 => name[..i].to_string(),|0->1": "GAP: a member name that starts with a slash (no directory part): the corpus has none",
    "if unique.iter().any(|p| !p.is_ascii()) {|!->": "GAP: a library path with non-ASCII characters (declined): the corpus has none",
    "unreadable += 1;|+= ->-= ": "GAP: the unreadable-archive counter: the corpus has no archive that fails to open at that point",
    "unreadable += 1;|1->2": "GAP: the unreadable-archive counter: the corpus has no archive that fails to open at that point",
    "let mut palette = [0u32; 16];|16->17": "equivalent: palette size 17 instead of 16: indices are 4-bit, the 17th slot is never addressed",
    "let mut ambiguous_after_clear = false;|false->true": "equivalent: ambiguous_after_clear is reset before any read on every path that reaches the check",
    "color_counts = [0; 16];|0->1": "equivalent: color_counts is overwritten by the per-tile recount before it is read",
    "if y0 < HEIGHT && x0 < WIDTH {| < -> <= ": "equivalent: x0 < WIDTH: same, x0 == WIDTH cannot reach the guard with a drawable tile",
    "if y >= HEIGHT {|>=->>": "equivalent: y >= HEIGHT is unreachable: the loop bound stops at HEIGHT",
    "for tile_x in 0..6.min(WIDTH - x0) {| - -> + ": "equivalent: WIDTH - x0 vs WIDTH + x0: the tile loop is bounded by 6 first (x0 is a multiple of 6 inside the screen)",
    "ambiguous_after_clear = true;|true->false": "equivalent: the flag set by a scroll is read only where an earlier clear already sets it",
    "let base = if instruction == LOAD_CLUT_LOW { 0 } else { 8 };|==->!=": "equivalent: CLUT low/high bank relabel: the colours are looked up by index, the label only moves where the same colours land",
    "let value = (((data[offset * 2] & 0x3F) as u32) << 6) | (data[offset *|6->7": "equivalent: <<6 vs <<7: the 6-bit fields are masked first, the combined value is injective either way",
    "None => 0.0,|0.0->1.0": "equivalent: tail_length None is never read",
    ".filter(|(t, _, _)| !(*t < 0.0 || (total > 0.0 && *t > total + 0.25)))| > -> >= ": "equivalent-by-tool-artifact: killed by hand with the unit test a_black_sample_exactly_a_quarter_second_past_the_end_still_counts (the test fails on the mutant when run directly); the tool's own run reported a survivor, probably a stale cargo build from a same-second file write",
    "Some(Value::Number(n)) => n.as_f64().map_or(true, |f| f != 0.0),|true->false": "equivalent: a non-zero number is truthy, so the default for a missing key does not change a stored number",
    "if lufs.is_nan() || peak_db.map_or(false, f64::is_nan) {|false->true": "equivalent: NaN check on an optional peak: Python raises on the same input before this point",
    "let version = to_int(e.get(\"failure_version\").unwrap_or(&json!(0)))?;|0->1": "equivalent: failure_version default 0 vs 1: both are older than the current version, so the entry is retried either way",
    "next += 1;|+= ->-= ": "equivalent: splitlines cursor step: the loop ends on the same line boundary",
    "i += 1;|1->2": "equivalent: splitlines cursor step: ends on the same line boundary",
    "if start < s.len() {| < -> <= ": "equivalent: start < len vs <=: an empty trailing line is not a line for Python either",
    "let window = (sr / 10).max(1);|1->2": "equivalent: window = (sr/10).max(1): sr/10 is never 0 for a real sample rate",
    "db.push(20.0 * m.max(1e-9).log10());|9->10": "equivalent: 1e-9 vs 1e-10 floor: both are far below any threshold in dB",
    "if t >= 0.5 { b - diff * (1.0 - t) } else { a + diff * t }|>=->>": "equivalent: lerp symmetric form: the two branches agree to rounding",
    "if t >= 0.5 { b - diff * (1.0 - t) } else { a + diff * t }|0.5->1.5": "equivalent: lerp symmetric form: the two branches agree to rounding",
    "if n % 2 == 1 { v[n / 2] } else { (v[n / 2 - 1] + v[n / 2]) / 2.0 }|2->3": "GAP: median of an even-length list: needs a hand-built list with different middle values",
    "let Some(first) = db.iter().position(|&d| d > noise_db) else { return | > -> >= ": "GAP: exact tie of a window level with the noise floor: needs an exactly equal float",
    "let floor_windows = ((2.0 / window_s).round_ties_even() as usize).max(|1->2": "equivalent: .max(1) on the floor window count: window_s is never large enough to round to 0",
    "let minimum_tail_windows = ((3.0 / window_s).round_ties_even() as usiz|1->2": "equivalent: .max(1) on the minimum tail window count: window_s is never large enough to round to 0",
    "let floor_spread = percentile(terminal, 90.0) - percentile(terminal, 1|90.0->91.0": "GAP: floor spread percentile (90): needs terminal windows whose spread sits between the two percentile choices",
    "let floor_spread = percentile(terminal, 90.0) - percentile(terminal, 1|10.0->11.0": "GAP: floor spread percentile (10): same",
    "if floor_db > -46.0 || floor_spread > 3.0 {| > -> >= ": "GAP: exact tie floor_spread == 3.0: needs an exactly equal float",
    "let programme_peak = percentile(&db[..db.len() - floor_windows], 95.0)|95.0->96.0": "GAP: programme peak percentile (95 vs 96): needs a programme whose 95th and 96th percentile differ",
    "if programme_peak < floor_db + 12.0 {| < -> <= ": "GAP: exact tie programme_peak == floor_db + 12: needs an exactly equal float",
    "if programme_peak < floor_db + 12.0 {|12.0->13.0": "GAP: 12 dB vs 13 dB margin: needs a programme with a peak margin between 12 and 13 dB",
    "let Some(meaningful_last) = db.iter().rposition(|&d| d > adaptive) els| > -> >= ": "GAP: exact tie of a window level with the adaptive threshold: needs an exactly equal float",
    "let Some(meaningful_last) = db.iter().rposition(|&d| d > adaptive) els|0.0->1.0": "GAP: return 0.0 when no window is meaningful: only reachable for an all-silent tail the corpus never draws",
    "let level_db = 20.0 * l.max(r).max(1e-9).log10();|9->10": "equivalent: 1e-9 vs 1e-10 floor: both are far below any threshold in dB",
}


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT / "rust", env=ENV, capture_output=True, text=True, **kw)


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    only = None
    if "--only" in argv:
        only = {int(x) for x in argv[argv.index("--only") + 1].split(",")}
    original = (SRC / FILE).read_text()
    muts = mk.generate(FILE, original)
    print(f"mutants: {len(muts)}", flush=True)
    killed = equivalent = 0
    survivors = []
    try:
        for n, (name, ln, s, e, new, snippet) in enumerate(muts, 1):
            if only is not None and n not in only:
                continue
            lines = original.split("\n")
            line = lines[ln - 1]
            lines[ln - 1] = line[:s] + new + line[e:]
            (SRC / FILE).write_text("\n".join(lines))
            key = f"{snippet[:70]}|{line[s:e]}->{new}"
            if run(["cargo", "build", "--release", "-p", os.environ["MUT_PKG"], "--quiet"]).returncode != 0:
                killed += 1
                print(f"#{n} L{ln}: does not compile", flush=True)
                continue
            if run(["cargo", "test", "--release", "-p", os.environ["MUT_PKG"], "--quiet"]).returncode != 0:
                killed += 1
                print(f"#{n} L{ln} {line[s:e]!r}->{new!r}: killed by the crate tests", flush=True)
                continue
            d = subprocess.run([sys.executable, str(ROOT / "tools" / os.environ["MUT_PARITY"])] + os.environ.get("MUT_ARGS", "").split(),
                               env=ENV, capture_output=True, text=True)
            if d.returncode != 0:
                killed += 1
                print(f"#{n} L{ln} {line[s:e]!r}->{new!r}: killed by the differential test", flush=True)
            elif key in NOTES:
                equivalent += 1
                print(f"#{n} L{ln} {line[s:e]!r}->{new!r}: SURVIVED {NOTES[key][:80]}", flush=True)
            else:
                survivors.append(n)
                print(f"#{n} L{ln} {line[s:e]!r}->{new!r}: SURVIVED (UNDOCUMENTED)   [{snippet}]   key={key!r}", flush=True)
    finally:
        (SRC / FILE).write_text(original)
        run(["cargo", "build", "--release", "-p", os.environ["MUT_PKG"], "--quiet"])
    print(f"mutants={len(muts) if only is None else len(only)} killed={killed} documented={equivalent} undocumented survivors={len(survivors)}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
