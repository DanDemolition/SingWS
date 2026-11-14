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
        self.kwcalls = []
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: (self.calls.append((name, a)), self.kwcalls.append(k))


class FakeService:
    def __init__(self, state="ready"):
        self.state = state
        self.web = FakeWeb()
        self.started = 0
        self.stopped = 0
    def start(self, download=False):
        self.started += 1
        self.downloads = getattr(self, 'downloads', 0) + (1 if download else 0)
    def stop(self):
        self.state = "idle"; self.stopped += 1


class FakeApp:
    def __init__(self):
        self.settings = {"karafun_provider_enabled": True}
        self.entry = None
        self.statuses, self.notes, self.finished, self.faded, self.shown_kt = [], [], [], [], []
        self._active_external_karafun = {"entry": None}
    def _run_on_ui_thread(self, fn): fn()
    def _karafun_entry_artist_title(self, entry): return entry.get("artist", ""), entry.get("title", "")
    def _set_karafun_entry_status(self, entry, status, *, message="", notify=False, level="info"):
        entry["karafun_status"] = status
        self.statuses.append((status, message))
    def _karafun_active_entry(self): return self.entry
    def _karafun_search_queries_for_entry(self, entry): return ["NOFX Linoleum", "Linoleum NOFX", "Linoleum", "NOFX"]
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
    def test_a_song_added_by_hand_without_an_id_is_played_by_title_and_artist(self):
        app, entry, service, host = make()
        entry.pop("provider_track_id")
        app._is_external_karafun_entry = lambda e: True
        self.assertTrue(host.can_start(entry))
        self.assertTrue(host.start(entry, 0, 100))
        self.assertIn(("play", (0, "NOFX Linoleum", ["NOFX Linoleum", "Linoleum NOFX", "Linoleum", "NOFX"], "NOFX", "Linoleum")), service.web.calls)

    def test_something_that_is_not_a_karafun_song_is_refused(self):
        app, entry, service, host = make()
        entry.pop("provider_track_id")
        app._is_external_karafun_entry = lambda e: False
        self.assertFalse(host.can_start(entry))
        self.assertFalse(host.start(entry, 0, 100))

    def test_start_plays_the_id_by_artist_and_title_when_ready(self):
        app, entry, service, host = make()
        self.assertTrue(host.start(entry, 2, 90))
        self.assertIn(("play", (3241559, "NOFX Linoleum", ["NOFX Linoleum", "Linoleum NOFX", "Linoleum", "NOFX"], "NOFX", "Linoleum")), service.web.calls)
        self.assertEqual(app.statuses[0], ("launching", "Starting KaraFun..."))

    def _picture_host(self):
        """A host whose real _start_frames/_stop_frames/_place_picture run, over a fake audience window."""
        app, entry, service, host = make()
        host.service = service
        host._start_frames = KaraFunWebHost._start_frames.__get__(host)
        host._stop_frames = KaraFunWebHost._stop_frames.__get__(host)
        class Area:
            def width(self): return 1920
            def height(self): return 1080
            def mapToGlobal(self, point): return type("P", (), {"x": lambda s: 1440, "y": lambda s: 0})()
        class Screen:
            def refreshRate(self): return 120.0
        state = {"visible": True, "minimized": False, "shown": 0}
        def toggle(): state["visible"] = True; state["shown"] += 1
        app.toggle_karaoke_window = toggle
        app.video_window = type("VW", (), {"video_area": Area(), "idle": True, "screen": lambda s: Screen(), "winId": lambda s: 0,
                                           "isVisible": lambda s: state["visible"], "isMinimized": lambda s: state["minimized"]})()
        app._vw_state = state
        pstate = {"visible": True}
        class PArea:
            def isVisible(self): return pstate["visible"]
            def width(self): return 528
            def height(self): return 298
            def mapToGlobal(self, point): return type("P", (), {"x": lambda s: 1410, "y": lambda s: 160})()
        app.preview_window = type("PW", (), {"video_area": PArea()})()
        app.isVisible = lambda: True
        app.isMinimized = lambda: False
        app._preview_state = pstate
        app._set_mpv_hosts_visible = lambda on: None
        class FakeTimer:
            def __init__(self, parent=None):
                self.timeout = type("Sig", (), {"connect": lambda s, fn: None})()
            def setInterval(self, ms): self.ms = ms
            def start(self): self.running = True
            def stop(self): self.running = False
        patcher = mock.patch("PyQt6.QtCore.QTimer", FakeTimer)
        patcher.start(); self.addCleanup(patcher.stop)
        return app, service, host

    def test_starting_the_picture_asks_for_720p_places_the_window_and_sets_the_refresh(self):
        app, service, host = self._picture_host()
        with mock.patch.object(host_module, "_ns_window_number", return_value=77):
            host._start_frames()
        calls = {name: args for name, args in service.web.calls}
        self.assertEqual(calls["start_screencast"], (1280, 720))
        self.assertEqual(calls["window"], (True, 1440, 0, 1920, 1080, 0, 77))
        self.assertEqual(calls["refresh"], (120,))
        self.assertFalse(app.video_window.idle)
        host._picture_timer.stop()

    def test_the_picture_window_is_not_resent_unless_the_audience_window_moved(self):
        app, service, host = self._picture_host()
        with mock.patch.object(host_module, "_ns_window_number", return_value=77):
            host._start_frames()
            before = len([c for c in service.web.calls if c[0] == "window"])
            host._place_picture()
            self.assertEqual(len([c for c in service.web.calls if c[0] == "window"]), before)
            host._picture_placed = None
            host._place_picture()
            self.assertEqual(len([c for c in service.web.calls if c[0] == "window"]), before + 1)
        host._picture_timer.stop()

    def test_a_hidden_audience_window_is_shown_first_so_the_picture_has_a_home(self):
        app, service, host = self._picture_host()
        app._vw_state["visible"] = False
        with mock.patch.object(host_module, "_ns_window_number", return_value=77):
            host._start_frames()
        self.assertEqual(app._vw_state["shown"], 1)
        self.assertEqual([a for n, a in service.web.calls if n == "window"][-1][0], True)
        host._picture_timer.stop()

    def test_the_picture_window_is_hidden_while_the_audience_window_is_hidden_or_minimised_and_returns_with_it(self):
        app, service, host = self._picture_host()
        with mock.patch.object(host_module, "_ns_window_number", return_value=77):
            host._start_frames()
            app._vw_state["minimized"] = True
            host._place_picture()
            self.assertEqual([a for n, a in service.web.calls if n == "window"][-1], (False,))
            count = len([c for c in service.web.calls if c[0] == "window"])
            host._place_picture()                       # still hidden: nothing more is sent
            self.assertEqual(len([c for c in service.web.calls if c[0] == "window"]), count)
            app._vw_state["minimized"] = False
            host._place_picture()
            self.assertEqual([a for n, a in service.web.calls if n == "window"][-1][0], True)
        host._picture_timer.stop()

    def test_the_host_preview_pane_gets_the_same_picture_in_its_own_window_over_the_main_window(self):
        app, service, host = self._picture_host()
        numbers = {id(app): 11, id(app.video_window): 77}
        with mock.patch.object(host_module, "_ns_window_number", side_effect=lambda w: numbers[id(w)]):
            host._start_frames()
            windows = [(a, k) for (n, a), k in zip(service.web.calls, service.web.kwcalls) if n == "window"]
        previews = [a for a, k in windows if k.get("id") == "preview"]
        self.assertEqual(previews[-1], (True, 1410, 160, 528, 298, 0, 11))      # over the main window (number 11), over the pane
        audience = [a for a, k in windows if k.get("id") is None]
        self.assertEqual(audience[-1][-1], 77)                                  # the audience picture still sits over the audience window
        host._picture_timer.stop()

    def test_the_preview_picture_hides_when_the_pane_is_not_on_screen_and_returns(self):
        app, service, host = self._picture_host()
        with mock.patch.object(host_module, "_ns_window_number", return_value=5):
            host._start_frames()
            app._preview_state["visible"] = False                               # another tab
            host._place_picture()
            last = [(a, k) for (n, a), k in zip(service.web.calls, service.web.kwcalls) if n == "window" and k.get("id") == "preview"][-1]
            self.assertEqual(last[0], (False,))
            app._preview_state["visible"] = True
            host._place_picture()
            last = [(a, k) for (n, a), k in zip(service.web.calls, service.web.kwcalls) if n == "window" and k.get("id") == "preview"][-1]
            self.assertEqual(last[0][0], True)
        host._picture_timer.stop()

    def test_stopping_hides_the_picture_window_and_restores_the_slideshow(self):
        app, service, host = self._picture_host()
        with mock.patch.object(host_module, "_ns_window_number", return_value=77):
            host._start_frames()
        host._stop_frames()
        names = [name for name, _ in service.web.calls]
        self.assertIn("stop_screencast", names)
        self.assertEqual([a for n, a in service.web.calls if n == "window"][-1][0], False)
        self.assertTrue(app.video_window.idle)

    def test_the_players_sound_follows_singwss_own_output_not_the_system_default(self):
        app, entry, service, host = make()
        app._safe_local_audio_output_name = lambda: "MacBook Pro Speakers"
        host.service = service
        host._route_sound()
        self.assertIn(("output", ("MacBook Pro Speakers",)), service.web.calls)

    def test_a_missing_chromium_asks_first_and_downloads_only_on_yes(self):
        from PyQt6.QtWidgets import QMessageBox
        app, entry, service, host = make(state="starting")
        host.start(entry, 0, 100)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as ask:
            host._service_state("needs_browser", "needs Chromium")
        ask.assert_called_once()
        self.assertEqual(service.downloads, 1)
        self.assertFalse(host._browser_declined)

    def test_declining_the_chromium_download_fails_the_song_and_is_not_asked_again(self):
        from PyQt6.QtWidgets import QMessageBox
        app, entry, service, host = make(state="starting")
        host.start(entry, 0, 100)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as ask:
            host._service_state("needs_browser", "needs Chromium")
            self.assertEqual(getattr(service, "downloads", 0), 0)
            self.assertTrue(host._browser_declined)
            host._service_state("needs_browser", "needs Chromium")
        ask.assert_called_once()

    def test_start_while_the_player_is_still_starting_plays_when_it_is_ready(self):
        app, entry, service, host = make(state="starting")
        host.start(entry, 0, 100)
        self.assertEqual(service.web.calls, [])
        service.state = "ready"
        host._service_state("ready", "Signed in")
        self.assertIn(("play", (3241559, "NOFX Linoleum", ["NOFX Linoleum", "Linoleum NOFX", "Linoleum", "NOFX"], "NOFX", "Linoleum")), service.web.calls)

    def test_signing_in_waits_then_plays_when_ready(self):
        app, entry, service, host = make(state="sign_in")
        host.start(entry, 0, 100)
        self.assertEqual(service.web.calls, [])
        self.assertIsNotNone(host.asked)
        service.state = "ready"
        host._service_state("ready", "Signed in")
        self.assertIn(("play", (3241559, "NOFX Linoleum", ["NOFX Linoleum", "Linoleum NOFX", "Linoleum", "NOFX"], "NOFX", "Linoleum")), service.web.calls)

    def test_never_signing_in_fails_with_the_sign_in_message(self):
        app, entry, service, host = make(state="sign_in")
        host._arm_watchdog = KaraFunWebHost._arm_watchdog.__get__(host)
        with mock.patch.object(host_module.time, "monotonic", return_value=1000.0):
            host.start(entry, 0, 100)
        host.asked["at"] = 0.0
        host.asked["heard"] = False
        # the watchdog's check, run once the limit has passed
        from PyQt6.QtCore import QTimer
        with mock.patch.object(QTimer, "singleShot", side_effect=lambda ms, fn: fn()):
            with mock.patch.object(host_module.time, "monotonic", return_value=1000.0):
                host._arm_watchdog()
        self.assertEqual(app.statuses[-1][0], "failed")
        self.assertIn("Press Stop in SingWS to return the song to the queue.", app.notes[-1][1])

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

    def test_a_song_that_ends_by_itself_still_stops_the_player_and_the_picture(self):
        # _complete clears `asked` BEFORE the app's finish calls on_finish: the picture stream must still be stopped (the audience kept KaraFun's page).
        app, entry, service, host = self._playing()
        service.web.calls.clear()
        host._handle(status(playing=False, position=129.5, duration=130.0))      # natural end -> _complete -> asked is None
        self.assertIsNone(host.asked)
        self.assertEqual(host.on_finish(), True)
        self.assertIn(("stop", ()), service.web.calls)
        host._stop_frames.assert_called()
        self.assertEqual(host.on_finish(), False)                                # a second finish does nothing

    def test_the_browser_dying_mid_song_restarts_the_player_and_resumes_the_song(self):
        app, entry, service, host = self._playing()
        host._handle(status(position=95.0, duration=130.0))                    # playing, 95 s in
        entry["karafun_key_current"], entry["karafun_tempo_current"] = -2, 110   # the operator changed them live
        service.web.calls.clear()
        service.state = "error"
        host._service_state("error", "The player's browser stopped")
        self.assertEqual((service.stopped, service.started), (1, 1))            # restarted, once
        self.assertEqual(app.statuses[-1], ("launching", "Restarting KaraFun's player..."))
        self.assertIn("restarting it", app.notes[-1][1])
        self.assertIsNotNone(host.asked)                                         # the SAME song is still open: nothing was finished or failed
        self.assertEqual(app.finished, [])
        # the new browser comes up and is ready: the song is played again...
        service.state = "ready"
        host._service_state("ready", "Signed in")
        self.assertIn(("play", (3241559, "NOFX Linoleum", ["NOFX Linoleum", "Linoleum NOFX", "Linoleum", "NOFX"], "NOFX", "Linoleum")), service.web.calls)
        # ... and when it plays, it is put back where it was, with the key and tempo it had
        service.web.calls.clear()
        host._handle(status(position=0.5, duration=130.0))
        self.assertIn(("seek", (95.0,)), service.web.calls)
        self.assertIn(("set_key", (-2,)), service.web.calls)
        self.assertIn(("set_tempo", (110,)), service.web.calls)

    def test_it_gives_up_after_three_restarts(self):
        app, entry, service, host = self._playing()
        for n in range(3):
            service.state = "error"
            host._service_state("error", "x")
            service.state = "ready"
            host._service_state("ready", "ok")
            host._handle(status(position=10.0))
        self.assertEqual(service.started, 3)
        service.state = "error"
        host._service_state("error", "x")
        self.assertEqual(service.started, 3)                                    # no fourth restart
        self.assertEqual(app.statuses[-1][0], "failed")

    def test_an_error_before_the_song_started_still_fails_it_as_before(self):
        app, entry, service, host = make(state="starting")
        host.start(entry, 0, 100)
        service.state = "error"
        host._service_state("error", "no browser")
        self.assertEqual(app.statuses[-1][0], "failed")
        self.assertEqual((service.stopped, service.started), (0, 0))

    def test_a_song_whose_playback_state_lags_still_starts_when_its_position_moves(self):
        # the failure seen live: the page was playing the song but its playback state never said so, so the app never faded the music or showed the picture
        app, entry, service, host = make()
        host.start(entry, 0, 100)
        host._handle({"type": "status", "playing": False, "moving": True, "state": 2, "position": 1.5, "duration": 130.0,
                      "song": {"id": 3241559, "title": "Linoleum", "artist": "NOFX"}})
        self.assertEqual(app.statuses[-1], ("playing", "Playing in KaraFun"))
        self.assertEqual(app.faded, [1])
        host._start_frames.assert_called_once()

    def test_the_last_chance_check_never_fails_a_song_that_is_sounding(self):
        app, entry, service, host = make()
        host._arm_watchdog = KaraFunWebHost._arm_watchdog.__get__(host)
        host.start(entry, 0, 100)
        host.status = {"playing": True, "moving": False, "state": 3, "position": 8.0, "duration": 130.0, "song": {"id": 3241559}}
        host.asked["at"] = 0.0                                                   # long overdue
        from PyQt6.QtCore import QTimer
        with mock.patch.object(QTimer, "singleShot", side_effect=lambda ms, fn: fn()):
            host._arm_watchdog()
        self.assertEqual(app.statuses[-1][0], "playing")                         # started, not failed
        self.assertNotIn("failed", [s[0] for s in app.statuses])

    def test_a_song_that_really_never_plays_is_failed_with_the_last_status_and_the_player_stopped(self):
        app, entry, service, host = make()
        host._arm_watchdog = KaraFunWebHost._arm_watchdog.__get__(host)
        logs = []
        host._diag_fn = logs.append
        host.start(entry, 0, 100)
        host.status = {"playing": False, "moving": False, "state": 0, "position": 0, "duration": 0, "song": None}
        host.asked["at"] = 0.0
        from PyQt6.QtCore import QTimer
        with mock.patch.object(QTimer, "singleShot", side_effect=lambda ms, fn: fn()):
            host._arm_watchdog()
        self.assertEqual(app.statuses[-1][0], "failed")
        self.assertIn(("stop", ()), service.web.calls)
        self.assertTrue(any("last status: playing=False" in line for line in logs))

    def test_audio_nobody_asked_for_is_stopped(self):
        app, entry, service, host = make()
        host.service = service                         # the player is up (started earlier), no song has been asked for
        logs = []
        host._diag_fn = logs.append
        host._handle({"type": "status", "playing": True, "moving": True, "state": 3, "position": 5.0, "song": {"id": 999}})
        self.assertIn(("stop", ()), service.web.calls)
        service.web.calls.clear()
        host._handle({"type": "status", "playing": True, "moving": True, "state": 3, "position": 6.0, "song": {"id": 999}})
        self.assertEqual(service.web.calls, [])                                  # not hammered: once per few seconds

    def test_a_song_that_SingWS_started_is_never_treated_as_stray(self):
        app, entry, service, host = self._playing()
        service.web.calls.clear()
        host._handle(status(position=20.0))
        self.assertNotIn(("stop", ()), service.web.calls)

    def test_finish_without_a_web_song_says_so(self):
        app, entry, service, host = make()
        self.assertEqual(host.on_finish(), False)
        host._stop_frames.assert_not_called()

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
    def test_the_old_kafun_app_path_and_its_settings_are_gone(self):
        for gone in ("_automate_karafun_search_and_play", "_start_karafun_completion_monitor", "_run_karafun_applescript_sync", "_handoff_show_screen_to_karafun"):
            self.assertNotIn(gone, SOURCE, gone)
        # the two retired settings are named ONCE, in the list that removes them from saved profiles
        self.assertEqual(SOURCE.count('"karafun_web_player"'), 1)
        self.assertEqual(SOURCE.count('"karafun_auto_queue_enabled"'), 1)

    def test_every_control_hook_checks_the_web_player_first(self):
        for name, marker in (("def _toggle_karafun_playback(self):", "_karafun_web_active"),
                             ("def _karafun_seek_to(self, target_sec: float) -> bool:", "_karafun_web_active"),
                             ("def _karafun_apply_key_tempo(self, entry: dict, target_key: int, target_tempo: int):", "_karafun_web_host().apply_key_tempo")):
            body = SOURCE[SOURCE.index(name):][:2200]
            self.assertIn(marker, body, name)

    def test_finish_and_shutdown_and_start_are_hooked(self):
        finish = SOURCE[SOURCE.index("def _finish_external_karafun_playback"):][:1500]
        self.assertIn("_web.on_finish()", finish)
        whole = SOURCE[SOURCE.index("def _finish_external_karafun_playback"):][:7000]
        self.assertIn('self._show_idle_background_after_karaoke(reason="karafun_web_end", advance_slideshow=True)', whole)
        self.assertIn("self._karafun_web_shutdown()", SOURCE)
        start = SOURCE[SOURCE.index("def _start_external_karafun_playback"):][:9000]
        self.assertIn("self._karafun_web_host().start(", start)
        self.assertNotIn("automatic_playback", start)       # no second way to start a KaraFun song


if __name__ == "__main__":
    unittest.main()
