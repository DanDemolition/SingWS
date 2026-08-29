import importlib.util
import os
from types import SimpleNamespace
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
        self.primary_norm = None

    def set_master_volume(self, value):
        self.master_volume = float(value)
        self.calls.append(float(value))

    def set_primary_normalize_gain(self, value):
        self.primary_norm = float(value)


class FakeBgMusic:
    def __init__(self):
        self.playlist = ["/tmp/bg.mp3"]
        self.is_playing = False
        self.stopped = False
        self.fade_in_calls = []

    def stop(self):
        self.stopped = True

    def get_current_track_info(self):
        return "bg"

    def fade_in(self, *args, **kwargs):
        self.fade_in_calls.append((args, kwargs))
        self.is_playing = True


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
        player._bg_norm_gen = {}
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

    def test_unknown_normalized_track_uses_unity_playback_while_analysis_runs(self):
        player = self.make_player({
            "bg_volume": 0.8,
            "simple_audio_mode": False,
            "bg_normalize_enabled": True,
        })
        self.singws.analyze_loudness_async = lambda path: None

        factor, info = player._bg_norm_factor_for_path("/tmp/not-yet-analyzed.mp3")

        self.assertIsNone(info)
        self.assertAlmostEqual(factor, self.singws.BGM_UNKNOWN_ANALYSIS_PREGAIN)
        self.assertAlmostEqual(factor, 1.0)

    def test_bg_normalization_off_does_not_read_cache_or_start_analysis(self):
        player = self.make_player({
            "bg_volume": 0.8,
            "simple_audio_mode": False,
            "bg_normalize_enabled": False,
        })
        calls = []
        orig_info = self.singws.loudness_info_cached
        orig_analyze = self.singws.analyze_loudness_async
        try:
            self.singws.loudness_info_cached = lambda path: calls.append(("cache", path))
            self.singws.analyze_loudness_async = lambda path: calls.append(("analyze", path))

            factor, info = player._bg_norm_factor_for_path("/tmp/disabled.mp3")
            player._refresh_bg_normalize("/tmp/disabled.mp3", deck="primary")

            self.assertEqual(calls, [])
            self.assertIsNone(info)
            self.assertAlmostEqual(factor, 1.0)
            self.assertAlmostEqual(player._bass_engine.primary_norm, 1.0)
        finally:
            self.singws.loudness_info_cached = orig_info
            self.singws.analyze_loudness_async = orig_analyze

    def test_library_analysis_items_skip_everything_when_normalization_disabled(self):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = {
            "simple_audio_mode": True,
            "karaoke_normalize_enabled": False,
            "bg_normalize_enabled": False,
        }
        app.tracks = [{"path": "/tmp/song.mp3", "display": "Song"}]
        app._bgm_analysis_items = lambda: [("BGM", "/tmp/bg.mp3", "BG")]
        calls = []
        orig_load = self.singws._loudness_load_cache
        try:
            self.singws._loudness_load_cache = lambda: calls.append("cache_load")
            self.assertEqual(app._library_loudness_analysis_items(), [])
            self.assertEqual(calls, [])
        finally:
            self.singws._loudness_load_cache = orig_load

    def test_bg_handoff_reports_false_when_session_has_not_started(self):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = {"bg_enabled": True, "bg_autoplay_on_idle": True}
        app.bg_music = FakeBgMusic()
        app._bgm_session_started = False
        app._bg_resume_reason = "karaoke_end_overlap"
        app._clear_bg_transition_timers = lambda: None

        self.assertFalse(app._start_bg_with_fade())
        self.assertFalse(app.bg_music.is_playing)

    def test_normal_karaoke_end_uses_fast_post_eos_fade(self):
        app = SimpleNamespace(
            settings={
                "bg_enabled": True,
                "bg_autoplay_on_idle": True,
                "bg_volume": 0.8,
            },
            bg_music=FakeBgMusic(),
            _bgm_session_started=True,
            _bg_resume_reason="karaoke_end",
            _clear_bg_transition_timers=lambda: None,
            update_bg_button_state=lambda: None,
        )

        self.assertTrue(self.singws.KaraokeApp._start_bg_with_fade(app))
        self.assertEqual(app.bg_music.fade_in_calls[-1][0][1], 1000)


if __name__ == "__main__":
    unittest.main()
