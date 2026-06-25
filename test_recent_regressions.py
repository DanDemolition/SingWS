import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock


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

    def test_settings_save_scheduler_debounces_ui_thread_writes(self):
        app = make_app(self.singws)
        calls = []
        app.save_settings = lambda: calls.append("save")

        class FakeTimer:
            def __init__(self):
                self.started = []

            def start(self, delay):
                self.started.append(delay)

        class FakeApp:
            def thread(self):
                return "ui-thread"

        fake_timer = FakeTimer()
        app._save_settings_timer = fake_timer

        with mock.patch.object(self.singws.QApplication, "instance", return_value=FakeApp()), \
             mock.patch.object(self.singws.QThread, "currentThread", return_value="ui-thread"):
            app._schedule_save_settings(700)
            app._schedule_save_settings(250)

        self.assertEqual(calls, [])
        self.assertEqual(fake_timer.started, [700, 250])

    def test_library_volume_analysis_collects_karaoke_and_bgm(self):
        app = make_app(self.singws)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cdg = root / "karaoke.cdg"
            mp3 = root / "karaoke.mp3"
            bgm = root / "bgm.mp3"
            cdg.write_text("", encoding="utf-8")
            mp3.write_text("audio", encoding="utf-8")
            bgm.write_text("audio", encoding="utf-8")

            app.tracks = [{"path": str(cdg), "display": "Karaoke Song"}]
            app.bg_music = SimpleNamespace(playlist=[str(bgm)])
            app.bg_manager = SimpleNamespace(current_playlist=[{"path": str(bgm)}])
            app.settings = {"bg_import_folders": []}

            with mock.patch.object(self.singws.Path, "home", return_value=root):
                with mock.patch.object(self.singws, "loudness_gain_db_cached", return_value=None):
                    items = app._library_loudness_analysis_items(force=False)

                paths = [item[1] for item in items]
                self.assertEqual(paths.count(str(mp3)), 1)
                self.assertEqual(paths.count(str(bgm)), 1)
                self.assertTrue(any(item[0] == "Karaoke" for item in items))
                self.assertTrue(any(item[0] == "BGM" for item in items))

                with mock.patch.object(
                    self.singws,
                    "loudness_gain_db_cached",
                    side_effect=lambda path: 0.0 if path == str(mp3) else None,
                ):
                    incremental = app._library_loudness_analysis_items(force=False)
                    forced = app._library_loudness_analysis_items(force=True)

            self.assertNotIn(str(mp3), [item[1] for item in incremental])
            self.assertIn(str(mp3), [item[1] for item in forced])

    def test_loudness_measurement_uses_single_ebur128_peak_pass(self):
        calls = []
        stderr = b"""
            [Parsed_ebur128_0] Summary:
              Integrated loudness:
                I:         -20.5 LUFS
              True peak:
                Peak:       -1.2 dBFS
        """

        class FakeProc:
            pid = 12345

            def communicate(self, timeout=None):
                return b"", stderr

        def fake_popen(cmd, **kwargs):
            calls.append(cmd)
            return FakeProc()

        with mock.patch.object(self.singws.subprocess, "Popen", side_effect=fake_popen):
            lufs, peak_db = self.singws._measure_loudness_lufs("/tmp/song.mp3")

        self.assertEqual(lufs, -20.5)
        self.assertEqual(peak_db, -1.2)
        self.assertEqual(len(calls), 1)
        self.assertIn("ebur128=peak=true", calls[0])
        self.assertFalse(any("volumedetect" in " ".join(cmd) for cmd in calls))

    def test_library_volume_worker_cancel_stops_before_next_track(self):
        worker = self.singws.AnalyzeLibraryWorker([
            ("Karaoke", "/tmp/one.mp3", "One"),
            ("Karaoke", "/tmp/two.mp3", "Two"),
        ])
        measured = []

        def fake_measure(path, cancel_check=None):
            measured.append(path)
            worker.cancel()
            self.assertTrue(cancel_check())
            return -20.0, -2.0

        with mock.patch.object(self.singws, "_measure_loudness_lufs", side_effect=fake_measure), \
             mock.patch.object(self.singws, "_loudness_file_sig", return_value=(1, 2)), \
             mock.patch.object(self.singws, "_loudness_save_cache"):
            worker.run()

        self.assertEqual(measured, ["/tmp/one.mp3"])


if __name__ == "__main__":
    unittest.main()
