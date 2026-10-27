#!/usr/bin/env python3
"""Write a small synthetic karaoke CDG (title, lyric lines, a colour wipe via XOR tiles, a clear) for the Rust display demo
(`singws-shell --display-demo FILE.cdg`) and for eyeballing the decoder. It is made up here: no show data, no library file.

    python3 tools/make_demo_cdg.py OUT.cdg
"""
import sys

# 5x7 capital letters, one string per row, '#' = on
F = {
 "A": [" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
 "B": ["#### ", "#   #", "#   #", "#### ", "#   #", "#   #", "#### "],
 "C": [" ####", "#    ", "#    ", "#    ", "#    ", "#    ", " ####"],
 "D": ["#### ", "#   #", "#   #", "#   #", "#   #", "#   #", "#### "],
 "E": ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####"],
 "F": ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#    "],
 "G": [" ####", "#    ", "#    ", "# ###", "#   #", "#   #", " ####"],
 "H": ["#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
 "I": ["#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "#####"],
 "K": ["#   #", "#  # ", "# #  ", "##   ", "# #  ", "#  # ", "#   #"],
 "L": ["#    ", "#    ", "#    ", "#    ", "#    ", "#    ", "#####"],
 "M": ["#   #", "## ##", "# # #", "# # #", "#   #", "#   #", "#   #"],
 "N": ["#   #", "##  #", "# # #", "#  ##", "#   #", "#   #", "#   #"],
 "O": [" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
 "P": ["#### ", "#   #", "#   #", "#### ", "#    ", "#    ", "#    "],
 "R": ["#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #"],
 "S": [" ####", "#    ", "#    ", " ### ", "    #", "    #", "#### "],
 "T": ["#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "],
 "U": ["#   #", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
 "W": ["#   #", "#   #", "#   #", "# # #", "# # #", "## ##", "#   #"],
 "X": ["#   #", "#   #", " # # ", "  #  ", " # # ", "#   #", "#   #"],
 "Y": ["#   #", "#   #", " # # ", "  #  ", "  #  ", "  #  ", "  #  "],
 "!": ["  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "     ", "  #  "],
 " ": ["     "] * 7,
}
ROWMAP = [0, 0, 1, 1, 2, 3, 3, 4, 4, 5, 6, 6]   # 7 font rows -> a 12-row CDG tile


def pk(inst, data=b""):
    return bytes([9, inst, 0, 0]) + bytes(data).ljust(16, b"\0")[:16] + bytes(4)


def glyph_rows(ch):
    g = F[ch]
    return [int("".join("1" if c == "#" else "0" for c in g[r]) + "0", 2) for r in ROWMAP]  # 5 columns + 1 blank = 6 bits


def tile(ch, row, col, c0, c1, xor=False):
    return pk(38 if xor else 6, bytes([c0, c1, row, col]) + bytes(glyph_rows(ch)))


def pal(entries):
    d = b""
    for r, g, b in entries:
        c = (r << 8) | (g << 4) | b
        d += bytes([(c >> 6) & 0x3F, c & 0x3F])
    return d


def main(out):
    p = []
    low = [(1, 0, 4), (15, 15, 15), (15, 12, 0), (15, 15, 15)] + [(0, 0, 0)] * 4
    p += [pk(30, pal(low)), pk(31, pal([(0, 0, 0)] * 8)), pk(1, [0]), pk(2, [0])]

    def line(text, row, color_bg=0, color_fg=1):
        col = (50 - len(text)) // 2
        return [tile(ch, row, col + i, color_bg, color_fg) for i, ch in enumerate(text)], col

    t = []
    for text, row in (("SINGWS", 3), ("RUST CDG DISPLAY", 5)):
        pk_, _ = line(text, row)
        t += pk_
    for text, row in (("FRAME EXACT AGAINST FFMPEG", 10), ("SING ALONG", 12)):
        pk_, col = line(text, row)
        t += pk_
    p += t
    # idle until 1.5 s, then wipe the second lyric line yellow letter by letter at 12 letters a second
    while len(p) < 450:
        p.append(pk(63))
    text, row = "SING ALONG", 12
    col = (50 - len(text)) // 2
    for i, ch in enumerate(text):
        p.append(tile(ch, row, col + i, 0, 3, xor=True))   # glyph pixels 1 -> 1^3 = 2 (yellow); background 0 stays 0
        p += [pk(63)] * 24
    while len(p) < 3000:
        p.append(pk(63))
    p.append(pk(1, [0]))                                    # clear
    p += [pk(63)] * 300
    open(out, "wb").write(b"".join(p))


if __name__ == "__main__":
    main(sys.argv[1])
