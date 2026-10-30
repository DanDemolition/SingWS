#!/usr/bin/env python3
"""Parity of singws-cdg `idle_overlay` with the app's idle "now playing" card (VideoAreaWidget in 0.2.18.1.py).

The app's own methods `_get_idle_bg_overlay_info`, `_draw_idle_bg_overlay`, `set_audio_level` and `advance_visualizer` are extracted by ast and run
unchanged. The draw method gets a QPainter subclass that records every call (rounded rectangles, text positions, fonts, pixmap placement) and a
QFontMetrics stand-in that records the ascent it returned; the Rust layout is given those same ascents and must reproduce every rectangle, baseline
and point size. Also compared: the rule that decides whether a card exists at all (idle window, playing background player, index inside the
playlist), the meter animation over random operation sequences, and Qt's KeepAspectRatioByExpanding size for the artwork.

Not compared: the elided text (Qt's QFontMetrics.elidedText; the QML Text item elides itself) and the antialiased pixels (a QML rectangle is
not a QPainter rounded rectangle). Needs PyQt6 (main checkout .venv); QT_QPA_PLATFORM=offscreen.

    CARGO_TARGET_DIR=... tools/idle_overlay_parity.py [--quick]
"""
import ast, math, os, random, subprocess, sys, types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter, QPixmap

ROOT = Path(__file__).resolve().parent.parent
CLI = os.environ.get("CARGO_TARGET_DIR", str(Path.home() / ".cache/singws-rust-target-display")) + "/release/singws-idle-overlay-cli"
QUICK = "--quick" in sys.argv or bool(os.environ.get("CDG_PARITY_QUICK"))
app = QGuiApplication([])

tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
wanted = {"_get_idle_bg_overlay_info", "_draw_idle_bg_overlay", "set_audio_level", "advance_visualizer"}
funcs = []
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == "VideoAreaWidget":
        funcs = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
assert {f.name for f in funcs} == wanted, [f.name for f in funcs]

ASCENTS = []


class RecMetrics(QFontMetrics):
    def ascent(self):
        a = super().ascent()
        ASCENTS.append(a)
        return a


class Painter(QPainter):
    def __init__(self, img):
        super().__init__(img)
        self.log = []
        self.font_log = []

    def drawRoundedRect(self, x, y, w, h, rx, ry): self.log.append(("rr", x, y, w, h, rx, ry))
    def drawText(self, x, y, text): self.log.append(("text", x, y, text))
    def drawPixmap(self, x, y, pm): self.log.append(("pm", x, y, pm.width(), pm.height()))
    def setFont(self, f): self.font_log.append((f.pointSize(), f.bold())); super().setFont(f)


class Track:
    names = []

    @staticmethod
    def display_for_file(path):
        Track.names.append(path)
        return ("Artist " + path, "Title " + path)


ns = {"math": math, "Qt": Qt, "QColor": QColor, "QPainter": QPainter, "QFontMetrics": RecMetrics, "QPixmap": QPixmap, "track_display": Track}
mod = ast.Module([ast.ClassDef("Fake", [], [], funcs, [])], [])
exec(compile(ast.fix_missing_locations(mod), "app", "exec"), ns)
Fake = ns["Fake"]


class Box:
    def __init__(self, w, h, levels, peaks):
        self._w, self._h = w, h
        self._viz_levels, self._viz_peaks = levels, peaks
        self._viz_audio_level = 0.0
        self._viz_phase = 0.0
        self._idle_bg_artwork_path = ""
        self._idle_bg_text = ("", "")
        self._idle_bg_artwork = QPixmap()
        self._parent = None

    width = lambda self: self._w
    height = lambda self: self._h
    parent = lambda self: self._parent


for f in wanted:
    setattr(Box, f, getattr(Fake, f))


def rust(lines):
    out = subprocess.run([CLI], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True).stdout.split("\n")
    return out[:len(lines)]


rng = random.Random(11)
bad = 0
total = 0


def fail(msg):
    global bad
    bad += 1
    if bad <= 8:
        print("DIFF", msg)


# ---- 1. card layout against the recorded drawing
cases = []
n_cases = 600 if QUICK else 4000
for _ in range(n_cases):
    w = rng.choice([rng.randrange(60, 200), rng.randrange(100, 3200), 1280, 1920, 800])
    h = rng.choice([rng.randrange(60, 200), rng.randrange(100, 2200), 720, 1080, 1000])
    art = None
    if rng.random() < 0.6:
        art = QPixmap(rng.randrange(1, 900), rng.randrange(1, 900))
        art.fill(QColor("red"))
    nlev = rng.choice([0, 14, 14, 14, rng.randrange(1, 60)])
    levels = [rng.choice([rng.random(), rng.random() * 2 - 0.5, 0.0, 1.0, 0.03]) for _ in range(nlev)]
    npk = rng.choice([nlev, nlev, rng.randrange(0, nlev + 2)])
    peaks = [rng.choice([rng.random(), 1.5, 0.0]) for _ in range(npk)]
    artist = rng.choice(["", "Some Artist", "A"])
    cases.append((w, h, art, levels, peaks, artist))

lines, wants = [], []
for (w, h, art, levels, peaks, artist) in cases:
    box = Box(w, h, levels, peaks)
    img = QImage(16, 16, QImage.Format.Format_ARGB32)
    p = Painter(img)
    ASCENTS.clear()
    info = {"title": "Title", "artist": artist, "artwork": art if art is not None else QPixmap()}
    box._draw_idle_bg_overlay(p, info)
    p.end()
    rr = [e for e in p.log if e[0] == "rr"]
    texts = [e for e in p.log if e[0] == "text"]
    pms = [e for e in p.log if e[0] == "pm"]
    want = None
    if w > 120 and h > 120:
        want = (rr, texts, pms, list(p.font_log), list(ASCENTS))
    wants.append(want)
    has_art = art is not None
    # the first two ascents recorded are title then artist
    ta, aa = (ASCENTS + [0, 0])[:2] if want else (0, 0)
    lv = levels
    lines.append("card %d %d %d %d %d %d %d %s %d %s" % (w, h, int(has_art), int(bool(artist)), ta, aa, len(lv), " ".join(map(repr, lv)), len(peaks), " ".join(map(repr, peaks))))
got = rust(lines)
for (case, want, line, g) in zip(cases, wants, lines, got):
    total += 1
    w, h, art, levels, peaks, artist = case
    if want is None:
        if g != "none":
            fail(f"{line} python no card, rust {g}")
        continue
    rr, texts, pms, fonts, ascents = want
    parts = [s.strip() for s in g.split("|")]
    # expected lists from Python
    card = rr[0][1:5]
    exp = ["card %d %d %d %d" % card]
    idx = 1
    if art is not None:
        art_rr = rr[1]
        exp.append("art %d %d %d %d" % art_rr[1:5])
        idx = 2
        if art_rr[5:] != (10, 10) or rr[0][5:] != (16, 16):
            fail(f"radii differ {line}")
    else:
        exp.append("art 0")
    if texts:
        tx = texts[0][1]
        artist_y = texts[1][2] if len(texts) > 1 else -1
        title_pt, artist_pt = fonts[0][0], (fonts[1][0] if len(fonts) > 1 else None)
        # the artist font is only set when an artist is drawn; its size is checked from the Rust side through card_h
        card_h = card[3]
        from_rust_fonts = (max(11, int(card_h * 0.20)), max(9, int(card_h * 0.14)))
        if title_pt != from_rust_fonts[0] or (artist_pt is not None and artist_pt != from_rust_fonts[1]):
            fail(f"python font sizes {fonts} vs {from_rust_fonts} {line}")
        # text width: python uses text_w for elision, derive from the lane or card geometry
        text_w = (card[0] + card[2] - 12) - tx
        exp.append("text %d %d %d %d %d %d" % (tx, texts[0][2], artist_y, from_rust_fonts[0], from_rust_fonts[1], text_w))
    else:
        exp.append("text 0")
    rest = rr[idx:]
    if rest:
        lane = rest[0]
        exp.append("lane %d %d %d %d" % lane[1:5])
        if lane[5:] != (6, 6):
            fail(f"lane radius {line}")
        bars = rest[1:]
        # bars: [bar, cap, bar, cap ...]
        nb = len(bars) // 2
        s = "%d" % nb
        for k in range(nb):
            b, c = bars[2 * k], bars[2 * k + 1]
            s += " %d %d %d %d %d %d %d %d" % (b[1:5] + c[1:5])
            if b[5:] != (2, 2) or c[5:] != (1, 1):
                fail(f"bar radii {line}")
        exp.append(s)
    else:
        exp.append("lane 0")
        exp.append("0")
    exp_text = " | ".join(exp)
    if exp_text != " | ".join(parts):
        fail(f"{line}\n  python {exp_text}\n  rust   {g}")
    if art is not None and pms:
        art_size = max(0, card[3] - 24)
        sw, sh = pms[0][3], pms[0][4]
        if (pms[0][1], pms[0][2]) != (card[0] + 12, card[1] + 12):
            fail(f"art placement {line}")
        got_size = rust([f"expand {art.width()} {art.height()} {art_size} {art_size}"])[0].split()
        if (int(got_size[0]), int(got_size[1])) != (sw, sh):
            fail(f"expand {art.width()}x{art.height()} into {art_size}: qt {sw}x{sh} rust {got_size}")
print(f"card layout: {total} cases")

# ---- 2. expanding size against Qt directly on many shapes
exp_lines, exp_want = [], []
for _ in range(500 if QUICK else 3000):
    iw, ih = rng.randrange(1, 4000), rng.randrange(1, 4000)
    bw, bh = rng.randrange(1, 800), rng.randrange(1, 800)
    s = QSize(iw, ih).scaled(QSize(bw, bh), Qt.AspectRatioMode.KeepAspectRatioByExpanding)
    exp_lines.append(f"expand {iw} {ih} {bw} {bh}")
    exp_want.append((s.width(), s.height()))
for l, w_, g in zip(exp_lines, exp_want, rust(exp_lines)):
    total += 1
    if tuple(map(int, g.split())) != w_:
        fail(f"{l} qt {w_} rust {g}")
print("expanding sizes compared with QSize.scaled")

# ---- 3. which track the card shows
idx_lines, idx_want = [], []
for _ in range(400):
    idle, exists, playing = rng.random() < 0.8, rng.random() < 0.9, rng.random() < 0.8
    plen = rng.choice([0, 1, 3, 10])
    cur = rng.choice([-1, 0, 1, 2, plen - 1, plen, plen + 3, -7])
    box = Box(800, 600, [0.08] * 14, [0.12] * 14)
    bg = types.SimpleNamespace(is_playing=playing, playlist=[f"t{i}" for i in range(plen)], current_index=cur, get_album_artwork=lambda p: None)
    vw = types.SimpleNamespace(idle=idle, _external_owner=(types.SimpleNamespace(bg_music=bg) if exists else types.SimpleNamespace()), parent=lambda: None)
    box._parent = vw
    Track.names.clear()
    info = box._get_idle_bg_overlay_info()
    idx_lines.append(f"index {int(idle)} {int(exists)} {int(playing)} {plen} {cur}")
    idx_want.append(str(int(Track.names[0][1:])) if info else "none")
    if info and info["title"] != "Title " + Track.names[0]:
        fail("title not from display_for_file")
for l, w_, g in zip(idx_lines, idx_want, rust(idx_lines)):
    total += 1
    if w_ != g:
        fail(f"{l} python {w_} rust {g}")
print("card presence compared")

# ---- 4. the meter
viz_lines, viz_runs = [], []
for _ in range(60 if QUICK else 300):
    ops = []
    for _ in range(rng.randrange(1, 60)):
        r = rng.random()
        ops.append("A" if r < 0.5 else ("I" if r < 0.7 else "L" + repr(rng.choice([rng.random(), 0.0, 1.0, 1.7, -0.4]))))
    box = Box(800, 600, [0.08] * 14, [0.12] * 14)
    run = []
    for op in ops:
        if op == "A": box.advance_visualizer(True)
        elif op == "I": box.advance_visualizer(False)
        else: box.set_audio_level(float(op[1:]))
        run.append((list(box._viz_levels), list(box._viz_peaks), box._viz_audio_level, box._viz_phase))
    viz_lines.append("viz " + " ".join(ops))
    viz_runs.append(run)
import subprocess as sp
res = sp.run([CLI], input="\n".join(viz_lines) + "\n", capture_output=True, text=True, check=True).stdout.strip().split("\n")
pos = 0
worst = 0.0
for run, line in zip(viz_runs, viz_lines):
    for step, (lv, pk, al, ph) in enumerate(run):
        total += 1
        parts = res[pos].split(); pos += 1
        rl = [float(x) for x in parts[0].split(",")]; rp = [float(x) for x in parts[1].split(",")]
        vals = rl + rp + [float(parts[2]), float(parts[3])]
        want = lv + pk + [al, ph]
        d = max(abs(a - b) for a, b in zip(vals, want))
        worst = max(worst, d)
        if d > 1e-12:
            fail(f"meter step {step} of {line[:80]}: max difference {d}")
print(f"meter: {pos} steps, largest difference {worst:.2e}")
print(f"{total} comparisons, {bad} differences")
sys.exit(1 if bad else 0)
