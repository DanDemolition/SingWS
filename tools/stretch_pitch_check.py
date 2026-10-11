#!/usr/bin/env python3
"""Proves key and tempo are independent in the libmpv runtime's `signalsmith` audio filter (or `rubberband` for comparison).

    .venv-test-arm64-fresh/bin/python tools/stretch_pitch_check.py [--engine signalsmith|rubberband] [--tolerance-cents 3]

A pure two-tone signal goes through mpv offline exactly as the app routes it (`af=<engine>=pitch-scale=...` for the key, the `speed`
property for the tempo). The output's pitch is measured by FFT and its length compared with input/speed. Tempo alone must never move the
pitch; key alone must move it by exactly the requested semitones; the two together must both be exact. Exit status 1 on any failure.
"""
from __future__ import annotations

import argparse
import math
import os
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-pitchcheck-"))

RATE = 44100
F1, F2 = 440.0, 1320.0


def write_wav(path, samples):
    pcm = (np.clip(samples, -1, 1) * 32767.0).astype("<i2")
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + stereo.nbytes) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, RATE, RATE * 4, 4, 16))
        fh.write(b"data" + struct.pack("<I", stereo.nbytes) + stereo.tobytes())


def read_wav_mono(path):
    raw = open(path, "rb").read()
    pos = raw.find(b"data")
    fmt = raw.find(b"fmt ")
    code, ch = struct.unpack("<HH", raw[fmt + 8:fmt + 12])
    bits = struct.unpack("<H", raw[fmt + 22:fmt + 24])[0]
    data = raw[pos + 8:]
    dt = {(1, 16): "<i2", (3, 32): "<f4", (65534, 16): "<i2", (65534, 32): "<f4"}[(code, bits)]
    a = np.frombuffer(data[: len(data) // (bits // 8) * (bits // 8)], dtype=dt).astype(np.float64)
    if dt == "<i2":
        a /= 32768.0
    return a.reshape(-1, ch)[:, 0]


def peak_hz(x, lo, hi):
    n = 1 << 18
    w = np.hanning(len(x))
    spec = np.abs(np.fft.rfft(x * w, n))
    freqs = np.fft.rfftfreq(n, 1.0 / RATE)
    sel = (freqs >= lo) & (freqs <= hi)
    i = np.argmax(np.where(sel, spec, 0))
    a, b, c = np.log(spec[i - 1] + 1e-12), np.log(spec[i] + 1e-12), np.log(spec[i + 1] + 1e-12)
    d = 0.5 * (a - c) / (a - 2 * b + c)
    return (i + d) * RATE / n


def run(engine, src, semitones, speed):
    from libmpv_media_jobs import OfflineMpvJob
    out = os.path.join(tempfile.mkdtemp(prefix="singws-pc-"), "o.wav")
    job = OfflineMpvJob()
    try:
        for k, v in (("config", "no"), ("vid", "no"), ("ao", "pcm"), ("ao-pcm-file", out), ("ao-pcm-waveheader", "yes"),
                     ("audio-format", "s16"), ("audio-samplerate", str(RATE)), ("audio-channels", "stereo"), ("untimed", "yes")):
            job.option(k, v)
        if semitones or speed != 1.0:
            job.option("af", f"{engine}=pitch-scale={2 ** (semitones / 12.0):.6f}")
        if speed != 1.0:
            job.option("speed", f"{speed:.6f}")
        job.initialize()
        job.command("loadfile", src, "replace")
        job.wait_for_end(180.0)
    finally:
        job.close()
    return read_wav_mono(out)


def cents(measured, expected):
    return 1200.0 * math.log2(measured / expected)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="signalsmith")
    ap.add_argument("--tolerance-cents", type=float, default=6.0)
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()
    t = np.arange(int(RATE * args.seconds)) / RATE
    src = os.path.join(tempfile.mkdtemp(prefix="singws-pcsrc-"), "tone.wav")
    write_wav(src, 0.3 * np.sin(2 * np.pi * F1 * t) + 0.2 * np.sin(2 * np.pi * F2 * t))
    cases = [(0, s) for s in (0.5, 0.75, 0.9, 1.1, 1.5, 2.0)] + [(k, 1.0) for k in (-12, -5, -2, -1, 1, 2, 5, 12)] \
        + [(2, 0.9), (-3, 1.1), (5, 0.75), (-7, 1.5), (12, 2.0), (-12, 0.5)]
    failures = 0
    print(f"engine={args.engine}  tolerance={args.tolerance_cents} cents  input {args.seconds:.1f}s tones {F1:.0f}+{F2:.0f} Hz")
    print(f"{'key':>4} {'tempo':>6} | {'440 err':>9} {'1320 err':>9} {'length':>9} | verdict")
    for semi, speed in cases:
        y = run(args.engine, src, semi, speed)
        mid = y[len(y) // 4: 3 * len(y) // 4]
        pitch = 2 ** (semi / 12.0)
        e1 = cents(peak_hz(mid, F1 * pitch * 0.7, F1 * pitch * 1.4), F1 * pitch)
        e2 = cents(peak_hz(mid, F2 * pitch * 0.7, F2 * pitch * 1.4), F2 * pitch)
        ratio = (len(y) / RATE) / (args.seconds / speed)
        ok = abs(e1) <= args.tolerance_cents and abs(e2) <= args.tolerance_cents and abs(ratio - 1.0) <= 0.02
        failures += 0 if ok else 1
        print(f"{semi:>+4} {speed * 100:>5.0f}% | {e1:>+8.2f}c {e2:>+8.2f}c {ratio:>8.4f}x | {'ok' if ok else 'FAIL'}")
    print("RESULT:", "PASS" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
