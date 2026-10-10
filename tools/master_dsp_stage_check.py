#!/usr/bin/env python3
"""Prove every stage of the BGM master processor (gate, tilt EQ, exciter, compressor, limiter, output clip) is ACTIVE in the
Rust library, using the operator's own settings and (optionally) the exact dylib shipped inside an installer.

    SINGWS_DSP_LIB=/path/to/libsingws_dsp_ffi.dylib python tools/master_dsp_stage_check.py [--settings ~/SingWS/settings.json]

Each stage is switched off in turn and the effect of switching it ON is measured on a test signal chosen to show that stage.
Also checks that a parameter change made while audio is flowing (a slider move) changes the very next output, and that
the Rust output equals the Python reference processor. Read-only: prints only the master-audio settings, never anything else.
Exit status 0 when every stage is shown to work.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SINGWS_HOME", __import__("tempfile").mkdtemp(prefix="singws-dspcheck-"))

RATE = 44100.0


def load_app():
    spec = importlib.util.spec_from_file_location("singws_main_dspcheck", ROOT / "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def db(x: float) -> float:
    return 20.0 * np.log10(max(float(x), 1e-12))


def rms(a) -> float:
    a = np.asarray(a, dtype=np.float64)
    return float(np.sqrt(np.mean(a * a)))


def band_rms(sig: np.ndarray, lo: float, hi: float) -> float:
    spec = np.fft.rfft(sig[:, 0] * np.hanning(len(sig)))
    f = np.fft.rfftfreq(len(sig), 1.0 / RATE)
    m = (f >= lo) & (f < hi)
    return float(np.sqrt(np.sum(np.abs(spec[m]) ** 2)))


def stereo(mono: np.ndarray) -> np.ndarray:
    return np.stack([mono, mono], axis=1).astype(np.float32)


def run(proc_cls, params, signal, warm=0):
    p = proc_cls(sample_rate=RATE, channels=2)
    p.set_enabled(True)  # a new processor is a pass-through until the host enables it (the app does this when master audio is on)
    p.set_params(params)
    p.reset_state()
    return p.process_f32_array(signal.copy()), p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", default=os.path.expanduser("~/SingWS/settings.json"))
    args = ap.parse_args()
    from rust_master_dsp import RustMasterProcessor, RustDspUnavailable
    from singws_master_audio import MasterAudioProcessor, DEFAULT_PARAMS

    try:
        RustMasterProcessor(sample_rate=RATE, channels=2)
    except RustDspUnavailable as exc:
        print("Rust master DSP unavailable:", exc)
        return 2
    app = load_app()
    settings = {}
    try:
        raw = json.load(open(args.settings))
        settings = {k: v for k, v in raw.items() if str(k).startswith("master_audio") or k == "master_dsp_engine"}
    except Exception:
        pass
    print("master-audio settings in use:", json.dumps(settings, sort_keys=True))
    ns = SimpleNamespace(settings=settings)
    base = {**DEFAULT_PARAMS, **app.KaraokeApp._compute_master_audio_params(ns)}
    print("-> processor params:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in base.items()
                                  if k in ("gate_enabled", "eq_enabled", "comp_enabled", "limiter_enabled", "comp_ratio", "comp_makeup_db",
                                           "high_shelf_db", "presence_db", "low_shelf_db", "exciter_mix", "limiter_ceiling_db")})
    probe, _ = run(RustMasterProcessor, base, stereo(1.6 * np.sin(2 * np.pi * 220 * np.arange(int(RATE)) / RATE)))
    if float(np.max(np.abs(probe))) > 1.5:
        print("The processor passed a +4 dB signal through unchanged: it is not processing. Aborting instead of reporting a vacuous result.")
        return 3
    rng = np.random.default_rng(3)
    n = int(RATE * 3)
    t = np.arange(n) / RATE
    ok = True

    def check(name, passed, detail):
        nonlocal ok
        ok &= bool(passed)
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    # 1. compressor: a loud burst then a quiet burst; the gap between them must shrink when the compressor is on
    steps = np.concatenate([0.5 * np.sin(2 * np.pi * 1000 * t[: n // 2]), 0.03 * np.sin(2 * np.pi * 1000 * t[n // 2:])])
    on, _ = run(RustMasterProcessor, {**base, "comp_enabled": 1.0, "comp_ratio": 3.0, "comp_makeup_db": 0.0, "limiter_enabled": 0.0, "eq_enabled": 0.0, "exciter_mix": 0.0}, stereo(steps))
    off, _ = run(RustMasterProcessor, {**base, "comp_enabled": 0.0, "limiter_enabled": 0.0, "eq_enabled": 0.0, "exciter_mix": 0.0}, stereo(steps))
    gap = lambda o: db(rms(o[int(RATE * 0.8): int(RATE * 1.2), 0])) - db(rms(o[int(RATE * 2.3): int(RATE * 2.8), 0]))
    check("compressor", gap(on) < gap(off) - 3.0, f"loud-to-quiet gap {gap(off):.1f} dB off -> {gap(on):.1f} dB on")

    # 2. limiter: its detector is a 1.2 ms smoothed peak envelope (same in the Python reference), so it reacts to SUSTAINED
    #    overloads, not to a lone sine peak. A square wave 3 dB over the ceiling is the fair test: on, it must settle at the ceiling.
    hot = stereo(1.3 * np.sign(np.sin(2 * np.pi * 220 * t)))
    lim_on, _ = run(RustMasterProcessor, {**base, "limiter_enabled": 1.0, "comp_enabled": 0.0, "eq_enabled": 0.0, "exciter_mix": 0.0}, hot)
    lim_off, _ = run(RustMasterProcessor, {**base, "limiter_enabled": 0.0, "comp_enabled": 0.0, "eq_enabled": 0.0, "exciter_mix": 0.0}, hot)
    ceiling = 10 ** (base["limiter_ceiling_db"] / 20.0)
    settled = slice(int(RATE * 0.5), None)
    pk_on, pk_off = float(np.max(np.abs(lim_on[settled]))), float(np.max(np.abs(lim_off[settled])))
    check("limiter", pk_on <= ceiling * 1.03 and pk_off > pk_on + 0.02, f"sustained overload peak {db(pk_off):.2f} dBFS off -> {db(pk_on):.2f} dBFS on (ceiling {base['limiter_ceiling_db']:.1f} dB)")
    hard = 10 ** (base["output_ceiling_db"] / 20.0)
    check("output clip safety", float(np.max(np.abs(lim_off))) <= hard * 1.001 + 1e-6, f"never above {base['output_ceiling_db']:.1f} dBFS even with the limiter off (peak {db(float(np.max(np.abs(lim_off)))):.2f})")

    # 3. tilt EQ: exaggerated tilt so the effect is unmistakable; direction must be brighter and lighter in the bass
    noise = stereo(0.1 * rng.standard_normal(n))
    flat, _ = run(RustMasterProcessor, {**base, "eq_enabled": 0.0, "comp_enabled": 0.0, "limiter_enabled": 0.0, "exciter_mix": 0.0}, noise)
    bright, _ = run(RustMasterProcessor, {**base, "eq_enabled": 1.0, "high_shelf_db": 6.0, "presence_db": 3.0, "low_shelf_db": -4.0, "comp_enabled": 0.0, "limiter_enabled": 0.0, "exciter_mix": 0.0}, noise)
    hf = db(band_rms(bright, 9000, 18000)) - db(band_rms(flat, 9000, 18000))
    lf = db(band_rms(bright, 30, 90)) - db(band_rms(flat, 30, 90))
    check("tilt EQ", hf > 3.0 and lf < -2.0, f"high band {hf:+.1f} dB, low band {lf:+.1f} dB")
    mine, _ = run(RustMasterProcessor, {**base, "comp_enabled": 0.0, "limiter_enabled": 0.0, "exciter_mix": 0.0}, noise)
    check("your tilt setting", abs(db(band_rms(mine, 9000, 18000)) - db(band_rms(flat, 9000, 18000))) > 0.05 or base["high_shelf_db"] == 0,
          f"your high shelf {base['high_shelf_db']:+.2f} dB moves the top band {db(band_rms(mine, 9000, 18000)) - db(band_rms(flat, 9000, 18000)):+.2f} dB")

    # 4. exciter: adds high-frequency content to a signal that has little
    dull = stereo(0.2 * np.sin(2 * np.pi * 1500 * t) + 0.05 * rng.standard_normal(n))
    ex_off, _ = run(RustMasterProcessor, {**base, "exciter_mix": 0.0, "eq_enabled": 0.0, "comp_enabled": 0.0, "limiter_enabled": 0.0}, dull)
    ex_on, _ = run(RustMasterProcessor, {**base, "exciter_mix": max(base["exciter_mix"], 0.3), "eq_enabled": 0.0, "comp_enabled": 0.0, "limiter_enabled": 0.0}, dull)
    d = db(band_rms(ex_on, 6000, 16000)) - db(band_rms(ex_off, 6000, 16000))
    check("exciter", d > 0.3, f"6-16 kHz energy {d:+.2f} dB with the exciter on (your mix {base['exciter_mix']:.3f})")

    # 5. gate: a very quiet signal is pulled down when the gate is on
    hiss = stereo(10 ** (-70 / 20.0) * rng.standard_normal(n))
    g_off, _ = run(RustMasterProcessor, {**base, "gate_enabled": 0.0, "eq_enabled": 0.0, "comp_enabled": 0.0, "limiter_enabled": 0.0, "exciter_mix": 0.0}, hiss)
    g_on, _ = run(RustMasterProcessor, {**base, "gate_enabled": 1.0, "eq_enabled": 0.0, "comp_enabled": 0.0, "limiter_enabled": 0.0, "exciter_mix": 0.0}, hiss)
    reduction = db(rms(g_off[int(RATE):])) - db(rms(g_on[int(RATE):]))
    check("gate", reduction > 4.0, f"-70 dB hiss is {reduction:.1f} dB quieter with the gate on")

    # 6. live parameter change: a "slider move" part-way through changes the very next audio
    p = RustMasterProcessor(sample_rate=RATE, channels=2)
    p.set_enabled(True)
    p.set_params({**base, "comp_enabled": 1.0, "comp_ratio": 1.5, "comp_makeup_db": 0.0, "limiter_enabled": 0.0, "eq_enabled": 0.0, "exciter_mix": 0.0})
    p.reset_state()
    loud = stereo(0.5 * np.sin(2 * np.pi * 1000 * t))
    a = p.process_f32_array(loud[: int(RATE * 1.5)].copy())
    p.set_params({**base, "comp_enabled": 1.0, "comp_ratio": 3.0, "comp_makeup_db": 0.0, "limiter_enabled": 0.0, "eq_enabled": 0.0, "exciter_mix": 0.0})
    b = p.process_f32_array(loud[int(RATE * 1.5):].copy())
    before, after = db(rms(a[int(RATE * 1.0):, 0])), db(rms(b[int(RATE * 0.9):, 0]))
    check("live slider change", after < before - 0.5, f"output level {before:.2f} dB -> {after:.2f} dB after raising the ratio mid-stream")

    # 7. Rust == the Python reference for the operator's own settings
    mixed = stereo(0.3 * np.sin(2 * np.pi * 440 * t) + 0.08 * rng.standard_normal(n))
    r_out, _ = run(RustMasterProcessor, base, mixed)
    py = MasterAudioProcessor(sample_rate=RATE, channels=2)
    py.set_enabled(True)
    py.set_params(base)
    py.reset_state()
    p_out = py.process_f32_array(mixed.copy())
    diff = float(np.max(np.abs(r_out - p_out)))
    check("matches the Python reference", diff < 1e-5, f"largest sample difference {diff:.2e} on your settings")

    print("\nALL STAGES ACTIVE" if ok else "\nAT LEAST ONE STAGE DID NOT BEHAVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
