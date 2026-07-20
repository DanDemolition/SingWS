import importlib.util
import inspect
import sys
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_soundboard", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Recorder:
    def __init__(self):
        self.calls = []

    def prepare_audio_output_change(self):
        self.calls.append("prepare")

    def apply_audio_output_change(self):
        self.calls.append("apply")


class SoundboardRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_soundboard_pad_uses_bass_not_gstreamer_or_qt_audio(self):
        source = inspect.getsource(self.singws.SoundboardPad)
        self.assertNotIn("from PyQt6.QtMultimedia import QMediaPlayer", source)
        self.assertNotIn("from PyQt6.QtMultimedia import QAudioOutput", source)
        self.assertNotIn("Gst.", source)
        self.assertIn("BassSoundboardChannel", source)
        self.assertIn("backend=BASS", source)

    def test_soundboard_pad_playing_style_tracks_playback_state(self):
        source = inspect.getsource(self.singws.SoundboardPad)
        self.assertNotIn("_visual_playing", source)
        self.assertNotIn("_flash_playing_style", source)
        self.assertIn("_poll_bass_channel", source)
        self.assertIn("self._channel.is_playing()", source)
        self.assertIn("self._playing = True", source)
        self.assertIn("self._playing = False", source)

    def test_audio_output_change_rebinds_soundboard_strip(self):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = {"audio_output_id": "default"}
        app._audio_output_exists = lambda output_id: True
        app.save_settings = lambda: None
        app._update_audio_output_button = lambda: None

        bg = _Recorder()
        strip = _Recorder()
        app.bg_music = bg
        app.soundboard_strip = strip

        app._set_audio_output_id("dev_test")

        self.assertEqual(app.settings["audio_output_id"], "dev_test")
        self.assertEqual(bg.calls, ["apply"])
        self.assertEqual(strip.calls, ["prepare", "apply"])


if __name__ == "__main__":
    unittest.main()
