#!/usr/bin/env python3
"""Parity of singws-cdg `window_rules::rect_on_attached_screen` with the app's `_rect_on_attached_screen` (extracted from 0.2.18.1.py by ast, run on real
QRects with a stand-in QApplication that reports the screens we choose). Fuzzed rectangles and screen layouts incl. negative coordinates, edge contact,
no primary screen. Needs PyQt6 (main checkout .venv); QT_QPA_PLATFORM=offscreen."""
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
CLI = os.environ.get("CARGO_TARGET_DIR", str(Path.home() / ".cache/singws-rust-target-cdg")) + "/release/singws-window-cli"

rng = random.Random(7)
cases, lines = [], []
for _ in range(3000):
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
print(f"{len(cases)} cases, {bad} differences")
sys.exit(1 if bad else 0)
