#!/usr/bin/env python3
"""Mutation test for the QUICK-update branch of the Rust library scan (rust/crates/singws-scan/src/quick.rs). Every mutant must be caught by the crate's unit tests OR by the
differential test against the app's own Python (tools/scan_parity.py --quick-cases, a fixed-seed run). Mutants are generated mechanically (operators, constants, boolean flips,
the same generator as tools/mutate_karafun.py). A survivor must carry a note in NOTES: `equivalent: <why>` or `GAP: <what the corpus lacks>`.

Run it ALONE (it rewrites quick.rs and rebuilds per mutant).
  python3 tools/mutate_scan_quick.py [--only 3,7]      CARGO_TARGET_DIR defaults to ~/.cache/singws-rust-target-scan
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import mutate_karafun as mk  # noqa: E402  (generate)

SRC = ROOT / "rust/crates/singws-scan/src"
TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-scan"))
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
ENV["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + ENV["PATH"]
FILE = "quick.rs"

# "snippet[:70]|old->new" -> "equivalent: why" | "GAP: what is missing"
NOTES: dict = {
    'if o.get("path").map(truthy).unwrap_or(false) {|false->true': 'equivalent: an old entry without a path would be stored under an empty key, and the dedupe at the end drops every entry without a path',
    'let new0 = sig[0].as_i64().unwrap_or(0) as i128;|0->1': 'equivalent: a folder signature built from a stat always holds integers, so the default is never read',
    'let new1 = sig[1].as_i64().unwrap_or(0) as i128;|0->1': 'equivalent: a folder signature built from a stat always holds integers, so the default is never read',
    'if no_dir_cache && c.baseline_ns != 0 && new0 <= c.baseline_ns {|0->1': 'equivalent: with a baseline of 0 the comparison `new0 <= 0` is false for every real folder time, so testing `!= 1` instead of `!= 0` changes nothing',
    'if lower.ends_with(".mp4") || lower.ends_with(".cdg") || lower.ends_wi|||->&&': 'equivalent on this file system: macOS refuses file names that are not valid UTF-8, so the decline branch cannot be reached by a media file name',
    'let is_dir = e.file_type().map(|t| t.is_dir()).unwrap_or(false);|false->true': 'equivalent: the file type of a directory entry only fails when the entry vanished mid-listing, which the fuzz cannot time',
    'let Some(Value::Object(o)) = old else { return Ok((Value::Null, false,|false->true': 'equivalent: the reuse branch is only entered when the old track is an object, so the else branch cannot be reached',
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
            if run(["cargo", "build", "--release", "-p", "singws-scan", "--quiet"]).returncode != 0:
                killed += 1
                print(f"#{n} L{ln}: does not compile", flush=True)
                continue
            if run(["cargo", "test", "--release", "-p", "singws-scan", "--quiet"]).returncode != 0:
                killed += 1
                print(f"#{n} L{ln} {line[s:e]!r}->{new!r}: killed by the crate tests", flush=True)
                continue
            d = subprocess.run([sys.executable, str(ROOT / "tools/scan_parity.py"), "--cases", "0", "--index-cases", "0", "--quick-cases", "150", "--seed", "5"],
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
        run(["cargo", "build", "--release", "-p", "singws-scan", "--quiet"])
    print(f"mutants={len(muts) if only is None else len(only)} killed={killed} documented={equivalent} undocumented survivors={len(survivors)}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
