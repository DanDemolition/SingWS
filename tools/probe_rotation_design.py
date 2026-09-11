"""Scratch-profile visual rehearsal of the approved TV rotation design.

Run with QT_QPA_PLATFORM=cocoa and a scratch SINGWS_HOME. Captures the
actual macOS window number, not QWidget's NSView pointer.
"""
import argparse
import importlib.util
import io
import json
import statistics
import time
import os
from pathlib import Path
import subprocess
import sys
import tempfile

if not os.environ.get('SINGWS_HOME'):
    raise SystemExit('Set a scratch SINGWS_HOME before importing the app')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QEventLoop, QTimer
from PyQt6.QtGui import QPixmap
from tools.probe_cdg_render import _extract
from mpv_playback_iina import MpvPlaybackPlugin


def _wait(app, predicate, timeout):
    # Run the actual Qt event loop, rather than sleeping between processEvents
    # calls (which caps the frame cadence of the probe itself).
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(10)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll.start(); deadline.start(int(timeout * 1000))
    if not predicate():
        loop.exec()
    poll.stop(); deadline.stop()
    return bool(predicate())


def capture(widget, destination):
    import objc
    view = objc.objc_object(c_void_p=int(widget.winId()))
    number = int(view.window().windowNumber())
    subprocess.run(['/usr/sbin/screencapture', '-x', '-o', '-l', str(number), str(destination)], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    spec = importlib.util.spec_from_file_location('rotation_design_probe', Path(__file__).resolve().parents[1] / '0.2.18.1.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner = QWidget()
    owner.settings = {'rotation_vfx_enabled': True,
                      'rotation_announcement_enabled': True,
                      'rotation_announcement_message': '2-FOR-1 DRINKS  •  KITCHEN OPEN LATE'}
    owner.karaoke_playing = True
    owner._current_karaoke_mode = 'cdg' if args.archive.suffix.lower() == '.zip' else 'mp4'
    output, preview = QWidget(), QWidget()
    output.resize(640, 360)
    output.show()
    plugin = MpvPlaybackPlugin(log=print)
    owner._mpv_playback = plugin
    assert plugin.attach(preview, output), plugin.errorString()
    try:
        with tempfile.TemporaryDirectory() as scratch:
            cdg, mp3 = _extract(args.archive, Path(scratch)) if args.archive.suffix.lower() == ".zip" else (args.archive, None)
            assert plugin.loadSingWSMedia(cdg, mp3, autoplay=False)
            plugin.setVolume(0)
            plugin.playMedia()
            assert _wait(app, plugin.visualsReady, 10)
            assert _wait(app, lambda: plugin.positionMs() > 1000, 15), "audio never started"
            plugin.seekMedia(90000)
            assert _wait(app, lambda: plugin.positionMs() >= 90000, 10), "seek never reached audio clock"
            view = module.RotationView(owner)
            view.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
            rows = [{'name': name, 'songs': [{'song_info': str(args.archive), 'title': 'Set Adrift on Memory Bliss', 'artist': 'PM Dawn'}]} for name in ('Sam', 'Jordan', 'Taylor', 'Chris', 'Morgan')]
            view.update_rotation(rows, [], 'Alex')
            import qrcode
            buffer = io.BytesIO()
            qrcode.make('https://example.com/rotation-design-preview').save(buffer, format='PNG')
            qr = QPixmap(); qr.loadFromData(buffer.getvalue()); view.set_request_qr(qr)
            view.set_now_playing_strip('Alex — Set Adrift on Memory Bliss', 'Sam')
            view.setGeometry(40, 40, 1280, 720)
            view.show(); view.raise_(); view.activateWindow()
            _wait(app, lambda: False, 2)
            capture(view, args.output / 'rotation-720-live.png')
            capture(output, args.output / 'main-video.png')
            view._next_up_spotlight_started = module.time.monotonic() - 31
            view._tick_animated_backdrop()
            _wait(app, lambda: False, 0.25)
            capture(view, args.output / 'rotation-spotlight-rise.png')
            _wait(app, lambda: False, 0.85)
            capture(view, args.output / 'rotation-spotlight-hold.png')
            _wait(app, lambda: False, 1)
            capture(view, args.output / 'rotation-spotlight-settled.png')
            more = rows + [{'name': name, 'songs': rows[0]['songs']} for name in ('Alexandra & Christopher', 'Jamie', 'Pat', 'Casey', 'Riley')]
            view.update_rotation(more, [], 'Alex')
            frames = []
            if view.rotation_rail is not None:
                view.rotation_rail._view.frameSwapped.connect(lambda: frames.append(time.monotonic()))
            _wait(app, lambda: False, 3)
            capture(view, args.output / 'rotation-overflow-a.png')
            _wait(app, lambda: False, 2)
            capture(view, args.output / 'rotation-overflow-b.png')
            intervals = [1000*(b-a) for a,b in zip(frames, frames[1:])]
            if intervals:
                timing = {'frames': len(frames), 'median_ms': statistics.median(intervals),
                          'p95_ms': sorted(intervals)[int(len(intervals)*.95)]}
                (args.output / 'scroll-timing.json').write_text(json.dumps(timing, indent=2))
                print('QUEUE_RENDER_TIMING', timing)
            view.resize(1920, 1080)
            _wait(app, lambda: False, 1)
            capture(view, args.output / 'rotation-1080.png')
            view.showMaximized()
            _wait(app, lambda: False, 1.2)
            capture(view, args.output / 'rotation-maximized.png')
            view.showNormal()
            _wait(app, lambda: False, .8)
            view.hide(); _wait(app, lambda: False, .3)
            view.show(); _wait(app, lambda: False, .8)
            capture(view, args.output / 'rotation-reopened.png')
            view.hide()
    finally:
        plugin.shutdown()
        output.close(); preview.close()


if __name__ == '__main__':
    main()
