#!/usr/bin/env python3
"""Refuse to package a Rust library that is older than the Rust source it is built from.

The libraries under native/singws_*/<arch>/ are git-ignored build outputs (rust/build_dsp.sh, build_bgm.sh, build_video.sh). The installer specs bundle whatever
is there, so a forgotten rebuild ships a stale library: 1.0.2.4 shipped a 2026-10-26 libsingws_dsp_ffi.dylib that did not know the newer start ops, and every
START-SHADOW call on the Intel show failed with code=-1. For each library this takes its crate plus every local crate it depends on (cargo tree), finds the
newest git commit that touched them, and fails when the library file is older than that commit (or when the library is missing and --require is given).

    python3 tools/check_native_libs_fresh.py [--arch arm64|x86_64|all] [--require]
Exit 0 = the DSP library is current; 1 = it is stale (or missing with --require). The bgm and video libraries only warn."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# library -> (crate, build script that refreshes it, fatal). Only the DSP library is fatal: the app calls newer ops in it every start. The bgm and video libraries
# only WARN, because a commit that touched just their tests or a prototype path also trips the date test, and rebuilding them changes shipped audio/video code
# that has run on real shows; rebuild those deliberately.
LIBS = {
    "native/singws_dsp/{arch}/libsingws_dsp_ffi.dylib": ("singws-dsp-ffi", "rust/build_dsp.sh", True),
    "native/singws_bgm/{arch}/libsingws_bgm_ffi.dylib": ("singws-bgm-ffi", "rust/build_bgm.sh", False),
    "native/singws_video/{arch}/libsingws_video.dylib": ("singws-video", "rust/build_video.sh", False),
}
ARCHS = ("arm64", "x86_64")


def local_crates(crate):
    out = subprocess.run(["cargo", "tree", "-p", crate, "--prefix", "none", "--offline"], cwd=ROOT / "rust", capture_output=True, text=True)
    names = {line.split()[0] for line in out.stdout.splitlines() if "(" + str(ROOT) in line}
    return names or {crate}


def newest_commit(crates):
    paths = [f"rust/crates/{c}" for c in sorted(crates) if (ROOT / "rust" / "crates" / c).is_dir()]
    out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", *paths], cwd=ROOT, capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


def check(archs, require):
    stale, warn = [], []
    for template, (crate, script, fatal) in LIBS.items():
        newest = None
        for arch in archs:
            lib = ROOT / template.format(arch=arch)
            if not lib.is_file():
                if require:
                    (stale if fatal else warn).append(f"MISSING {lib.relative_to(ROOT)}  (run {script})")
                continue
            newest = newest if newest is not None else newest_commit(local_crates(crate))
            if int(lib.stat().st_mtime) < newest:
                (stale if fatal else warn).append(f"{'STALE  ' if fatal else 'WARN   '} {lib.relative_to(ROOT)}  is older than the newest commit to {crate} or its local dependencies  (run {script})")
    return stale, warn


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arch", default="all", choices=["arm64", "x86_64", "all"])
    ap.add_argument("--require", action="store_true", help="a missing library is also a failure")
    args = ap.parse_args()
    problems, warnings = check(ARCHS if args.arch == "all" else (args.arch,), args.require)
    for p in warnings + problems:
        print(p)
    print("native libraries: " + ("STALE" if problems else "current"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
