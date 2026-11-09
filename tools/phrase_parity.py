#!/usr/bin/env python3
"""Tolerance parity: the waveform-editor analysis of phrase_detect.py (numpy/scipy, float32 FFTs) vs the Rust `singws-phrase` (f64) on synthetic songs (kick and hat patterns at a known
tempo with swing, bass tones, silence, noise, level steps, section changes) and odd inputs. Exact where the maths is exact (peaks, moving average); within a stated tolerance where a float32 FFT is
involved (tempo, beat phase, confidence, suggested start, section times). The agreement rate is the result: a port of numerical code cannot be IDENTICAL, so a threshold is asserted instead.

  CARGO_TARGET_DIR=... python3 tools/phrase_parity.py [--cases N] [--min-agree 0.97]
"""
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import phrase_detect as pd  # noqa: E402

CLI = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-karafun")) + "/release/singws-phrase"


def synth(r, sr):
    seconds = r.choice([4.0, 6.0, 9.0, 14.0, 22.0, 35.0])
    n = int(sr * seconds)
    rng = np.random.default_rng(r.randrange(2**31))
    bpm = r.choice([72.0, 85.0, 96.0, 100.0, 110.0, 120.0, 128.0, 140.0, 150.0, 170.0, r.uniform(75, 175)])
    t = np.arange(n) / sr
    x = np.zeros(n, dtype=np.float64)
    beat = 60.0 / bpm
    kick_every = r.choice([1, 1, 2])
    phase = r.uniform(0, 4 * beat)
    k = 0
    while True:
        at = phase + k * beat * (1 if r.random() > 0.0 else 1)
        if at >= seconds:
            break
        swing = beat / 6 if (k % 2 == 1 and r.random() < 0.3) else 0.0
        i = int((at + swing) * sr)
        if k % kick_every == 0:
            ln = min(int(0.12 * sr), n - i)
            if ln > 0:
                x[i:i + ln] += 0.8 * np.sin(2 * np.pi * 60 * np.arange(ln) / sr) * np.exp(-np.arange(ln) / (0.03 * sr))
        if r.random() < 0.7:
            j = int((at + beat / 2) * sr)
            ln = min(int(0.04 * sr), n - j)
            if ln > 0:
                x[j:j + ln] += 0.25 * rng.standard_normal(ln) * np.exp(-np.arange(ln) / (0.01 * sr))
        k += 1
    for f, a in ((110.0, 0.2), (220.0, 0.1)):
        x += a * np.sin(2 * np.pi * f * t)
    if r.random() < 0.5:                                  # a louder second half (a section change)
        x[n // 2:] *= r.uniform(1.3, 3.0)
    if r.random() < 0.3:
        x[: int(sr * r.uniform(0.5, 3.0))] *= 0.02        # a quiet intro
    if r.random() < 0.15:
        x[:] = rng.standard_normal(n) * 0.1               # noise only
    if r.random() < 0.05:
        x[:] = 0.0                                        # silence
    return np.clip(x * r.uniform(0.2, 1.0), -1.0, 1.0).astype(np.float32), bpm


def run_rust(batch):
    out = subprocess.run([CLI], input=json.dumps({"batch": batch}), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("cli failed: " + out.stderr[:300])
    return json.loads(out.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=120)
    ap.add_argument("--min-agree", type=float, default=0.97)
    a = ap.parse_args(argv)
    work = tempfile.mkdtemp(prefix="phrase_")
    ops, wants = [], []
    for seed in range(a.cases):
        r = random.Random(seed)
        sr = r.choice([4000, 8000, 8000])
        pcm, true_bpm = synth(r, sr)
        path = os.path.join(work, f"{seed}.f32")
        pcm.tofile(path)
        kind = r.choice(["tempo", "tempo", "tempo", "peaks", "suggest", "smooth", "sections", "sections"])
        if kind == "tempo":
            ops.append({"op": "tempo", "sr": sr, "pcm_path": path})
            wants.append(("tempo", pd.estimate_tempo_and_beat(pcm, sr)))
        elif kind == "peaks":
            cols = r.choice([1, 7, 100, 333, 2000])
            ops.append({"op": "peaks", "n_cols": cols, "pcm_path": path})
            wants.append(("peaks", pd.peaks(pcm, cols)))
        elif kind == "suggest":
            bpm = r.choice([None, 120.0, 93.5])
            ops.append({"op": "suggest_start", "sr": sr, "pcm_path": path, "bpm": bpm})
            wants.append(("suggest", pd.suggest_start(pcm, sr, bpm=bpm)))
        elif kind == "sections":
            bpm = r.choice([None, 120.0])
            ops.append({"op": "sections", "sr": sr, "pcm_path": path, "bpm": bpm})
            wants.append(("sections", pd.detect_sections(pcm, sr, bpm=bpm)))
        else:
            w = r.choice([2, 3, 4, 5, 16])
            ops.append({"op": "smooth", "w": w, "pcm_path": path})
            wants.append(("smooth", pd._smooth(pcm[: min(len(pcm), 600)].astype(np.float32), w)))
            ops[-1]["pcm_path"] = path
            ops[-1]["limit"] = 600
    # the smooth op reads only the first 600 samples (the CLI takes the whole file: trim here)
    for op in ops:
        if op.get("limit"):
            data = np.fromfile(op["pcm_path"], dtype=np.float32)[: op["limit"]]
            data.tofile(op["pcm_path"])
    got = run_rust(ops)
    stats, bad = {}, []
    for (kind, want), g, op in zip(wants, got, ops):
        ok = False
        v = g.get("v")
        if kind == "tempo":
            if want is None or v is None:
                ok = (want is None) == (v is None)
            else:
                fb_ok = (want["first_beat"] is None) == (v["first_beat"] is None) and (want["first_beat"] is None or abs(want["first_beat"] - v["first_beat"]) <= 0.02)
                ok = abs(want["bpm"] - v["bpm"]) <= 0.05 and fb_ok and abs(want["confidence"] - v["confidence"]) <= 0.02
        elif kind == "peaks":
            ok = np.allclose(np.array(v, dtype=np.float32), want, atol=1e-7) if v is not None else False
        elif kind == "suggest":
            ok = (want is None) == (v is None) and (want is None or abs(want - v) <= 0.06)
        elif kind == "sections":
            ok = len(want) == len(v) and all(abs(a["seconds"] - b["seconds"]) <= 0.26 and a["label"] == b["label"] for a, b in zip(want, v))
        else:
            ok = np.allclose(np.array(v, dtype=np.float64), want.astype(np.float64), atol=1e-6)
        s = stats.setdefault(kind, [0, 0])
        s[0] += 1
        s[1] += int(ok)
        if not ok:
            bad.append((kind, op, want, v))
    nonempty = sum(1 for (k, w), g in zip(wants, got) if k == "sections" and w)
    print(f"sections with at least one marker: {nonempty}")
    for k, (n, good) in sorted(stats.items()):
        print(f"{k}: {good}/{n} within tolerance")
    total = sum(n for n, _ in stats.values())
    good = sum(g for _, g in stats.values())
    for kind, op, want, v in bad[:5]:
        print(f" - {kind} {op.get('pcm_path', '')[-12:]}: py={str(want)[:120]} rs={str(v)[:120]}")
    rate = good / max(1, total)
    if "sections" in stats:
        sec_rate = stats["sections"][1] / max(1, stats["sections"][0])
        print(f"sections agreement {sec_rate:.3f} (need 0.85)")
        if sec_rate < 0.85:
            rate = 0.0
    exact_kinds = [k for k in ("peaks", "smooth") if k in stats and stats[k][0] != stats[k][1]]
    print(f"agreement {rate:.3f} (need {a.min_agree})" + ("; EXACT-KIND MISMATCH " + ",".join(exact_kinds) if exact_kinds else ""))
    print("AGREE" if rate >= a.min_agree and not exact_kinds else "DISAGREE")
    return 0 if rate >= a.min_agree and not exact_kinds else 1


if __name__ == "__main__":
    sys.exit(main())
