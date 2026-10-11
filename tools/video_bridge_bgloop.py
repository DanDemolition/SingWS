#!/usr/bin/env python3
"""Background-animation sequencing check: a short clip PLAYS TO ITS END, then the next clip is loaded the way the app does it (load with the
current opacity, then ramp the opacity for the dissolve). Records the background position and a hash of the output picture every 0.4 s so a
frozen background shows up as repeating hashes.

    QT_QPA_PLATFORM=cocoa ... .venv-test-arm64-fresh/bin/python tools/video_bridge_bgloop.py --dylib PATH
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_bridge_parity import DEFAULT_ZIP, bind  # noqa: E402

OUT_RECT = (60, 80, 640, 360)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dylib", required=True)
    ap.add_argument("--clips", type=int, default=3)
    ap.add_argument("--seconds", type=int, default=3)
    ap.add_argument("--pause-cdg", action="store_true", help="pause the CDG first (the earlier static-clip test did)")
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--files", default="", help="comma-separated real clips to use instead of generated ones")
    args = ap.parse_args()
    from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication, QWidget

    lib, CB = bind(args.dylib)
    logs = []
    sink = CB(lambda b: logs.append(b.decode(errors="replace")))
    lib.singws_bridge_set_log_callback(sink)
    tmp = Path(tempfile.mkdtemp(prefix="singws-bgloop-"))
    with zipfile.ZipFile(DEFAULT_ZIP) as z:
        z.extractall(tmp)
    files = sorted(tmp.rglob("*"))
    cdg = str(next(f for f in files if f.suffix.lower() == ".cdg"))
    mp3 = str(next(f for f in files if f.suffix.lower() == ".mp3"))
    clips = [f for f in args.files.split(",") if f]
    for i, pattern in ([] if clips else list(enumerate(("testsrc2", "mandelbrot", "rgbtestsrc")[: args.clips]))):
        pass
    for i, pattern in ([] if clips else list(enumerate(("testsrc2", "mandelbrot", "rgbtestsrc")[: args.clips]))):
        out = tmp / f"loop{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"{pattern}=size=640x360:rate={args.fps}", "-t", str(args.seconds),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out)], check=True)
        clips.append(str(out))

    app = QApplication([])
    out_w, prev_w = QWidget(), QWidget()
    for w in (out_w, prev_w):   # frameless and always on top: another window covering the capture region looked exactly like a frozen picture
        w.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    out_w.setGeometry(*OUT_RECT)
    prev_w.setGeometry(60, 460, 320, 180)
    out_w.show(); prev_w.show(); app.processEvents()

    class Main(QObject):
        call = pyqtSignal(object)
        finished = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.call.connect(lambda box: box.__setitem__("result", box["fn"](*box["args"])), Qt.ConnectionType.BlockingQueuedConnection)
            self.finished.connect(app.quit)

    main = Main()

    class Proxy:
        def __getattr__(self, name):
            fn = getattr(lib, name)

            def go(*a):
                box = {"fn": fn, "args": a}
                main.call.emit(box)
                return box.get("result")
            return go

    B = Proxy()
    report = {"timeline": []}

    def shot_hash():
        p = tmp / "s.png"
        subprocess.run(["screencapture", "-x", "-R", ",".join(map(str, OUT_RECT)), str(p)], check=True)
        return hashlib.md5(p.read_bytes()).hexdigest()[:8]

    def scenario():
        try:
            h = B.singws_bridge_create(int(out_w.winId()), int(prev_w.winId()), cdg.encode(), mp3.encode())
            B.singws_bridge_set_volume(h, 0.0)
            B.singws_bridge_play(h)
            time.sleep(1.0)
            if args.pause_cdg:
                B.singws_bridge_pause(h)
                time.sleep(0.5)
            opacity = 0.6
            for i, clip in enumerate(clips):
                B.singws_bridge_load_background(h, clip.encode(), opacity if i == 0 else 0.0)
                if i > 0:
                    # the app's dissolve: the new clip starts at 0 and the opacity is ramped up
                    for step in range(1, 9):
                        B.singws_bridge_set_background_opacity(h, opacity * step / 8)
                        time.sleep(0.04)
                t0 = time.time()
                last_pos = -1
                # play it to its end (the app advances when at_end is true), sampling the picture as it goes
                while time.time() - t0 < (14 if args.files else args.seconds + 4):
                    pos = B.singws_bridge_background_position(h)
                    at_end = bool(B.singws_bridge_background_at_end(h))
                    report["timeline"].append((i, round(time.time() - t0, 1), pos, at_end, shot_hash()))
                    if at_end and i < len(clips) - 1:
                        break
                    time.sleep(0.4)
            report["present_count"] = int(B.singws_bridge_present_count(h))
        except Exception as exc:  # noqa: BLE001
            report["error"] = repr(exc)
        finally:
            main.finished.emit()

    threading.Thread(target=scenario, daemon=True).start()
    QTimer.singleShot(120000, app.quit)
    app.exec()
    cur = None
    for clip, t, pos, at_end, hsh in report["timeline"]:
        if clip != cur:
            print(f"--- clip {clip}")
            cur = clip
        print(f"  t={t:4.1f}s bg_pos={pos:5d}ms at_end={at_end!s:5} picture={hsh}")
    verdicts = []
    for clip in range(len(clips)):
        rows = [x for x in report["timeline"] if x[0] == clip]
        max_pos = max((x[2] for x in rows), default=0)
        saw_end = any(x[3] for x in rows)
        ok = max_pos > 500 and saw_end
        verdicts.append(ok)
        print(f"clip {clip}: max background position {max_pos} ms, end signal seen: {saw_end}", "" if ok else "<-- NO POSITION / NO END SIGNAL (the app would never advance)")
    print("SEQUENCING OK" if all(verdicts) else "SEQUENCING BROKEN")
    for clip in range(len(clips)):
        hashes = [x[4] for x in report["timeline"] if x[0] == clip]
        print(f"clip {clip}: {len(set(hashes))} distinct pictures in {len(hashes)} samples", "<-- FROZEN?" if len(hashes) > 3 and len(set(hashes)) < 3 else "")
    for x in logs:
        if x.startswith("[bridge]") and (" view " in x or "drawable" in x or "render thread" in x):
            print("  VIEWLOG", x[:120])
    print("  present_count", report.get("present_count"))
    for x in logs:
        if x.startswith("[background]"):
            print("  LOG", x[:150])
    if "error" in report:
        print("error:", report["error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
