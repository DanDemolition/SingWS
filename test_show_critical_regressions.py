"""Regression coverage for show-critical playback and queue-integrity fixes."""

import importlib.util
import inspect
import unittest
from pathlib import Path
from unittest import mock


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_show_critical", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_transport_module():
    spec = importlib.util.spec_from_file_location("singws_gst_show_critical", "gst_karaoke_transport.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_legacy_transport_module():
    spec = importlib.util.spec_from_file_location("singws_legacy_show_critical", "python_karaoke_transport.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShowCriticalRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()
        cls.transport = load_transport_module()
        cls.legacy_transport = load_legacy_transport_module()

    def bare_app(self):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.queue = []
        app._request_queue_display_refresh = lambda *a, **k: None
        app._schedule_save_data = lambda *a, **k: None
        return app

    def test_gstreamer_preroll_never_blocks_qt_main_thread(self):
        start = inspect.getsource(self.transport.GstKaraokeTransport.start)
        finish = inspect.getsource(self.transport.GstKaraokeTransport._finish_start_after_preroll)
        self.assertNotIn("get_state(4 * Gst.SECOND)", start)
        self.assertIn("QTimer.singleShot", start)
        self.assertIn("get_state(0)", finish)
        self.assertIn("self.started.emit()", finish)

    def test_legacy_transport_obeys_same_countdown_gate(self):
        start = inspect.getsource(self.legacy_transport.PythonKaraokeTransport.start)
        delayed = inspect.getsource(self.legacy_transport.PythonKaraokeTransport._finish_delayed_start)
        self.assertIn("QTimer.singleShot", start)
        self.assertNotIn("self.seek(start_seconds)", start)
        self.assertIn("self.seek(start_seconds)", delayed)
        self.assertIn("self.started.emit()", delayed)

    def test_distinct_request_ids_survive_identical_metadata(self):
        app = self.bare_app()
        app.queue = [{
            "name": "Rina",
            "songs": [
                {"remote_request_id": 266, "artist": "Artist", "title": "Song", "song_info": "/a.mp3"},
                {"remote_request_id": 267, "artist": "Artist", "title": "Song", "song_info": "/a.mp3"},
            ],
        }]
        self.assertEqual(app._cleanup_duplicate_singer_songs(reason="regression"), 0)
        self.assertEqual([song["remote_request_id"] for song in app.queue[0]["songs"]], [266, 267])

    def test_only_identical_permanent_request_id_is_deduplicated(self):
        app = self.bare_app()
        first = {"remote_request_id": 209, "artist": "A", "title": "B", "song_info": "/b.mp3"}
        replay = dict(first)
        app.queue = [{"name": "James", "songs": [first, replay]}]
        self.assertEqual(app._cleanup_duplicate_singer_songs(reason="reconnect"), 1)
        self.assertEqual(app.queue[0]["songs"], [first])

    def test_renamed_singer_matches_by_immutable_server_identity(self):
        app = self.bare_app()
        app.queue = [{
            "name": "New Display Name",
            "singer_id": "local-1",
            "server_singer_id": "server-42",
            "server_singer_session_id": 812,
            "songs": [],
        }]
        self.assertEqual(
            app._queue_singer_match_index(
                "Retired Name",
                {"singer_id": "server-42", "singer_session_id": 812},
            ),
            0,
        )

    def test_rotation_advance_handles_large_rotation_and_multiple_songs(self):
        app = self.bare_app()
        app.queue = [
            {
                "name": f"Singer {i}",
                "singer_id": f"sid-{i}",
                "songs": [{"request_uid": f"request-{i}", "song_info": f"/{i}.mp3", "skipped": False}],
                "skipped": False,
            }
            for i in range(500)
        ]
        # The just-started singer has rotated to the end with a later song.
        app.queue.append({
            "name": "Started",
            "singer_id": "started-id",
            "songs": [{"request_uid": "later-song", "song_info": "/later.mp3", "skipped": False}],
            "skipped": False,
        })
        selected = []
        app._select_queue_singer_for_host = selected.append
        self.assertEqual(app._select_next_rotation_after_start("started-id"), 0)
        self.assertEqual(selected, [0])

        app.queue = [app.queue[-1]]
        selected.clear()
        self.assertEqual(app._select_next_rotation_after_start("started-id"), 0)
        self.assertEqual(selected, [0])

    def test_rapid_play_actions_cannot_consume_large_rotation_during_preroll(self):
        app = self.bare_app()
        app.queue = [
            {
                "name": f"Singer {i}",
                "singer_id": f"sid-{i}",
                "songs": [{"request_uid": f"request-{i}", "song_info": f"/{i}.mp3", "skipped": False}],
                "skipped": False,
            }
            for i in range(500)
        ]
        original_ids = [row["singer_id"] for row in app.queue]
        app._pending_play_start_token = object()
        app._next_in_progress = False
        app._stop_in_progress = False
        app._intro_loop_active = False
        app._pending_intro_loop = False
        app.karaoke_transport = None
        app._flush_deferred_remote_adds = lambda *_a, **_k: None
        app._cancel_pending_media_end_cleanup = lambda *_a, **_k: None
        with mock.patch.object(self.singws, "_diag", lambda *_a, **_k: None):
            for _ in range(1000):
                app.play_next_file()
        self.assertEqual([row["singer_id"] for row in app.queue], original_ids)
        self.assertEqual(len(app.queue), 500)

    def test_play_sequence_is_countdown_then_start_then_remote_completion(self):
        source = inspect.getsource(self.singws.KaraokeApp.play_next_file)
        countdown = source.index("_pending_playback_countdown_payload =")
        media_start = source.index("start_ok = bool(self.play_", countdown)
        accepted = source.index("if not start_ok", media_start)
        remote_complete = source.index("self._complete_remote_request", accepted)
        self.assertLess(countdown, media_start)
        self.assertLess(accepted, remote_complete)
        self.assertIn("transport_for_start.started.connect(_commit_pending_start)", source)
        self.assertLess(source.index("def _commit_pending_start"), remote_complete)
        self.assertIn("zip_prepare_pending", source)
        self.assertIn("action=play_start_rollback", source)
        self.assertIn("QTimer.singleShot(3500", source)
        self.assertIn("playback preroll/countdown not committed", source)

        prepared_start = inspect.getsource(self.singws.KaraokeApp._start_python_karaoke_transport)
        self.assertLess(
            prepared_start.index("_trigger_show_screen_singer_start_vfx"),
            prepared_start.index("transport.start(start_seconds)"),
        )

    def test_countdown_restarts_one_timer_and_runs_at_show_speed(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn("countdownTimer.stop()", source)
        self.assertIn("interval: 700", source)
        self.assertIn("startCountdownValue = 3", source)

    def test_replace_track_search_includes_and_validates_karafun(self):
        source = inspect.getsource(self.singws.KaraokeApp.open_replace_track_dialog)
        self.assertIn("karafun_search_url=", source)
        self.assertIn("/karafun_search.php", source)
        self.assertIn("karafun_tenant=", source)
        self.assertIn("KaraFun result is unavailable", source)

    def test_waitlist_state_is_reconciled_even_when_locally_disabled(self):
        source = inspect.getsource(self.singws.KaraokeApp._reconcile_remote_requests)
        self.assertIn("_sync_waitlist_state_from_server_async", source)
        self.assertNotIn("if self._is_waitlist_enabled_cached():\n                self._sync_waitlist_state_from_server_async", source)


if __name__ == "__main__":
    unittest.main()
