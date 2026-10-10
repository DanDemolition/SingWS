#!/usr/bin/env python3
"""Prove the Rust BGM engine's effect chain (graphic EQ + full master chain) does what the BASS engine does (roadmap item 1, milestone 4).

    .venv/bin/python tools/bgm_rust_engine_check.py [--settings ~/SingWS/settings.json] [--bass-json PATH] [--skip-eq] [--skip-chain]

Part A, EQ: the operator's own BGM EQ curve (settings `eq_bgm`) and a harsh +/-12 dB alternating curve are run through the Rust engine
(`singws-bgm-render --process --eq ...`) and the measured gain at 40 frequencies is compared with the REAL BASS engine measured by
tools/bgm_eq_response.py (BASS plays near-silently on the default output, as bgm_eq_check.py does). Tolerance 0.5 dB.

Part B, master chain: tools/master_dsp_stage_check.py (gate, tilt EQ, exciter, compressor, limiter, output clip, live slider change,
agreement with the Python reference) is run unchanged with the Rust ENGINE substituted for the Rust library, so the same signals and
pass criteria apply to the engine path (decks -> EQ -> master chain -> output).
Exit status 0 when everything passes.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-rustenginecheck-"))

RENDER = Path(os.environ.get("CARGO_TARGET_DIR", str(Path.home() / ".cache/singws-rust-target"))) / "release/singws-bgm-render"
FREQS = [round(20 * (20000 / 20) ** (i / 39)) for i in range(40)]


def write_f32_wav(path: Path, x: np.ndarray, rate: int) -> None:
    data = np.asarray(x, dtype="<f4").tobytes()
    hdr = (b"RIFF" + (36 + len(data)).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little") + (3).to_bytes(2, "little")
           + (2).to_bytes(2, "little") + rate.to_bytes(4, "little") + (rate * 8).to_bytes(4, "little") + (8).to_bytes(2, "little")
           + (32).to_bytes(2, "little") + b"data" + len(data).to_bytes(4, "little"))
    path.write_bytes(hdr + data)


def read_f32_wav(path: Path) -> np.ndarray:
    raw = path.read_bytes()
    i = raw.index(b"data")
    n = int.from_bytes(raw[i + 4:i + 8], "little")
    return np.frombuffer(raw[i + 8:i + 8 + n], dtype="<f4").reshape(-1, 2).copy()


def engine_process(signal: np.ndarray, rate: int, eq=None, schedule=None) -> np.ndarray:
    """Run a stereo float32 signal through the Rust engine's effect chain and return the output (same length)."""
    with tempfile.TemporaryDirectory() as tmp:
        inp, out = Path(tmp) / "in.wav", Path(tmp) / "out.wav"
        write_f32_wav(inp, signal, rate)
        cmd = [str(RENDER), "--process", "--rate", str(rate), "--volume", "1.0", "--out", str(out)]
        if eq is not None:
            cmd += ["--eq", ",".join(f"{g:.4f}" for g in eq)]
        if schedule:
            pf = Path(tmp) / "params.txt"
            lines = []
            for at, params in schedule:
                lines.append(f"at {int(at)}")
                lines += [f"{k} {float(v)!r}" for k, v in params.items()]
            pf.write_text("\n".join(lines) + "\n")
            cmd += ["--master-params", str(pf)]
        cmd.append(str(inp))
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"renderer failed: {res.stderr.strip()}")
        return read_f32_wav(out)


class RustEngineProc:
    """Drop-in for RustMasterProcessor/MasterAudioProcessor: every call re-renders the history through the Rust ENGINE."""

    def __init__(self, sample_rate=44100.0, channels=2):
        from singws_master_audio import DEFAULT_PARAMS
        self.rate = int(sample_rate)
        self._names = list(DEFAULT_PARAMS)
        self._defaults = dict(DEFAULT_PARAMS)
        self.params = dict(DEFAULT_PARAMS)
        self.history = np.zeros((0, 2), dtype=np.float32)
        self.sched = [(0, dict(self.params))]
        self.enabled = False

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)

    def set_params(self, params):
        self.params = {**self._defaults, **{k: v for k, v in dict(params).items() if k in self._names}}
        at = len(self.history)
        if at == 0:
            self.sched = [(0, dict(self.params))]
        else:
            self.sched.append((at, dict(self.params)))

    def reset_state(self):
        self.history = np.zeros((0, 2), dtype=np.float32)
        self.sched = [(0, dict(self.params))]

    def process_f32_array(self, samples):
        start = len(self.history)
        self.history = np.concatenate([self.history, np.asarray(samples, dtype=np.float32)], axis=0)
        if not self.enabled:
            return samples
        out = engine_process(self.history, self.rate, schedule=self.sched)
        return out[start:start + len(samples)]


def gain_at(rate: int, freq: float, eq) -> float:
    n = rate * 2
    t = np.arange(n) / rate
    sig = (0.1 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    x = np.stack([sig, sig], axis=1)
    y = engine_process(x, rate, eq=eq)
    a, b = x[rate:, 0].astype(np.float64), y[rate:, 0].astype(np.float64)
    return 20 * math.log10(math.sqrt(np.mean(b * b)) / math.sqrt(np.mean(a * a)))


def part_a(args) -> bool:
    raw = {}
    try:
        raw = json.load(open(args.settings))
    except Exception:
        pass
    mine = [max(-12.0, min(12.0, float(v or 0.0))) for v in (raw.get("eq_bgm") or [0.0] * 10)]
    curves = {"your BGM EQ curve": mine, "harsh alternating +/-12 dB": [12, -12, 12, -12, 12, -12, 12, -12, 12, -12]}
    ok = True
    for name, gains in curves.items():
        print(f"\nEQ: {name}  gains={gains}")
        ref = None
        if args.bass_json and name == "your BGM EQ curve" and Path(args.bass_json).exists():
            ref = {float(k): v for k, v in json.load(open(args.bass_json))["response"].items()}
        if ref is None:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "bass.json"
                env = dict(os.environ)
                r = subprocess.run([sys.executable, str(ROOT / "tools/bgm_eq_response.py"), "--gains", ",".join(map(str, gains)), "--out", str(out)],
                                   capture_output=True, text=True, env=env)
                if r.returncode != 0 or not out.exists():
                    print("  BASS measurement failed:", (r.stderr or r.stdout)[-300:])
                    ok = False
                    continue
                ref = {float(k): v for k, v in json.load(open(out))["response"].items()}
        worst, worst_f = 0.0, 0.0
        rows = []
        for f in FREQS:
            if ref.get(float(f)) is None:
                continue
            g = gain_at(48000, f, gains)
            d = g - ref[float(f)]
            rows.append((f, ref[float(f)], g, d))
            if abs(d) > abs(worst):
                worst, worst_f = d, f
        print("   Hz     BASS    Rust   diff")
        for f, b, r_, d in rows[::3]:
            print(f"  {f:6d} {b:+6.2f} {r_:+6.2f} {d:+5.2f}")
        good = abs(worst) <= 0.5
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] worst difference {worst:+.2f} dB at {worst_f} Hz over {len(rows)} frequencies (tolerance 0.5 dB)")
    return ok


def part_b(args) -> bool:
    import master_dsp_stage_check as chk

    fake = types.ModuleType("rust_master_dsp")
    fake.RustMasterProcessor = RustEngineProc
    fake.RustDspUnavailable = RuntimeError
    sys.modules["rust_master_dsp"] = fake
    if not RENDER.exists():
        print("build the renderer first:", RENDER)
        return False
    print("\nMaster chain through the Rust ENGINE (tools/master_dsp_stage_check.py with the engine substituted):")
    sys.argv = ["master_dsp_stage_check.py", "--settings", args.settings]
    return chk.main() == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", default=os.path.expanduser("~/SingWS/settings.json"))
    ap.add_argument("--bass-json", default="")
    ap.add_argument("--skip-eq", action="store_true")
    ap.add_argument("--skip-chain", action="store_true")
    args = ap.parse_args()
    if not RENDER.exists():
        print("build the renderer first: cd rust && cargo build --release -p singws-bgm ->", RENDER)
        return 2
    ok = True
    if not args.skip_eq:
        ok &= part_a(args)
    if not args.skip_chain:
        ok &= part_b(args)
    print("\nRUST ENGINE EFFECT CHAIN MATCHES" if ok else "\nRUST ENGINE EFFECT CHAIN DID NOT PASS")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
