"""The real SoundboardPad widget on the Rust engine (2026-10-11): click fires, a second click LAYERS, Shift-click stops every instance, the soundboard
slider scales the pads and the music volume does not. Real BackgroundMusicPlayer + real Rust engine, headless (no sound card, nothing audible)."""
import importlib.util
import math
import os
import sys
import tempfile
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


def tone(path, hz=1000, amp=0.1, seconds=3.0):
    n = int(seconds * RATE)
    pcm = (np.repeat((amp * np.sin(2 * np.pi * hz * np.arange(n) / RATE))[:, None], 2, axis=1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm.tobytes())
    return str(path)


def rms(x):
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


@unittest.skipUnless(rbe.available(), "libsingws_bgm_ffi.dylib not built")
class PadWidgetOnRustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-padwidget-")
        spec = importlib.util.spec_from_file_location("singws_main_padwidget", _ROOT / "0.2.18.1.py")
        cls.app = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.app
        spec.loader.exec_module(cls.app)
        from PyQt6.QtWidgets import QApplication
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        from PyQt6.QtCore import QObject
        from PyQt6.QtWidgets import QWidget
        app = self.app
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        class Host(QObject):
            def __init__(h):
                super().__init__()
                h.settings = {"bgm_engine": "rust", "simple_audio_mode": True, "soundboard_volume": 1.0}
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

        player = self.player

        class Owner(QWidget):
            """What SoundboardPad._app_owner() looks for."""
            bg_music = player

            def _get_selected_audio_output_id(o):
                return "default"

        self.owner = Owner()
        self.addCleanup(self.owner.close)
        self.pad = app.SoundboardPad(0, parent=self.owner)
        self.addCleanup(lambda: self.pad._channel.close())
        self.clip = tone(Path(self.tmp.name) / "pad.wav")
        self.pad._set_clip(self.clip, "Pad", 1.0)
        self.stopped = []
        self.pad.stopped.connect(self.stopped.append)

    def level(self, frames=2400):
        return rms(self.engine.render(frames)[frames // 4:])

    def click(self, shift=False):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        QTest.mouseClick(self.pad, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier)
        self.qapp.processEvents()

    def test_the_pad_uses_the_rust_engine_not_bass(self):
        self.assertIs(self.player.soundboard_runtime(), self.engine)
        self.assertTrue(self.pad._channel.supports_layering)

    def test_click_plays_and_a_second_click_layers_instead_of_stopping(self):
        one = 0.1 / math.sqrt(2)
        self.click()
        self.assertTrue(self.pad._playing)
        self.assertAlmostEqual(self.level(), one, delta=0.01)
        self.click()                                  # still playing: this must NOT stop it
        self.assertTrue(self.pad._playing)
        self.assertAlmostEqual(self.level(), 2 * one, delta=0.02, msg="two layered instances")
        self.click()
        self.assertAlmostEqual(self.level(), 3 * one, delta=0.03)
        self.assertEqual(self.stopped, [], "a plain click never stops the pad")

    def test_shift_click_stops_every_instance(self):
        self.click()
        self.click()
        self.click()
        self.engine.render(2400)
        self.click(shift=True)
        self.engine.render(1200)
        time.sleep(0.12)
        self.qapp.processEvents()
        self.assertEqual(float(np.abs(self.engine.render(2400)).max()), 0.0, "all instances silenced")
        self.assertFalse(self.pad._playing)
        self.assertEqual(self.stopped, [0], "the pad reports it stopped once")

    def test_shift_click_on_a_silent_pad_does_not_fire_it(self):
        self.click(shift=True)
        self.assertEqual(float(np.abs(self.engine.render(2400)).max()), 0.0)
        self.assertFalse(self.pad._playing)

    def test_the_soundboard_slider_scales_the_pads_and_the_music_volume_does_not(self):
        host = self.host
        host.soundboard_strip = mock.Mock()
        host.bg_music = self.player
        host._schedule_save_settings = mock.Mock()
        self.click()
        full = self.level()
        self.app.KaraokeApp._apply_soundboard_volume(host, 0.5)
        self.engine.render(480)
        self.assertAlmostEqual(self.level() / full, 0.5, delta=0.03)
        self.assertEqual(host.settings["soundboard_volume"], 0.5)
        host._schedule_save_settings.assert_called_once()
        host.soundboard_strip.refresh_volumes.assert_called_once()
        self.player.set_volume(0.0)                   # the MUSIC slider to zero
        self.engine.render(4800)
        self.assertAlmostEqual(self.level() / full, 0.5, delta=0.03, msg="music volume is separate")

    def test_a_clip_that_cannot_load_is_flagged_on_the_pad_and_recovers(self):
        self.pad._set_clip("/nonexistent/missing.wav", "Gone", 1.0)
        self.assertIn("\u26a0", self.pad.text(), "the pad shows a warning mark")
        self.assertIn("could not be loaded", self.pad.toolTip())
        self.click()                                   # clicking it must not crash
        self.assertFalse(self.pad._playing)
        self.pad._set_clip(self.clip, "Pad", 1.0)       # a good clip clears the warning
        self.assertNotIn("\u26a0", self.pad.text())
        self.assertEqual(self.pad._load_error, "")
        self.click()
        self.assertTrue(self.pad._playing)

    def test_the_slider_value_survives_an_engine_rebuild(self):
        self.player.set_soundboard_volume(0.4)
        self.host.settings["soundboard_volume"] = 0.4
        self.player._init_bgm_engine()               # what an output-device change does
        self.assertAlmostEqual(self.player._bgm_engine.soundboard_volume, 0.4, places=3)

    def test_the_bgm_section_has_the_soundboard_slider_wired_to_the_host(self):
        src = (_ROOT / "0.2.18.1.py").read_text()
        self.assertIn('self.soundboard_slider.valueChanged.connect(self.on_soundboard_volume_changed)', src)
        manager = mock.Mock()
        manager._host.return_value = self.host
        self.host._apply_soundboard_volume = mock.Mock()
        self.app.BackgroundMusicManager.on_soundboard_volume_changed(manager, 37)
        self.host._apply_soundboard_volume.assert_called_once_with(0.37)


if __name__ == "__main__":
    unittest.main()
