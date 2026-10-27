#!/usr/bin/env python3
"""Differential test: the Rust CDG decoder (rust/crates/singws-cdg) against
FFmpeg's `cdgraphics` decoder, which is what libmpv draws CDG with in the app.

The app has no Python CDG pixel renderer (transition_analysis.py only simulates
tiles to find blank tails), so FFmpeg is the reference. FFmpeg emits one RGBA
frame per 24-byte packet; the Rust `singws-cdg-dump` does the same and the two
streams are compared byte for byte, frame by frame.

Streams: hand-built ones covering every instruction (including scroll in all
direction/offset combinations, repeats, out-of-range tiles, non-CDG packets and
unknown instructions) plus seeded random fuzz, plus any real .cdg files given
with --cdg (extract them to a temp folder first; never the live library).

    python3 tools/cdg_diff.py [--dump PATH] [--fuzz N] [--cdg FILE ...]
Exit status 0 only if every frame of every stream is identical.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

W, H = 300, 216
FRAME = W * H * 4
HERE = Path(__file__).resolve().parent


def pk(inst: int, data: bytes = b"", cmd: int = 9) -> bytes:
    return bytes([cmd, inst, 0, 0]) + bytes(data).ljust(16, b"\0")[:16] + bytes(4)


def pal_pkts(rng: random.Random):
    out = []
    for inst in (30, 31):
        d = bytearray()
        for _ in range(8):
            c = rng.randrange(4096)
            d += bytes([(c >> 6) & 0x3F, c & 0x3F])
        out.append(pk(inst, d))
    return out


def tile(c0, c1, row, col, rows, xor=False):
    return pk(38 if xor else 6, bytes([c0, c1, row, col]) + bytes(rows))


def structured(seed: int) -> bytes:
    rng = random.Random(seed)
    p = []
    p += pal_pkts(rng)
    p.append(pk(1, bytes([3])))                 # memory preset
    p.append(pk(1, bytes([4, 1])))              # repeat != 0: ignored
    p.append(pk(2, bytes([5])))                 # border
    p.append(pk(2, bytes([6, 1])))              # border with repeat byte
    p.append(pk(28, bytes([2])))                # define transparent (ignored)
    for r in range(18):
        for c in range(0, 50, 7):
            p.append(tile(rng.randrange(16), rng.randrange(16), r, c, [rng.randrange(256) for _ in range(12)], xor=rng.random() < 0.3))
    p.append(tile(1, 2, 31, 0, [63] * 12))     # row out of range
    p.append(tile(1, 2, 0, 63, [63] * 12))     # column out of range
    p.append(tile(1, 2, 17, 49, [63] * 12))    # last legal tile
    p.append(pk(1 + 0x40, bytes([7])))         # high bits on instruction (unmasked by FFmpeg)
    p.append(pk(1, bytes([8]), cmd=0x49))      # command with high bits set: masked
    p.append(pk(1, bytes([9]), cmd=0x08))      # not a CDG command
    p.append(pk(99, bytes(16)))                # unknown instruction
    p += pal_pkts(rng)
    # every scroll combination, preset then copy, with fine offsets
    for inst in (20, 24):
        for hs in range(4):
            for vs in range(4):
                for ho in (0, 3, 7):
                    for vo in (0, 5, 15):
                        # a palette before every scroll: FFmpeg's no-op scroll
                        # wipes it, and a transparent screen would hide any error
                        p += pal_pkts(rng)
                        p.append(tile(rng.randrange(16), rng.randrange(16), 3, 3, [rng.randrange(256) for _ in range(12)]))
                        p.append(pk(inst, bytes([rng.randrange(16), (hs << 4) | ho, (vs << 4) | vo])))
                        p.append(tile(rng.randrange(16), rng.randrange(16), 5, 8, [rng.randrange(256) for _ in range(12)], xor=True))
    p.append(pk(1, bytes([0])))
    return b"".join(p)


def fuzz(seed: int, n: int = 500) -> bytes:
    rng = random.Random(seed)
    insts = [1, 2, 6, 20, 24, 28, 30, 31, 38, 0, 3, 63, 0x46, 0xA6]
    out = []
    for _ in range(n):
        cmd = 9 if rng.random() < 0.95 else rng.randrange(256)
        data = bytes(rng.randrange(256) for _ in range(16))
        if rng.random() < 0.5:
            data = bytes(b & 0x3F for b in data)
        out.append(bytes([cmd, rng.choice(insts), rng.randrange(256), rng.randrange(256)]) + data + bytes(rng.randrange(256) for _ in range(4)))
    return b"".join(out)


def sanitize(raw: bytes) -> tuple[bytes, int]:
    """Replace scroll packets that move nothing with an unknown instruction.

    FFmpeg's output for such a packet is undefined (it returns a recycled,
    unfilled frame buffer: zeros when fresh, an older frame otherwise), so it
    cannot be a reference. The Rust decoder keeps the picture and counts them.
    """
    h = v = 0
    out, replaced = bytearray(), 0
    for i in range(0, len(raw) - 23, 24):
        p = bytearray(raw[i:i + 24])
        if (p[0] & 0x3F) == 9 and (p[1] & 0x3F) in (20, 24):
            d = p[4:20]
            h_off, v_off = min(d[1] & 7, 5), min(d[2] & 15, 11)
            hs, vs = (d[1] & 0x30) >> 4, (d[2] & 0x30) >> 4
            hinc = h_off - h + (6 if hs == 1 else -6 if hs == 2 else 0)
            vinc = v - v_off + (12 if vs == 1 else -12 if vs == 2 else 0)
            if hinc == 0 and vinc == 0:
                p[1] = 3
                replaced += 1
            else:
                h, v = h_off, v_off
        out += p
    return bytes(out), replaced


def frames(stream):
    while True:
        buf = stream.read(FRAME)
        if not buf:
            return
        while len(buf) < FRAME:
            more = stream.read(FRAME - len(buf))
            if not more:
                raise RuntimeError("short frame")
            buf += more
        yield buf


def compare(path: Path, dump: str) -> tuple[int, int | None, str]:
    """Returns (packets, first differing packet index or None, note)."""
    packets = path.stat().st_size // 24
    # FFmpeg drops the frame for non-CDG packets and out-of-range tiles, so the
    # streams are compared frame by frame, not packet by packet.
    ff = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "cdg", "-i", str(path),
                           "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    rs = subprocess.Popen([dump, str(path)], stdout=subprocess.PIPE)
    bad, count = None, 0
    hashes: list[str] = []
    for i, (x, y) in enumerate(itertools.zip_longest(frames(ff.stdout), frames(rs.stdout))):
        count += 1
        if x is not None:
            hashes.append(hashlib.sha1(x).hexdigest())
        if x is None or y is None:
            bad = (i, "frame count differs: " + ("FFmpeg ended first" if x is None else "Rust ended first"))
        elif x != y:
            bad = (i, f"{sum(1 for k in range(0, FRAME, 4) if x[k:k + 4] != y[k:k + 4])} pixels differ")
        if bad:
            ff.kill(); rs.kill()
            break
    ff.wait(); rs.wait()
    if bad is not None:
        return packets, bad[0], bad[1]
    note = time_points(path, dump, hashes)
    if note:
        return count, count, note
    return count, None, "identical"


def time_points(path: Path, dump: str, hashes: list[str]) -> str | None:
    """render_at(t) must equal FFmpeg's frame in force at t.

    FFmpeg's frame pts is the packet index, so the picture at t is the last
    emitted frame with pts <= floor(t*300) (nothing before the first one).
    """
    pts = [int(x) for x in subprocess.run(
        ["ffprobe", "-v", "error", "-f", "cdg", "-show_entries", "frame=pts", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.split()]
    if len(pts) != len(hashes):
        return f"ffprobe saw {len(pts)} frames, decode gave {len(hashes)}"
    n = path.stat().st_size // 24
    rng = random.Random(path.stat().st_size)
    times = [0.0, -1.0, n / 300.0 + 5] + [rng.uniform(0, n / 300.0) for _ in range(30)]
    out = subprocess.run([dump, str(path)] + [x for t in times for x in ("--at", repr(t))], capture_output=True).stdout
    got = [hashlib.sha1(out[i * FRAME:(i + 1) * FRAME]).hexdigest() for i in range(len(times))]
    blank = hashlib.sha1(bytes(FRAME)).hexdigest()
    for t, g in zip(times, got):
        idx = 0 if t <= 0 else int(t * 300)
        idx = min(idx, n - 1)
        want = [h for q, h in zip(pts, hashes) if q <= idx]
        want = want[-1] if want else blank
        if g != want:
            return f"render_at({t:.4f}) differs from FFmpeg's frame in force"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=os.environ.get("CARGO_TARGET_DIR", str(Path.home() / ".cache/singws-rust-target-cdg")) + "/release/singws-cdg-dump")
    ap.add_argument("--fuzz", type=int, default=40)
    ap.add_argument("--cdg", nargs="*", default=[])
    ap.add_argument("--keep", help="write generated streams here")
    a = ap.parse_args()
    tmp = Path(a.keep) if a.keep else Path(tempfile.mkdtemp(prefix="cdgdiff-"))
    tmp.mkdir(parents=True, exist_ok=True)
    jobs = []
    for s in range(4):
        f = tmp / f"structured_{s}.cdg"; f.write_bytes(sanitize(structured(s))[0]); jobs.append(f)
    for s in range(a.fuzz):
        f = tmp / f"fuzz_{s}.cdg"; f.write_bytes(sanitize(fuzz(s))[0]); jobs.append(f)
    for c in a.cdg:
        f = tmp / ("real_" + Path(c).name); data, n = sanitize(Path(c).read_bytes()); f.write_bytes(data)
        if n:
            print(f"{c}: {n} no-op scroll packets neutralised (FFmpeg output undefined)")
        jobs.append(f)
    failed, total = 0, 0
    for f in jobs:
        n, bad, note = compare(f, a.dump)
        total += n
        if bad is not None:
            failed += 1
            print(f"DIFF  {f.name}: frame {bad}: {note}")
    print(f"{len(jobs)} streams (frame-by-frame plus 33 time points each), {total} frames compared, {failed} streams differ")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
