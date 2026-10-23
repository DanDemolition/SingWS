#!/usr/bin/env python3
"""Stage C parity: run the app's real transition-planning functions and the Rust ``singws-plan`` rules on the same inputs.

Inputs are the records in the app's own analysis cache (``~/SingWS/transition-analysis.json``): 69 background tracks with full
loudness envelopes plus the karaoke / CDG / MP4 records. For every record the Python function (``transition_analysis``, the
oracle) and the Rust export (``singws_plan_json``) are called and compared EXACTLY (they perform the same float operations, so
results must be bit-identical). Extra probes use perturbed durations and defaults to reach the branches a cache of real files
rarely hits.

Usage:  python3 tools/plan_parity.py [cache.json] [--lib PATH]
Exit status 1 on any disagreement.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import transition_analysis as ta  # noqa: E402

_OUT_CAP = 8192


def load_lib(path=None):
    candidates = [path or "", os.environ.get("SINGWS_DSP_LIB") or "",
                  os.path.expanduser("~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib")]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            lib = ctypes.CDLL(str(candidate))
            fn = lib.singws_plan_json  # AttributeError if the dylib predates Stage C
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            return lib
    raise SystemExit("no libsingws_dsp_ffi.dylib with singws_plan_json found (build rust/ or pass --lib)")


def call(lib, request):
    """Call the Rust planner. ``allow_nan=False`` keeps non-JSON floats from silently turning into something else."""
    buf = ctypes.create_string_buffer(_OUT_CAP)
    n = lib.singws_plan_json(json.dumps(request, allow_nan=False).encode(), buf, _OUT_CAP)
    if n < 0:
        raise RuntimeError(f"rust plan failed code={n} request_op={request.get('op')}")
    return json.loads(buf.raw[:n].decode())


def record_payload(record, with_envelope=False):
    payload = asdict(record)
    if not with_envelope:
        payload.pop("envelope_db", None)
    return payload


def same(a, b):
    return a == b  # exact, including None; both sides do identical float operations


def compare_all(lib, payloads, stats, bad):
    """payloads: raw cache dicts. Fills stats (Counter) and bad (list of (what, key, python, rust))."""
    for key, raw in payloads.items():
        record = ta.TransitionAnalysis.from_dict(raw)
        rust_valid = call(lib, {"op": "valid", "record": {k: v for k, v in raw.items() if k != "envelope_db"}})["valid"]
        py_valid = record is not None
        stats["validity_checked"] += 1
        if rust_valid != py_valid:
            bad.append(("valid", key, py_valid, rust_valid))
        if record is None:
            stats["invalid_records_skipped"] += 1
            continue
        rec = record_payload(record)
        env = list(record.envelope_db)
        if env:
            hop = record.hop_seconds
            py_b = ta.audio_boundaries_from_envelope(env, hop_seconds=hop)
            r = call(lib, {"op": "boundaries", "envelope": env, "hop": hop})
            stats["boundaries_checked"] += 1
            if not same(py_b, (r["start"], r["end"])):
                bad.append(("boundaries", key, py_b, (r["start"], r["end"])))
            py_f = ta.estimate_fade_out_from_envelope(env, hop_seconds=hop)
            r = call(lib, {"op": "fade", "envelope": env, "hop": hop})
            stats["fade_checked"] += 1
            if not same(py_f, (r["fade_start"], r["confidence"])):
                bad.append(("fade", key, py_f, (r["fade_start"], r["confidence"])))
        # window: the record's own duration plus perturbations that cross the 1.0 s tolerance
        for delta in (0.0, 0.4, -0.4, 1.0, 1.01, -2.0, 30.0):
            duration = record.duration + delta
            py_w = ta.bgm_audio_window(record, duration)
            r = call(lib, {"op": "window", "record": rec, "duration": duration})
            stats["window_checked"] += 1
            if not same(py_w, (r["start"], r["end"])):
                bad.append(("window", key, (duration, py_w), (r["start"], r["end"])))
        for default in (5.0, 2.0, 8.0, 0.5, 20.0):
            py_c = ta.select_bgm_crossfade_seconds(record, default_seconds=default)
            r = call(lib, {"op": "crossfade", "record": rec, "default_seconds": default})
            stats["crossfade_checked"] += 1
            stats[f"crossfade_reason:{py_c[1]}"] += 1
            if not same(py_c, (r["seconds"], r["reason"])):
                bad.append(("crossfade", key, (default, py_c), (r["seconds"], r["reason"])))
        # effective karaoke end mutates the record, so use a fresh copy
        fresh = ta.TransitionAnalysis.from_dict(raw)
        py_e = ta.calculate_effective_karaoke_end(fresh)
        r = call(lib, {"op": "effective_end", "record": rec})
        stats["effective_end_checked"] += 1
        stats[f"effective_end_reason:{r['reason']}"] += 1
        py_tuple = (py_e, fresh.safe_for_early_completion, fresh.safety_reason)
        rust_tuple = (r["effective"], r["safe"], r["reason"])
        if not same(py_tuple, rust_tuple) or (r["safety_margin"] is not None and r["safety_margin"] != fresh.safety_margin):
            bad.append(("effective_end", key, py_tuple, rust_tuple))
    return stats, bad


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cache", nargs="?", default=os.path.expanduser("~/SingWS/transition-analysis.json"))
    parser.add_argument("--lib")
    args = parser.parse_args(argv)
    lib = load_lib(args.lib)
    with open(args.cache, encoding="utf-8") as handle:
        data = json.load(handle)
    records = data.get("records", {})
    # Skip the temp-file records that unit tests used to leave in the live cache before test isolation existed.
    real = {k: v for k, v in records.items() if not str(k).startswith(("/var/folders", "/private/var", "/tmp"))}
    stats, bad = Counter(), []
    compare_all(lib, real, stats, bad)
    stats["records_total"], stats["records_used"] = len(records), len(real)
    print(json.dumps(dict(sorted(stats.items())), indent=2))
    for what, key, py, rust in bad[:10]:
        print(f"DISAGREEMENT {what} {os.path.basename(str(key))}: python={py!r} rust={rust!r}")
    if not (stats["boundaries_checked"] and stats["effective_end_checked"]):
        print("NO STRONG EVIDENCE: the cache had no envelopes or no karaoke records")
    print("PLAN PARITY OK" if not bad else f"PLAN PARITY: {len(bad)} disagreement(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
