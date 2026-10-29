#!/usr/bin/env python3
"""Parity of the Rust display helpers (singws-cdg: clean_near_black, side_fill_color) with the app's own Python functions
`_clean_cdg_near_black` and `_cdg_side_fill_color`. The two functions are pulled out of 0.2.18.1.py by ast and run on QImages, so the app
is not imported (no live log, no settings). Frames come from singws-cdg-dump on the same hand-built and fuzz streams as cdg_diff.py.
Needs PyQt6 + numpy (the main checkout's .venv/bin/python). QT_QPA_PLATFORM=offscreen."""
import ast, os, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cdg_diff as c  # noqa: E402

src = (HERE.parent / "0.2.18.1.py").read_text(encoding="utf-8")
tree = ast.parse(src)
ns = {"QImage": QImage, "QColor": QColor, "np": np}
exec("_CLEAN_CDG_CACHE = {'key': None, 'threshold': None, 'transparent': None, 'result': None}", ns)
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in ("_clean_cdg_near_black", "_cdg_side_fill_color"):
        exec(compile(ast.Module([node], []), "app", "exec"), ns)
clean, side = ns["_clean_cdg_near_black"], ns["_cdg_side_fill_color"]
DUMP = os.environ.get("CARGO_TARGET_DIR", str(Path.home() / ".cache/singws-rust-target-cdg")) + "/release/singws-cdg-dump"


def run(path, *extra):
    return subprocess.run([DUMP, str(path), *extra], capture_output=True).stdout


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cdgdisp-"))
    files = []
    for s in range(3):
        f = tmp / f"s{s}.cdg"; f.write_bytes(c.sanitize(c.structured(s))[0]); files.append(f)
    for s in range(15):
        f = tmp / f"f{s}.cdg"; f.write_bytes(c.sanitize(c.fuzz(s, 120))[0]); files.append(f)
    quick = os.environ.get('CDG_PARITY_QUICK') == '1'   # used by tools/mutate_display.py: a smaller but still varied sample
    if quick:
        files = files[:2] + files[3:5]
    n = bad = 0
    for f in files:
        raw = run(f)
        count = len(raw) // c.FRAME
        sides = run(f, "--sidefill")
        for thr, tr in ((10, False), (0, False), (0, True), (32, False), (14, True), (50, False)):
            out = run(f, "--clean", f"{thr}:t" if tr else str(thr))
            for i in range(0, count, 7 if not quick else 23):
                fr = raw[i * c.FRAME:(i + 1) * c.FRAME]
                img = QImage(fr, 300, 216, 1200, QImage.Format.Format_RGBA8888).copy()
                ref = clean(img, thr, transparent_black=tr).convertToFormat(QImage.Format.Format_RGBA8888)
                ptr = ref.constBits(); ptr.setsize(ref.sizeInBytes())
                n += 1
                if bytes(ptr)[:c.FRAME] != out[i * c.FRAME:(i + 1) * c.FRAME]:
                    bad += 1; print("CLEAN DIFF", f.name, i, thr, tr)
        for i in range(0, count, 1 if not quick else 5):
            fr = raw[i * c.FRAME:(i + 1) * c.FRAME]
            img = QImage(fr, 300, 216, 1200, QImage.Format.Format_RGBA8888).copy()
            col = side(img)
            n += 1
            if (col.red(), col.green(), col.blue()) != tuple(sides[i * 3:i * 3 + 3]):
                bad += 1; print("SIDEFILL DIFF", f.name, i)
    # arbitrary pixels (the decoder only makes multiples of 17 with alpha 0 or 255): near-black values on both sides of every threshold, odd alphas
    import random
    rng = random.Random(11)
    frames = []
    for k in range(40):
        a = np.zeros((216, 300, 4), dtype=np.uint8)
        a[..., :3] = np.array([rng.choice([0, 0, 1, 5, 9, 10, 11, 14, 15, 31, 32, 33, 40, 200]) for _ in range(216 * 300 * 3)], dtype=np.uint8).reshape(216, 300, 3)
        a[..., 3] = np.array([rng.choice([0, 0, 1, 128, 254, 255, 255]) for _ in range(216 * 300)], dtype=np.uint8).reshape(216, 300)
        if k % 4 == 0:   # make the edges uniform so the side fill has real majorities and ties
            a[0, :, :] = a[0, 0, :]; a[-1, :, :] = a[-1, 0, :]; a[:, 0, :] = a[0, 0, :]; a[:, -1, :] = a[-1, 0, :]
        frames.append(a)
    rawf = tmp / "random.raw"; rawf.write_bytes(b"".join(x.tobytes() for x in frames))
    sides = run(rawf, "--raw-in", "--sidefill")
    for thr, tr in ((0, False), (0, True), (1, True), (9, False), (10, False), (31, False), (32, False), (33, False), (32, True), (40, False), (-5, True), (14, True)):
        out = run(rawf, "--raw-in", "--clean", f"{thr}:t" if tr else str(thr))
        for i, a in enumerate(frames):
            img = QImage(a.tobytes(), 300, 216, 1200, QImage.Format.Format_RGBA8888).copy()
            ref = clean(img, thr, transparent_black=tr).convertToFormat(QImage.Format.Format_RGBA8888)
            ptr = ref.constBits(); ptr.setsize(ref.sizeInBytes())
            n += 1
            if bytes(ptr)[:c.FRAME] != out[i * c.FRAME:(i + 1) * c.FRAME]:
                bad += 1; print("CLEAN DIFF (random pixels)", i, thr, tr)
    for i, a in enumerate(frames):
        img = QImage(a.tobytes(), 300, 216, 1200, QImage.Format.Format_RGBA8888).copy()
        col = side(img)
        n += 1
        if (col.red(), col.green(), col.blue()) != tuple(sides[i * 3:i * 3 + 3]):
            bad += 1; print("SIDEFILL DIFF (random pixels)", i)
    # compose against Qt on pictures where every pixel differs (the decoder's own frames are mostly flat, which hides centring and scaling slips)
    qframes = []
    for k in range(3):
        a = np.zeros((216, 300, 4), dtype=np.uint8)
        a[..., :3] = np.array([rng.randrange(256) for _ in range(216 * 300 * 3)], dtype=np.uint8).reshape(216, 300, 3)
        a[..., 3] = np.array([rng.choice([0, 255, 255, 255]) for _ in range(216 * 300)], dtype=np.uint8).reshape(216, 300)
        qframes.append(a)
    qraw = tmp / "qnoise.raw"; qraw.write_bytes(b"".join(x.tobytes() for x in qframes))
    for (ww, wh) in ((1280, 720), (1000, 500), (901, 333), (500, 400), (300, 216), (301, 217), (100, 18), (211, 97), (640, 360), (305, 300), (302, 301)):
        for mode in ("fit", "sidefill", "stretch"):
            out = run(qraw, "--raw-in", "--compose", f"{ww}x{wh}:{mode}")
            size = ww * wh * 4
            for i, a in enumerate(qframes):
                img = QImage(a.tobytes(), 300, 216, 1200, QImage.Format.Format_RGBA8888).copy()
                aspect = Qt.AspectRatioMode.IgnoreAspectRatio if mode == "stretch" else Qt.AspectRatioMode.KeepAspectRatio
                sc = img.scaled(QSize(ww, wh), aspect, Qt.TransformationMode.FastTransformation)
                dst = QImage(ww, wh, QImage.Format.Format_ARGB32_Premultiplied)
                dst.fill(side(img) if mode == "sidefill" else QColor("black"))
                pt = QPainter(dst); pt.drawImage((ww - sc.width()) // 2, (wh - sc.height()) // 2, sc); pt.end()
                ref = dst.convertToFormat(QImage.Format.Format_RGBA8888)
                ptr = ref.constBits(); ptr.setsize(ref.sizeInBytes())
                n += 1
                if bytes(ptr)[:size] != out[i * size:(i + 1) * size]:
                    bad += 1; print("COMPOSE DIFF (noise)", i, ww, wh, mode)
    # layout + nearest scaling + compositing, against Qt itself (QImage.scaled FastTransformation, QPainter on an opaque window image)
    # (the dump composes EVERY frame of a file, so quick mode keeps to the short fuzz files and the smaller windows)
    for f in (files[:6] if not quick else [x for x in files if x.name.startswith('f')][:2]):
        raw = run(f)
        count = len(raw) // c.FRAME
        for (ww, wh) in ((1280, 720), (1920, 1080), (1000, 500), (901, 333), (500, 400), (300, 216), (301, 217)):
            if quick and ww * wh > 1000 * 500:
                continue
            for mode in ("fit", "sidefill", "stretch"):
                out = run(f, "--compose", f"{ww}x{wh}:{mode}")
                size = ww * wh * 4
                for i in range(0, count, 53 if not quick else 31):
                    fr = raw[i * c.FRAME:(i + 1) * c.FRAME]
                    if any(fr[k] not in (0, 255) for k in range(3, c.FRAME, 4)):
                        continue  # in-between alphas: Rust rounds its own way
                    img = QImage(fr, 300, 216, 1200, QImage.Format.Format_RGBA8888).copy()
                    aspect = Qt.AspectRatioMode.IgnoreAspectRatio if mode == "stretch" else Qt.AspectRatioMode.KeepAspectRatio
                    sc = img.scaled(QSize(ww, wh), aspect, Qt.TransformationMode.FastTransformation)
                    dst = QImage(ww, wh, QImage.Format.Format_ARGB32_Premultiplied)
                    dst.fill(side(img) if mode == "sidefill" else QColor("black"))
                    pt = QPainter(dst); pt.drawImage((ww - sc.width()) // 2, (wh - sc.height()) // 2, sc); pt.end()
                    ref = dst.convertToFormat(QImage.Format.Format_RGBA8888)
                    ptr = ref.constBits(); ptr.setsize(ref.sizeInBytes())
                    n += 1
                    if bytes(ptr)[:size] != out[i * size:(i + 1) * size]:
                        bad += 1; print("COMPOSE DIFF", f.name, i, ww, wh, mode)
    print(f"{n} comparisons, {bad} differences")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
