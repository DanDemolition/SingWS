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


def load_legacy_transport_module():
    spec = importlib.util.spec_from_file_location("singws_legacy_show_critical", "python_karaoke_transport.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShowCriticalRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()
        cls.legacy_transport = load_legacy_transport_module()

    def bare_app(self):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.queue = []
        app._request_queue_display_refresh = lambda *a, **k: None
        app._schedule_save_data = lambda *a, **k: None
        return app

    def playback_app(self, queue):
        app = self.bare_app()
        app.queue = queue
        app._next_in_progress = False
        app._stop_in_progress = False
        app._intro_loop_active = False
        app._pending_intro_loop = False
        app._pending_play_start_token = None
        app._pending_play_start_rollback = None
        app._singer_start_generation = 0
        app._confirmed_singer_start_generation = 0
        app._current_karaoke_singer_name = ""
        app._current_karaoke_singer_display = ""
        app._current_karaoke_singer_id = ""
        app._current_karaoke_request_id = ""
        app._current_karaoke_song_path = ""
        app.karaoke_transport = None
        app.karaoke_playing = False
        app._play_confirmation_open = False
        app._play_control_starting = False
        app._pending_play_start_context = None
        app._mp3g_prepare_then_play_next = ""
        app._active_external_karafun = None
        app._flush_deferred_remote_adds = lambda *_a, **_k: None
        app._cancel_pending_media_end_cleanup = lambda *_a, **_k: None
        app._is_rotation_mode = lambda: False
        app._is_rotation_locked = lambda: False
        app._resolve_phrase_start = lambda *_a, **_k: 0.0
        app._song_info_primary_path = lambda info: info[0] if isinstance(info, (tuple, list)) else info
        app._refresh_queue_entry_metadata = lambda *_a, **_k: None
        app._queue_entry_remote_request_id = lambda entry: entry.get("remote_request_id") if isinstance(entry, dict) else None
        app._ensure_singer_id = lambda singer: singer.setdefault("singer_id", f"sid-{singer['name']}")
        app._ensure_queue_entry_id = lambda entry: entry.setdefault("request_uid", f"rid-{entry.get('song_info')}")
        app._get_duration_secs = lambda *_a, **_k: 180
        app._set_karaoke_tempo = lambda *_a, **_k: None
        app.lookup_display_name = lambda path, **_k: f"Artist {path} • Title {path}"
        app._update_last_sung_card = lambda: None
        app._clear_next_up_overlay_pending = lambda *_a, **_k: None
        app._hide_next_up_transition_overlay = lambda *_a, **_k: None
        app._schedule_waiting_for_add_view_refresh = lambda *_a, **_k: None
        app.completed_requests = []
        app._complete_remote_request = lambda request_id, **_k: app.completed_requests.append(request_id)
        app._record_singer_history_play = lambda *_a, **_k: None
        app._sync_singer_history_async = lambda *_a, **_k: None
        app._send_stage_notifications_for_transition = lambda *_a, **_k: None
        app._clear_routine_processing_text_for_playback = lambda: None
        app._select_next_rotation_after_start = lambda *_a, **_k: 0
        app._reapply_rotation_presentation = lambda *_a, **_k: None
        app.update_queue_display = lambda: None
        app.save_data = lambda: None
        app.play_button_enabled = True
        app.play_next = type("PlayButton", (), {
            "setEnabled": lambda _self, enabled: setattr(app, "play_button_enabled", bool(enabled)),
            "setToolTip": lambda _self, _text: None,
        })()
        app.video_window = type("Window", (), {
            "force_black": False,
            "idle": True,
            "update": lambda _self: None,
        })()
        app.preview_window = type("Window", (), {
            "force_black": False,
            "update": lambda _self: None,
        })()
        app.presentations = []
        app._set_now_singing_3line = lambda singer, artist, title: app.presentations.append(
            ("card", singer, artist, title)
        )
        app._trigger_show_screen_singer_start_vfx = lambda singer, artist, title, **kwargs: app.presentations.append(
            ("vfx", singer, artist, title, kwargs.get("generation"))
        )

        class Signal:
            def __init__(self):
                self.callbacks = []

            def connect(self, callback):
                self.callbacks.append(callback)

            def emit(self):
                for callback in list(self.callbacks):
                    callback()

        class Transport:
            def __init__(self):
                self.started = Signal()
                self.stopped = False
                self.paused = False

            def stop(self):
                self.stopped = True

            def is_paused(self):
                return self.paused

        app.created_transports = []

        def play_mp3(*_args, **_kwargs):
            transport = Transport()
            app.created_transports.append(transport)
            app.karaoke_transport = transport
            return True

        app.play_mp3 = play_mp3
        return app

    @staticmethod
    def singer(name, *, skipped=False):
        return {
            "name": name,
            "singer_id": f"sid-{name}",
            "skipped": skipped,
            "songs": [{
                "request_uid": f"rid-{name}",
                "song_info": f"/{name}.mp3",
                "artist": f"Artist {name}",
                "title": f"Title {name}",
                "skipped": False,
            }],
        }

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

    def test_reconnected_same_name_reuses_existing_rotation_row(self):
        app = self.bare_app()
        app.queue = [{
            "name": "Harry",
            "singer_id": "local-1",
            "server_singer_id": "old-browser-session",
            "server_singer_session_id": 812,
            "songs": [],
        }]

        self.assertEqual(
            app._queue_singer_match_index(
                " Harry ",
                {"singer_id": "new-browser-session", "singer_session_id": 913},
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

    def test_skip_one_singer_then_play_announces_the_confirmed_next_singer(self):
        app = self.playback_app([self.singer("Skipped", skipped=True), self.singer("Actual")])
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app.play_next_file()
        self.assertEqual(app.presentations, [])
        app.created_transports[-1].started.emit()
        self.assertEqual(app._current_karaoke_singer_name, "Actual")
        self.assertEqual([row[1] for row in app.presentations], ["Actual", "Actual"])

    def test_one_play_control_press_starts_exactly_one_song(self):
        first = self.singer("First")
        second = self.singer("Second")
        third = self.singer("Third")
        second_entry = second["songs"][0]
        third_entry = third["songs"][0]
        app = self.playback_app([first, second, third])
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app._on_play_control_clicked()
        self.assertEqual(len(app.created_transports), 1)
        self.assertTrue(app.play_button_enabled)
        self.assertTrue(app._play_control_starting)
        app.created_transports[0].started.emit()
        self.assertEqual(app._current_karaoke_singer_name, "First")
        self.assertEqual([row["name"] for row in app.queue], ["Second", "Third", "First"])
        self.assertIs(second["songs"][0], second_entry)
        self.assertIs(third["songs"][0], third_entry)
        self.assertEqual(app.completed_requests, [])
        self.assertTrue(app.play_button_enabled)
        self.assertFalse(app._play_control_starting)

    def test_play_button_has_one_ui_command_handler(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        live_source = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertEqual(
            live_source.count("self.play_next.clicked.connect(self._on_play_control_clicked)"),
            1,
        )
        self.assertNotIn("self.play_next.clicked.connect(self.play_next_file)", live_source)

    def test_rapid_double_click_during_startup_cannot_consume_second_song(self):
        app = self.playback_app([self.singer("First"), self.singer("Second")])
        confirmations = []
        app._confirm_repeated_play_advance = lambda: confirmations.append("asked") or False
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app._on_play_control_clicked()
            queue_after_first = list(app.queue)
            app._on_play_control_clicked()
        self.assertEqual(confirmations, ["asked"])
        self.assertEqual(len(app.created_transports), 1)
        self.assertEqual(app.queue, queue_after_first)
        self.assertEqual(app.queue[0]["name"], "Second")
        self.assertEqual(len(app.queue[0]["songs"]), 1)

    def test_duplicate_touch_events_are_coalesced_behind_confirmation(self):
        app = self.playback_app([self.singer("Touch One"), self.singer("Touch Two")])
        app._confirm_repeated_play_advance = lambda: False
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            for _ in range(8):
                app._on_play_control_clicked()
        self.assertEqual(len(app.created_transports), 1)
        self.assertEqual([row["name"] for row in app.queue], ["Touch Two"])

    def test_confirmed_repeated_play_during_startup_retains_first_song_and_advances(self):
        first = self.singer("Pending")
        retained_entry = first["songs"][0]
        second = self.singer("Confirmed Next")
        app = self.playback_app([first, second])
        app._confirm_repeated_play_advance = lambda: True
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app._on_play_control_clicked()
            old_transport = app.created_transports[-1]
            app._on_play_control_clicked()
        self.assertTrue(old_transport.stopped)
        self.assertEqual(len(app.created_transports), 2)
        self.assertEqual([row["name"] for row in app.queue], ["Pending"])
        self.assertIs(app.queue[0]["songs"][0], retained_entry)
        self.assertFalse(first.get("skipped", False))
        app.created_transports[-1].started.emit()
        self.assertEqual(app._current_karaoke_singer_name, "Confirmed Next")

    def test_repeated_play_while_playing_cancel_is_side_effect_free(self):
        app = self.playback_app([self.singer("Current"), self.singer("Next")])
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app._on_play_control_clicked()
        app.created_transports[-1].started.emit()
        queue_snapshot = [(row["name"], list(row.get("songs", []))) for row in app.queue]
        presentation_snapshot = list(app.presentations)
        transport_snapshot = app.karaoke_transport
        app._confirm_repeated_play_advance = lambda: False
        app._on_play_control_clicked()
        self.assertIs(app.karaoke_transport, transport_snapshot)
        self.assertEqual([(row["name"], list(row.get("songs", []))) for row in app.queue], queue_snapshot)
        self.assertEqual(app.presentations, presentation_snapshot)
        self.assertEqual(len(app.created_transports), 1)

    def test_repeated_play_while_playing_requires_confirmation_before_advance(self):
        app = self.playback_app([self.singer("Current"), self.singer("Next")])
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app._on_play_control_clicked()
        app.created_transports[-1].started.emit()
        app._confirm_repeated_play_advance = lambda: True
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app._on_play_control_clicked()
        self.assertEqual(len(app.created_transports), 2)
        self.assertEqual(app._current_karaoke_singer_name, "Current")
        app.created_transports[-1].started.emit()
        self.assertEqual(app._current_karaoke_singer_name, "Next")

    def test_repeated_play_payload_identifies_paused_current_and_next(self):
        app = self.playback_app([self.singer("Paused"), self.singer("Next")])
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app._on_play_control_clicked()
        app.created_transports[-1].started.emit()
        app.karaoke_transport.paused = True
        current, next_payload, state = app._repeated_play_payloads()
        self.assertEqual(state, "paused")
        self.assertEqual(current["singer"], "Paused")
        self.assertEqual(next_payload["singer"], "Next")

    def test_repeated_play_warning_names_both_songs_and_all_queue_effects(self):
        app = self.playback_app([self.singer("Current"), self.singer("Next")])
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app._on_play_control_clicked()
        app.created_transports[-1].started.emit()
        captured = {}

        class Dialog:
            def __init__(_self, _parent, **kwargs):
                captured.update(kwargs)

            def exec(_self):
                return self.singws.QDialog.DialogCode.Rejected

        with mock.patch.object(self.singws, "ConfirmInterruptDialog", Dialog):
            self.assertFalse(app._confirm_repeated_play_advance())
        message = captured["message"]
        self.assertIn("Current (playing)", message)
        self.assertIn("Current:", message)
        self.assertIn("Next:", message)
        self.assertIn("will stop", message)
        self.assertIn("No other singer or song will be skipped, completed, deleted, or removed", message)
        self.assertEqual(captured["confirm_text"], "Stop & Play Next")
        self.assertEqual(captured["cancel_text"], "Keep Current")

    def test_manual_play_wins_over_nearby_stale_automatic_advance(self):
        app = self.playback_app([self.singer("Manual"), self.singer("Untouched")])
        app._confirmed_singer_start_generation = 9
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app._on_play_control_clicked()
        self.assertFalse(app._auto_play_next_if_generation(9, reason="test_race"))
        self.assertEqual(len(app.created_transports), 1)
        app.created_transports[-1].started.emit()
        self.assertFalse(app._auto_play_next_if_generation(9, reason="test_delayed_race"))
        self.assertEqual(len(app.created_transports), 1)
        self.assertEqual(app._current_karaoke_singer_name, "Manual")

    def test_multiple_consecutive_skips_announce_only_first_playable_singer(self):
        app = self.playback_app([
            self.singer("Skip One", skipped=True),
            self.singer("Skip Two", skipped=True),
            self.singer("Performer"),
        ])
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app.play_next_file()
        app.created_transports[-1].started.emit()
        self.assertEqual(app._current_karaoke_singer_name, "Performer")
        self.assertNotIn("Skip One", repr(app.presentations))
        self.assertNotIn("Skip Two", repr(app.presentations))

    def test_rapid_skip_and_play_ignores_delayed_old_transport_signal(self):
        first = self.singer("Old")
        second = self.singer("Current")
        app = self.playback_app([first, second])
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app.play_next_file()
            old_transport = app.created_transports[-1]
            self.assertTrue(app._cancel_pending_singer_start_for_queue_change("rapid_skip"))
            first["skipped"] = True
            app._next_in_progress = False
            app.play_next_file()
        current_transport = app.created_transports[-1]
        current_transport.started.emit()
        snapshot = list(app.presentations)
        old_transport.started.emit()
        self.assertEqual(app._current_karaoke_singer_name, "Current")
        self.assertEqual(app.presentations, snapshot)
        self.assertNotIn("Old", repr(app.presentations))

    def test_manual_queue_selection_uses_selected_item_identity(self):
        first = self.singer("First")
        selected = self.singer("Selected")
        app = self.playback_app([selected, first])  # host selection moves this singer to the top
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app.play_next_file()
        app.created_transports[-1].started.emit()
        self.assertEqual(app._current_karaoke_singer_id, "sid-Selected")
        self.assertIn(("card", "Selected", "Artist Selected", "Title Selected"), app.presentations)

    def test_automatic_advancement_uses_same_confirmed_start_path(self):
        app = self.playback_app([self.singer("Auto Next")])
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app.play_next_file(skip_confirmation=True)
        self.assertEqual(app.presentations, [])
        app.created_transports[-1].started.emit()
        self.assertEqual(app._current_karaoke_singer_name, "Auto Next")

    def test_playback_startup_failure_does_not_announce_or_consume_singer(self):
        singer = self.singer("Failure")
        app = self.playback_app([singer])
        app.play_mp3 = lambda *_a, **_k: False
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app.play_next_file()
        self.assertEqual(app.presentations, [])
        self.assertEqual([row["name"] for row in app.queue], ["Failure"])
        self.assertEqual(len(app.queue[0]["songs"]), 1)
        self.assertEqual(getattr(app, "_current_karaoke_singer_name", ""), "")

    def test_replay_keeps_confirmed_singer_and_does_not_reannounce(self):
        app = self.playback_app([self.singer("Replay")])
        with mock.patch.object(self.singws.os.path, "exists", return_value=True):
            app.play_next_file()
        transport = app.created_transports[-1]
        transport.seek = lambda seconds: setattr(transport, "seek_seconds", seconds)
        app._apply_karaoke_tempo_live = lambda: None
        app._restart_in_progress = False
        app._stop_in_progress = False
        transport.started.emit()
        snapshot = list(app.presentations)
        app.restart_track(skip_confirmation=True)
        self.assertEqual(transport.seek_seconds, 0.0)
        self.assertEqual(app.presentations, snapshot)
        self.assertEqual(app._current_karaoke_singer_name, "Replay")

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
        commit = source.index("def _commit_pending_start")
        token_check = source.index("_pending_play_start_token", commit)
        active_identity = source.index("self._current_karaoke_singer_name =", commit)
        animation = source.index("self._trigger_show_screen_singer_start_vfx(", commit)
        self.assertLess(token_check, active_identity)
        self.assertLess(active_identity, animation)
        self.assertIn("generation=start_generation", source[animation:])

        prepared_start = inspect.getsource(self.singws.KaraokeApp._start_python_karaoke_transport)
        transport_start = prepared_start.index("transport.start(start_seconds)")
        self.assertNotIn("_trigger_show_screen_singer_start_vfx", prepared_start[:transport_start])
        self.assertNotIn('reason="playback_started"', prepared_start[transport_start:])
        self.assertEqual(prepared_start.count("_trigger_show_screen_singer_start_vfx"), 0)
        self.assertEqual(prepared_start.count("transport.start(start_seconds)"), 1)
        self.assertNotIn("self._playback_start_countdown_ms = 2100", source)

    def test_stale_singer_start_generation_cannot_replace_current_animation(self):
        app = self.bare_app()
        calls = []

        def _show_singer_start(_self, singer, title, artist):
            # The real VideoAreaWidget reports whether the overlay was shown.
            calls.append((singer, artist, title))
            return True

        area = type("Area", (), {"show_singer_start_vfx": _show_singer_start})()
        app.video_window = type("Window", (), {"video_area": area})()
        app._confirmed_singer_start_generation = 8

        self.assertFalse(app._trigger_show_screen_singer_start_vfx("Skipped", "A", "Old", generation=7))
        self.assertTrue(app._trigger_show_screen_singer_start_vfx("Current", "B", "New", generation=8))
        self.assertEqual(calls, [("Current", "B", "New")])

    def test_skip_invalidates_and_stops_an_unconfirmed_start(self):
        app = self.bare_app()
        singer = {"name": "Skipped", "songs": [], "skipped": False}
        app.queue = [singer]
        app._singer_start_generation = 4
        app._pending_playback_countdown_payload = {"generation": 4, "singer": "Skipped"}
        rollback_reasons = []

        class Transport:
            stopped = False

            def stop(self):
                self.stopped = True

        transport = Transport()
        app.karaoke_transport = transport
        app._pending_play_start_rollback = lambda reason: rollback_reasons.append(reason) or True
        app.update_queue_display = lambda: None
        app.save_data = lambda: None
        app._begin_undoable_action = lambda *_a, **_k: None
        app._commit_undoable_action = lambda *_a, **_k: None

        app.toggle_singer_skip(0)

        self.assertTrue(singer["skipped"])
        self.assertEqual(rollback_reasons, ["singer_skip_changed"])
        self.assertTrue(transport.stopped)
        self.assertIsNone(app.karaoke_transport)
        self.assertIsNone(app._pending_playback_countdown_payload)
        self.assertEqual(app._singer_start_generation, 5)

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

    def test_legacy_waitlist_karafun_version_is_treated_as_external_link(self):
        detect = self.singws.KaraokeApp._remote_request_is_karafun
        self.assertTrue(detect({"selected_version": "KaraFun"}))
        self.assertTrue(detect({"selected_brand": "KaraFun Online"}))
        self.assertTrue(detect({"selected_source": "karafun"}))
        self.assertFalse(detect({"selected_version": "Sound Choice"}))

        prepare = inspect.getsource(self.singws.KaraokeApp._prepare_remote_request_add_payload)
        self.assertIn("provider_url=provider_url", prepare)
        self.assertIn("is_karafun = self._remote_request_is_karafun(req)", prepare)
        process = inspect.getsource(self.singws.KaraokeApp.process_external_request)
        self.assertIn(
            "if _sig in _failed_sigs and not self._remote_request_is_karafun(req):",
            process,
        )

    def test_waitlist_state_is_reconciled_even_when_locally_disabled(self):
        source = inspect.getsource(self.singws.KaraokeApp._reconcile_remote_requests)
        self.assertIn("_sync_waitlist_state_from_server_async", source)
        self.assertNotIn("if self._is_waitlist_enabled_cached():\n                self._sync_waitlist_state_from_server_async", source)


if __name__ == "__main__":
    unittest.main()
