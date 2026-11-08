#!/usr/bin/env python3
"""Parity: karafun_preview_pane_rect, karafun_fill_region and karafun_preview_region_changed of the real 0.2.18.1.py (read by AST, the app is not imported) vs the Rust
`singws-karafun` geometry.rs, on a large corpus of probe texts and rectangles. Declined inputs (a number form Rust does not model: NaN/inf, underscores, non-ASCII digits) are
counted, never compared.

  python3 tools/karafun_geometry_parity.py [--seeds N] [--binary PATH]
"""
import argparse
import ast
import json
import math
import os
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FUNCS = ("karafun_preview_pane_rect", "karafun_fill_region", "karafun_preview_region_changed")
CONSTS = ("KARAFUN_PREVIEW_OVERLAY_MARGIN", "KARAFUN_FILL_ZOOM_OUT")


def load():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in FUNCS)
            or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in CONSTS for t in n.targets))]
    assert len(body) == len(FUNCS) + len(CONSTS), "a Python function or constant moved"
    ns = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), "geometry", "exec"), ns)
    return ns


def run_rust(binary, reqs):
    out = subprocess.run([binary], input=json.dumps({"batch": reqs}).encode(), capture_output=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode()[-400:])
    return json.loads(out.stdout)


ROLES = ["AXSplitter", "AXSplitter", "AXImage", "AXStaticText", "AXButton", "AXSlider", "AXPopUpButton", "AXPopUpButton", "AXGroup", "AXTextField", ""]
NUM_BAD = ["abc", "", " ", "1,5", "--1", "1e", "0x10", "12px", "None", "1.2.3"]


def num(r, lo, hi):
    v = r.uniform(lo, hi)
    c = r.random()
    if c < 0.3:
        return str(int(v))
    if c < 0.4:
        return f" {v:.2f} "
    if c < 0.45:
        return f"{v:.3e}"
    if c < 0.5:
        return f"+{abs(v):.1f}"
    return f"{v:.2f}"


def realistic_probe(r):
    """A layout KaraFun could really report: window, one (or several) tall thin splitters right of 30% of the width, a queue header popup, overlay controls above it."""
    ww, wh = r.choice([1728, 1440, 1200, 900]), r.choice([1013, 900, 760])
    wx, wy = r.choice([0, 0, 40]), r.choice([0, 34])
    lines = [f"WINDOW|{wx}|{wy}|{ww}|{wh}"]
    sx = wx + ww * r.uniform(0.28, 0.75)
    top = wy + r.choice([40, 60, 90, 120])
    for _ in range(r.choice([1, 1, 2, 3])):
        x = sx + r.choice([0, 0, 1.5, 30, -40])
        lines.append(f"E|AXSplitter||d|{x:.1f}|{top}|{r.choice([1, 2, 4, 6, 7])}|{r.choice([280, 300, 520, 700])}")
    left = sx + 4
    queue_y = top + r.choice([100, 200, 340, 420, 500])
    if r.random() < 0.8:
        lines.append(f"E|AXPopUpButton|q|d|{left + r.choice([-3, 0, 5, 40])}|{queue_y}|120|24")
    for _ in range(r.randint(0, 8)):
        role = r.choice(["AXImage", "AXStaticText", "AXButton", "AXSlider", "AXGroup"])
        y = top + r.choice([30, 61, 80, 150, 250, 330, 400, 470, 560])
        lines.append(f"E|{role}|n|d|{left + r.choice([-3, -1, 0, 10, 120, 400])}|{y}|{r.choice([20, 90, 300])}|{r.choice([10, 20])}")
    return "\n".join(lines)


def probe_text(r):
    if r.random() < 0.5:
        return realistic_probe(r)
    ww, wh = r.choice([1728, 1440, 1200, 800]), r.choice([1013, 900, 700, 400])
    wx, wy = r.choice([0, 0, 40, -10]), r.choice([0, 34, 25])
    lines = []
    if r.random() < 0.95:
        lines.append(f"WINDOW|{wx}|{wy}|{ww}|{wh}" if r.random() < 0.95 else f"WINDOW|{wx}|{wy}|{ww}")
    if r.random() < 0.1:
        lines.append(f"WINDOW|{r.choice(NUM_BAD)}|{wy}|{ww}|{wh}")
    for _ in range(r.randint(0, 14)):
        role = r.choice(ROLES)
        if role == "AXSplitter" and r.random() < 0.7:
            x, y, w, h = num(r, wx + 0.3 * ww, wx + ww), num(r, wy, wy + 80), num(r, 0, 8), num(r, 250, wh)
        else:
            x, y, w, h = num(r, wx, wx + ww), num(r, wy, wy + wh), num(r, 0, 300), num(r, 0, 120)
        if r.random() < 0.04:
            h = r.choice(NUM_BAD)
        name = r.choice(["", "name", "a|b", "Play"])
        extra = r.choice(["", "", "|x"])
        sep = r.choice(["\n", "\n", "\r\n", "\r", "\x0b", "\x0c", "\x1c", "\x85", " "])
        lines.append(f"E|{role}|{name}|d|{x}|{y}|{w}|{h}{extra}" if r.random() < 0.95 else f"E|{role}|{x}|{y}|{w}")
        if r.random() < 0.1:
            lines.append(r.choice(["", "  ", "junk", "WINDOW", "E", "E|AXButton"]))
        lines[-1] = lines[-1] + (sep if sep != "\n" else "")
    text = "\n".join(lines)
    if r.random() < 0.05:
        text = ""
    return text


def rects(r):
    f = lambda: r.choice([r.uniform(-50, 2000), r.randint(-5, 2000), 0, 0.0])
    return [f(), f(), r.choice([r.uniform(0, 3000), r.randint(0, 3000), 0, 1566.0, 480.0]), r.choice([r.uniform(0, 1100), r.randint(0, 1100), 0, 580.0, 270])]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=4000)
    ap.add_argument("--binary", default=os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun"))
    args = ap.parse_args(argv)
    ns = load()
    reqs, cases = [], []
    for seed in range(args.seeds):
        r = random.Random(seed)
        t = probe_text(r)
        reqs.append({"op": "pane_rect", "text": t})
        cases.append(("pane_rect", t))
        reg = rects(r)
        asp = r.choice([16.0 / 9.0, 16.0 / 9.0, 1.0, 2.0, 4.0 / 3.0])
        reqs.append({"op": "fill_region", "region": reg, "aspect": asp})
        cases.append(("fill_region", (reg, asp)))
        old = r.choice([None, rects(r)])
        new = r.choice([None, list(old) if old and r.random() < 0.5 else rects(r)])
        if old and new and r.random() < 0.5:
            new = [a + r.choice([0, 1.5, 2.0, 2.5, -3.0]) for a in old]
        tol = r.choice([2.0, 2.0, 0.5, 5.0])
        reqs.append({"op": "region_changed", "old": old, "new": new, "tolerance": tol})
        cases.append(("region_changed", (old, new, tol)))
    got = run_rust(args.binary, reqs)
    diffs, declined, counts = [], 0, {"pane_found": 0, "pane_none": 0, "fill_cropped": 0, "changed_true": 0}
    for (kind, arg), g in zip(cases, got):
        if "decline" in g:
            declined += 1
            continue
        if kind == "pane_rect":
            want = ns["karafun_preview_pane_rect"](arg)
            mine = g["rect"]
            wn = None if want is None else {"window": list(want["window"]), "region": list(want["region"])}
            counts["pane_none" if want is None else "pane_found"] += 1
            if wn != mine:
                diffs.append((kind, arg[:200], wn, mine))
        elif kind == "fill_region":
            reg, asp = arg
            want = list(ns["karafun_fill_region"](tuple(reg), asp))
            if want != g["region"]:
                diffs.append((kind, arg, want, g["region"]))
            elif want != reg:
                counts["fill_cropped"] += 1
        else:
            old, new, tol = arg
            want = ns["karafun_preview_region_changed"](None if old is None else tuple(old), None if new is None else tuple(new), tol)
            counts["changed_true"] += int(want)
            if want != g["changed"]:
                diffs.append((kind, arg, want, g["changed"]))
    print(f"cases={len(cases)} declined={declined} {counts}")
    for d in diffs[:4]:
        print(" -", d)
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
