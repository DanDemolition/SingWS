#!/usr/bin/env python3
"""The picture the AUDIENCE sees, against the Rust `compose`: loads a CDG + MP3 through the app's MpvPlaybackPlugin (default Rust video bridge),
pauses and seeks, screenshots the output window with `screencapture` and compares it with `singws-cdg-dump --compose WxH:fit` at the screenshot's
pixel size. Prints how many pixels differ by more than --tolerance and the largest difference. Opens a 640x360 always-on-top window for a few
seconds; silent; uses only the files you pass.

    SINGWS_VIDEO_BRIDGE=rust SINGWS_VIDEO_LIB=.../libsingws_video.dylib QT_QPA_PLATFORM=cocoa <venv>/bin/python tools/cdg_window_compare.py SONG.cdg SONG.mp3
"""
import argparse, os, subprocess, sys, tempfile, time
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QWidget
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mpv_playback_iina import MpvPlaybackPlugin  # noqa: E402

DUMP = os.environ.get("CARGO_TARGET_DIR", str(Path.home() / ".cache/singws-rust-target-display")) + "/release/singws-cdg-dump"
RECT = (60, 80, 640, 360)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cdg"); ap.add_argument("mp3")
    ap.add_argument("--at", type=float, nargs="*", default=[3.0, 8.0])
    ap.add_argument("--tolerance", type=int, default=8)
    ap.add_argument("--margin", type=int, default=3, help="ignore this many pixels at the window edge (the frameless window has a faint border/shadow that is not the picture)")
    ap.add_argument("--fill", choices=["off", "colour", "blur"], default="off", help="bridge side fill mode (0 / 1 / 2 = the app's fit / sidefill / blur)")
    ap.add_argument("--size", default="640x360", help="output window size in points, e.g. 400x500 for a window narrower than the picture")
    ap.add_argument("--bg", help="a (static) background video to play under the lyrics: compares the shader's modes 4 and 5 (needs ffmpeg to read its frame)")
    ap.add_argument("--bg-size", default="1280x720", help="size of the --bg video's frame (the bridge's background texture is 16:9)")
    ap.add_argument("--bg-opacity", type=float, default=1.0)
    ap.add_argument("--keep", help="write the screenshot and the Rust image here as PNG")
    a = ap.parse_args()
    global RECT
    RECT = (60, 80, *map(int, a.size.split("x")))
    app = QApplication([])
    out, prev = QWidget(), QWidget()
    for w, r in ((out, RECT), (prev, (900, 80, 320, 180))):
        w.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        w.setGeometry(*r); w.show()
    app.processEvents()
    plugin = MpvPlaybackPlugin(log=lambda m: None)
    if not plugin.attach(prev, out):
        raise SystemExit(plugin.errorString())
    bad = 0
    def pump(sec):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            app.processEvents(); time.sleep(0.02)
    try:
        if not plugin.loadSingWSMedia(a.cdg, a.mp3, autoplay=False):
            raise SystemExit(plugin.errorString() or "load failed")
        plugin.setVolume(0.0)
        plugin.setCdgOutputSidefill({'off': 0, 'colour': 1, 'blur': 2}[a.fill])
        end = time.monotonic() + 8
        while time.monotonic() < end and not plugin.visualsReady():
            pump(0.05)
        bg_args = []
        if a.bg:
            if not plugin.loadBackgroundVideo(a.bg, a.bg_opacity):
                raise SystemExit("background video did not load: " + str(plugin.errorString()))
            pump(3.0)
            bgraw = Path(tempfile.mkdtemp()) / "bg.rgba"
            with open(bgraw, "wb") as f:
                subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", a.bg, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"], stdout=f, check=True)
            bg_args = ["--bg", f"{bgraw}:{a.bg_size}", "--bg-opacity", repr(a.bg_opacity)]
        for t in a.at:
            plugin.pauseMedia(); pump(0.4); plugin.seekMedia(int(t * 1000)); pump(2.0)
            shot = Path(tempfile.mkdtemp()) / "s.png"
            subprocess.run(["screencapture", "-x", "-R", ",".join(map(str, RECT)), str(shot)], check=True)
            img = QImage(str(shot)).convertToFormat(QImage.Format.Format_RGBA8888)
            ptr = img.constBits(); ptr.setsize(img.sizeInBytes()); got = bytes(ptr)
            w, h = img.width(), img.height()
            want = subprocess.run([DUMP, a.cdg, "--at", repr(t), "--bridge", f"{w}x{h}:{a.fill}", *bg_args], capture_output=True).stdout
            stride = img.bytesPerLine()
            n = over = mx = 0
            for y in range(h):
                for x in range(w):
                    if min(x, y, w - 1 - x, h - 1 - y) < a.margin:
                        continue
                    o, k = y * stride + x * 4, (y * w + x) * 4
                    d = max(abs(got[o + c] - want[k + c]) for c in range(3))
                    n += 1; over += d > a.tolerance; mx = max(mx, d)
            print(f"t={t}s window {w}x{h}: {over} of {n} pixels differ by more than {a.tolerance} (largest {mx})")
            if a.keep:
                Path(a.keep).mkdir(parents=True, exist_ok=True)
                img.save(f"{a.keep}/window_{t}.png")
                QImage(want, w, h, w * 4, QImage.Format.Format_RGBA8888).save(f"{a.keep}/compose_{t}.png")
            bad += over > 0
    finally:
        plugin.shutdown(); out.close(); prev.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
