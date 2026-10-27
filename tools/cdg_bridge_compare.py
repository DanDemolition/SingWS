#!/usr/bin/env python3
"""Does the Rust CDG decoder draw what the app's real video bridge (libmpv) holds? Loads a CDG + MP3 through the app's MpvPlaybackPlugin
(muted), pauses, seeks to chosen moments, reads the bridge's retained 300x216 frame with `grabFrame()` (what the phone preview uses) and
compares it with `singws-cdg-dump --at` for the same moment. Only RGB of opaque pixels is compared (the bridge frame carries no alpha).

    QT_QPA_PLATFORM=cocoa <main checkout>/.venv/bin/python tools/cdg_bridge_compare.py SONG.cdg SONG.mp3 [--at 3 5 8]
Opens two small windows for a few seconds (cocoa platform: libmpv needs a real GL view). Silent. Uses only the files you pass in."""
import argparse, os, subprocess, sys, time
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QWidget
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from mpv_playback_iina import MpvPlaybackPlugin  # noqa: E402

DUMP = os.environ.get("CARGO_TARGET_DIR", str(Path.home() / ".cache/singws-rust-target-cdg")) + "/release/singws-cdg-dump"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cdg"); ap.add_argument("mp3")
    ap.add_argument("--at", type=float, nargs="*", default=[3.0, 5.0, 8.0, 10.6])
    ap.add_argument("--tolerance", type=int, default=1, help="allowed difference per colour channel (libmpv converts the palette picture through YUV and dithers: off by one in a few percent of pixels)")
    ap.add_argument("--slack-ms", type=int, default=0, help="also accept the Rust frame this many ms before/after")
    a = ap.parse_args()
    app = QApplication.instance() or QApplication([])
    out, prev = QWidget(), QWidget()
    out.resize(640, 360); prev.resize(320, 180); out.show(); prev.show(); app.processEvents()
    plugin = MpvPlaybackPlugin(log=lambda m: print(m, file=sys.stderr))
    if not plugin.attach(prev, out):
        raise SystemExit(plugin.errorString())
    bad = 0
    try:
        if not plugin.loadSingWSMedia(a.cdg, a.mp3, autoplay=False):
            raise SystemExit(plugin.errorString() or "load failed")
        plugin.setVolume(0.0)
        end = time.monotonic() + 8
        while time.monotonic() < end and not plugin.visualsReady():
            app.processEvents(); time.sleep(0.02)
        for t in a.at:
            plugin.pauseMedia(); plugin.seekMedia(int(t * 1000))
            end = time.monotonic() + 3
            while time.monotonic() < end:
                app.processEvents(); time.sleep(0.02)
            img = plugin.grabFrame()
            if img is None:
                print(f"t={t}: no frame from the bridge"); bad += 1; continue
            img = img.convertToFormat(img.Format.Format_RGBA8888)
            ptr = img.constBits(); ptr.setsize(img.sizeInBytes())
            got = bytes(ptr)
            w, h = img.width(), img.height()
            ok_any = False; best = None
            for dms in range(-a.slack_ms, a.slack_ms + 1, 10):
                want = subprocess.run([DUMP, a.cdg, "--at", repr(max(0.0, t + dms / 1000))], capture_output=True).stdout
                diff = sum(1 for k in range(0, 300 * 216 * 4, 4) if want[k + 3] == 255 and max(abs(want[k + c] - got[k + c]) for c in range(3)) > a.tolerance) if (w, h) == (300, 216) else -1
                best = diff if best is None else min(best, diff)
                ok_any |= diff == 0
            if os.environ.get("CDG_COMPARE_VERBOSE") and not ok_any and (w, h) == (300, 216):
                from collections import Counter
                c = Counter((want[k:k + 3].hex(), got[k:k + 3].hex(), (k // 4) // 300 // 12) for k in range(0, 300 * 216 * 4, 4) if want[k + 3] == 255 and want[k:k + 3] != got[k:k + 3])
                print("  top differences (rust rgb, bridge rgb, tile row):", c.most_common(6))
            print(f"t={t}s bridge frame {w}x{h}: {'identical' if ok_any else f'{best} opaque pixels differ by more than {a.tolerance}'} (bridge position {plugin.positionMs()} ms)")
            bad += not ok_any
    finally:
        plugin.shutdown(); out.close(); prev.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
