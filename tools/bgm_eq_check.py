#!/usr/bin/env python3
"""Prove the BACKGROUND-MUSIC graphic EQ is active in the real BASS engine, band by band, using the operator's own curve.

    .venv/bin/python tools/bgm_eq_check.py [--settings ~/SingWS/settings.json]

For each of the ten EQ band centres it plays a pure tone through the real BassBackgroundEngine at near-zero volume (inaudible,
the same trick test_bgm_native_eq_bandwidth.py uses), once with the EQ detached and once with the operator's `eq_bgm` curve
attached the way the app attaches it, and compares the measured level change with the gain that was set. Exit status 0 when
every band is within tolerance. Uses the default output device but at -54 dB, so nothing is audible.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-bgmeq-"))

SR = 48000
BANDS = (31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)
TOLERANCE_DB = 2.5   # the ten DX8 bands are an octave wide, so neighbours add a little at each centre


def tone(tmp, freq):
    path = Path(tmp) / f"tone{freq}.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SR)
        chunk = bytearray()
        for i in range(SR * 4):
            v = int(0.25 * 32767.0 * math.sin(2.0 * math.pi * freq * i / SR))
            chunk += struct.pack("<hh", v, v)
        wav.writeframes(bytes(chunk))
    return path


def mixer_rms(engine):
    n = 8192
    buf = (ctypes.c_float * n)()
    got = engine.bass.BASS_ChannelGetData(engine.mixer, buf, (n * 4) | 0x40000000)
    if got in (-1, 0):
        return -1.0
    m = min(got // 4, n)
    return math.sqrt(sum(buf[i] * buf[i] for i in range(m)) / max(1, m))


def measure(engine, path, eq):
    engine.set_eq(eq)
    engine.load(str(path), paused=False)
    time.sleep(0.6)
    vals = [v for v in (mixer_rms(engine) for _ in range(6)) if v > 0]
    engine.stop()
    return sum(vals) / len(vals) if vals else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", default=os.path.expanduser("~/SingWS/settings.json"))
    args = ap.parse_args()
    from bass_background_engine import BassBackgroundEngine
    from singws_eq import GraphicEQ

    raw = json.load(open(args.settings))
    gains = [float(v or 0.0) for v in (raw.get("eq_bgm") or [0.0] * 10)]
    enabled = bool(raw.get("eq_bgm_enabled", False))
    print("BGM EQ enabled=%s gains=%s" % (enabled, gains))
    eq = GraphicEQ(sample_rate=SR, channels=2)
    eq.set_all_gains_db(gains)
    eq.set_enabled(True)
    engine = BassBackgroundEngine()
    engine.set_master_volume(0.002)
    ok = True
    try:
        with tempfile.TemporaryDirectory() as tmp:
            print(f"{'band':>8} {'set':>6} {'measured':>9}")
            for freq, gain in zip(BANDS, gains):
                path = tone(tmp, freq)
                off = measure(engine, path, None)
                on = measure(engine, path, eq)
                if off <= 0 or on <= 0:
                    print(f"{freq:>7.1f}  no measurable data (BASS output unavailable?)")
                    ok = False
                    continue
                delta = 20.0 * math.log10(on / off)
                good = abs(delta - max(-12.0, min(12.0, gain))) <= TOLERANCE_DB
                ok &= good
                print(f"{freq:>6.0f}Hz {gain:>+5.1f}dB {delta:>+8.2f}dB  {'PASS' if good else 'FAIL'}")
    finally:
        engine.close()
    print("\nBGM EQ ACTIVE ON EVERY BAND" if ok else "\nAT LEAST ONE BGM EQ BAND DID NOT BEHAVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
