#!/usr/bin/env python3
"""Parity: `detect_lead_silence`, `detect_trailing_silence` and `audible_through_end` of phrase_detect.py (the app's own numpy code; only the libmpv decode and the metadata probe are
replaced by the synthetic PCM / duration the scenario supplies) vs the Rust `singws_analysis::tail` on the same samples. Synthetic programmes: loud music, steady analogue hiss
tails (the adaptive-floor rule), quiet outros, silent tails, fades, one channel louder than the other, too-short decodes. Compared: the number each returns, exactly.

  CARGO_TARGET_DIR=... python3 tools/tail_silence_parity.py [--cases N]
"""
import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import phrase_detect as pd  # noqa: E402

CLI = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-karafun")) + "/release/singws-pcm-cli"


def make_pcm(r, sr):
    window = max(1, sr // 10)
    n_windows = r.choice([0, 1, 2, 5, 12, 40, 60, 90, 150, 200, 260])
    levels = []
    kind = r.randrange(7)
    music = r.uniform(-24, -8)
    for w in range(n_windows):
        if kind == 0:                                   # music to the end
            levels.append(music + r.uniform(-4, 2))
        elif kind == 1:                                 # music, then steady hiss (the adaptive-floor case)
            cut = int(n_windows * r.uniform(0.3, 0.9))
            levels.append(music + r.uniform(-4, 2) if w < cut else r.uniform(-49, -43) if r.random() < 0.95 else r.uniform(-60, -35))
        elif kind == 2:                                 # music, then digital silence
            cut = int(n_windows * r.uniform(0.3, 0.95))
            levels.append(music + r.uniform(-4, 2) if w < cut else -120.0)
        elif kind == 3:                                 # quiet outro
            cut = int(n_windows * 0.7)
            levels.append(music + r.uniform(-4, 2) if w < cut else r.uniform(-62, -50))
        elif kind == 4:                                 # silence, music (a lead-in)
            lead = r.choice([0, 2, 4, 6, 9, 15, 150])
            levels.append(-100.0 if w < lead else music + r.uniform(-4, 2))
        elif kind == 5:                                 # a fade
            levels.append(music - 70.0 * w / max(1, n_windows))
        else:                                           # everything silent / noise floor
            levels.append(r.choice([-120.0, -80.0, -66.0, -64.9, -65.1, -50.0, -49.9, -45.0]))
    rng = np.random.default_rng(r.randrange(2**31))
    chans = []
    for c in range(2):
        gain_db = 0.0 if c == 0 or r.random() < 0.6 else r.uniform(-12, 4)
        seg = []
        for lv in levels:
            amp = 10 ** ((lv + gain_db) / 20) * 1.7320508          # uniform noise of amplitude A has RMS A / sqrt(3)
            seg.append(rng.uniform(-1, 1, window) * min(amp, 0.999))
        chans.append(np.concatenate(seg) if seg else np.zeros(0))
    tail_extra = r.choice([0, 0, 0, 3, 77])
    stereo = np.stack(chans, axis=1) if n_windows else np.zeros((0, 2))
    pcm = np.clip(np.round(stereo * 32768.0), -32768, 32767).astype(np.int16).reshape(-1)
    if tail_extra:
        pcm = np.concatenate([pcm, rng.integers(-3000, 3000, tail_extra * 2).astype(np.int16)])
    if r.random() < 0.1:
        pcm = pcm[:-1]                                   # an odd sample count
    return pcm


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=900)
    a = ap.parse_args(argv)
    here = str(Path(__file__).resolve())
    cases, ops = [], []
    for seed in range(a.cases):
        r = random.Random(seed)
        sr = r.choice([1000, 1000, 1000, 2000, 16000 if seed % 40 == 0 else 1000])
        pcm = make_pcm(r, sr)
        flat = (pcm.astype(np.float32) / np.float32(32768.0)).astype(np.float32)
        pd._decode_pcm = lambda *args, _s=flat, **kw: _s
        pd.probe_duration_seconds = lambda p, _d=r.choice([60.0, 12.0, 200.0, 0.0]): _d
        kind = r.choice(["lead", "trailing", "trailing", "audible", "audible"])
        if kind == "lead":
            noise, minimum = r.choice([-50.0, -40.0, -60.0, -30.0]), r.choice([0.5, 0.0, 1.0, 0.3])
            want = pd.detect_lead_silence(here, noise_db=noise, min_silence=minimum, sr=sr)
            ops.append({"op": "lead", "sr": sr, "pcm": pcm.tolist(), "noise_db": noise, "min_silence": minimum})
        elif kind == "trailing":
            noise = r.choice([-65.0, -65.0, -55.0, -50.0, -75.0])
            hint = r.choice([0.0, 40.0])
            try:
                want = pd.detect_trailing_silence(here, noise_db=noise, sr=sr, duration_hint=hint, raise_on_error=True)
            except Exception:
                want = "error"
            ops.append({"op": "trailing", "sr": sr, "pcm": pcm.tolist(), "noise_db": noise})
            if pd.probe_duration_seconds(here) <= 0 and hint <= 0:
                want = "error"                                    # the duration check happens before any decode: not the pure core's business
                ops[-1]["skip"] = True
        else:
            window, threshold = r.choice([0.5, 0.5, 0.2, 1.0, 0.05]), r.choice([-40.0, -40.0, -30.0, -50.0])
            pd_duration = pd.probe_duration_seconds(here)
            want = pd.audible_through_end(here, window_seconds=window, threshold_db=threshold, sr=sr, duration_hint=0.0)
            ops.append({"op": "audible", "sr": sr, "pcm": pcm.tolist(), "window": window, "threshold": threshold})
            if pd_duration <= 0:
                ops[-1]["skip"] = True
        cases.append((kind, seed, want))
    out = subprocess.run([CLI], input=json.dumps({"batch": ops}), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("cli failed: " + out.stderr[:300])
    got = json.loads(out.stdout)
    diffs, kinds, skipped = [], {}, 0
    for (kind, seed, want), g, op in zip(cases, got, ops):
        if op.get("skip"):
            skipped += 1
            continue
        mine = "error" if "err" in g else g["v"]
        kinds[kind] = kinds.get(kind, 0) + 1
        if isinstance(want, float) and isinstance(mine, (int, float)):
            same = want == mine
        else:
            same = want == mine
        if not same:
            diffs.append((kind, seed, want, mine))
    nonzero = sum(1 for (k, s, w) in cases if isinstance(w, float) and w > 0)
    print(f"cases={len(cases)} skipped={skipped} compared: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())) + f"; non-zero answers={nonzero}; True answers={sum(1 for (k,s,w) in cases if w is True)}")
    for kind, seed, want, mine in diffs[:5]:
        print(f" - {kind} seed {seed}: py={want!r} rs={mine!r}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
