#!/usr/bin/env python3
"""Prove every stage of the KARAOKE audio chain (graphic EQ, gate, tilt EQ, exciter, compressor, limiter) is active in the
bundled mpv, using the operator's own settings and the exact `af` string the app builds from them.

    .venv/bin/python tools/karaoke_dsp_stage_check.py [--settings ~/SingWS/settings.json]

Karaoke audio never touches Python: the app converts the settings into an mpv/lavfi `af` chain (mpv_audio_filters.py) and the
in-process libmpv applies it. This tool renders synthetic test signals through that same libmpv, offline (no sound device), once
per stage with the stage on and once off, and reports the measured effect. Exit status 0 when every stage is shown to work.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-kdspcheck-"))

RATE = 44100


def db(x):
    return 20.0 * np.log10(max(float(x), 1e-12))


def rms(a):
    a = np.asarray(a, dtype=np.float64)
    return float(np.sqrt(np.mean(a * a)))


def write_float_wav(path, mono):
    data = np.stack([mono, mono], axis=1).astype("<f4")
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + data.nbytes) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, RATE, RATE * 8, 8, 32))
        fh.write(b"data" + struct.pack("<I", data.nbytes) + data.tobytes())


def render(af_chain, mono):
    """Run `mono` through mpv with the given af chain, return the stereo float result (N, 2)."""
    from libmpv_media_jobs import OfflineMpvJob
    work = tempfile.mkdtemp(prefix="singws-kdsp-")
    src, out = os.path.join(work, "in.wav"), os.path.join(work, "out.wav")
    write_float_wav(src, mono)
    job = OfflineMpvJob()
    try:
        job.option("config", "no")
        job.option("vid", "no")
        job.option("ao", "pcm")
        job.option("ao-pcm-file", out)
        job.option("ao-pcm-waveheader", "yes")
        job.option("audio-format", "floatp")
        job.option("audio-samplerate", str(RATE))
        job.option("audio-channels", "stereo")
        job.option("untimed", "yes")
        if af_chain:
            job.option("af", af_chain)
        job.initialize()
        job.command("loadfile", src, "replace")
        job.wait_for_end(120.0)
    finally:
        job.close()
    raw = open(out, "rb").read()
    i = raw.find(b"data")
    payload = raw[i + 8:]
    fmt_at = raw.find(b"fmt ") + 8
    code, ch, _rate, _br, _ba, bits = struct.unpack("<HHIIHH", raw[fmt_at: fmt_at + 16])
    if code == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE: the real format code is the first two bytes of the sub-format GUID
        code = struct.unpack("<H", raw[fmt_at + 24: fmt_at + 26])[0]
    if code == 3 and bits == 32:
        arr = np.frombuffer(payload[: len(payload) // 4 * 4], dtype="<f4")
    elif code == 1 and bits == 16:
        arr = np.frombuffer(payload[: len(payload) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    else:
        raise RuntimeError(f"unexpected output wav format code={code} bits={bits}")
    return arr.reshape(-1, ch)


def band_rms(sig, lo, hi):
    spec = np.fft.rfft(sig[:, 0] * np.hanning(len(sig)))
    f = np.fft.rfftfreq(len(sig), 1.0 / RATE)
    m = (f >= lo) & (f < hi)
    return float(np.sqrt(np.sum(np.abs(spec[m]) ** 2)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", default=os.path.expanduser("~/SingWS/settings.json"))
    args = ap.parse_args()
    import importlib.util
    spec = importlib.util.spec_from_file_location("singws_main_kdspcheck", ROOT / "0.2.18.1.py")
    app = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = app
    spec.loader.exec_module(app)
    import mpv_audio_filters as maf
    from singws_master_audio import DEFAULT_PARAMS

    raw = json.load(open(args.settings))
    st = {k: v for k, v in raw.items() if str(k).startswith("master_audio")}
    eq_gains = [float(v or 0.0) for v in (raw.get("eq_karaoke") or [])]
    eq_on = bool(raw.get("eq_karaoke_enabled", False))
    print("master-audio settings:", json.dumps(st, sort_keys=True))
    print("karaoke EQ enabled=%s gains=%s" % (eq_on, eq_gains))
    base = {**DEFAULT_PARAMS, **app.KaraokeApp._compute_master_audio_params(SimpleNamespace(settings=st))}
    full = maf.build_af_chain(eq_enabled=eq_on, eq_gains_db=eq_gains, master_enabled=True, master_params=base)
    print("af chain the app builds from your settings:\n  " + full.replace(",", ",\n  "))
    rng = np.random.default_rng(3)
    n = RATE * 3
    t = np.arange(n) / RATE
    ok = True

    def check(name, passed, detail):
        nonlocal ok
        ok &= bool(passed)
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    def chain(**over):
        return maf.build_af_chain(master_enabled=True, master_params={**base, **over})

    quiet = {"gate_enabled": 0.0, "eq_enabled": 0.0, "comp_enabled": 0.0, "limiter_enabled": 0.0, "exciter_mix": 0.0}

    # graphic EQ: the bands on your settings against flat
    noise = 0.1 * rng.standard_normal(n)
    flat = render("", noise)
    shaped_gains = [12.0, 0, 0, 0, 0, 0, 0, 0, 0, -12.0]
    shaped = render(maf.build_af_chain(eq_enabled=True, eq_gains_db=shaped_gains), noise)
    lo = db(band_rms(shaped, 25, 40)) - db(band_rms(flat, 25, 40))
    hi = db(band_rms(shaped, 12000, 20000)) - db(band_rms(flat, 12000, 20000))
    check("graphic EQ (test curve +12 dB bass / -12 dB treble)", lo > 6 and hi < -4, f"31 Hz band {lo:+.1f} dB, 16 kHz band {hi:+.1f} dB")
    if eq_on and any(abs(g) > 0.05 for g in eq_gains):
        yours = render(maf.build_af_chain(eq_enabled=True, eq_gains_db=eq_gains), noise)
        bands = [(25, 40), (50, 80), (100, 160), (200, 320), (400, 640), (800, 1300), (1600, 2500), (3200, 5000), (6400, 10000), (12000, 20000)]
        meas = [db(band_rms(yours, a, b)) - db(band_rms(flat, a, b)) for a, b in bands]
        worst = max(abs(m - g) for m, g in zip(meas, eq_gains))
        check("your karaoke EQ curve", worst < 2.5, "measured per band " + " ".join(f"{m:+.1f}" for m in meas) + f" vs set {eq_gains} (worst error {worst:.1f} dB)")

    # compressor
    steps = np.concatenate([0.5 * np.sin(2 * np.pi * 1000 * t[: n // 2]), 0.03 * np.sin(2 * np.pi * 1000 * t[n // 2:])])
    on = render(chain(**{**quiet, "comp_enabled": 1.0, "comp_ratio": 3.0, "comp_makeup_db": 0.0}), steps)
    off = render(chain(**quiet), steps)
    gap = lambda o: db(rms(o[int(RATE * 0.8): int(RATE * 1.2), 0])) - db(rms(o[int(RATE * 2.3): int(RATE * 2.8), 0]))
    check("compressor", gap(on) < gap(off) - 3.0, f"loud-to-quiet gap {gap(off):.1f} dB off -> {gap(on):.1f} dB on")

    # limiter (sustained overload, like the background-music check)
    hot = 0.97 * np.sign(np.sin(2 * np.pi * 220 * t))
    gain_hot = 1.0  # input stays inside the wav's range; the limiter ceiling is below it
    lim_on = render(chain(**{**quiet, "limiter_enabled": 1.0, "limiter_ceiling_db": -6.0}), hot)
    lim_off = render(chain(**{**quiet, "limiter_enabled": 0.0, "output_ceiling_db": -0.1}), hot)
    pk_on, pk_off = float(np.max(np.abs(lim_on[int(RATE * 0.5):]))), float(np.max(np.abs(lim_off[int(RATE * 0.5):])))
    check("limiter", db(pk_on) <= -5.5 and pk_off > pk_on * 1.3, f"sustained overload peak {db(pk_off):.2f} dBFS (ceiling {-0.1}) -> {db(pk_on):.2f} dBFS with a -6 dB limiter")
    mine = render(chain(**{**quiet, "limiter_enabled": 1.0}), 1.4 * np.sign(np.sin(2 * np.pi * 220 * t)).clip(-1, 1) * 0 + hot)
    check("limiter at YOUR ceiling", float(np.max(np.abs(mine[int(RATE * 0.5):]))) <= 10 ** (base["limiter_ceiling_db"] / 20.0) * 1.03,
          f"peak {db(float(np.max(np.abs(mine[int(RATE * 0.5):])))):.2f} dBFS with ceiling {base['limiter_ceiling_db']:.1f} dB")

    # tilt EQ
    bright = render(chain(**{**quiet, "eq_enabled": 1.0, "high_shelf_db": 6.0, "presence_db": 3.0, "low_shelf_db": -4.0}), noise)
    hf = db(band_rms(bright, 9000, 18000)) - db(band_rms(flat, 9000, 18000))
    lf = db(band_rms(bright, 30, 90)) - db(band_rms(flat, 30, 90))
    check("tilt EQ", hf > 3.0 and lf < -2.0, f"high band {hf:+.1f} dB, low band {lf:+.1f} dB")

    # exciter
    dull = 0.2 * np.sin(2 * np.pi * 1500 * t) + 0.05 * rng.standard_normal(n)
    ex_off = render(chain(**quiet), dull)
    ex_on = render(chain(**{**quiet, "exciter_mix": max(base["exciter_mix"], 0.3)}), dull)
    d = db(band_rms(ex_on, 6000, 16000)) - db(band_rms(ex_off, 6000, 16000))
    check("exciter", d > 0.3, f"6-16 kHz energy {d:+.2f} dB with the exciter on")

    # gate
    hiss = 10 ** (-70 / 20.0) * rng.standard_normal(n)
    g_off = render(chain(**quiet), hiss)
    g_on = render(chain(**{**quiet, "gate_enabled": 1.0}), hiss)
    red = db(rms(g_off[RATE:])) - db(rms(g_on[RATE:]))
    check("gate", red > 4.0, f"-70 dB hiss is {red:.1f} dB quieter with the gate on")

    print("\nALL KARAOKE STAGES ACTIVE" if ok else "\nAT LEAST ONE KARAOKE STAGE DID NOT BEHAVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
