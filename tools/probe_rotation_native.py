"""Muted, scratch-data comparison of the show and rotation native composites."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mpv_playback_iina import MpvPlaybackPlugin
from tools.probe_cdg_render import _extract, _wait, _frame_stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--next-archive", type=Path)
    parser.add_argument("--background", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hold-seconds", type=float, default=0.0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    output, rotation, preview = QWidget(), QWidget(), QWidget()
    output.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
    rotation.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
    output.setWindowTitle("SingWS native show probe")
    rotation.setWindowTitle("SingWS native rotation probe")
    output.setGeometry(30, 60, 600, 338)
    rotation.setGeometry(650, 60, 600, 338)
    output.show()
    rotation.show()
    app.processEvents()
    plugin = MpvPlaybackPlugin(log=print)
    assert plugin.attach(preview, output), plugin.errorString()
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="singws-rotation-media-") as folder:
            cdg, mp3 = _extract(args.archive, Path(folder))
            assert plugin.loadSingWSMedia(cdg, mp3, autoplay=False), plugin.errorString()
            plugin.setVolume(0)
            plugin.playMedia()
            assert _wait(app, plugin.visualsReady, 10), "no native CDG frame"
            assert plugin.setRotationVideoHost(rotation), "rotation API unavailable"
            plugin.seekMedia(60000)
            _wait(app, lambda: plugin.positionMs() >= 60000, 10)
            plugin.pauseMedia()
            _wait(app, lambda: False, 0.5)
            modes = [("off", 0), ("color", 1), ("blur", 2)]
            if args.background:
                modes.append(("video", 0))
            for name, mode in modes:
                plugin.setCdgOutputSidefill(mode)
                if name == "video":
                    assert plugin.loadBackgroundVideo(args.background, 0.75)
                    _wait(app, lambda: plugin.backgroundVideoPositionMs() > 500, 5)
                _wait(app, lambda: False, 0.25)
                images = []
                for label, window in (("show", output), ("rotation", rotation)):
                    image = app.primaryScreen().grabWindow(int(window.winId())).toImage()
                    image.save(str(args.output / f"{name}-{label}.png"))
                    images.append(image)
                # Paused CDG modes must produce exactly the same composite.
                # Video keeps running, so compare nonblack output, not samples
                # taken on different native frames.
                stats = [_frame_stats(image) for image in images]
                # macOS draws different active/inactive window edge shadows.
                # Compare the content, excluding only four edge pixels.
                cropped = [image.copy(4, 4, image.width()-8, image.height()-8) for image in images]
                equal = cropped[0] == cropped[1]
                ok = all(stat and stat["colors"] > 1 for stat in stats)
                results.append({"mode": name, "equal": equal, "stats": stats, "ok": ok})
            plugin.stopBackgroundVideo()
            plugin.setCdgOutputSidefill(2)
            _wait(app, lambda: False, 0.15)
            # Exercise the actual Qt/QQuick foreground above the native view.
            import importlib.util
            spec = importlib.util.spec_from_file_location("rotation_probe_app", Path(__file__).resolve().parents[1] / "0.2.18.1.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            owner = QWidget()
            owner.settings = {
                "rotation_native_cdg_backdrop": True,
                "rotation_vfx_enabled": True,
                "rotation_announcement_enabled": True,
                "rotation_announcement_message": "Two-for-one drinks • Kitchen open late",
            }
            owner.karaoke_playing = True
            owner._current_karaoke_mode = "cdg"
            owner._mpv_playback = plugin
            layout = module.RotationView(owner)
            layout.setGeometry(50, 50, 1100, 680)
            rows = [{"name": name, "songs": [{"song_info": str(args.archive), "title": "Set Adrift On Memory Bliss", "artist": "PM Dawn"}]} for name in ("Alex", "Sam", "Jordan")]
            layout.update_rotation(rows, [], "Alex", "3:20")
            layout.show()
            _wait(app, lambda: False, 1.0)
            app.primaryScreen().grabWindow(int(layout.winId())).save(str(args.output / "rotation-layout.png"))
            if args.next_archive:
                with tempfile.TemporaryDirectory(prefix="singws-rotation-next-") as next_folder:
                    next_cdg, next_mp3 = _extract(args.next_archive, Path(next_folder))
                    assert plugin.loadSingWSMedia(next_cdg, next_mp3, autoplay=False)
                    plugin.setVolume(0)
                    plugin.playMedia()
                    _wait(app, lambda: False, 0.35)
                    app.primaryScreen().grabWindow(int(layout.winId())).save(
                        str(args.output / "rotation-layout-next-song-loading.png")
                    )
                    assert _wait(app, plugin.visualsReady, 10), "replacement CDG produced no frame"
                    assert _wait(app, lambda: plugin.positionMs() > 750, 10), "replacement CDG did not advance"
                    app.primaryScreen().grabWindow(int(layout.winId())).save(
                        str(args.output / "rotation-layout-next-song.png")
                    )
            if args.hold_seconds > 0:
                _wait(app, lambda: False, args.hold_seconds)
            layout.hide()
            layout.show()
            _wait(app, lambda: False, 0.5)
            app.primaryScreen().grabWindow(int(layout.winId())).save(str(args.output / "rotation-layout-reopened.png"))
            layout.hide()
            owner.close()
            plugin.setRotationVideoHost(None, False)
            rotation.hide()
            _wait(app, lambda: False, 0.1)
            rotation.show()
            assert plugin.setRotationVideoHost(rotation, True)
            _wait(app, lambda: False, 0.25)
    finally:
        plugin.shutdown()
        output.close()
        rotation.close()
        preview.close()
    (args.output / "results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results))
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
