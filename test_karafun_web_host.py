"""karafun_web_host.py: the show side of the KaraFun web player, against a fake app and a fake player service (no Qt, no network)."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import karafun_web_host as host_module
from karafun_web_host import KaraFunWebHost, song_id_from_entry

SOURCE = (Path(__file__).parent / "0.2.18.1.py").read_text(encoding="utf-8")


class FakeWeb:
    def __init__(self):
        self.calls = []
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: self.calls.append((name, a))


class FakeService:
    def __init__(self, state="ready"):
        self.state = state
        self.web = FakeWeb()
        self.started = 0
    def start(self): self.started += 1
    def stop(self): self.state = "idle"


class FakeApp:
    def __init__(self):
        self.settings = {"karafun_web_player": True}
        self.entry = None
        self.statuses, self.notes, self.finished, self.faded, self.shown_kt = [], [], [], [], []
        self._active_external_karafun = {"entry": None}
    def _run_on_ui_thread(self, fn): fn()
    def _karafun_entry_artist_title(self, entry): return entry.get("artist", ""), entry.get("title", "")
    def _set_karafun_entry_status(self, entry, status, *, message="", notify=False, level="info"):
        entry["karafun_status"] = status
        self.statuses.append((status, message))
    def _karafun_active_entry(self): return self.entry
    def _show_processing_notification(self, text, level="info"): self.notes.append((level, text))
    def _schedule_save_data(self, ms): pass
    def update_queue_display(self): pass
    @staticmethod
    def _apply_verified_duration(entry, value, *, source):
        entry["duration"] = value; entry["duration_estimated"] = False; return True
    def _fade_bg_for_external_karafun(self): self.faded.append(1)
    def _karafun_show_key_tempo(self, key, tempo): self.shown_kt.append((key, tempo))
    def _transport_shadow(self): return None
    def _finish_external_karafun_playback(self, action, *, expected_active=None): self.finished.append((action, expected_active))


def make(state="ready"):
    app = FakeApp()
    entry = {"artist": "NOFX", "title": "Linoleum", "provider_track_id": "kf_3241559", "duration": 240, "duration_estimated": True}
    app.entry = entry
    app._active_external_karafun = {"entry": entry}
    service = FakeService(state)
    host = KaraFunWebHost(app, service_factory=lambda *a, **k: service, diag=lambda t: None)
    host._start_frames = mock.Mock()
    host._stop_frames = mock.Mock()
    host._arm_watchdog = mock.Mock()
    return app, entry, service, host


def status(playing=True, song=3241559, position=0.0, duration=130.0):
    return {"type": "status", "playing": playing, "position": position, "duration": duration, "signedIn": True,
            "song": ({"id": song, "title": "Linoleum", "artist": "NOFX"} if song else None)}


class IdTests(unittest.TestCase):
    def test_the_kafun_id_comes_from_the_provider_id_or_the_path(self):
        self.assertEqual(song_id_from_entry({"provider_track_id": "kf_3250037"}), 3250037)
        self.assertEqual(song_id_from_entry({"path": "karafun_streaming:kf_77"}), 77)
        self.assertEqual(song_id_from_entry({}, "karafun_streaming:kf_5"), 5)
        self.assertIsNone(song_id_from_entry({"path": "karafun_streaming:manual:abc"}))
        self.assertIsNone(song_id_from_entry({"provider_track_id": "123"}))


@unittest.skipUnless(sys.platform == "darwin", "the web player is macOS only")
class HostTests(unittest.TestCase):
    def test_off_means_the_app_path_handles_it(self):
        app, entry, service, host = make()
        app.settings["karafun_web_player"] = False
        self.assertFalse(host.can_start(entry))
        self.assertFalse(host.start(entry, 0, 100))

    def test_a_song_without_a_kafun_id_is_left_to_the_app_path(self):
        app, entry, service, host = make()
        entry.pop("provider_track_id")
        self.assertFalse(host.can_start(entry))

    def test_start_plays_the_id_by_artist_and_title_when_ready(self):
        app, entry, service, host = make()
        self.assertTrue(host.start(entry, 2, 90))
        self.assertIn(("play", (3241559, "NOFX Linoleum")), service.web.calls)
        self.assertEqual(app.statuses[0], ("launching", "Starting KaraFun..."))

    def test_start_while_the_player_is_still_starting_plays_when_it_is_ready(self):
        app, entry, service, host = make(state="starting")
        host.start(entry, 0, 100)
        self.assertEqual(service.web.calls, [])
        service.state = "ready"
        host._service_state("ready", "Signed in")
        self.assertIn(("play", (3241559, "NOFX Linoleum")), service.web.calls)

    def test_signed_out_fails_the_song_with_the_normal_notification(self):
        app, entry, service, host = make(state="sign_in")
        host.start(entry, 0, 100)
        self.assertEqual(app.statuses[-1][0], "failed")
        self.assertEqual(entry["karafun_submission_state"], "karafun_failed")
        self.assertIn("Press Stop in SingWS to return the song to the queue.", app.notes[-1][1])
        self.assertIsNone(host.asked)

    def test_the_page_failing_to_play_fails_the_song(self):
        app, entry, service, host = make()
        host.start(entry, 0, 100)
        host._handle({"type": "error", "message": "Play: KaraFun didn't find song 3241559"})
        self.assertEqual(app.statuses[-1][0], "failed")

    def test_started_sets_the_clock_duration_key_tempo_and_fades_the_music(self):
        app, entry, service, host = make()
        host.start(entry, 2, 90)
        host._handle(status(position=1.0, duration=130.4))
        self.assertEqual(app.statuses[-1], ("playing", "Playing in KaraFun"))
        self.assertEqual(entry["duration"], 130)
        self.assertFalse(entry["duration_estimated"])
        self.assertEqual(entry["karafun_submission_state"], "karafun_queued")
        self.assertEqual(app.faded, [1])
        self.assertIn(("set_key", (2,)), service.web.calls)
        self.assertIn(("set_tempo", (90,)), service.web.calls)
        self.assertEqual(app.shown_kt[-1], (2, 90))
        host._start_frames.assert_called_once()

    def test_other_songs_playing_do_not_count_as_started(self):
        app, entry, service, host = make()
        host.start(entry, 0, 100)
        host._handle(status(song=999))
        self.assertFalse(host.asked["heard"])

    def test_pause_and_resume_keep_the_display_clock_true(self):
        app, entry, service, host = make()
        host.start(entry, 0, 100)
        host._handle(status(position=1.0))
        with mock.patch.object(host_module.time, "monotonic", return_value=1000.0):
            host._handle(status(playing=False, position=20.0))
            self.assertEqual(entry["karafun_paused_at"], 1000.0)
            self.assertEqual(entry["karafun_display_started_at"], 980.0)
            host._handle(status(playing=True, position=20.0))
            self.assertNotIn("karafun_paused_at", entry)

    def _playing(self):
        app, entry, service, host = make()
        host.start(entry, 0, 100)
        host._handle(status(position=1.0))
        return app, entry, service, host

    def test_stopped_at_the_end_completes_the_song_once(self):
        app, entry, service, host = self._playing()
        active = app._active_external_karafun
        host._handle(status(playing=False, position=129.5, duration=130.0))
        self.assertEqual(app.finished, [("complete", active)])
        host._handle(status(playing=False, position=129.5, duration=130.0))
        self.assertEqual(len(app.finished), 1)

    def test_gone_or_another_song_completes_it(self):
        for the_status in (status(song=None, playing=False), status(song=555)):
            app, entry, service, host = self._playing()
            host._handle(the_status)
            self.assertEqual(len(app.finished), 1)

    def test_sitting_at_the_end_for_a_few_seconds_completes_it(self):
        app, entry, service, host = self._playing()
        with mock.patch.object(host_module.time, "monotonic") as clock:
            clock.return_value = 100.0
            host._handle(status(playing=True, position=129.0, duration=130.0))
            self.assertEqual(app.finished, [])
            clock.return_value = 101.5
            host._handle(status(playing=True, position=129.0, duration=130.0))
            self.assertEqual(app.finished, [])
            clock.return_value = 103.0
            host._handle(status(playing=True, position=129.0, duration=130.0))
        self.assertEqual(len(app.finished), 1)

    def test_a_song_in_the_middle_never_completes(self):
        app, entry, service, host = self._playing()
        host._handle(status(playing=True, position=60.0))
        host._handle(status(playing=False, position=60.0))      # paused
        self.assertEqual(app.finished, [])

    def test_a_status_for_an_older_session_does_nothing(self):
        app, entry, service, host = self._playing()
        app.entry = {"another": "song"}        # the active KaraFun song is no longer this one
        host._handle(status(playing=False, song=None))
        self.assertEqual(app.finished, [])

    def test_controls_go_to_the_player(self):
        app, entry, service, host = self._playing()
        service.web.calls.clear()
        host._handle(status(playing=True, position=30.0))
        self.assertTrue(host.toggle_pause())
        host._handle(status(playing=False, position=30.0))
        self.assertTrue(host.toggle_pause())
        self.assertEqual([c[0] for c in service.web.calls], ["pause", "resume"])
        service.web.calls.clear()
        self.assertTrue(host.seek_to(500.0))                      # clamped to the end guard: 130 - 8
        self.assertEqual(service.web.calls, [("seek", (122.0,))])
        self.assertEqual(host.apply_key_tempo(entry, -2, 110), (-2, 110))
        self.assertIn(("set_key", (-2,)), service.web.calls)
        self.assertIn(("set_tempo", (110,)), service.web.calls)

    def test_finishing_stops_the_player_and_the_picture(self):
        app, entry, service, host = self._playing()
        service.web.calls.clear()
        host.on_finish()
        self.assertIn(("stop", ()), service.web.calls)
        host._stop_frames.assert_called()
        self.assertIsNone(host.asked)
        self.assertFalse(host.active())


class HookSourceTests(unittest.TestCase):
    """The hooks in 0.2.18.1.py: each starts with the web player's own check, and the setting is off by default."""
    def test_setting_default_off(self):
        self.assertRegex(SOURCE, r'"karafun_web_player": False,')

    def test_every_control_hook_checks_the_web_player_first(self):
        for name, marker in (("def _toggle_karafun_playback(self):", "_karafun_web_active"),
                             ("def _karafun_seek_to(self, target_sec: float) -> bool:", "_karafun_web_active"),
                             ("def _karafun_apply_key_tempo(self, entry: dict, target_key: int, target_tempo: int):", "_karafun_web_active")):
            body = SOURCE[SOURCE.index(name):][:2200]
            self.assertIn(marker, body, name)

    def test_finish_and_shutdown_and_start_are_hooked(self):
        finish = SOURCE[SOURCE.index("def _finish_external_karafun_playback"):][:1500]
        self.assertIn("_web.on_finish()", finish)
        self.assertIn("self._karafun_web_shutdown()", SOURCE)
        start = SOURCE[SOURCE.index("def _start_external_karafun_playback"):][:9000]
        self.assertIn("web_playback", start)
        self.assertIn("elif automatic_playback:", start)

    def test_the_proven_automation_function_is_untouched_by_web_code(self):
        body = SOURCE[SOURCE.index("def _automate_karafun_search_and_play"):SOURCE.index("def _start_karafun_completion_monitor")]
        self.assertNotIn("karafun_web", body)


if __name__ == "__main__":
    unittest.main()
