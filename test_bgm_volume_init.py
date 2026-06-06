import importlib.util
import os
import unittest


def load_main_module():
    os.environ["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
    spec = importlib.util.spec_from_file_location("singws_main_bgm_volume", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeHost:
    def __init__(self, settings):
        self.settings = dict(settings)
        self.bg_manager = None


class FakeBassEngine:
    def __init__(self):
        self.master_volume = 0.0
        self.calls = []

    def set_master_volume(self, value):
        self.master_volume = float(value)
        self.calls.append(float(value))


class BgmVolumeInitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def make_player(self, settings):
        player = self.singws.BackgroundMusicPlayer.__new__(self.singws.BackgroundMusicPlayer)
        player.volume = 0.8
        player.is_playing = False
        player._bass_engine = FakeBassEngine()
        player._bg_last_volume_source = ""
        player.parent = lambda: FakeHost(settings)
        return player

    def test_startup_volume_sync_applies_saved_volume_to_bass_master(self):
        player = self.make_player({
            "bg_volume": 0.37,
            "simple_audio_mode": True,
            "bg_normalize_enabled": False,
        })

        player.initialize_startup_volume()

        self.assertAlmostEqual(player.volume, 0.37)
        self.assertAlmostEqual(player._bass_engine.master_volume, 0.37)
        self.assertIn(0.37, player._bass_engine.calls)

    def test_unknown_normalized_track_uses_safe_pregain_only_when_enabled(self):
        player = self.make_player({
            "bg_volume": 0.8,
            "simple_audio_mode": False,
            "bg_normalize_enabled": True,
        })
        self.singws.analyze_loudness_async = lambda path: None

        factor, info = player._bg_norm_factor_for_path("/tmp/not-yet-analyzed.mp3")

        self.assertIsNone(info)
        self.assertAlmostEqual(factor, self.singws.BGM_UNKNOWN_ANALYSIS_PREGAIN)


if __name__ == "__main__":
    unittest.main()
