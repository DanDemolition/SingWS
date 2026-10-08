#!/usr/bin/env python3
"""Compare the Rust analysis prototype with the libmpv results already in the loudness cache.

READ-ONLY with respect to show data: the cache is only read (never written) and no setting is touched.
Nothing in the app imports this file.

    python3 tools/rust_analysis_compare.py [--cache ~/SingWS/loudness.json] [--jobs 4] [--limit 0]
                                           [--libmpv-sample 0] [--binary rust/target/release/singws-analyze]

What it reports: share of files whose integrated loudness / sample peak agree within tolerance, the
worst outliers, error categories, Rust throughput and peak memory. With --libmpv-sample N it also times
the existing libmpv path on N files for a per-track baseline (needs the app's libmpv runtime).
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_oracle(cache_path: Path, limit: int):
    data = json.loads(cache_path.read_text())
    items = []
    for path, rec in data.items():
        if not isinstance(rec, dict) or rec.get("mode") != "full" or rec.get("i") is None:
            continue
        if not os.path.exists(path):
            continue
        items.append((path, float(rec["i"]), rec.get("peak_db")))
    items.sort()
    return items[:limit] if limit else items


def run_rust(binary: Path, paths: list[str], jobs: int):
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    t0 = time.perf_counter()
    proc = subprocess.run(
        [str(binary), "--jobs", str(jobs), "--stdin"],
        input="\n".join(paths) + "\n", capture_output=True, text=True, check=False,
    )
    wall = time.perf_counter() - t0
    peak_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    results = {}
    for line in proc.stdout.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        results[rec["path"]] = rec
    return results, wall, peak_rss, proc.returncode, proc.stderr.strip()[:300]


def libmpv_baseline(paths: list[str]):
    sys.path.insert(0, str(ROOT))
    try:
        import libmpv_media_jobs as jobs  # type: ignore
    except Exception as exc:  # runtime not available in this venv
        return None, f"libmpv path unavailable: {exc}"
    times, failures = [], 0
    for p in paths:
        t0 = time.perf_counter()
        try:
            jobs.measure_loudness_lufs(p, timeout=120.0)
            times.append(time.perf_counter() - t0)
        except Exception:
            failures += 1
    return times, f"{failures} failures" if failures else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=os.path.expanduser("~/SingWS/loudness.json"))
    ap.add_argument("--binary", default=str(ROOT / "rust/target/release/singws-analyze"))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--libmpv-sample", type=int, default=0)
    ap.add_argument("--lu-tol", type=float, default=0.1)
    ap.add_argument("--peak-tol", type=float, default=0.1)
    args = ap.parse_args()

    binary = Path(args.binary)
    if not binary.exists():
        print(f"binary not found: {binary} (build with: cd rust && cargo build --release)")
        return 2
    oracle = load_oracle(Path(args.cache), args.limit)
    if not oracle:
        print("no usable oracle entries (need mode=full entries whose files still exist)")
        return 2
    print(f"oracle entries: {len(oracle)}  (cache is read, never written)")

    results, wall, rss, code, err = run_rust(binary, [p for p, _, _ in oracle], args.jobs)
    print(f"rust: {len(results)}/{len(oracle)} results in {wall:.1f}s  ({len(oracle)/wall:.2f} files/s, jobs={args.jobs})"
          f"  peak child RSS {rss/1e6 if sys.platform=='darwin' else rss/1e3:.0f} MB  exit={code}")
    if err:
        print("stderr:", err)

    deltas, pdeltas, errors, outliers, ms, peak_outliers = [], [], {}, [], [], []
    for path, ref_i, ref_peak in oracle:
        rec = results.get(path)
        if rec is None:
            errors["missing"] = errors.get("missing", 0) + 1
            continue
        if not rec["ok"]:
            errors[rec["error"]] = errors.get(rec["error"], 0) + 1
            outliers.append((999.0, path, f"error {rec['error']}: {rec.get('detail','')[:60]}"))
            continue
        ms.append(rec["ms"])
        if rec["i"] is None:
            errors["no_loudness"] = errors.get("no_loudness", 0) + 1
            outliers.append((999.0, path, "no loudness"))
            continue
        d = rec["i"] - ref_i
        deltas.append(d)
        if ref_peak is not None:
            pd = rec["peak_db"] - float(ref_peak)
            pdeltas.append(pd)
            if abs(pd) > args.peak_tol:
                peak_outliers.append((abs(pd), path, f"rust peak {rec['peak_db']} vs libmpv {ref_peak}"))
        if abs(d) > args.lu_tol:
            outliers.append((abs(d), path, f"rust {rec['i']} vs libmpv {ref_i}"))

    if deltas:
        within = sum(1 for d in deltas if abs(d) <= args.lu_tol)
        print(f"loudness: {within}/{len(deltas)} within +-{args.lu_tol} LU ({100*within/len(deltas):.1f}%)  "
              f"mean delta {statistics.fmean(deltas):+.3f}  max |delta| {max(abs(d) for d in deltas):.2f}")
    if pdeltas:
        pw = sum(1 for d in pdeltas if abs(d) <= args.peak_tol)
        print(f"sample peak: {pw}/{len(pdeltas)} within +-{args.peak_tol} dB ({100*pw/len(pdeltas):.1f}%)  "
              f"max |delta| {max(abs(d) for d in pdeltas):.2f}")
    if ms:
        print(f"rust per-file time: median {statistics.median(ms):.0f} ms  p90 {sorted(ms)[int(len(ms)*0.9)-1]:.0f} ms")
    if errors:
        print("non-ok categories:", errors)
    if outliers:
        print("worst outliers:")
        for _, path, why in sorted(outliers, reverse=True)[:15]:
            print(f"  {why:50s} {path}")

    if peak_outliers:
        print("sample-peak outliers:")
        for _, path, why in sorted(peak_outliers, reverse=True)[:10]:
            print(f"  {why:50s} {path}")

    if args.libmpv_sample:
        sample = [p for p, _, _ in oracle[: args.libmpv_sample]]
        times, note = libmpv_baseline(sample)
        if times:
            print(f"libmpv baseline on {len(times)} files: median {statistics.median(times)*1000:.0f} ms/track "
                  f"(sequential, one job at a time) {note}")
        else:
            print(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
