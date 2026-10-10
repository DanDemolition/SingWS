#!/usr/bin/env python3
"""A/B the Rust BGM engine against the BASS engine on the SAME two real files (roadmap item 1, milestone 2).

    .venv/bin/python tools/bgm_ab_compare.py A.mp3 B.mp3 [--xfade-at 100] [--lead 10] [--tail 6] [--xfade-ms 5000] [--volume 0.8]
                                             [--norm-a DB] [--norm-b DB] [--keep DIR]

Both engines render the identical scenario without a sound card: BASS through its decode-only mixer (the way test_bgm_gapless.py
does), Rust through `singws-bgm-render`. The fade starts exactly `lead` seconds after the render begins. Reports, per scenario:
silent frames, peak, per-second RMS of both, the alignment lag between them (decoder start offsets), the difference signal, the
fade-gain curve each engine really applied, and a pass/fail against the plan's targets (gap < 5 ms, fade level within 0.5 dB).
Read-only: writes WAVs only into a scratch directory.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-abbgm-"))

RATE = 48000
RUST_BIN = Path(os.environ.get("CARGO_TARGET_DIR", str(Path.home() / ".cache/singws-rust-target"))) / "release/singws-bgm-render"


def db_to_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


times_holder: list = []


def render_bass(a: str, b: str, a_args) -> np.ndarray:
    import bass_background_engine as bass

    with patch.object(bass.BassBackgroundEngine, "_init_output"):
        e = bass.BassBackgroundEngine()
    if not e.bass.BASS_Init(0, RATE, 0, None, None):
        raise SystemExit("BASS_Init(0) failed")
    e.play = Mock(return_value=True)

    def ensure():
        if not e.mixer:
            e.mixer = e.mix.BASS_Mixer_StreamCreate(RATE, 2, bass.BASS_SAMPLE_FLOAT | bass.BASS_STREAM_DECODE | bass.BASS_MIXER_NONSTOP)

    e._ensure_mixer = ensure
    e.bass.BASS_ChannelGetData.argtypes = [bass.DWORD, ctypes.c_void_p, bass.DWORD]
    e.bass.BASS_ChannelGetData.restype = bass.DWORD

    out: list[np.ndarray] = []

    def pull(frames: int):
        left = frames
        while left > 0:
            n = min(1024, left)
            data = np.empty((n, 2), dtype=np.float32)
            got = e.bass.BASS_ChannelGetData(e.mixer, data.ctypes.data, data.nbytes)
            if got != data.nbytes:
                data[got // 8:] = 0.0
            out.append(data)
            left -= n

    try:
        e.load(a, volume=a_args.volume, paused=True)
        e.set_primary_normalize_gain(db_to_gain(a_args.norm_a))
        first = a_args.xfade_at - a_args.lead
        if first > 0:
            e.seek(first)
        e.preload_secondary(b, norm_gain=db_to_gain(a_args.norm_b))
        pull(round(a_args.lead * RATE))
        times_holder.append(e.get_times())
        ok = e.start_crossfade(b, a_args.xfade_ms, norm_gain=db_to_gain(a_args.norm_b), start_seconds=0.0)
        if not ok:
            raise SystemExit("BASS start_crossfade refused")
        total = 0
        after = 0
        limit = RATE * 600
        while total < limit:
            pull(1024)
            total += 1024
            if e.crossfade_finished():
                e.complete_crossfade()
            if not e.crossfade_active() if hasattr(e, "crossfade_active") else getattr(e, "_crossfade_end_bytes", 0) == 0:
                after += 1024
                if after >= a_args.tail * RATE:
                    break
    finally:
        try:
            e.close()
        except Exception:
            pass
        e.bass.BASS_Free()
    return np.concatenate(out, axis=0)


def read_f32_wav(path: Path) -> np.ndarray:
    raw = path.read_bytes()
    i = raw.index(b"data")
    n = int.from_bytes(raw[i + 4:i + 8], "little")
    return np.frombuffer(raw[i + 8:i + 8 + n], dtype="<f4").reshape(-1, 2).copy()


def render_rust(a: str, b: str, args, out: Path) -> np.ndarray:
    cmd = [str(RUST_BIN), "--out", str(out), "--rate", str(RATE), "--xfade-ms", str(args.xfade_ms), "--xfade-at", str(args.xfade_at),
           "--lead", str(args.lead), "--tail", str(args.tail), "--volume", str(args.volume),
           "--norm-a", str(args.norm_a), "--norm-b", str(args.norm_b), a, b]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"rust render failed: {res.stderr}")
    return read_f32_wav(out), res.stdout


def write_f32_wav(path: Path, x: np.ndarray) -> None:
    data = x.astype("<f4").tobytes()
    hdr = b"RIFF" + (36 + len(data)).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little") + (3).to_bytes(2, "little") \
        + (2).to_bytes(2, "little") + RATE.to_bytes(4, "little") + (RATE * 8).to_bytes(4, "little") + (8).to_bytes(2, "little") \
        + (32).to_bytes(2, "little") + b"data" + len(data).to_bytes(4, "little")
    path.write_bytes(hdr + data)


def rms_db(x: np.ndarray) -> float:
    return 10.0 * np.log10(max(float(np.mean(np.square(x.astype(np.float64)))), 1e-12))


def per_second(x: np.ndarray) -> list[float]:
    return [rms_db(x[i:i + RATE]) for i in range(0, len(x) - RATE + 1, RATE)]


def best_lag(a: np.ndarray, b: np.ndarray, span: int = 4000) -> int:
    """Lag (frames) that best aligns b to a, using the first 5 s of the left channel (pure track A, before any fade)."""
    n = min(len(a), len(b), RATE * 5)
    x, y = a[:n, 0].astype(np.float64), b[:n, 0].astype(np.float64)
    best, best_v = 0, -1e30
    for lag in range(-span, span + 1):
        if lag >= 0:
            v = float(np.dot(x[lag:], y[:n - lag]))
        else:
            v = float(np.dot(x[:n + lag], y[-lag:]))
        if v > best_v:
            best, best_v = lag, v
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--xfade-at", type=float, default=100.0)
    ap.add_argument("--lead", type=float, default=10.0)
    ap.add_argument("--tail", type=float, default=6.0)
    ap.add_argument("--xfade-ms", type=int, default=5000)
    ap.add_argument("--volume", type=float, default=1.0, help="1.0: BASS ignores the master volume on a decode-only mixer, so 0.8 would only show a fixed -1.94 dB")
    ap.add_argument("--norm-a", type=float, default=0.0)
    ap.add_argument("--norm-b", type=float, default=0.0)
    ap.add_argument("--keep", default="")
    args = ap.parse_args()
    if not RUST_BIN.exists():
        raise SystemExit(f"build the renderer first: {RUST_BIN}")
    keep = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="bgm-ab-"))
    keep.mkdir(parents=True, exist_ok=True)

    rust, rust_report = render_rust(args.a, args.b, args, keep / "rust.wav")
    bass = render_bass(args.a, args.b, args)
    write_f32_wav(keep / "bass.wav", bass)
    n = min(len(rust), len(bass))
    print(f"frames: rust {len(rust)}  bass {len(bass)}  (compared over {n})")
    lag = best_lag(bass, rust)
    print(f"alignment: rust lags bass by {lag} frames ({lag * 1000 / RATE:+.2f} ms) -- decoder start offset")
    if lag >= 0:
        r, b = rust[:n - lag], bass[lag:n]
    else:
        r, b = rust[-lag:n], bass[:n + lag]
    m = min(len(r), len(b))
    r, b = r[:m], b[:m]

    print("\nper-second RMS dB   sec  bass   rust   diff")
    pb, pr = per_second(b), per_second(r)
    for i, (x, y) in enumerate(zip(pb, pr)):
        print(f"                    {i:3d} {x:6.1f} {y:6.1f} {y - x:+5.2f}")

    d = (r - b).astype(np.float64)
    print("\nper-second difference rms dB (rust - bass), and the largest single-sample difference in that second:")
    for i in range(0, m - RATE + 1, RATE):
        seg = d[i:i + RATE]
        print(f"                    {i // RATE:3d} {rms_db(seg):7.1f}   max {np.abs(seg).max():.4f}")
    sig = rms_db(b)
    err = rms_db(d)
    print(f"\ndifference signal: rms {err:.1f} dB vs signal {sig:.1f} dB  (SNR {sig - err:.1f} dB), peak |diff| {np.abs(d).max():.4f}")

    fade_start = round(args.lead * RATE)
    fade_len = round(args.xfade_ms / 1000 * RATE)
    win = slice(fade_start, min(fade_start + fade_len, m))
    # worst 100 ms RMS disagreement during the fade
    step = RATE // 10
    worst = 0.0
    for i in range(win.start, win.stop - step, step):
        worst = max(worst, abs(rms_db(r[i:i + step]) - rms_db(b[i:i + step])))
    # silent frames: both channels exactly zero for >= 1 ms inside the played region, outside the known leading silence of B
    def silent_run_ms(x):
        z = (np.abs(x).max(axis=1) < 1e-7)
        longest = cur = 0
        for v in z[fade_start:]:
            cur = cur + 1 if v else 0
            longest = max(longest, cur)
        return longest * 1000.0 / RATE
    sb, sr = silent_run_ms(b), silent_run_ms(r)
    print(f"longest exact-zero run after the fade starts: bass {sb:.1f} ms, rust {sr:.1f} ms")
    print(f"worst 100 ms level disagreement inside the fade: {worst:.2f} dB")
    # Spectral comparison: which band does the difference live in? (Different resampler filters differ near the top of the band.)
    seg = slice(0, min(m, RATE * 20))
    win = np.hanning(seg.stop - seg.start)
    fr = np.fft.rfftfreq(seg.stop - seg.start, 1.0 / RATE)
    F = np.fft.rfft(d[seg, 0] * win)
    G = np.fft.rfft(b[seg, 0].astype(np.float64) * win)
    print("\nband      signal(bass)  difference  margin")
    low_margin = 1e9
    for lo, hi in ((0, 1000), (1000, 5000), (5000, 10000), (10000, 16000), (16000, 20000)):
        mk = (fr >= lo) & (fr < hi)
        sg = 10 * np.log10(np.sum(np.abs(G[mk]) ** 2) + 1e-20)
        df = 10 * np.log10(np.sum(np.abs(F[mk]) ** 2) + 1e-20)
        print(f"{lo:5d}-{hi:5d}  {sg:8.1f} dB  {df:8.1f} dB  {sg - df:6.1f} dB")
        if hi <= 10000:
            low_margin = min(low_margin, sg - df)
    ok_band = low_margin >= 50.0
    print(f"below 10 kHz the difference is at least {low_margin:.1f} dB under the signal (target >= 50 dB)")
    bt = times_holder[0] if times_holder else (0.0, 0.0)
    rt = None
    for line in rust_report.splitlines():
        if line.startswith("times at fade start"):
            parts = line.split()
            rt = (float(parts[parts.index("pos") + 1]), float(parts[parts.index("dur") + 1]))
    if rt:
        print(f"\nreadouts at fade start (audible position, duration): bass {bt[0]:.4f} / {bt[1]:.4f}   rust {rt[0]:.4f} / {rt[1]:.4f}   "
              f"(position diff {abs(bt[0] - rt[0]) * 1000:.2f} ms, duration diff {abs(bt[1] - rt[1]) * 1000:.2f} ms)")
    print("\nrust report:\n" + "\n".join("  " + l for l in rust_report.strip().splitlines()[:2]))

    ok_gap = sr < 5.0 or sr <= sb + 1.0
    ok_fade = worst < 0.5
    ok_all = ok_gap and ok_fade and ok_band
    print("\nRESULT:", "PASS" if ok_all else "REVIEW",
          f"(gap {'ok' if ok_gap else 'FAIL'}, fade level {'ok' if ok_fade else 'FAIL'}, audible-band match {'ok' if ok_band else 'FAIL'})")
    print(f"WAVs kept in {keep}")


if __name__ == "__main__":
    main()
