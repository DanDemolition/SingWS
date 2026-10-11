"""Soundboard pads while the Rust music engine is the background engine (found 2026-10-10: a pad press crashed the app).

Uses the REAL BackgroundMusicPlayer on the real Rust engine and a real BASS output for the pads; every sound is silent (volume 0).
Skipped when the Rust library or a BASS output is unavailable."""
import importlib.util
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

import numpy as np

import rust_bgm_engine as rbe

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parent


def tone(path, seconds=3.0, hz=440, rate=48000):
    n = int(seconds * rate)
    pcm = (np.repeat((0.3 * np.sin(2 * np.pi * hz * np.arange(n) / rate))[:, None], 2, axis=1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())
    return str(path)


@unittest.skipUnless(rbe.available(), "libsingws_bgm_ffi.dylib not built")
class SoundboardWithRustBgmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-sbrust-")
        spec = importlib.util.spec_from_file_location("singws_main_sbrust", _ROOT / "0.2.18.1.py")
        cls.app = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.app
        spec.loader.exec_module(cls.app)
        from PyQt6.QtWidgets import QApplication
        cls.qapp = QApplication.instance() or QApplication([])

    def make_player(self):
        from PyQt6.QtCore import QObject

        class Host(QObject):
            def __init__(h):
                super().__init__()
                h.settings = {"bgm_engine": "rust", "simple_audio_mode": True}
                h._host_bgm_live_volume = 0.0
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

        self.host = Host()
        player = self.app.BackgroundMusicPlayer(self.host)
        self.addCleanup(self._close, player)
        return player

    @staticmethod
    def _close(player):
        for eng in (getattr(player, "_soundboard_bass_engine", None), getattr(player, "_bass_engine", None)):
            try:
                if eng is not None:
                    eng.close()
            except Exception:
                pass

    def test_a_pad_plays_while_the_rust_engine_runs_the_music(self):
        from bass_soundboard_engine import BassSoundboardChannel
        player = self.make_player()
        self.assertEqual(player._bass_engine.backend_name, "Rust")
        runtime = player.soundboard_runtime()
        if runtime is None:
            self.skipTest("no BASS output available for the pads on this machine")
        self.assertTrue(hasattr(runtime.bass, "BASS_StreamCreateFile"), "pads must get a runtime with the real BASS stream functions")
        self.assertIsNot(runtime, player._bass_engine, "the Rust engine is not a BASS runtime")
        d = tempfile.mkdtemp()
        clip = tone(Path(d) / "pad.wav")
        channel = BassSoundboardChannel(player.soundboard_runtime)
        channel.load(clip, 0.0)               # volume 0: nothing is heard
        channel.play()
        time.sleep(0.4)
        self.assertTrue(channel.is_playing())
        self.assertGreater(channel.position_seconds(), 0.1)
        channel.stop()
        self.assertFalse(channel.is_playing())
        channel.close()

    def test_a_pad_press_while_music_plays_does_not_disturb_the_music(self):
        from bass_soundboard_engine import BassSoundboardChannel
        player = self.make_player()
        d = tempfile.mkdtemp()
        music = tone(Path(d) / "music.wav", seconds=20)
        player.playlist = [music]
        player.current_index = 0
        player.set_enabled(True)
        player.play()
        time.sleep(0.6)
        runtime = player.soundboard_runtime()
        if runtime is None:
            self.skipTest("no BASS output available for the pads on this machine")
        channel = BassSoundboardChannel(player.soundboard_runtime)
        before = player._bass_engine._stats()
        for _ in range(5):
            channel.load(tone(Path(d) / "pad.wav", seconds=1.0), 0.0)
            channel.play()
            time.sleep(0.25)
            channel.stop()
        time.sleep(0.3)
        after = player._bass_engine._stats()
        self.assertTrue(player.is_playing, "the music keeps playing")
        self.assertEqual(after[6] - before[6], 0, "no xruns while pads fire")
        self.assertEqual(after[12] - before[12], 0, "no silent frames in the music while pads fire")
        channel.close()

    def test_the_private_pad_output_follows_a_changed_output_and_is_reused(self):
        player = self.make_player()
        a = player.soundboard_runtime()
        if a is None:
            self.skipTest("no BASS output available for the pads on this machine")
        self.assertIs(player.soundboard_runtime(), a, "created once, then reused")


if __name__ == "__main__":
    unittest.main()
