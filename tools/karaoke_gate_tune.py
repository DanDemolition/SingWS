#!/usr/bin/env python3
"""Find the mpv `agate` parameters that best reproduce the REAL gate/expander (singws_master_audio.MasterAudioProcessor, same as the Rust one).
The gate only acts below its threshold (-58 dBFS by default), so the test material is where that happens: the quiet tails of real karaoke songs
(fade-outs, the dither noise after the last note) plus controlled quiet music at -50..-75 dB with silent gaps. Scored on the QUIET frames only.

    QT_QPA_PLATFORM=offscreen .venv-test-arm64-fresh/bin/python tools/karaoke_gate_tune.py [--songs 3]
"""
from __future__ import annotations

import argparse
import glob
import itertools
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-gtune-"))
import karaoke_chain_compare as kc  # noqa: E402

R = kc.RATE


def reference(wav, params):
    from singws_master_audio import MasterAudioProcessor
    p = MasterAudioProcessor(sample_rate=R, channels=2)
    quiet = {"eq_enabled": 0.0, "comp_enabled": 0.0, "limiter_enabled": 0.0, "exciter_mix": 0.0, "gate_enabled": 1.0}
    p.set_params({**params, **quiet})
    p.set_enabled(True)
    return p.process_f32_array(wav.astype(np.float32))


def lin(db):
    return 10 ** (db / 20.0)


def candidate(params, attack, release, detection, thr_offset_db, knee, ratio_scale):
    thr = min(1.0, max(0.000976563, lin(float(params["gate_threshold_db"]) + thr_offset_db)))
    rng = min(1.0, max(0.0, lin(float(params["gate_floor_db"]))))
    ratio = max(1.0, float(params["gate_ratio"]) * ratio_scale)
    return (f"agate=threshold={thr:.9f}:range={rng:.6f}:ratio={ratio:.4f}:attack={attack}:release={release}:knee={knee}"
            f":detection={detection}:link=average")


def tails(count, seconds):
    lib = Path.home() / "Music/Karaoke/Karaoke Library"
    zips = sorted(glob.glob(str(lib / "*.zip")))
    rng = np.random.default_rng(23)
    rng.shuffle(zips)
    work = Path(tempfile.mkdtemp(prefix="singws-gtune-songs-"))
    out = []
    for z in zips:
        if len(out) >= count:
            break
        try:
            with zipfile.ZipFile(z) as zf:
                name = next((n for n in zf.namelist() if n.lower().endswith(".mp3") and "__MACOSX" not in n), None)
                if not name:
                    continue
                src = work / f"{len(out)}.mp3"
                src.write_bytes(zf.read(name))
        except Exception:
            continue
        wav = kc.mpv_render(str(src), "")
        if len(wav) < R * (seconds + 10):
            continue
        piece = wav[-R * seconds:]
        path = work / f"tail{len(out)}.wav"
        kc.write_wav(path, piece)
        out.append((f"tail of {Path(z).stem[:34]}", str(path)))
    return out


def synthetic():
    rng = np.random.default_rng(5)
    t = np.arange(R * 14) / R
    work = Path(tempfile.mkdtemp(prefix="singws-gtune-syn-"))
    out = []
    for level in (-52, -60, -68):
        env = (np.sin(2 * np.pi * t / 6) > 0).astype(float)
        sig = 10 ** (level / 20) * (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 1330 * t)) * (0.25 + 0.75 * env)
        sig = sig + 10 ** (-76 / 20) * rng.standard_normal(len(t))
        path = work / f"syn{level}.wav"
        kc.write_wav(path, np.stack([sig, sig * 0.92], axis=1).astype(np.float32))
        out.append((f"quiet music at {level} dB", str(path)))
    return out


def score(a, ref):
    a, ref, _ = kc.align(a, ref)
    sa, sr = kc.short_term(a), kc.short_term(ref)
    quiet = (sr < -48) & (sr > -95) & (sa > -100)
    if quiet.sum() < 5:
        return None
    d = (sa - sr)[quiet]
    return {"mean": float(d.mean()), "p95": float(np.percentile(np.abs(d), 95)), "frames": int(quiet.sum())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", type=int, default=3)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--settings", default=os.path.expanduser("~/SingWS/settings.json"))
    args = ap.parse_args()
    import importlib.util
    spec = importlib.util.spec_from_file_location("singws_main_gtune", ROOT / "0.2.18.1.py")
    app = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = app
    spec.loader.exec_module(app)
    from singws_master_audio import DEFAULT_PARAMS

    raw = json.load(open(args.settings))
    st = {k: v for k, v in raw.items() if str(k).startswith("master_audio")}
    params = {**DEFAULT_PARAMS, **app.KaraokeApp._compute_master_audio_params(SimpleNamespace(settings=st))}
    print("gate params:", {k: params[k] for k in ("gate_threshold_db", "gate_ratio", "gate_attack_ms", "gate_release_ms", "gate_floor_db")})
    material = tails(args.songs, args.seconds) + synthetic()
    cases = []
    for name, path in material:
        cases.append((name, path, reference(kc.read_wav(path), params)))
    print(f"{len(cases)} cases\n")
    from mpv_audio_filters import master_bus_filters
    base_chain = [c for c in master_bus_filters({**params, "eq_enabled": 0.0, "comp_enabled": 0.0, "limiter_enabled": 0.0, "exciter_mix": 0.0}) if c.startswith("agate")]
    today = base_chain[0] if base_chain else None
    results = []
    combos = list(itertools.product((5, 60, 140, 300), (140, 300, 600, 1000), ("rms", "peak"), (-3.0, 0.0, 3.0, 6.0), (1.0, 2.8), (1.0, 1.4)))
    for att, rel, det, off, knee, rs in combos:
        sc = []
        for _n, path, ref in cases:
            s = score(kc.mpv_render(path, candidate(params, att, rel, det, off, knee, rs)), ref)
            if s:
                sc.append(s)
        if not sc:
            continue
        results.append({"attack": att, "release": rel, "detection": det, "offset": off, "knee": knee, "ratio_scale": rs,
                        "mean": float(np.mean([s["mean"] for s in sc])), "spread": float(np.std([s["mean"] for s in sc])),
                        "p95": float(np.mean([s["p95"] for s in sc]))})
    results.sort(key=lambda r: abs(r["mean"]) + r["p95"] + r["spread"])
    print("best candidates (mean = lavfi minus real, in the quiet frames; lower |mean|, p95, spread is better):")
    for r in results[:10]:
        print(f"  attack {r['attack']:>3} release {r['release']:>4} {r['detection']:4} offset {r['offset']:+4.1f} knee {r['knee']:3.1f} ratio x{r['ratio_scale']:3.1f} | "
              f"mean {r['mean']:+5.2f} dB  p95 {r['p95']:5.2f} dB  spread {r['spread']:4.2f}")
    sc = []
    for _n, path, ref in cases:
        s = score(kc.mpv_render(path, today), ref)
        if s:
            sc.append(s)
    print(f"TODAY (agate as the app builds it): mean {np.mean([s['mean'] for s in sc]):+5.2f} dB  p95 {np.mean([s['p95'] for s in sc]):5.2f} dB  spread {np.std([s['mean'] for s in sc]):4.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
