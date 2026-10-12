"""The `karaoke_audio_engine` setting: which engine plays the karaoke SOUND for a song, and what happens when the Rust one is unavailable.

Default "mpv" (nothing changes). "rust": the Rust audio engine plays the song and mpv keeps the picture and its audio clock (shadow audio);
ANY trouble (an Objective-C bridge, no Rust engine, a headless engine) means that song plays with mpv audio."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-kae-"))
ROOT = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("singws_main_kae", ROOT / "0.2.18.1.py")
APP = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = APP
_spec.loader.exec_module(APP)


class FakePlugin:
    def __init__(self, supports=True):
        self.supports = supports
        self.shadow_calls = []

    def supportsShadowAudio(self):
        return self.supports

    def setShadowAudio(self, on):
        self.shadow_calls.append(bool(on)); return True


class FakeEngine:
    def __init__(self, headless=False):
        self._headless = headless

    def _stats(self):
        return [0, 0, 0, 0, 12_500, 512, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]


def host(engine_name, engine=None):
    runtime = SimpleNamespace(soundboard_runtime=lambda: engine)
    return SimpleNamespace(settings={"karaoke_audio_engine": engine_name}, bg_music=runtime)


class KaraokeAudioEngineSettingTests(unittest.TestCase):
    def call(self, h, plugin):
        return APP.KaraokeApp._karaoke_rust_voice_for_song(h, plugin)

    def test_the_default_is_mpv(self):
        self.assertEqual(APP.DEFAULTS["karaoke_audio_engine"], "mpv")

    def test_mpv_means_no_voice_and_shadow_audio_off(self):
        p = FakePlugin()
        self.assertIsNone(self.call(host("mpv", FakeEngine()), p))
        self.assertEqual(p.shadow_calls, [False])

    def test_rust_gives_a_voice_and_turns_shadow_audio_on(self):
        p = FakePlugin()
        h = host("rust", FakeEngine())
        v = self.call(h, p)
        self.assertIsNotNone(v)
        self.assertEqual(p.shadow_calls, [True])
        self.assertIs(self.call(h, p), v, "the same voice is reused for the same engine")

    def test_rust_without_the_rust_bridge_falls_back_to_mpv_audio(self):
        p = FakePlugin(supports=False)
        self.assertIsNone(self.call(host("rust", FakeEngine()), p))
        self.assertEqual(p.shadow_calls, [])

    def test_rust_without_a_running_rust_engine_falls_back(self):
        p = FakePlugin()
        self.assertIsNone(self.call(host("rust", None), p))
        self.assertEqual(p.shadow_calls, [False], "shadow audio must be OFF so mpv plays the sound")

    def test_a_headless_engine_is_never_used(self):
        p = FakePlugin()
        self.assertIsNone(self.call(host("rust", FakeEngine(headless=True)), p))
        self.assertEqual(p.shadow_calls, [False])

    def test_an_unknown_value_means_mpv(self):
        p = FakePlugin()
        self.assertIsNone(self.call(host("something", FakeEngine()), p))

    def test_a_replaced_engine_gets_a_new_voice(self):
        p = FakePlugin()
        h = host("rust", FakeEngine())
        v1 = self.call(h, p)
        h.bg_music = SimpleNamespace(soundboard_runtime=lambda: FakeEngine())
        v2 = self.call(h, p)
        self.assertIsNot(v1, v2)


if __name__ == "__main__":
    unittest.main()
