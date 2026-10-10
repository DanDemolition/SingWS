#!/usr/bin/env python3
"""On-screen proof that karaoke frames keep being drawn while the GUI thread is frozen (render-thread mode).

Opens two small windows (output + preview) driven by a bridge dylib, plays a silent moving test clip, then freezes the Qt
main thread for several seconds and checks, from another thread, that the picture kept changing:
  * the bridge's own present counter keeps increasing, and
  * two screenshots taken during the freeze differ (the on-screen picture really moved).

    .venv/bin/python tools/render_thread_probe.py --dylib PATH [--mode off|on] [--freeze 5] [--hold-gil]

It shows windows on the screen for ~20 s and plays NO sound. Run it once per mode (the mode is read when the bridge is
created). Exit status 0 = the picture kept moving during the freeze, 1 = it froze.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget


def make_clip(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-t", "25",
         "-an", "-pix_fmt", "yuv420p", str(path)], check=True)


def shot(rect, out: Path) -> bool:
    x, y, w, h = rect
    r = subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(out)], capture_output=True)
    return r.returncode == 0 and out.exists()


def mean_diff(a: Path, b: Path) -> float:
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert("L"), Image.open(b).convert("L")
    if ia.size != ib.size:
        return -1.0
    diff = ImageChops.difference(ia, ib)
    hist = diff.histogram()
    total = sum(i * c for i, c in enumerate(hist))
    return total / (ia.size[0] * ia.size[1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dylib", required=True)
    ap.add_argument("--mode", choices=("off", "on"), default="off")
    ap.add_argument("--freeze", type=float, default=5.0)
    ap.add_argument("--hold-gil", action="store_true", help="freeze by spinning in Python (holds the GIL) instead of sleeping")
    ap.add_argument("--scenario", choices=("freeze", "cdgshot", "resize", "soak"), default="freeze")
    ap.add_argument("--cdg-zip", help="MP3+G zip (copied to a scratch folder, played at volume 0) for --scenario cdgshot")
    ap.add_argument("--seek-ms", type=int, default=25000)
    ap.add_argument("--soak-seconds", type=float, default=45.0)
    ap.add_argument("--shot", help="where --scenario cdgshot writes its screenshot")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="singws-probe-"))
    clip = work / "probe.mp4"
    audio = None
    if args.scenario in ("cdgshot", "soak") and args.cdg_zip:
        import zipfile
        with zipfile.ZipFile(args.cdg_zip) as zf:
            zf.extractall(work)
        cdg_path = next(work.rglob("*.cdg"))
        mp3_path = next(work.rglob("*.mp3"))
        if args.scenario == "cdgshot":
            clip, audio = cdg_path, str(mp3_path).encode()
        else:
            make_clip(clip)
    else:
        make_clip(clip)

    app = QApplication(sys.argv)

    class Bridge(QObject):
        # Signals emitted from the observer thread are delivered on the main thread (the receiver lives there). A bare
        # QTimer.singleShot from a plain Python thread never fires: that thread has no event loop.
        freeze = pyqtSignal()
        quit_now = pyqtSignal()

    signals = Bridge()
    out = QWidget(); out.setWindowTitle("probe output"); out.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    out.setGeometry(60, 80, 640, 360); out.show()
    prev = QWidget(); prev.setWindowTitle("probe preview"); prev.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    prev.setGeometry(740, 80, 320, 180); prev.show()
    app.processEvents()

    lib = ctypes.CDLL(args.dylib, mode=os.RTLD_LAZY | os.RTLD_LOCAL)
    lib.singws_bridge_create.restype = ctypes.c_void_p
    lib.singws_bridge_create.argtypes = [ctypes.c_size_t, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_char_p]
    lib.singws_bridge_play.argtypes = [ctypes.c_void_p]
    lib.singws_bridge_destroy.argtypes = [ctypes.c_void_p]
    lib.singws_bridge_present_count.argtypes = [ctypes.c_void_p]
    lib.singws_bridge_present_count.restype = ctypes.c_uint64
    lib.singws_bridge_render_thread_active.argtypes = [ctypes.c_void_p]
    lib.singws_bridge_position.argtypes = [ctypes.c_void_p]
    lib.singws_bridge_position.restype = ctypes.c_int64
    lib.singws_bridge_set_render_thread(1 if args.mode == "on" else 0)
    logs = []
    CB = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
    thunk = CB(lambda t: logs.append(t.decode("utf-8", "replace")))
    lib.singws_bridge_set_log_callback(thunk)
    lib.singws_bridge_set_volume.argtypes = [ctypes.c_void_p, ctypes.c_double]
    lib.singws_bridge_pause.argtypes = [ctypes.c_void_p]
    lib.singws_bridge_seek.argtypes = [ctypes.c_void_p, ctypes.c_int64]
    handle = lib.singws_bridge_create(int(out.winId()), int(prev.winId()), str(clip).encode(), audio)
    if not handle:
        print("bridge create failed; log:", *logs[-8:], sep="\n  ")
        return 2
    lib.singws_bridge_set_volume(handle, 0.0)
    lib.singws_bridge_play(handle)
    active = bool(lib.singws_bridge_render_thread_active(handle))
    print(f"mode requested={args.mode} render_thread_active={active}")
    result = {}

    def freeze_main():
        print(f"FREEZING the GUI thread for {args.freeze:.0f}s ({'spinning, holds the GIL' if args.hold_gil else 'sleeping'})", flush=True)
        end = time.monotonic() + args.freeze
        if args.hold_gil:
            while time.monotonic() < end:
                pass
        else:
            time.sleep(args.freeze)

    def observer_cdgshot():
        time.sleep(3.0)
        lib.singws_bridge_seek(handle, args.seek_ms)
        time.sleep(3.0)
        lib.singws_bridge_pause(handle)
        time.sleep(1.0)
        shot((out.x(), out.y() + 28, 640, 330), Path(args.shot))
        result["shot"] = args.shot
        signals.quit_now.emit()

    class Resizer(QObject):
        go = pyqtSignal(int, int)

    resizer = Resizer()
    resizer.go.connect(lambda w, h: out.resize(w, h))

    def observer_resize():
        from PIL import Image
        time.sleep(3.0)
        resizer.go.emit(900, 520)
        time.sleep(2.0)
        a, b = work / "resize_a.png", work / "resize_b.png"
        rect = (out.x(), out.y() + 28, 900, 490)
        shot(rect, a); time.sleep(0.7); shot(rect, b)
        img = Image.open(a).convert("L")
        w2, h2 = img.size
        # the picture must fill the enlarged window (testsrc2 has no black areas), including its far corner
        far = img.crop((int(w2 * 0.85), int(h2 * 0.85), w2, h2))
        dark = sum(1 for px in far.getdata() if px < 12) / max(1, far.size[0] * far.size[1])
        result["resize_filled"] = dark < 0.5
        result["resize_far_corner_dark_fraction"] = round(dark, 2)
        result["resize_moving_diff"] = mean_diff(a, b)
        signals.quit_now.emit()

    def observer_soak():
        import random
        random.seed(7)
        lib.singws_bridge_load.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.singws_bridge_load.restype = ctypes.c_int
        sizes = [(640, 360), (800, 450), (500, 300), (960, 540)]
        actions = []
        t_end = time.monotonic() + args.soak_seconds
        n = 0
        while time.monotonic() < t_end:
            time.sleep(1.1)
            n += 1
            kind = random.choice(["resize", "hide", "load", "load", "freeze"] if n % 5 else ["freeze"])
            actions.append(kind)
            if kind == "resize":
                resizer.go.emit(*random.choice(sizes))
            elif kind == "hide":
                visibility.toggle.emit()
            elif kind == "load":
                loader.go.emit(n)
            else:
                signals.freeze.emit(); time.sleep(args.freeze)
        result["actions"] = {k: actions.count(k) for k in set(actions)}
        time.sleep(1.0)
        c0 = lib.singws_bridge_present_count(handle); time.sleep(2.0); c1 = lib.singws_bridge_present_count(handle)
        result["frames_in_final_2s"] = c1 - c0
        signals.quit_now.emit()

    class Visibility(QObject):
        toggle = pyqtSignal()

    visibility = Visibility()

    def _toggle():
        out.hide(); app.processEvents(); time.sleep(0.2); out.show()
    visibility.toggle.connect(_toggle)

    class Loader(QObject):
        go = pyqtSignal(int)

    loader = Loader()

    def _load(i):
        if args.cdg_zip and i % 2 == 0:
            lib.singws_bridge_load(handle, str(cdg_path).encode(), str(mp3_path).encode())
        else:
            lib.singws_bridge_load(handle, str(clip).encode(), None)
        lib.singws_bridge_set_volume(handle, 0.0)
        lib.singws_bridge_play(handle)
    loader.go.connect(_load)

    def observer():
        time.sleep(3.0)  # let playback settle
        before_count = lib.singws_bridge_present_count(handle)
        base_a, base_b = work / "base_a.png", work / "base_b.png"
        rect = (out.x(), out.y() + 28, 640, 330)  # inside the window, below the title bar
        shot(rect, base_a); time.sleep(0.6); shot(rect, base_b)
        result["baseline_diff"] = mean_diff(base_a, base_b)
        signals.freeze.emit()
        time.sleep(1.0)
        c0 = lib.singws_bridge_present_count(handle); p0 = lib.singws_bridge_position(handle)
        a, b = work / "frozen_a.png", work / "frozen_b.png"
        shot(rect, a); time.sleep(args.freeze - 2.5); shot(rect, b)
        c1 = lib.singws_bridge_present_count(handle); p1 = lib.singws_bridge_position(handle)
        result["frozen_diff"] = mean_diff(a, b)
        result["frames_during_freeze"] = c1 - c0
        result["media_position_advanced_ms"] = p1 - p0
        time.sleep(1.8)
        result["total_frames"] = lib.singws_bridge_present_count(handle) - before_count
        signals.quit_now.emit()

    signals.freeze.connect(freeze_main)
    signals.quit_now.connect(app.quit)
    target = {"freeze": observer, "cdgshot": observer_cdgshot, "resize": observer_resize, "soak": observer_soak}[args.scenario]
    threading.Thread(target=target, daemon=True).start()
    QTimer.singleShot(int((args.soak_seconds + 40) * 1000) if args.scenario == "soak" else 60000, app.quit)
    app.exec()
    lib.singws_bridge_destroy(handle)
    if args.scenario == "cdgshot":
        print(result); return 0 if Path(args.shot).exists() else 1
    if args.scenario == "soak":
        print(result)
        ok = result.get("frames_in_final_2s", 0) > 20
        print("RESULT:", "survived the soak and still drawing" if ok else "soak FAILED (not drawing at the end)")
        return 0 if ok else 1
    if args.scenario == "resize":
        print(result)
        ok = result.get("resize_filled") and result.get("resize_moving_diff", 0) > 1.0
        print("RESULT:", "picture refilled the resized window and kept moving" if ok else "resize FAILED")
        return 0 if ok else 1
    moved = result.get("frozen_diff", 0) > 1.0 and result.get("frames_during_freeze", 0) > 10
    print(result)
    print("RESULT:", "picture KEPT MOVING during the freeze" if moved else "picture FROZE with the GUI thread")
    for line in logs:
        if "render thread" in line or "error" in line.lower():
            print("  bridge:", line.strip())
    return 0 if moved else 1


if __name__ == "__main__":
    sys.exit(main())
