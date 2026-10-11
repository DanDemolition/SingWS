#!/usr/bin/env python3
"""On-screen A/B of the picture path: screenshots of the output and preview windows from a bridge dylib, and a pixel diff between two runs.

    # one process per dylib; two windows appear at fixed positions for ~45 s, NO sound (volume 0)
    QT_QPA_PLATFORM=cocoa QT_QPA_PLATFORM_PLUGIN_PATH=/tmp/singws-release-qt-platforms \\
      .venv-test-arm64-fresh/bin/python tools/video_bridge_shots.py run --dylib PATH --label objc --outdir shots_objc
    SINGWS_LIBMPV=.../singws_libmpv.2.dylib ... tools/video_bridge_shots.py run --dylib libsingws_video.dylib --label rust --outdir shots_rust
    .venv-test-arm64-fresh/bin/python tools/video_bridge_shots.py compare shots_objc shots_rust

Frames are taken after an exact seek + pause so both bridges show the same frame: a CDG at 45 s (plain, side fill mode 1, side fill mode 2) and an
MP4 at 30 s. `compare` prints, per image, how many pixels differ and the largest channel difference (exit 0 when every image is identical or
within `--tolerance`, default 0 differing pixels beyond a 2/255 channel difference).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_bridge_parity import DEFAULT_MP4, DEFAULT_ZIP, bind  # noqa: E402

OUT_RECT = (60, 80, 640, 360)       # x, y, w, h in screen points
PREV_RECT = (60, 460, 320, 180)


def make_static_clips(directory: Path):
    """Two static 60 s clips with real detail (so a screenshot is the same whatever the clock says)."""
    from PIL import Image, ImageDraw
    paths = []
    for name, c1, c2 in (("bgA", (30, 90, 200), (240, 220, 60)), ("bgB", (200, 40, 90), (40, 200, 160))):
        img = Image.new("RGB", (1280, 720))
        d = ImageDraw.Draw(img)
        for x in range(1280):
            t = x / 1279
            d.line([(x, 0), (x, 719)], fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
        for k in range(0, 720, 60):
            d.rectangle([0, k, 1279, k + 8], fill=(255, 255, 255))
        d.ellipse([440, 160, 840, 560], outline=(0, 0, 0), width=14)
        png = directory / f"{name}.png"
        img.save(png)
        mp4 = directory / f"{name}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", "60", "-framerate", "10", "-i", str(png), "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-an", str(mp4)], check=True)
        paths.append(str(mp4))
    return paths


def shoot(rect, path: Path) -> None:
    x, y, w, h = rect
    subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(path)], check=True)


def run(args) -> int:
    from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication, QWidget

    lib, CB = bind(args.dylib)
    lib.singws_bridge_set_sidefill.argtypes = [ctypes.c_void_p, ctypes.c_int]
    logs: list[str] = []
    sink = CB(lambda b: logs.append(b.decode(errors="replace")))
    lib.singws_bridge_set_log_callback(sink)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="singws-shots-")
    with zipfile.ZipFile(args.zip) as z:
        z.extractall(tmp)
    files = sorted(Path(tmp).rglob("*"))
    cdg = next(str(f) for f in files if f.suffix.lower() == ".cdg")
    mp3 = next(str(f) for f in files if f.suffix.lower() == ".mp3")

    clip_a, clip_b = make_static_clips(Path(tmp))
    app = QApplication([])
    out_w, prev_w = QWidget(), QWidget()
    for w, rect in ((out_w, OUT_RECT), (prev_w, PREV_RECT)):
        w.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        w.setGeometry(*rect)
        w.show()
    app.processEvents()

    class Main(QObject):
        call = pyqtSignal(object)
        finished = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.call.connect(self._run, Qt.ConnectionType.BlockingQueuedConnection)
            self.finished.connect(app.quit)

        def _run(self, box):
            box["result"] = box["fn"](*box["args"])

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
    result: dict = {"label": args.label, "shots": [], "notes": {}}

    def wait_for(fn, timeout=20.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if fn():
                return True
            time.sleep(0.05)
        return False

    def settle_at(h, ms):
        # pause FIRST, then seek: an exact seek while paused shows the frame at exactly `ms`; pausing after the seek lets the clock run
        # on by a variable amount (a karaoke wipe moves a pixel column between runs)
        B.singws_bridge_pause(h)
        time.sleep(0.4)
        B.singws_bridge_seek(h, ms)
        wait_for(lambda: abs(B.singws_bridge_position(h) - ms) < 400, 8.0)
        time.sleep(1.2)

    def take(h, name, with_preview=True):
        # force one more present from the retained texture so the screenshot is of the settled picture
        B.singws_bridge_refresh_views(h, int(out_w.winId()), int(prev_w.winId()))
        time.sleep(0.6)
        shoot(OUT_RECT, outdir / f"{name}_output.png")
        result["shots"].append(f"{name}_output.png")
        if with_preview:
            shoot(PREV_RECT, outdir / f"{name}_preview.png")
            result["shots"].append(f"{name}_preview.png")

    def scenario():
        try:
            h = B.singws_bridge_create(int(out_w.winId()), int(prev_w.winId()), cdg.encode(), mp3.encode())
            result["notes"]["created"] = bool(h)
            if not h:
                return
            B.singws_bridge_set_volume(h, 0.0)
            wait_for(lambda: B.singws_bridge_duration(h) > 0)
            B.singws_bridge_play(h)
            wait_for(lambda: B.singws_bridge_visual_ready(h), 10.0)
            settle_at(h, 45000)
            result["notes"]["cdg_visual_ready"] = bool(B.singws_bridge_visual_ready(h))
            take(h, "cdg_plain")
            B.singws_bridge_set_sidefill(h, 1)
            time.sleep(0.4)
            take(h, "cdg_sidefill1", with_preview=False)
            B.singws_bridge_set_sidefill(h, 2)
            time.sleep(0.4)
            take(h, "cdg_sidefill2", with_preview=False)
            B.singws_bridge_set_sidefill(h, 0)
            # grab_frame of a CDG comes from the retained 300x216 texture: the bytes themselves must match
            import hashlib
            def grab():
                w_, h_, s_ = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
                p_ = B.singws_bridge_grab_frame(h, ctypes.byref(w_), ctypes.byref(h_), ctypes.byref(s_))
                if not p_:
                    return None
                data = ctypes.string_at(p_, s_.value * h_.value)
                B.singws_bridge_free_frame(p_)
                return {"w": w_.value, "h": h_.value, "stride": s_.value, "sha1": hashlib.sha1(data).hexdigest()}
            result["notes"]["grab_cdg"] = grab()
            # background animation under the CDG: static clips, opacity, outgoing-frame dissolve, stop
            B.singws_bridge_load_background(h, clip_a.encode(), 0.6)
            wait_for(lambda: B.singws_bridge_background_position(h) > 300, 6.0)
            time.sleep(0.6)
            result["notes"]["bg_playing"] = {"pos_advancing": B.singws_bridge_background_position(h) > 0,
                                             "paused": bool(B.singws_bridge_background_paused(h)), "at_end": bool(B.singws_bridge_background_at_end(h))}
            take(h, "cdg_bg_a")
            B.singws_bridge_load_background(h, clip_b.encode(), 0.6)
            B.singws_bridge_set_background_opacity(h, 0.3)      # mix = 0.3 / 0.6 = 0.5: halfway through the dissolve
            time.sleep(1.0)
            take(h, "cdg_bg_crossfade")
            B.singws_bridge_set_background_opacity(h, 0.6)       # dissolve complete
            time.sleep(0.5)
            take(h, "cdg_bg_b")
            B.singws_bridge_stop_background(h)
            time.sleep(0.6)
            result["notes"]["bg_after_stop"] = {"pos": B.singws_bridge_background_position(h), "at_end": bool(B.singws_bridge_background_at_end(h))}
            take(h, "cdg_after_bg_stop")
            # Qt hides and re-shows the audience window: the retained texture must come back without recreating anything
            def qt(fn):
                main.call.emit({"fn": fn, "args": ()})
            qt(lambda: (out_w.hide(), prev_w.hide()))
            time.sleep(0.6)
            qt(lambda: (out_w.show(), prev_w.show()))
            time.sleep(0.6)
            take(h, "cdg_after_hide_show")
            # the audience window is resized: hold the output through the transition, then present at the settled size
            big = (60, 80, 900, 500)
            qt(lambda: out_w.setGeometry(*big))
            B.singws_bridge_begin_transition(h, 150)
            time.sleep(1.0)
            B.singws_bridge_refresh_views(h, int(out_w.winId()), int(prev_w.winId()))
            time.sleep(0.6)
            shoot(big, outdir / "cdg_after_resize_output.png")
            result["shots"].append("cdg_after_resize_output.png")
            qt(lambda: out_w.setGeometry(*OUT_RECT))
            time.sleep(0.6)
            B.singws_bridge_load(h, str(args.mp4).encode(), None)
            wait_for(lambda: B.singws_bridge_duration(h) > 0)
            B.singws_bridge_play(h)
            wait_for(lambda: B.singws_bridge_visual_ready(h), 10.0)
            settle_at(h, 30000)
            take(h, "mp4")
            g = grab()
            result["notes"]["grab_mp4"] = g and {"w": g["w"], "h": g["h"], "stride": g["stride"]}
            result["notes"]["present_count"] = int(B.singws_bridge_present_count(h))
            B.singws_bridge_destroy(h)
        except Exception as exc:  # noqa: BLE001
            result["notes"]["error"] = repr(exc)
        finally:
            result["log"] = [x for x in logs if x.startswith("[bridge]") and (" view " in x or "drawable" in x or "reparent" in x)]
            result["full_log"] = [x for x in logs if x.startswith("[bridge]")]
            (outdir / "result.json").write_text(json.dumps(result, indent=1))
            main.finished.emit()

    threading.Thread(target=scenario, daemon=True).start()
    QTimer.singleShot(150000, app.quit)
    app.exec()
    print(f"wrote {outdir}")
    return 0


def cycles(args) -> int:
    """Stress the lifecycle: many create/destroy cycles and rapid CDG<->MP4 switches on one handle, with real windows. Passing = no crash,
    every create succeeds, every destroy logs 'clean shutdown', memory growth stays small."""
    import resource
    from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication, QWidget

    lib, CB = bind(args.dylib)
    logs: list[str] = []
    sink = CB(lambda b: logs.append(b.decode(errors="replace")))
    lib.singws_bridge_set_log_callback(sink)
    tmp = tempfile.mkdtemp(prefix="singws-cycles-")
    with zipfile.ZipFile(args.zip) as z:
        z.extractall(tmp)
    files = sorted(Path(tmp).rglob("*"))
    cdg = next(str(f) for f in files if f.suffix.lower() == ".cdg")
    mp3 = next(str(f) for f in files if f.suffix.lower() == ".mp3")
    app = QApplication([])
    out_w, prev_w = QWidget(), QWidget()
    for w, rect in ((out_w, OUT_RECT), (prev_w, PREV_RECT)):
        w.setGeometry(*rect)
        w.show()
    app.processEvents()

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
    report: dict = {}

    def scenario():
        try:
            rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            created = 0
            for _ in range(args.cycles):
                h = B.singws_bridge_create(int(out_w.winId()), int(prev_w.winId()), cdg.encode(), mp3.encode())
                created += bool(h)
                if h:
                    B.singws_bridge_set_volume(h, 0.0)
                    B.singws_bridge_play(h)
                    time.sleep(0.8)
                    B.singws_bridge_destroy(h)
            report["created"], report["cycles"] = created, args.cycles
            report["clean_shutdowns"] = sum(1 for x in logs if "clean shutdown" in x)
            h = B.singws_bridge_create(int(out_w.winId()), int(prev_w.winId()), cdg.encode(), mp3.encode())
            B.singws_bridge_set_volume(h, 0.0)
            B.singws_bridge_play(h)
            for i in range(args.switches):
                B.singws_bridge_load(h, (str(args.mp4) if i % 2 == 0 else cdg).encode(), None if i % 2 == 0 else mp3.encode())
                time.sleep(0.35)          # faster than a load completes: the loads must queue and settle, not wedge
            time.sleep(2.5)
            report["alive_after_switches"] = B.singws_bridge_duration(h) > 0
            report["visual_ready_after_switches"] = bool(B.singws_bridge_visual_ready(h))
            B.singws_bridge_destroy(h)
            report["rss_growth_mb"] = round((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - rss0) / 1048576, 1)
            report["tracebacks"] = sum(1 for x in logs if "Traceback" in x)
        except Exception as exc:  # noqa: BLE001
            report["error"] = repr(exc)
        finally:
            Path(args.out).write_text(json.dumps(report, indent=1))
            main.finished.emit()

    threading.Thread(target=scenario, daemon=True).start()
    QTimer.singleShot(240000, app.quit)
    app.exec()
    print(json.dumps(report, indent=1))
    ok = (report.get("created") == report.get("cycles") and report.get("clean_shutdowns", 0) >= report.get("cycles", 1)
          and report.get("alive_after_switches") and "error" not in report)
    print("LIFECYCLE OK" if ok else "LIFECYCLE FAILED")
    return 0 if ok else 1


def compare(args) -> int:
    from PIL import Image, ImageChops
    a, b = Path(args.a), Path(args.b)
    ra, rb = (json.loads((d / "result.json").read_text()) for d in (a, b))
    bad = 0
    for note in sorted(set(ra["notes"]) | set(rb["notes"])):
        print(f"note {note}: {ra['notes'].get(note)} | {rb['notes'].get(note)}")
    for name in ra["shots"]:
        pa, pb = a / name, b / name
        if not pb.exists():
            print(f"MISSING {name}")
            bad += 1
            continue
        ia, ib = Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB")
        if ia.size != ib.size:
            print(f"DIFF {name}: sizes {ia.size} vs {ib.size}")
            bad += 1
            continue
        diff = ImageChops.difference(ia, ib)
        px = list(diff.get_flattened_data()) if hasattr(diff, 'get_flattened_data') else list(diff.getdata())
        over = sum(1 for p in px if max(p) > args.tolerance)
        worst = max((max(p) for p in px), default=0)
        ok = over == 0
        bad += 0 if ok else 1
        print(f"{'ok  ' if ok else 'DIFF'} {name:28} differing pixels {over} of {len(px)}  worst channel diff {worst}")
        if not ok:
            ImageChops.difference(ia, ib).point(lambda v: min(255, v * 8)).save(b / f"diff_{name}")
    print("\nIDENTICAL" if not bad else f"\n{bad} image(s) differ (diff_*.png written next to the second run)")
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dylib", required=True)
    r.add_argument("--label", required=True)
    r.add_argument("--outdir", required=True)
    r.add_argument("--zip", default=str(DEFAULT_ZIP))
    r.add_argument("--mp4", default=str(DEFAULT_MP4))
    y = sub.add_parser("cycles")
    y.add_argument("--dylib", required=True)
    y.add_argument("--out", required=True)
    y.add_argument("--cycles", type=int, default=15)
    y.add_argument("--switches", type=int, default=10)
    y.add_argument("--zip", default=str(DEFAULT_ZIP))
    y.add_argument("--mp4", default=str(DEFAULT_MP4))
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--tolerance", type=int, default=2)
    args = ap.parse_args()
    return run(args) if args.cmd == "run" else cycles(args) if args.cmd == "cycles" else compare(args)


if __name__ == "__main__":
    sys.exit(main())
