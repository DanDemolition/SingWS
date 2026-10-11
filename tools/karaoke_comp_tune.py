#!/usr/bin/env python3
"""Find the mpv `acompressor` parameters that best reproduce the REAL compressor (singws_master_audio.MasterAudioProcessor, identical to the Rust
one) on real karaoke audio. Compressor-only chain, operator + stress settings; for each candidate the best possible make-up gain is applied
(so the search is about dynamics and curve, then the make-up offset is read off). Prints the ranked candidates.

    QT_QPA_PLATFORM=offscreen .venv-test-arm64-fresh/bin/python tools/karaoke_comp_tune.py [--songs 2] [--seconds 20]
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-ctune-"))
import karaoke_chain_compare as kc  # noqa: E402


def reference(wav, params):
    from singws_master_audio import MasterAudioProcessor
    p = MasterAudioProcessor(sample_rate=kc.RATE, channels=2)
    quiet = {"gate_enabled": 0.0, "eq_enabled": 0.0, "limiter_enabled": 0.0, "exciter_mix": 0.0, "comp_enabled": 1.0}
    p.set_params({**params, **quiet})
    p.set_enabled(True)
    return p.process_f32_array(wav.astype(np.float32))


def candidate_chain(params, attack, release, detection, offset_db, link, makeup_db=0.0):
    thr = min(0.0, float(params["comp_threshold_db"]) + offset_db)
    knee = max(1.0, min(8.0, 10 ** (max(0.0, float(params["comp_knee_db"])) / 20.0)))
    mk = max(1.0, 10 ** (makeup_db / 20.0))
    return (f"acompressor=threshold={thr:.4f}dB:ratio={float(params['comp_ratio']):.4f}:knee={knee:.4f}:attack={attack}:release={release}"
            f":makeup={mk:.5f}:detection={detection}:link={link}")


def score(a, ref):
    a, ref, _ = kc.align(a, ref)
    sa, sr = kc.short_term(a), kc.short_term(ref)
    live = (sa > -60) & (sr > -60)
    delta = (sr - sa)[live]
    gain_db = float(delta.mean())                    # the make-up gain that lines the two up
    g = 10 ** (gain_db / 20.0)
    resid = kc.db(kc.rms(a * g - ref)) - kc.db(kc.rms(ref))
    return {"makeup_fix_db": gain_db, "dyn_p95": float(np.percentile(np.abs(delta - gain_db), 95)), "null_db": resid}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", type=int, default=2)
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--refine", action="store_true", help="finer grid around the first search's winners")
    ap.add_argument("--settings", default=os.path.expanduser("~/SingWS/settings.json"))
    args = ap.parse_args()
    import importlib.util
    spec = importlib.util.spec_from_file_location("singws_main_ctune", ROOT / "0.2.18.1.py")
    app = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = app
    spec.loader.exec_module(app)
    from singws_master_audio import DEFAULT_PARAMS

    raw = json.load(open(args.settings))
    st = {k: v for k, v in raw.items() if str(k).startswith("master_audio")}
    stress_st = {**st, "master_audio_comp_amount": 70}
    settings = {
        "operator": {**DEFAULT_PARAMS, **app.KaraokeApp._compute_master_audio_params(SimpleNamespace(settings=st))},
        "stress": {**DEFAULT_PARAMS, **app.KaraokeApp._compute_master_audio_params(SimpleNamespace(settings=stress_st))},
    }
    for k, v in settings.items():
        print(k, {x: round(v[x], 2) for x in ("comp_threshold_db", "comp_ratio", "comp_knee_db", "comp_attack_ms", "comp_release_ms", "comp_makeup_db")})
    songs = kc.pick_songs(args.songs, args.seconds)
    import mpv_audio_filters as maf
    cases = []
    for name, path, frames in songs:
        wav = kc.read_wav(path)
        for sname, params in settings.items():
            cases.append((name, sname, path, params, reference(wav, params)))
    print(f"{len(cases)} cases\n")
    if args.refine:
        attacks, releases, detections, offsets = (250, 400, 700, 1200), (300, 500, 800), ("peak",), (3.0, 4.5, 6.0)
    else:
        attacks, releases, detections, offsets = (20, 60, 120, 180, 250), (100, 180, 300, 500), ("rms", "peak"), (0.0, 1.5, 3.0, 4.5)
    results = []
    for att, rel, det, off in itertools.product(attacks, releases, detections, offsets):
        scores = []
        for _n, _s, path, params, ref in cases:
            chain = candidate_chain(params, att, rel, det, off, "average")
            a = kc.mpv_render(path, chain)
            sc = score(a, ref)
            sc["extra_makeup_db"] = sc["makeup_fix_db"] - float(params["comp_makeup_db"])   # what must be ADDED to the setting's own make-up
            scores.append(sc)
        res = {"attack": att, "release": rel, "detection": det, "offset": off,
               "null": float(np.mean([s["null_db"] for s in scores])), "dyn": float(np.mean([s["dyn_p95"] for s in scores])),
               "makeup_fix": float(np.mean([s["extra_makeup_db"] for s in scores])), "makeup_spread": float(np.std([s["extra_makeup_db"] for s in scores]))}
        results.append(res)
    results.sort(key=lambda r: r["null"] + 2.0 * r["dyn"] + 1.0 * r["makeup_spread"])
    print("best candidates (lower null/dyn/spread is better):")
    for r in results[:12]:
        print(f"  attack {r['attack']:>3} release {r['release']:>3} {r['detection']:4} offset {r['offset']:3.1f} | null {r['null']:6.1f} dB  dynamics p95 {r['dyn']:4.2f} dB  "
              f"extra makeup {r['makeup_fix']:+5.2f} dB (spread {r['makeup_spread']:.2f})")
    print("current setting for reference: attack 18 release 180 rms offset 3.0 (scored below)")
    cur = [r for r in results if r["attack"] == 20 and r["release"] == 180 and r["detection"] == "rms" and r["offset"] == 3.0]
    for r in cur:
        print(f"  attack  20 release 180 rms  offset 3.0 | null {r['null']:6.1f} dB  dynamics p95 {r['dyn']:4.2f} dB  makeup fix {r['makeup_fix']:+5.2f} dB")
    json.dump(results, open(os.path.join(tempfile.gettempdir(), "comp_tune_results.json"), "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
