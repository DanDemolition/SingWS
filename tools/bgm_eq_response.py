#!/usr/bin/env python3
"""Measure the real BASS background-music EQ's frequency response (gain in dB at many frequencies) for a given set of ten band gains.

    .venv/bin/python tools/bgm_eq_response.py --gains 0,0,0,0,0,12,0,0,0,0 --out resp.json

Uses the same near-inaudible method as bgm_eq_check.py (-54 dB master volume). The result is the reference the Rust EQ is matched to.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-eqresp-"))

import bgm_eq_check as chk  # noqa: E402

FREQS = [round(20 * (20000 / 20) ** (i / 39)) for i in range(40)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gains", default="0,0,0,0,0,12,0,0,0,0")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    gains = [float(v) for v in args.gains.split(",")]
    from bass_background_engine import BassBackgroundEngine
    from singws_eq import GraphicEQ

    eq = GraphicEQ(sample_rate=chk.SR, channels=2)
    eq.set_all_gains_db(gains)
    eq.set_enabled(True)
    engine = BassBackgroundEngine()
    engine.set_master_volume(0.002)
    out = {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for f in FREQS:
                path = chk.tone(tmp, f)
                off = chk.measure(engine, path, None)
                on = chk.measure(engine, path, eq)
                out[f] = 20.0 * math.log10(on / off) if on > 0 and off > 0 else None
                print(f"{f:6d} Hz {out[f]:+7.2f} dB" if out[f] is not None else f"{f:6d} Hz  (no data)", flush=True)
    finally:
        engine.close()
    if args.out:
        Path(args.out).write_text(json.dumps({"gains": gains, "response": out}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
