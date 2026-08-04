#!/usr/bin/env python3
"""Short Intel/macOS source smoke test for SingWS's mpv playback plugin."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import PyQt6
from PyQt6.QtCore import QCoreApplication, QPluginLoader, QTimer
from PyQt6.QtWidgets import QApplication, QWidget

from mpv_playback import MpvPlaybackPlugin


def main() -> int:
    media = PROJECT_ROOT / "test_media" / "singws_mp4_perf_test_1080p.mp4"
    if not media.is_file():
        print(f"SMOKE FAIL: missing fixture {media}")
        return 2
    qt_plugins = Path(PyQt6.__file__).resolve().parent / "Qt6" / "plugins"
    QCoreApplication.setLibraryPaths([str(qt_plugins)])
    cocoa_loader = QPluginLoader(str(qt_plugins / "platforms" / "libqcocoa.dylib"))
    if not cocoa_loader.load():
        print(f"SMOKE FAIL: Cocoa plugin load failed: {cocoa_loader.errorString()}")
        return 3
    app = QApplication(sys.argv[:1])
    output = QWidget()
    preview = QWidget()
    output.resize(640, 360)
    preview.resize(480, 270)
    output.show()
    preview.show()
    plugin = MpvPlaybackPlugin(log=print, preview_fast_profile=True)
    state = {"ok": False}

    def fail(message):
        print(f"SMOKE FAIL: {message}")
        plugin.shutdown()
        app.exit(1)

    if not plugin.attach(preview, output):
        fail(plugin.errorString() or "attach failed")
        return app.exec()
    if not plugin.loadSingWSMedia(str(media), autoplay=True):
        fail(plugin.errorString() or "load failed")
        return app.exec()

    QTimer.singleShot(1200, lambda: plugin.setTempoRatio(1.10))
    QTimer.singleShot(1800, lambda: plugin.setPitchSemitones(2))
    QTimer.singleShot(2400, lambda: plugin.seekMedia(1000))
    QTimer.singleShot(3000, plugin.pauseMedia)
    QTimer.singleShot(3300, plugin.playMedia)

    def verify():
        position = plugin.positionMs()
        duration = plugin.durationMs()
        ready = plugin.visualsReady()
        print(f"SMOKE VERIFY position_ms={position} duration_ms={duration} visuals_ready={int(ready)}")
        if position <= 0 or duration <= 0 or not ready:
            fail("clock/duration/visual readiness did not become valid")
            return
        state["ok"] = True
        plugin.stopMedia()
        plugin.shutdown()
        app.quit()

    QTimer.singleShot(5200, verify)
    QTimer.singleShot(9000, lambda: fail("timeout"))
    result = app.exec()
    if result == 0 and state["ok"]:
        print("SMOKE PASS")
        return 0
    return result or 1


if __name__ == "__main__":
    raise SystemExit(main())
