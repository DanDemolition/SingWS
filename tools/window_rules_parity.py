#!/usr/bin/env python3
"""Parity of singws-cdg `window_rules` with the app's audience-window placement rules (extracted from 0.2.18.1.py by ast, run on real QRects with a
stand-in QApplication that reports the screens we choose; fuzzed rectangles and screen layouts incl. negative coordinates, edge contact, no primary
screen, no cursor screen):

  1. `_rect_on_attached_screen`            <-> `rect_on_attached_screen`
  2. `_place_video_window_bottom_right`    <-> `place_video_window` (saved geometry with the `or` defaults, first-run bottom-right placement)
  3. `toggle_karaoke_window` (hidden path) <-> `restore_rect` (plain defaults, null fields, empty dictionary)
  4. `QRect.contains(QPoint)`, and `screenAt(frame.center()) or vw.screen() or primary` <-> `rect_contains_point`, `screen_for_window`
     (the order Qt visits screens in is Qt's own and is not tested: the screens are given in that order)

Needs PyQt6 (main checkout .venv); QT_QPA_PLATFORM=offscreen. CARGO_TARGET_DIR selects the build of singws-window-cli."""
import ast, os, random, subprocess, sys
from pathlib import Path
from PyQt6.QtCore import QRect

ROOT = Path(__file__).resolve().parent.parent
tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))


class FakeScreen:
    def __init__(self, r): self.r = r
    def availableGeometry(self): return self.r


class FakeApp:
    screens_list = []; primary = None
    @classmethod
    def screens(cls): return cls.screens_list
    @classmethod
    def primaryScreen(cls): return cls.primary


ns = {"QRect": QRect, "QApplication": FakeApp, "_diag": lambda m: None}
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == "_rect_on_attached_screen":
        exec(compile(ast.Module([node], []), "app", "exec"), ns)
fn = ns["_rect_on_attached_screen"]
CLI = os.environ.get("CARGO_TARGET_DIR", str(Path.home() / ".cache/singws-rust-target-display")) + "/release/singws-window-cli"

rng = random.Random(7)
cases, lines = [], []
N1 = 600 if os.environ.get("CDG_PARITY_QUICK") else 3000
for _ in range(N1):
    n = rng.choice([0, 1, 1, 2, 3])
    screens = [QRect(rng.randrange(-3000, 3000), rng.randrange(-2000, 2000), rng.randrange(200, 4000), rng.randrange(200, 2500)) for _ in range(n)]
    primary = rng.choice(screens) if screens and rng.random() < 0.9 else (QRect(0, 0, 1440, 900) if rng.random() < 0.5 else None)
    rect = QRect(rng.randrange(-4000, 5000), rng.randrange(-3000, 4000), rng.randrange(1, 5000), rng.randrange(1, 3500))
    if rng.random() < 0.25 and screens:   # touch an edge exactly
        s = rng.choice(screens); rect = QRect(s.right() + 1, s.y(), rng.randrange(1, 500), rng.randrange(1, 500))
    FakeApp.screens_list = [FakeScreen(s) for s in screens]
    FakeApp.primary = FakeScreen(primary) if primary is not None else None
    want = fn(rect)
    cases.append((want.x(), want.y(), want.width(), want.height()))
    parts = [rect.x(), rect.y(), rect.width(), rect.height(), len(screens)] + [v for s in screens for v in (s.x(), s.y(), s.width(), s.height())]
    parts += [1, primary.x(), primary.y(), primary.width(), primary.height()] if primary is not None else [0]
    lines.append(" ".join(map(str, parts)))
# primary None => Python's `target is None` branch needs primaryScreen() None, which FakeScreen(None) above avoids: covered by the 0 case
out = subprocess.run([CLI], input="\n".join(lines) + "\n", capture_output=True, text=True).stdout.split("\n")
bad = 0
for i, (want, got) in enumerate(zip(cases, out)):
    if tuple(map(int, got.split())) != want:
        bad += 1
        if bad < 5: print("DIFF", lines[i], "python", want, "rust", got)
print(f"{len(cases)} guard cases, {bad} differences")
total = len(cases)


# ---------------------------------------------------------------- 2. startup placement and 3. restore on show
import types
from PyQt6.QtCore import QPoint, Qt


class FakeCursor:
    @staticmethod
    def pos(): return QPoint(0, 0)


FakeApp.cursor_screen = None
FakeApp.screenAt = classmethod(lambda cls, pt: cls.cursor_screen)
fake_widgets = types.ModuleType("PyQt6.QtWidgets"); fake_widgets.QApplication = FakeApp
fake_gui = types.ModuleType("PyQt6.QtGui"); fake_gui.QCursor = FakeCursor
sys.modules["PyQt6.QtWidgets"] = fake_widgets
sys.modules["PyQt6.QtGui"] = fake_gui


class FakeVW:
    def __init__(self, fs, visible):
        self.fs, self.vis, self.log = fs, visible, []
    def isFullScreen(self): return self.fs
    def isMaximized(self): return False
    def isVisible(self): return self.vis
    def setGeometry(self, r): self.log.append(("geometry", r.x(), r.y(), r.width(), r.height()))
    def resize(self, w, h): self.log.append(("resize", w, h))
    def move(self, x, y): self.log.append(("move", x, y))
    def show(self): self.log.append(("show",)); self.vis = True
    def raise_(self): pass
    def activateWindow(self): pass
    def windowState(self): return Qt.WindowState.WindowNoState
    def setWindowState(self, s): pass
    def showNormal(self): pass


class FakeHost:
    def __init__(self, vw, saved):
        self.video_window = vw
        self.settings = {"karaoke_window_pos": saved} if saved is not None else {}
    def _activate_host_if_foreground(self): pass


methods = {}
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == "KaraokeApp":
        for n in node.body:
            if isinstance(n, ast.FunctionDef) and n.name in ("_place_video_window_bottom_right", "toggle_karaoke_window"):
                methods[n.name] = n
assert set(methods) == {"_place_video_window_bottom_right", "toggle_karaoke_window"}
ns2 = {"QRect": QRect, "QApplication": FakeApp, "QTimer": types.SimpleNamespace(singleShot=lambda *a, **k: None), "Qt": Qt,
       "_diag": lambda m: None, "_rect_on_attached_screen": fn}
for name, node in methods.items():
    exec(compile(ast.fix_missing_locations(ast.Module([node], [])), "app", "exec"), ns2)
place_fn, toggle_fn = ns2["_place_video_window_bottom_right"], ns2["toggle_karaoke_window"]


def code(v):
    return "a" if v is None else ("n" if v == "null" else str(v))


def rand_val(r):
    k = r.random()
    if k < 0.15: return None            # key absent
    if k < 0.25: return "null"
    if k < 0.45: return 0
    if k < 0.55: return -rng.randrange(1, 300)
    return rng.choice([rng.randrange(1, 800), rng.randrange(-300, 4000), 100, 640, 520])


def saved_dict(vals):
    d = {}
    for key, v in zip(("x", "y", "width", "height"), vals):
        if v is None: continue
        d[key] = None if v == "null" else v
    return d


def rand_layout(r):
    n = r.choice([0, 1, 1, 2, 3])
    screens = [QRect(r.randrange(-3000, 3000), r.randrange(-2000, 2000), r.randrange(200, 4000), r.randrange(200, 2500)) for _ in range(n)]
    primary = r.choice(screens) if screens and r.random() < 0.9 else (QRect(0, 0, 1440, 900) if r.random() < 0.5 else None)
    cursor = r.choice(screens) if screens and r.random() < 0.8 else None
    return screens, primary, cursor


def set_world(screens, primary, cursor):
    FakeApp.screens_list = [FakeScreen(s) for s in screens]
    FakeApp.primary = FakeScreen(primary) if primary is not None else None
    FakeApp.cursor_screen = FakeScreen(cursor) if cursor is not None else None


def rect_tokens(r): return [r.x(), r.y(), r.width(), r.height()]
def opt(r): return ["1"] + [str(t) for t in rect_tokens(r)] if r is not None else ["0"]


N2 = 1500 if os.environ.get("CDG_PARITY_QUICK") else 6000
cases2, lines2 = [], []
for _ in range(N2):
    screens, primary, cursor = rand_layout(rng)
    set_world(screens, primary, cursor)
    has_saved = rng.random() < 0.75
    vals = [rand_val(rng) for _ in range(4)]
    if has_saved and rng.random() < 0.5:
        vals[2], vals[3] = rng.randrange(1, 1500), rng.randrange(1, 1200)    # most saved windows have a real size
    fs, vis = rng.random() < 0.2, rng.random() < 0.5
    vw = FakeVW(fs, vis)
    place_fn(FakeHost(vw, saved_dict(vals) if has_saved else None))
    lines2.append("place " + " ".join(["1"] + [code(v) for v in vals] if has_saved else ["0"]) + f" {int(fs)} {int(vis)} " + " ".join(opt(cursor) + opt(primary) + [str(len(screens))] + [str(t) for s in screens for t in rect_tokens(s)]))
    g = next((e for e in vw.log if e[0] == "geometry"), None)
    rs = next((e for e in vw.log if e[0] == "resize"), None)
    mv = next((e for e in vw.log if e[0] == "move"), None)
    shown = any(e[0] == "show" for e in vw.log)
    cases2.append("geometry %s | resize %s | move %s | show %d" % (" ".join(map(str, g[1:])) if g else "-", " ".join(map(str, rs[1:])) if rs else "-", " ".join(map(str, mv[1:])) if mv else "-", shown))
out2 = subprocess.run([CLI], input="\n".join(lines2) + "\n", capture_output=True, text=True).stdout.split("\n")
bad2 = 0
for i, (want, got) in enumerate(zip(cases2, out2)):
    if want != got:
        bad2 += 1
        if bad2 < 6: print("PLACE DIFF", lines2[i], "\n  python", want, "\n  rust  ", got)
print(f"{len(cases2)} startup placements, {bad2} differences")
bad += bad2; total += len(cases2)

cases3, lines3 = [], []
for _ in range(N2):
    screens, primary, cursor = rand_layout(rng)
    set_world(screens, primary, cursor)
    vals = [rand_val(rng) for _ in range(4)]
    saved = saved_dict(vals)
    vw = FakeVW(False, False)
    toggle_fn(FakeHost(vw, saved))
    g = next((e for e in vw.log if e[0] == "geometry"), None)
    shown = any(e[0] == "show" for e in vw.log)
    cases3.append((" ".join(map(str, g[1:])) if g else "none", shown, all(v is None for v in vals)))
    lines3.append("restore " + " ".join(code(v) for v in vals) + " " + " ".join([str(len(screens))] + [str(t) for s in screens for t in rect_tokens(s)] + opt(primary)))
out3 = subprocess.run([CLI], input="\n".join(lines3) + "\n", capture_output=True, text=True).stdout.split("\n")
bad3 = 0
for i, ((want, shown, empty), got) in enumerate(zip(cases3, out3)):
    # the app shows the hidden window right after setting a geometry, or when there is nothing saved; an unreadable field raises before the show
    if want != got or shown != (want != "none" or empty):
        bad3 += 1
        if bad3 < 6: print("RESTORE DIFF", lines3[i], "python", want, "rust", got)
print(f"{len(cases3)} restores on show, {bad3} differences")
bad += bad3; total += len(cases3)

# ---------------------------------------------------------------- 4. which screen holds a rectangle's centre
lines4, cases4 = [], []
for _ in range(N2):
    screens = [QRect(rng.randrange(-3000, 3000), rng.randrange(-2000, 2000), rng.randrange(200, 3000), rng.randrange(200, 2000)) for _ in range(rng.choice([0, 1, 2, 3]))]
    frame = QRect(rng.randrange(-3500, 3500), rng.randrange(-2500, 2500), rng.randrange(1, 3000), rng.randrange(1, 2000))
    if screens and rng.random() < 0.4:     # put the centre on or next to a screen edge
        s = rng.choice(screens)
        edge = rng.choice([s.right(), s.right() + 1, s.left(), s.left() - 1])
        frame = QRect(edge - frame.width() // 2, frame.y(), frame.width(), frame.height())
    win = rng.randrange(-1, len(screens)) if screens else -1
    prim = rng.randrange(-1, len(screens)) if screens else -1
    c = frame.center()
    hit = next((i for i, s in enumerate(screens) if s.contains(c)), None)
    pick = hit if hit is not None else (win if win >= 0 else (prim if prim >= 0 else None))
    cases4.append("none" if pick is None else str(pick))
    lines4.append("screenfor " + " ".join(map(str, rect_tokens(frame) + [win, prim, len(screens)] + [t for s in screens for t in rect_tokens(s)])))
    px, py = rng.choice([c.x(), c.x() + rng.randrange(-3, 4)]), rng.choice([c.y(), c.y() + rng.randrange(-3, 4)])
    r = frame if rng.random() < 0.5 else QRect(*(rect_tokens(frame)))
    lines4.append("contains " + " ".join(map(str, rect_tokens(r) + [px, py])))
    cases4.append(str(int(r.contains(QPoint(px, py)))))
out4 = subprocess.run([CLI], input="\n".join(lines4) + "\n", capture_output=True, text=True).stdout.split("\n")
bad4 = 0
for i, (want, got) in enumerate(zip(cases4, out4)):
    if want != got:
        bad4 += 1
        if bad4 < 6: print("SCREEN DIFF", lines4[i], "python", want, "rust", got)
print(f"{len(cases4)} screen lookups, {bad4} differences")
bad += bad4; total += len(cases4)
print(f"{total} comparisons, {bad} differences")
sys.exit(1 if bad else 0)
