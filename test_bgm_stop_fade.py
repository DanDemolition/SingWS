"""The user's Stop button fades the background music out and then stops; Pause stays instant (2026-10-10).

Drives the real BackgroundMusicPlayer from 0.2.18.1.py on the real Rust engine, headless (no sound card): audio is rendered by a pump
thread in real time and measured."""
import importlib.util
import os
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

import rust_bgm_engine as rbe

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parent
RATE = 48000
try:  # test modules are all imported before any test runs, so this is the genuine timer even when an earlier test leaks a recorder
    from PyQt6.QtCore import QTimer as _QTimer
    _IMPORT_TIME_SINGLE_SHOT = _QTimer.singleShot
except Exception:
    _IMPORT_TIME_SINGLE_SHOT = None


def tone(directory, name, hz=440, amp=0.4, seconds=30.0):
    n = int(seconds * RATE)
    mono = amp * np.sin(2 * np.pi * hz * np.arange(n) / RATE)
    pcm = (np.repeat(mono[:, None], 2, axis=1) * 32767).astype("<i2")
    path = Path(directory) / name
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm.tobytes())
    return str(path)


@unittest.skipUnless(rbe.available(), "libsingws_bgm_ffi.dylib not built")
class StopFadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-stopfade-")
        spec = importlib.util.spec_from_file_location("singws_main_stopfade", _ROOT / "0.2.18.1.py")
        cls.app = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.app
        spec.loader.exec_module(cls.app)
        from PyQt6.QtWidgets import QApplication
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        from PyQt6.QtCore import QObject, QTimer
        app = self.app
        # Older tests in this suite leave QTimer.singleShot replaced by a recorder; this test needs real timers, for its own duration only.
        # Prefer the timer captured when THIS file was imported (all test files are imported before any test runs). conftest.py records the same thing, but under plain
        # unittest its first import can happen here, after test_bgm_master has already replaced the timer, and it would then record the replacement.
        real_single_shot = _IMPORT_TIME_SINGLE_SHOT
        if real_single_shot is None:
            try:
                from conftest import REAL_SINGLE_SHOT as real_single_shot
            except Exception:
                real_single_shot = QTimer.singleShot
        leaked = QTimer.singleShot
        QTimer.singleShot = staticmethod(real_single_shot)
        self.addCleanup(setattr, QTimer, "singleShot", staticmethod(leaked) if leaked is not real_single_shot else staticmethod(real_single_shot))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        class Host(QObject):
            def __init__(h):
                super().__init__()
                h.settings = {"bgm_engine": "rust", "simple_audio_mode": True}
                h._host_bgm_live_volume = 0.8
                h.karaoke_playing = False
                h.bgm_eq = None

            def _bgm_master_active(h): return False
            def _ensure_bgm_master_processor(h): return None
            def _ensure_eq_engines(h): pass
            def _master_chain_label(h, *a, **k): return "test"
            def _run_on_ui_thread(h, fn, *a, **k): return fn(*a, **k)
            def _safe_local_audio_output_name(h, *a, **k): return ""
            def update_bg_button_state(h, *a, **k): pass
            def update_bg_track_display(h, *a, **k): pass

        class Headless(rbe.RustBackgroundEngine):
            def __init__(e, output_name=None, sample_rate=48000):
                super().__init__(output_name, sample_rate, headless=True)

        patcher = mock.patch.object(rbe, "RustBackgroundEngine", Headless)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.host = Host()
        self.player = app.BackgroundMusicPlayer(self.host)
        self.engine = self.player._bgm_engine
        self.assertEqual(self.engine.backend_name, "Rust")
        self.player.playlist = [tone(self.tmp.name, "a.wav"), tone(self.tmp.name, "b.wav", hz=880)]
        self.player.current_index = 0
        self.player.set_enabled(True)
        self.levels = []
        self.stop_pump = threading.Event()

        def pump():
            t = time.perf_counter()
            while not self.stop_pump.is_set():
                t += 0.010
                block = self.engine.render(480)
                self.levels.append((time.perf_counter(), float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))))
                d = t - time.perf_counter()
                if d > 0:
                    time.sleep(d)
                else:
                    t = time.perf_counter()
        self.thread = threading.Thread(target=pump, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.stop_pump.set()
        self.thread.join(timeout=2)
        try:
            self.player.stop()
            self.engine.close()
        except Exception:
            pass

    def spin(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.qapp.processEvents()
            time.sleep(0.01)

    def level_between(self, t0, t1):
        vals = [v for t, v in self.levels if t0 <= t <= t1]
        return float(np.mean(vals)) if vals else 0.0

    def start_playing(self):
        self.player.play()
        self.spin(1.0)
        self.assertTrue(self.player.is_playing)
        self.assertGreater(self.level_between(time.perf_counter() - 0.5, time.perf_counter()), 0.05)

    def test_stop_fades_out_then_stops_and_the_next_play_is_audible(self):
        self.start_playing()
        full = self.level_between(time.perf_counter() - 0.4, time.perf_counter())
        t_press = time.perf_counter()
        self.player.stop_with_fade(600)
        self.spin(0.25)
        self.assertTrue(self.player.is_playing, "still fading, not stopped yet")
        mid = self.level_between(t_press + 0.15, t_press + 0.3)
        self.assertLess(mid, full * 0.85, "level should already be falling")
        self.assertGreater(mid, full * 0.05, "but not cut: a fade, not a hard stop")
        deadline = time.time() + 5.0        # generous: on a loaded machine (full suite, other apps) the Qt timer fires late
        while self.player.is_playing and time.time() < deadline:
            self.spin(0.05)
        self.assertFalse(self.player.is_playing, "stopped after the fade")
        self.assertIsNone(self.engine.primary)
        self.assertLess(self.level_between(time.perf_counter() - 0.2, time.perf_counter()), 0.001)
        self.assertAlmostEqual(self.engine.master_volume, 0.8, delta=0.01, msg="volume restored for the next Play")
        self.player.play()
        self.spin(1.2)
        self.assertGreater(self.level_between(time.perf_counter() - 0.4, time.perf_counter()), full * 0.8, "next Play is at normal volume")

    def test_pause_is_still_instant(self):
        self.start_playing()
        t_press = time.perf_counter()
        self.player.pause()
        self.spin(0.1)
        self.assertLess(self.level_between(t_press + 0.03, t_press + 0.1), 0.001)

    def test_play_pressed_during_the_fade_takes_over(self):
        self.start_playing()
        self.player.stop_with_fade(600)
        self.spin(0.2)
        self.player.pause()          # the user changes their mind: pause wins, the pending stop must not fire
        self.spin(1.5)
        self.assertFalse(self.player.is_playing)
        self.assertIsNotNone(self.engine.primary, "paused, not stopped: the position is kept")

    def test_stop_when_nothing_is_playing_just_stops(self):
        self.player.stop_with_fade(600)       # must not raise or wait
        self.assertFalse(self.player.is_playing)

    def test_both_stop_buttons_use_the_fade(self):
        src = (_ROOT / "0.2.18.1.py").read_text()
        i = src.index("    def _bg_main_stop(self):")
        self.assertIn("self.bg_music.stop_with_fade()", src[i:i + 200])
        j = src.index("    def stop_playback(self):", src.index("class BackgroundMusicManager"))
        self.assertIn("self.bg_music.stop_with_fade()", src[j:j + 300])


if __name__ == "__main__":
    unittest.main()
