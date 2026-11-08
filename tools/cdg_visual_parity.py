#!/usr/bin/env python3
"""Parity: `analyze_cdg_visual_bytes` (transition_analysis.py, the app's own module) vs the Rust `singws_cdg::visual` on the same bytes: hand-built screens (cleared, left lit, scrolled,
palette changes after a clear, XOR tiles, transparent colour, borders), fuzzed packet streams that concentrate on the instructions the analysis reads, truncated files, and real CD+G
files (`--real PATH ...`). Compared: every field of the result, exactly.

  CARGO_TARGET_DIR=... python3 tools/cdg_visual_parity.py [--seeds N] [--real FILE.cdg ...]
"""
import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import transition_analysis as ta  # noqa: E402

TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-cdg"))
CLI = TARGET + "/release/singws-cdg-visual-cli"
INSTR = [0x01, 0x02, 0x06, 0x14, 0x18, 0x1C, 0x1E, 0x1F, 0x26, 0x00, 0x03, 0x2A, 0x3F]


def pkt(r, instruction=None, command=0x09):
    instruction = r.choice(INSTR) if instruction is None else instruction
    data = bytes(r.choice([0, 0xFF, r.randrange(256)]) for _ in range(16))
    if instruction in (0x06, 0x26) and r.random() < 0.8:
        data = bytes([r.randrange(16), r.randrange(16), r.randrange(0, 20), r.randrange(0, 52)]) + bytes(r.choice([0, 0x3F, r.randrange(64)]) for _ in range(12))
    return bytes([command, instruction, 0, 0]) + data + bytes(4)


def stream(r):
    kind = r.randrange(6)
    out = bytearray()
    if kind == 0:                                             # a song: lyrics lit tile by tile, then cleared, then a tail
        out += pkt(r, 0x01)
        for _ in range(r.randrange(1, 40)):
            out += pkt(r, 0x06)
            out += bytes(24 * r.randrange(0, 12))
        if r.random() < 0.8:
            out += pkt(r, 0x01)
        out += bytes(24 * r.randrange(0, 600))
        if r.random() < 0.3:
            out += pkt(r, r.choice([0x1E, 0x1F, 0x1C, 0x14, 0x18, 0x02]))
            out += bytes(24 * r.randrange(0, 400))
    elif kind == 1:                                           # everything at random
        for _ in range(r.randrange(0, 300)):
            out += pkt(r, command=r.choice([0x09, 0x09, 0x09, 0x00, 0x49, 0x29]))
            if r.random() < 0.3:
                out += bytes(24 * r.randrange(0, 20))
    elif kind == 2:                                           # one colour cleared to another, XOR tiles that cancel out
        for _ in range(r.randrange(1, 6)):
            tile = pkt(r, 0x26)
            out += tile + tile + pkt(r, 0x01) + bytes(24 * r.randrange(0, 400))
    elif kind == 3:
        out += bytes(24 * r.randrange(0, 50))                 # nothing at all
    elif kind == 4:                                           # tiles written far past the screen edge, and the exact edge
        for row, col in ((0, 0), (17, 49), (17, 50), (18, 0), (31, 63), (0, 63), (16, 49)):
            out += bytes([0x09, 0x06, 0, 0, 2, 3, row, col]) + bytes([0x3F] * 12) + bytes(4)
        out += pkt(r, 0x01) + bytes(24 * r.randrange(0, 400))
    else:
        out += pkt(r, 0x01) + pkt(r, 0x06) + pkt(r, 0x01) + bytes(24 * r.randrange(250, 350))   # the 1.0 second boundary: 300 packets
    if r.random() < 0.2:
        out += bytes(r.randrange(1, 24))                       # a torn last packet
    return bytes(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3000)
    ap.add_argument("--real", nargs="*", default=[])
    a = ap.parse_args(argv)
    cases = []
    for seed in range(a.seeds):
        r = random.Random(seed)
        cases.append((f"seed {seed}", stream(r), r.choice([1.0, 1.0, 0.0, 0.5, 2.5, -1.0, 1.0 + 1e-9, 0.01])))
    for path in a.real:
        cases.append((path, Path(path).read_bytes(), 1.0))
    vcases = []
    for seed in range(a.seeds):
        r = random.Random(100000 + seed)
        n = r.choice([0, 1, 2, 3, 5, 12, 40])
        total = r.choice([0.0, 30.0, 180.0, 240.5, -1.0])
        end = total if total > 0 else 100.0
        samples = []
        for i in range(n):
            t = r.choice([i * end / max(n, 1), r.uniform(-1, end + 1), end + 0.25, end + 0.26, 0.0])
            black = r.random() < 0.5
            samples.append((t, r.choice([0.0, 0.03, 0.031, 0.5]) if black else r.choice([0.2, 0.9, 0.03, 0.04]), r.choice([0.0, 0.01, 0.011, 0.3]) if r.random() < 0.6 else r.choice([0.0, 0.5])))
        if r.random() < 0.15:
            samples = [(s[0], 0.0, 0.0) for s in samples[: max(1, n // 2)]] + [(s[0] + end * 0.6, 0.0, 0.0) for s in samples]
        vcases.append((f"video seed {seed}", samples, total, r.choice([1.0, 0.0, 0.5, -2.0, 2.0]), r.choice([(0.03, 0.01), (0.03, 0.01), (0.1, 0.05), (0.0, 0.0)])))
    vlines = "\n".join(f"video {m} {t} {b} {s} {';'.join(f'{x},{y},{z}' for x, y, z in smp) or '-'}" for _, smp, t, m, (b, s) in vcases) + "\n"
    lines = "\n".join(f"{m} {b.hex() or '-'}" for _, b, m in cases) + "\n"
    out = subprocess.run([CLI], input=lines, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("cli failed: " + out.stderr[:300])
    got = [json.loads(l) for l in out.stdout.splitlines()]
    vout = subprocess.run([CLI], input=vlines, capture_output=True, text=True)
    vgot = [json.loads(l) for l in vout.stdout.splitlines()]
    vreasons, vdiffs = {}, []
    for (label, smp, total, minimum, (black, stat)), g in zip(vcases, vgot):
        v = ta.analyze_video_tail_samples([ta.VideoFrameSample(*s) for s in smp], duration=total, black_luma_max=black, static_difference_max=stat, minimum_black_tail_seconds=minimum)
        want = {"visual_start": v.visual_start, "visual_end": v.visual_end, "confidence": v.confidence, "method": v.method, "safe": v.safe_for_early_completion, "reason": v.reason}
        vreasons[v.reason] = vreasons.get(v.reason, 0) + 1
        if want != g:
            vdiffs.append((label, want, g))
    print(f"video cases={len(vcases)} reasons: " + ", ".join(f"{k}={n}" for k, n in sorted(vreasons.items())))
    for label, want, g in vdiffs[:3]:
        print(f" - {label}\n   py={want}\n   rs={g}")
    diffs, reasons = [], {}
    for (label, payload, minimum), g in zip(cases, got):
        v = ta.analyze_cdg_visual_bytes(payload, minimum_blank_tail_seconds=minimum)
        want = {"visual_start": v.visual_start, "visual_end": v.visual_end, "duration": v.duration, "confidence": v.confidence, "method": v.method,
                "safe": v.safe_for_early_completion, "reason": v.reason, "last_change": v.last_change}
        reasons[v.reason] = reasons.get(v.reason, 0) + 1
        if want != g:
            diffs.append((label, want, g))
    print(f"cases={len(cases)} reasons: " + ", ".join(f"{k}={n}" for k, n in sorted(reasons.items())))
    for label, want, g in diffs[:4]:
        print(f" - {label}\n   py={want}\n   rs={g}")
    diffs = diffs + vdiffs
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
