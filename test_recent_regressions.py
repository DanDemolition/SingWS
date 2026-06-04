import importlib.util
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_recent_regressions", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_app(module):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = dict(module.DEFAULTS)
    app.queue = []
    app.bg_music = None
    app.karaoke_playing = False
    app._last_sung_singer_display = ""
    app._last_sung_title = ""
    app._current_karaoke_singer_name = ""
    app._current_karaoke_singer_display = ""
    app._current_karaoke_song_path = ""
    app._current_karaoke_semitones = 0
    app._karaoke_tempo_percent = 100
    app.lookup_display_name = lambda path, artist_title_only=False: "Artist • " + str(path).split("/")[-1]
    app._is_karaoke_paused = lambda: False
    app._gst_query_times = lambda: (0, 0)
    return app


class RecentRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_defaults_keep_simple_audio_and_ticker_speed(self):
        self.assertTrue(self.singws.DEFAULTS["simple_audio_mode"])
        self.assertIn("ticker_speed_px_per_sec", self.singws.DEFAULTS)
        self.assertGreater(float(self.singws.DEFAULTS["ticker_speed_px_per_sec"]), 0)
        self.assertEqual(int(self.singws.DEFAULTS["video_timing_offset_ms"]), 0)

    def test_host_rotation_state_empty_defaults(self):
        app = make_app(self.singws)
        state = app._host_control_state()
        rotation = state["rotation"]
        self.assertEqual(rotation["last"]["singer"], "")
        self.assertEqual(rotation["current"]["singer"], "")
        self.assertEqual(rotation["next"]["singer"], "")

    def test_host_rotation_current_and_next_are_different_items(self):
        app = make_app(self.singws)
        app.karaoke_playing = True
        app._current_karaoke_singer_name = "George"
        app._current_karaoke_singer_display = "George"
        app._current_karaoke_song_path = "/tmp/current.mp3"
        app._current_karaoke_semitones = 0
        app._karaoke_tempo_percent = 100
        app.queue = [
            {
                "name": "George",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/current.mp3", "title": "Current", "artist": "Artist", "skipped": False},
                    {"song_info": "/tmp/next.mp3", "title": "Next", "artist": "Artist", "skipped": False},
                ],
            }
        ]

        rotation = app._host_control_state()["rotation"]
        self.assertEqual(rotation["current"]["singer"], "George")
        self.assertEqual(rotation["next"]["singer"], "George")
        self.assertNotEqual(rotation["current"]["item_id"], rotation["next"]["item_id"])
        self.assertEqual(rotation["next"]["title"], "Next")


if __name__ == "__main__":
    unittest.main()
