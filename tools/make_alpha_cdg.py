#!/usr/bin/env python3
"""Write a made-up CDG whose palette has PARTIAL alpha (DEFINE_TRANSPARENT before the palette load), for checking what the audience window does with it
(`tools/cdg_window_compare.py alpha.cdg silent.mp3 --at 5`). Colour 0 is opaque black; colours 1..7 are saturated and get alphas 191, 127, 63, 3 (and 255 for the rest)
in vertical bands. No show data. Usage: make_alpha_cdg.py OUT.cdg"""
import sys


def pk(inst, data=b""):
    return bytes([9, inst, 0, 0]) + bytes(data).ljust(16, b"\0")[:16] + bytes(4)


def pal(entries):
    d = b""
    for r, g, b in entries:
        c = (r << 8) | (g << 4) | b
        d += bytes([(c >> 6) & 0x3F, c & 0x3F])
    return d


def solid(row, col, colour):
    return pk(6, bytes([colour, colour, row, col]) + bytes([0x3F] * 12))


def main(out):
    p = []
    alpha6 = [0] * 16                    # 6-bit values: alpha = 255 - 4*v
    for entry, v in ((1, 16), (2, 32), (3, 48), (4, 63)):
        alpha6[entry] = v
    p.append(pk(28, alpha6))
    low = [(0, 0, 0), (15, 2, 2), (2, 15, 2), (2, 2, 15), (15, 15, 2), (15, 2, 15), (2, 15, 15), (15, 15, 15)]
    p += [pk(30, pal(low)), pk(31, pal([(8, 8, 8)] * 8)), pk(1, [0]), pk(2, [0])]
    for row in range(3, 15):
        for col in range(6, 44):
            p.append(solid(row, col, 1 + ((col - 6) // 5) % 7))
    p += [pk(63)] * (300 * 10)
    open(out, "wb").write(b"".join(p))


if __name__ == "__main__":
    main(sys.argv[1])
