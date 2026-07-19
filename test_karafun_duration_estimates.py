import importlib.util
import json
import os
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "minimal")


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_karafun_duration", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class KaraFunDurationEstimateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def make_app(self):
        return self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)

    def test_duration_parser_accepts_seconds_and_karafun_clocks(self):
        parse = self.singws.normalize_karafun_duration_seconds
        self.assertEqual(parse(207), 207)
        self.assertEqual(parse("207"), 207)
        self.assertEqual(parse("3:27"), 207)
        self.assertEqual(parse("1:03:27"), 3807)

    def test_duration_parser_rejects_missing_malformed_zero_and_unsupported_values(self):
        parse = self.singws.normalize_karafun_duration_seconds
        for value in (None, "", "unknown", "3:99", "1:60:00", 0, "0:00", -1, True, [], {}):
            with self.subTest(value=value):
                self.assertIsNone(parse(value))

    def test_streaming_track_preserves_real_duration_or_adds_estimate(self):
        app = self.make_app()
        known = app._build_karafun_streaming_track(artist="A", title="Known", duration="3:47")
        self.assertEqual(known["duration"], 227)
        self.assertFalse(known.get("duration_estimated", False))
        self.assertEqual(known["duration_source"], "karafun_metadata")

        missing = app._build_karafun_streaming_track(artist="A", title="Missing")
        self.assertEqual(missing["duration"], 210)
        self.assertTrue(missing["duration_estimated"])
        self.assertEqual(missing["duration_source"], "karafun_default_estimate")

    def test_verified_duration_replaces_estimate_but_not_manual_duration(self):
        app = self.make_app()
        estimated = {"duration": 210, "duration_estimated": True, "duration_source": "karafun_default_estimate"}
        self.assertTrue(app._apply_verified_duration(estimated, "3:58", source="karafun_result"))
        self.assertEqual(estimated["duration"], 238)
        self.assertFalse(estimated.get("duration_estimated", False))
        self.assertEqual(estimated["duration_source"], "karafun_result")

        manual = {"duration": 199, "duration_source": "manual_host"}
        self.assertFalse(app._apply_verified_duration(manual, "3:58", source="karafun_result"))
        self.assertEqual(manual["duration"], 199)
        self.assertEqual(manual["duration_source"], "manual_host")

    def test_estimated_duration_is_visibly_distinguished(self):
        app = self.make_app()
        app.queue_display = None
        app._two_col_text = lambda _widget, left, right: f"{left} {right}"
        _visible, tooltip, _left, right = app._build_queue_song_row_text({
            "artist": "A", "title": "Song", "duration": 210,
            "duration_estimated": True,
        })
        self.assertIn("~3:30", right)
        self.assertIn("Estimated duration", tooltip)

    def test_rotation_totals_include_multiple_estimated_songs_and_mixed_media(self):
        app = self.make_app()
        app.settings = {"rotation_estimate_include_all_songs": True, "rotation_padding_sec": 0}
        app.karaoke_playing = False
        app._current_karaoke_singer_name = ""
        app._get_duration_secs = lambda _path: 0
        app.is_singer_active = lambda singer: any(not song.get("skipped", False) for song in singer.get("songs", []))
        app.queue = [
            {"name": "One", "songs": [
                {"song_info": "karafun_streaming:kf_1", "provider": "karafun_streaming", "duration": 210, "duration_estimated": True},
                {"song_info": "karafun_streaming:kf_2", "provider": "karafun_streaming", "duration": 210, "duration_estimated": True},
            ]},
            {"name": "Two", "songs": [{"song_info": "/local.mp3", "provider": "local", "duration": 180}]},
            {"name": "Three", "songs": [{"song_info": "/server.mp4", "provider": "local", "duration": 240, "remote_request_id": 7}]},
        ]
        singers, total = app._compute_queue_eta_seconds()
        self.assertEqual(singers, 3)
        self.assertEqual(total, 840)

    def test_estimate_marker_survives_json_restart_and_sync_upgrade(self):
        app = self.make_app()
        original = app._build_karafun_streaming_track(artist="A", title="Song")
        restored = json.loads(json.dumps(original))
        self.assertEqual(restored["duration"], 210)
        self.assertTrue(restored["duration_estimated"])
        self.assertTrue(app._apply_verified_duration(restored, 222, source="server_metadata"))
        self.assertEqual(restored["duration"], 222)
        self.assertFalse(restored.get("duration_estimated", False))

    def test_rotation_estimate_is_not_used_as_playback_completion_watchdog(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        monitor = source[source.index("def _start_karafun_completion_monitor"):]
        monitor = monitor[:monitor.index("def _finish_external_karafun_playback")]
        self.assertIn('0 if bool(entry.get("duration_estimated", False))', monitor)


if __name__ == "__main__":
    unittest.main()
