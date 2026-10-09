#!/usr/bin/env python3
"""Parity study: libmpv analysis vs the Rust helper on the SAME real files, for every kind of result the app uses.

READ-ONLY with respect to show data (the loudness cache is only read for a list of existing file paths). Needs the app's
libmpv runtime, so run it with the app's venv:

    .venv/bin/python tools/rust_analysis_parity.py [--limit 60] [--cache ~/SingWS/loudness.json] [--bin PATH]

Kinds compared (what libmpv_media_jobs.LoudnessSession returns -> what rust_analysis.RustAnalysisSession returns):
  * karaoke : (lufs, peak, duration, audio_start, audio_end)      measure_karaoke_transition
  * bgm     : (lufs, peak, envelope[100 ms windows])              measure_transition
It prints per-quantity agreement rates and the worst outliers so the ``RUST_*_VERIFIED`` flags in rust_analysis.py
are set from evidence, not hope.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import rust_analysis as ra  # noqa: E402


def pick_files(cache: Path, limit: int):
    data = json.loads(cache.read_text())
    files = []
    for path, rec in sorted(data.items()):
        if not isinstance(rec, dict) or rec.get("mode") != "full":
            continue
        if not path.lower().endswith((".mp3", ".mp4", ".m4a", ".flac", ".wav")) or not os.path.exists(path):
            continue
        files.append(path)
    # spread the sample across the sorted list so it is not one artist or folder
    if limit and len(files) > limit:
        step = len(files) / limit
        files = [files[int(i * step)] for i in range(limit)]
    return files


def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.1f}%)" if d else "0/0"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=os.path.expanduser("~/SingWS/loudness.json"))
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--bin", default=None, help="path to singws-analyze (default: auto-discovery / SINGWS_ANALYZE_BIN)")
    args = ap.parse_args()
    if args.bin:
        os.environ["SINGWS_ANALYZE_BIN"] = args.bin
    helper = ra.find_helper()
    if helper is None:
        print("singws-analyze not found: build it (rust/build_analyze.sh) or pass --bin")
        return 2
    try:
        from libmpv_media_jobs import LoudnessSession
    except Exception as exc:
        print(f"libmpv runtime unavailable here: {exc}")
        return 2

    files = pick_files(Path(args.cache), args.limit)
    print(f"files: {len(files)}   helper: {helper}")
    rust = ra.RustAnalysisSession()

    lufs_ok = peak_ok = lufs_n = peak_n = 0
    k = {"n": 0, "dur": [], "start": [], "end": [], "start_none": 0, "end_none": 0, "both_none": 0, "none_mismatch": 0}
    b = {"n": 0, "len_ratio": [], "mean_abs": [], "max_abs": [], "rate_mismatch": 0}
    outliers = []
    t_lib = t_rust = 0.0
    errors = {"libmpv": 0, "rust": 0}

    # ONE session for both calls: two libmpv cores alive in the same process mix up each other's log messages.
    with LoudnessSession() as lib_k:
        lib_b = lib_k
        for i, path in enumerate(files, 1):
            # ---- karaoke kind (boundaries) ----
            try:
                t0 = time.perf_counter(); a = lib_k.measure_karaoke_transition(path, timeout=120.0); t_lib += time.perf_counter() - t0
            except Exception as exc:
                errors["libmpv"] += 1; a = None
            try:
                t0 = time.perf_counter(); r = rust.measure_karaoke_transition(path, timeout=120.0); t_rust += time.perf_counter() - t0
            except Exception as exc:
                errors["rust"] += 1; r = None; outliers.append(("rust error", str(exc)[:80], path))
            if a is not None and r is not None:
                if a[0] is not None and r[0] is not None:
                    lufs_n += 1; lufs_ok += abs(a[0] - r[0]) <= ra.LU_TOLERANCE
                if a[1] is not None and r[1] is not None:
                    peak_n += 1; peak_ok += abs(a[1] - r[1]) <= ra.PEAK_TOLERANCE
                k["n"] += 1
                k["dur"].append(abs(a[2] - r[2]))
                for idx, name in ((3, "start"), (4, "end")):
                    if a[idx] is None and r[idx] is None:
                        k["both_none"] += 1
                    elif a[idx] is None or r[idx] is None:
                        k["none_mismatch"] += 1; outliers.append((f"{name} none-mismatch", f"libmpv={a[idx]} rust={r[idx]}", path))
                    else:
                        d = abs(a[idx] - r[idx]); k[name].append(d)
                        if d > ra.BOUNDARY_TOLERANCE_S:
                            outliers.append((f"{name} d={d:.2f}s", f"libmpv={a[idx]:.2f} rust={r[idx]:.2f}", path))
            # ---- bgm kind (envelope) ----
            try:
                a2 = lib_b.measure_transition(path, timeout=120.0)
            except Exception as exc:
                a2 = None; errors["libmpv_env"] = errors.get("libmpv_env", 0) + 1; env_err = f"libmpv: {exc}"
            try:
                r2 = rust.measure_transition(path, timeout=120.0)
            except Exception as exc:
                r2 = None; errors["rust_env"] = errors.get("rust_env", 0) + 1; env_err = f"rust: {exc}"
            if a2 is not None and r2 is not None:
                ea, eb = a2[2], r2[2]
                n = min(len(ea), len(eb))
                if n:
                    b["n"] += 1
                    b["len_ratio"].append(len(eb) / len(ea))
                    diffs = [abs(ea[j] - eb[j]) for j in range(n)]
                    b["mean_abs"].append(sum(diffs) / n); b["max_abs"].append(max(diffs))
            if i % 10 == 0:
                print(f"  ... {i}/{len(files)}", flush=True)

    def stats(vals):
        if not vals:
            return "n/a"
        vals = sorted(vals)
        return f"median {statistics.median(vals):.3f}  p95 {vals[int(0.95 * (len(vals) - 1))]:.3f}  max {vals[-1]:.3f}"

    print("\n=== loudness / peak (from the karaoke-kind call)")
    print(f"  integrated loudness within {ra.LU_TOLERANCE:.2f} LU: {pct(lufs_ok, lufs_n)}")
    print(f"  sample peak within {ra.PEAK_TOLERANCE:.2f} dB:       {pct(peak_ok, peak_n)}")
    print("\n=== karaoke boundaries (seconds, |libmpv - rust|)")
    print(f"  files compared: {k['n']}")
    print(f"  duration:    {stats(k['dur'])}   within {ra.DURATION_TOLERANCE_S}s: {pct(sum(d <= ra.DURATION_TOLERANCE_S for d in k['dur']), len(k['dur']))}")
    print(f"  audio_start: {stats(k['start'])}   within {ra.BOUNDARY_TOLERANCE_S}s: {pct(sum(d <= ra.BOUNDARY_TOLERANCE_S for d in k['start']), len(k['start']))}")
    print(f"  audio_end:   {stats(k['end'])}   within {ra.BOUNDARY_TOLERANCE_S}s: {pct(sum(d <= ra.BOUNDARY_TOLERANCE_S for d in k['end']), len(k['end']))}")
    print(f"  both 'no edge' (fully silent): {k['both_none']}   one-sided none: {k['none_mismatch']}")
    print("\n=== bgm envelope (100 ms windows, dB)")
    print(f"  files compared: {b['n']}")
    print(f"  length ratio rust/libmpv: {stats(b['len_ratio'])}")
    print(f"  mean |diff| per file:     {stats(b['mean_abs'])}")
    print(f"  max  |diff| per file:     {stats(b['max_abs'])}")
    print(f"\n=== speed (single call each, sequential): libmpv {t_lib:.1f}s   rust {t_rust:.1f}s   ({t_lib / t_rust:.1f}x)" if t_rust else "")
    print(f"=== errors: {errors}")
    if errors.get("libmpv_env") or errors.get("rust_env"):
        print(f"    last envelope error: {env_err}")
    if outliers:
        print("\nworst outliers:")
        for what, detail, path in outliers[:20]:
            print(f"  {what:22s} {detail:34s} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
