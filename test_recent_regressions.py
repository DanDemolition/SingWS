import importlib.util
import inspect
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
import zipfile
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


class FakeStatusLabel:
    def __init__(self):
        self._text = ""
        self.styles = []

    def setText(self, text):
        self._text = str(text or "")

    def text(self):
        return self._text

    def setStyleSheet(self, style):
        self.styles.append(style)


class FakeSingleShotTimer:
    def __init__(self):
        self.started = []
        self.stopped = 0

    def stop(self):
        self.stopped += 1

    def start(self, delay):
        self.started.append(delay)


class RecentRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()
        with open("0.2.18.1.py", "r", encoding="utf-8") as fh:
            cls.singws_source = fh.read()

    def test_defaults_keep_simple_audio_and_ticker_speed(self):
        self.assertTrue(self.singws.DEFAULTS["simple_audio_mode"])
        self.assertIn("ticker_speed_px_per_sec", self.singws.DEFAULTS)
        self.assertGreater(float(self.singws.DEFAULTS["ticker_speed_px_per_sec"]), 0)
        self.assertEqual(int(self.singws.DEFAULTS["video_timing_offset_ms"]), 0)
        self.assertFalse(self.singws.DEFAULTS["next_up_overlay_enabled"])
        self.assertEqual(int(self.singws.DEFAULTS["next_up_overlay_duration_sec"]), 10)
        self.assertIn("lyrics_background_video_opacity", self.singws.DEFAULTS)
        self.assertIn("crash_log_email_to", self.singws.DEFAULTS)

    def test_widget_surfaces_are_double_buffered_by_default_on_macos(self):
        """Single buffering is opt-in; it corrupted the operator window.

        Forcing SwapBehavior::SingleBuffer made the crashing blitBuffer call
        site unreachable, but it also lets the window server read the raster
        surface mid-paint. On 2026-08-09 that showed as torn and stale widgets
        on every build carrying it (0.4.3.9, 0.4.4.0) and on none without it
        (0.4.3.6). Qt's default now wins unless SINGWS_WIDGET_SWAP=single.
        """
        from PyQt6.QtGui import QSurfaceFormat

        original = QSurfaceFormat.defaultFormat()
        try:
            with mock.patch.object(self.singws.sys, "platform", "darwin"), \
                 mock.patch.dict(os.environ, {"SINGWS_WIDGET_SWAP": ""}):
                # Default: Qt keeps its swap chain, so widgets paint cleanly.
                self.assertFalse(self.singws._install_single_buffered_widget_surfaces())
                self.assertNotEqual(
                    QSurfaceFormat.defaultFormat().swapBehavior(),
                    QSurfaceFormat.SwapBehavior.SingleBuffer,
                )

            # The crash workaround is still reachable without a rebuild.
            QSurfaceFormat.setDefaultFormat(original)
            with mock.patch.object(self.singws.sys, "platform", "darwin"), \
                 mock.patch.dict(os.environ, {"SINGWS_WIDGET_SWAP": "single"}):
                self.assertTrue(self.singws._install_single_buffered_widget_surfaces())
                self.assertEqual(
                    QSurfaceFormat.defaultFormat().swapBehavior(),
                    QSurfaceFormat.SwapBehavior.SingleBuffer,
                )
        finally:
            QSurfaceFormat.setDefaultFormat(original)

        # Applied before the QApplication: the format is read when each platform
        # window is created, and the first ones exist during construction.
        main = self.singws_source
        self.assertLess(
            main.index("_install_single_buffered_widget_surfaces()\n\n    app = QApplication(["),
            main.index("app.setApplicationName(\"SingWS\")"),
        )

    def test_quick_views_keep_double_buffering(self):
        """The scene graph is the one surface where single buffering would show."""
        from PyQt6.QtGui import QSurfaceFormat

        class FakeView:
            def __init__(self):
                self._fmt = QSurfaceFormat()

            def format(self):
                return QSurfaceFormat(self._fmt)

            def setFormat(self, fmt):
                self._fmt = fmt

        view = FakeView()
        self.singws._apply_double_buffered_quick_format(view)
        self.assertEqual(view._fmt.swapBehavior(), QSurfaceFormat.SwapBehavior.DoubleBuffer)

        # Every QQuickView must get it, and before it is shown.
        main = self.singws_source
        self.assertEqual(main.count("self._view = QQuickView()"), 4)
        self.assertEqual(
            main.count("self._view = QQuickView()\n        _apply_double_buffered_quick_format(self._view)"),
            4,
        )

    def test_quick_child_surfaces_follow_the_setting_not_the_architecture(self):
        """Intel Macs get the animated ticker and transitions again.

        The 2026-08-01 Intel exclusion was a misattribution: the crash it was
        meant to stop happened again on 2026-08-09 in a session with no Quick
        surfaces running at all. "auto" now means on everywhere; only an
        explicit Off (setting or env) turns the Quick surfaces back off.
        """
        intel = lambda: (  # noqa: E731 - three patches, used twice below
            mock.patch.object(self.singws.sys, "platform", "darwin"),
            mock.patch.object(self.singws.platform, "machine", return_value="x86_64"),
            mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "", "SINGWS_QUICK_SURFACES": ""}),
        )
        try:
            for mode in ("auto", "on"):
                self.singws.set_quick_surfaces_override(mode)
                with intel()[0], intel()[1], intel()[2]:
                    self.assertTrue(
                        self.singws._native_quick_child_surfaces_supported(), mode
                    )
                    self.assertTrue(self.singws._rotation_quick_surfaces_supported(), mode)

            self.singws.set_quick_surfaces_override("off")
            with intel()[0], intel()[1], intel()[2]:
                self.assertFalse(self.singws._native_quick_child_surfaces_supported())
                self.assertFalse(self.singws._rotation_quick_surfaces_supported())

            # The env override still wins over the setting, for a one-off test.
            self.singws.set_quick_surfaces_override("on")
            with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "", "SINGWS_QUICK_SURFACES": "0"}):
                self.assertFalse(self.singws._native_quick_child_surfaces_supported())
        finally:
            self.singws.set_quick_surfaces_override("auto")

        video_init = inspect.getsource(self.singws.VideoWindow.__init__)
        show_vfx = inspect.getsource(self.singws.VideoWindow._attach_show_vfx)
        self.assertIn("_native_quick_child_surfaces_supported()", video_init)
        self.assertIn("_native_quick_child_surfaces_supported()", show_vfx)

    def test_ticker_effects_checkbox_is_live_after_switching_surfaces_on(self):
        """A pending on-at-next-launch state must not grey the control out.

        Otherwise turning GPU surfaces on disables the very checkbox it just
        enabled, which reads as the animated ticker being broken.
        """
        app = SimpleNamespace(video_window=None)
        supported = self.singws.KaraokeApp._ticker_effects_supported
        pending = self.singws.KaraokeApp._ticker_effects_pending_relaunch
        try:
            self.singws.set_quick_surfaces_override("on")
            with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "", "SINGWS_QUICK_SURFACES": ""}):
                self.assertTrue(supported(app))
                self.assertTrue(pending(app))

            self.singws.set_quick_surfaces_override("off")
            with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "", "SINGWS_QUICK_SURFACES": ""}):
                self.assertFalse(supported(app))
                self.assertFalse(pending(app))
        finally:
            self.singws.set_quick_surfaces_override("auto")

        # A live Quick ticker is authoritative and needs no relaunch note.
        live = SimpleNamespace(
            video_window=SimpleNamespace(
                ticker=SimpleNamespace(set_effects_enabled=lambda _enabled: None)
            )
        )
        self.assertTrue(supported(live))
        self.assertFalse(pending(live))

    def test_rotation_summary_uses_index_instead_of_full_library_scan(self):
        source = inspect.getsource(self.singws.KaraokeApp._update_rotation_summary_card)
        self.assertIn("song_index.find_by_path(current_path)", source)
        self.assertNotIn('for t in (getattr(self, "tracks", []) or [])', source)

    def test_polling_restart_waits_asynchronously_for_retirement(self):
        app = SimpleNamespace()
        app.settings = {"base_url": "https://wskar.com", "user": "wsk"}
        calls = []
        app._app_closing = False
        app.is_network_configured = lambda: True
        app._stop_request_polling_async = lambda callback: (calls.append("retire"), callback())
        app.start_request_polling = lambda **kwargs: calls.append(("start", kwargs))
        self.singws.KaraokeApp.restart_request_polling(app)
        self.assertEqual(calls, ["retire", ("start", {"stop_existing": False})])

    def test_venue_profiles_capture_signup_server_settings(self):
        app = SimpleNamespace(settings={
            "base_url": "https://requests.example",
            "user": "downtown",
            "tenant": "downtown",
            "api_key": "venue-secret",
            "header_qr_url": "https://requests.example/downtown",
            "host_controls_pin": "2468",
            "requests_accepting": False,
            "use_waiting_for_add": True,
            "show_request_qr": True,
        })
        app.VENUE_SCOPED_SETTINGS = self.singws.KaraokeApp.VENUE_SCOPED_SETTINGS

        captured = self.singws.KaraokeApp._capture_venue_settings(app)

        for key, value in app.settings.items():
            self.assertIn(key, captured)
            self.assertEqual(captured[key], value)

    def test_venue_live_refresh_avoids_forced_background_fade_and_rebinds_network(self):
        calls = []
        app = SimpleNamespace(
            _apply_runtime_media_settings=lambda: calls.append("media"),
            _update_audio_output_button=lambda: calls.append("audio"),
            schedule_ticker_update=lambda: calls.append("ticker"),
            _apply_idle_background=lambda **kwargs: calls.append(("background", kwargs)),
            _refresh_header_status=lambda: calls.append("status"),
            _update_header_qr_widget=lambda: calls.append("header_qr"),
            _refresh_show_screen_qr=lambda *args, **kwargs: calls.append(("show_qr", args, kwargs)),
            restart_request_polling=lambda: calls.append("polling"),
            _sync_session_location_for_venue=lambda: calls.append("location"),
            _apply_eq_settings_live=lambda reason: calls.append(("eq", reason)),
        )

        self.singws.KaraokeApp._apply_venue_settings_live(app, "Downtown")

        self.assertIn(("background", {"force": False, "advance_slideshow": False}), calls)
        self.assertIn("polling", calls)
        self.assertIn(("show_qr", ("venue_switch",), {"force": True}), calls)
        self.assertIn(("eq", "venue_switch"), calls)

    def test_ui_uses_the_system_arrow_cursor(self):
        """No web-style hand cursors: macOS reserves the pointing hand for links.

        Native controls keep the arrow, and Qt cursors inherit to children with
        nothing in this file ever resetting one, so a single stray call spreads
        further than its widget.
        """
        self.assertNotIn("PointingHandCursor", self.singws_source)

    def test_venue_profiles_scope_eq_and_ticker_settings(self):
        # A room's EQ curve and its ticker branding both belong to the venue,
        # not the laptop.
        scoped = set(self.singws.KaraokeApp.VENUE_SCOPED_SETTINGS)
        for key in ("eq_karaoke", "eq_karaoke_enabled", "eq_bgm", "eq_bgm_enabled"):
            self.assertIn(key, scoped, f"{key} must follow the venue")
        for key in ("ticker_enabled", "ticker_custom_enabled", "ticker_custom_message",
                    "ticker_color", "ticker_speed_px_per_sec", "ticker_size_index",
                    "ticker_bold", "ticker_vfx_enabled"):
            self.assertIn(key, scoped, f"{key} must follow the venue")

        # Round-trip: capture under one venue, change, switch back, get it back.
        app = SimpleNamespace(settings={
            "eq_karaoke": [3.0, -2.0, 0.0],
            "eq_karaoke_enabled": True,
            "eq_bgm": [1.5, 1.5],
            "eq_bgm_enabled": False,
            "ticker_custom_message": "Downtown Bar",
            "ticker_vfx_enabled": True,
        })
        app.VENUE_SCOPED_SETTINGS = self.singws.KaraokeApp.VENUE_SCOPED_SETTINGS
        captured = self.singws.KaraokeApp._capture_venue_settings(app)
        self.assertEqual(captured["eq_karaoke"], [3.0, -2.0, 0.0])
        self.assertEqual(captured["eq_karaoke_enabled"], True)
        self.assertEqual(captured["ticker_custom_message"], "Downtown Bar")
        self.assertEqual(captured["ticker_vfx_enabled"], True)

    def test_venue_eq_switch_moves_the_live_audio_path(self):
        # Writing the settings is not enough: the previous room's curve would
        # keep playing until the next restart.
        pushed = []

        class _EQ:
            def __init__(self):
                self.gains = None
                self.enabled = None

            def set_all_gains_db(self, g):
                self.gains = list(g)

            def set_enabled(self, on):
                self.enabled = bool(on)

        karaoke_eq, bgm_eq = _EQ(), _EQ()
        bass = SimpleNamespace(set_eq=lambda eq: pushed.append(("bass_eq", eq is bgm_eq)))
        app = SimpleNamespace(
            settings={
                "eq_karaoke": [4.0, 0.0, -1.0],
                "eq_karaoke_enabled": True,
                "eq_bgm": [2.0, -2.0],
                "eq_bgm_enabled": True,
            },
            karaoke_eq=karaoke_eq,
            bgm_eq=bgm_eq,
            bg_music=SimpleNamespace(_bass_engine=bass),
            _simple_audio_mode=lambda: False,
            _push_mpv_audio_processing=lambda reason: pushed.append(("mpv", reason)),
            _log_bgm_eq_route=lambda reason: pushed.append(("bgm_route", reason)),
        )

        self.singws.KaraokeApp._apply_eq_settings_live(app, "venue_switch")

        self.assertEqual(karaoke_eq.gains, [4.0, 0.0, -1.0])
        self.assertTrue(karaoke_eq.enabled)
        self.assertEqual(bgm_eq.gains, [2.0, -2.0])
        self.assertTrue(bgm_eq.enabled)
        # The karaoke chain must be rebuilt; mpv has no EQ object to re-point.
        self.assertIn(("mpv", "venue_eq:venue_switch"), pushed)
        self.assertIn(("bass_eq", True), pushed)

    def test_venue_eq_switch_does_not_build_engines(self):
        # _ensure_eq_engines imports scipy/numpy; a venue switch must not drag
        # that in for operators who never open the EQ.
        app = SimpleNamespace(
            settings={"eq_karaoke": [1.0], "eq_karaoke_enabled": True},
            karaoke_eq=None,
            bgm_eq=None,
            _ensure_eq_engines=lambda: self.fail("venue switch must not build EQ engines"),
        )
        self.singws.KaraokeApp._apply_eq_settings_live(app, "venue_switch")
        self.assertIsNone(app.karaoke_eq)

    def test_venue_eq_respects_simple_audio_mode(self):
        # Same rule as the EQ dialog: Simple Audio Mode keeps BGM EQ out of the
        # chain entirely.
        pushed = []

        class _EQ:
            def set_all_gains_db(self, g):
                pass

            def set_enabled(self, on):
                pass

        bgm_eq = _EQ()
        app = SimpleNamespace(
            settings={"eq_bgm": [1.0], "eq_bgm_enabled": True},
            karaoke_eq=None,
            bgm_eq=bgm_eq,
            bg_music=SimpleNamespace(
                _bass_engine=SimpleNamespace(set_eq=lambda eq: pushed.append(eq))
            ),
            _simple_audio_mode=lambda: True,
            _log_bgm_eq_route=lambda reason: None,
        )
        self.singws.KaraokeApp._apply_eq_settings_live(app, "venue_switch")
        self.assertEqual(pushed, [None], "Simple Audio Mode must detach the BGM EQ")

    def test_filesystem_probe_timeout_keeps_gui_path_bounded(self):
        started = time.monotonic()
        result = self.singws._bounded_filesystem_call(
            lambda: time.sleep(2.0) or True,
            default=False,
            timeout_sec=0.03,
            label="regression blocked provider",
        )
        elapsed = time.monotonic() - started

        self.assertFalse(result)
        self.assertLess(elapsed, 0.25)

    def test_background_video_settings_do_not_enumerate_folder_on_button_click(self):
        source = inspect.getsource(self.singws.KaraokeApp.configure_settings)
        refresh_source = source.split("def _refresh_bg_video_folder_label():", 1)[1].split(
            "bg_video_folder_row.addWidget", 1
        )[0]
        choose_source = source.split("def on_choose_bg_video_folder():", 1)[1].split(
            "bg_video_folder_btn.clicked.connect", 1
        )[0]
        self.assertNotIn("scan_background_video_folder", refresh_source)
        self.assertNotIn("scan_background_video_folder", choose_source)

    def test_log_package_redacts_credentials_and_skips_old_files(self):
        with tempfile.TemporaryDirectory() as td:
            old_logs_dir = self.singws.LOGS_DIR
            self.singws.LOGS_DIR = Path(td)
            try:
                recent = Path(td) / "singws_recent.log"
                recent.write_text("api_key=secret token=abc password: hunter2 ok", encoding="utf-8")
                old = Path(td) / "singws_old.log"
                old.write_text("old secret", encoding="utf-8")
                old_time = time.time() - (5 * 86400)
                old.touch()
                import os
                os.utime(old, (old_time, old_time))

                package, files, error = self.singws.prepare_log_email_package(days=3)
                self.assertEqual(error, "")
                self.assertIsNotNone(package)
                self.assertEqual([p.name for p in files], ["singws_recent.log"])
                with zipfile.ZipFile(package, "r") as zf:
                    text = zf.read("singws_recent.log").decode("utf-8")
                self.assertNotIn("secret", text)
                self.assertNotIn("hunter2", text)
                self.assertIn("api_key=***", text)
                self.assertIn("token=***", text)
                self.assertIn("password: ***", text)
            finally:
                self.singws.LOGS_DIR = old_logs_dir

    def test_rotation_data_omits_empty_singer_and_keeps_active_numbers_contiguous(self):
        app = make_app(self.singws)
        app._first_active_entry_for_singer = lambda singer: next((s for s in singer.get("songs", []) if not s.get("skipped", False)), None)
        app._queue_singer_display_for_entry = lambda singer, entry: singer.get("name", "")
        app.queue = [
            {"name": "Dan", "songs": [{"song_info": "/tmp/a.mp3", "skipped": False}]},
            {"name": "Steve", "songs": []},
            {"name": "Bill", "songs": [{"song_info": "/tmp/b.mp3", "skipped": False}]},
        ]

        rotation = self.singws.KaraokeApp.get_rotation_data(app)["rotation"]
        self.assertEqual([row["name"] for row in rotation], ["Dan", "Bill"])
        self.assertEqual([row["number"] for row in rotation], [1, 2])
        self.assertEqual([row["rotation_position"] for row in rotation], [1, 2])
        self.assertTrue(all(row["active"] for row in rotation))

    def test_server_rotation_uses_karafun_song_title_not_provider_id(self):
        app = make_app(self.singws)
        entry = {
            "song_info": "karafun_streaming:kf_7271",
            "path": "karafun_streaming:kf_7271",
            "provider": "karafun_streaming",
            "provider_track_id": "kf_7271",
            "artist": "Bruno Mars",
            "title": "Risk It All",
            "display_name": "Bruno Mars - Risk It All - KaraFun",
            "skipped": False,
        }
        app.queue = [{"name": "Daniel", "songs": [entry]}]
        app._first_active_entry_for_singer = lambda singer: singer["songs"][0]
        app._queue_singer_display_for_entry = lambda singer, _entry: singer["name"]

        rotation = app.get_rotation_data()["rotation"]

        self.assertEqual(rotation[0]["songs"], ["Bruno Mars • Risk It All"])
        self.assertNotIn("kf_7271", rotation[0]["songs"][0])

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

    def test_next_up_overlay_payload_uses_next_song_and_on_deck(self):
        app = make_app(self.singws)
        app._current_karaoke_singer_name = "Ada"
        app._current_karaoke_song_path = "/tmp/current.mp3"
        app.queue = [
            {
                "name": "Ada",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/current.mp3", "artist": "Artist", "title": "Current", "skipped": False},
                    {"song_info": "/tmp/next.mp3", "artist": "Artist", "title": "Next", "skipped": False},
                ],
            },
            {
                "name": "Bo",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/bo.mp3", "artist": "Other", "title": "On Deck", "skipped": False},
                ],
            },
        ]

        payload = app._next_up_transition_payload_from_queue()
        self.assertEqual(payload["singer"], "Ada")
        self.assertEqual(payload["title"], "Next")
        self.assertEqual(payload["artist"], "Artist")
        self.assertEqual(payload["on_deck"], "Bo")

    def test_next_up_overlay_setting_gate_and_duration(self):
        app = make_app(self.singws)
        calls = []

        class FakeArea:
            def show_next_up_overlay(self, payload, duration):
                calls.append((payload, duration))

        app.video_window = SimpleNamespace(video_area=FakeArea())
        app.settings["next_up_overlay_enabled"] = True
        app.settings["next_up_overlay_duration_sec"] = 7
        payload = {"singer": "Ada", "title": "Song", "artist": "Artist", "on_deck": "Bo"}

        self.assertTrue(app._show_next_up_transition_overlay(payload, reason="test"))
        self.assertEqual(calls, [(payload, 7.0)])

        app.settings["next_up_overlay_enabled"] = False
        self.assertFalse(app._show_next_up_transition_overlay(payload, reason="test"))
        self.assertEqual(len(calls), 1)

    def test_next_up_overlay_pressing_play_does_not_show(self):
        app = make_app(self.singws)
        calls = []

        class FakeArea:
            def show_next_up_overlay(self, payload, duration):
                calls.append((payload, duration))

        app.video_window = SimpleNamespace(video_area=FakeArea())
        app.settings["next_up_overlay_enabled"] = True
        app._next_up_overlay_completion_token = 1
        app._next_up_overlay_consumed_token = 0
        app._next_up_overlay_pending_payload = {"singer": "Ada", "title": "Next", "artist": "Artist", "on_deck": ""}

        singer = {"name": "Ada"}
        entry = {"song_info": "/tmp/next.mp3", "title": "Next", "artist": "Artist"}

        self.assertFalse(
            app._consume_next_up_overlay_for_transition(
                singer,
                entry,
                title="Next",
                artist="Artist",
                reason="test_play",
            )
        )
        self.assertEqual(calls, [])
        self.assertEqual(app._next_up_overlay_consumed_token, 1)

    def test_next_up_overlay_pause_resume_seek_restart_do_not_show(self):
        app = make_app(self.singws)
        calls = []

        class FakeArea:
            def show_next_up_overlay(self, payload, duration):
                calls.append((payload, duration))

        app.video_window = SimpleNamespace(video_area=FakeArea())
        app.settings["next_up_overlay_enabled"] = True
        singer = {"name": "Ada"}
        entry = {"song_info": "/tmp/next.mp3", "title": "Next", "artist": "Artist"}
        for idx, reason in enumerate(("pause_resume", "seek", "same_song_restart"), start=1):
            app._next_up_overlay_completion_token = idx
            app._next_up_overlay_consumed_token = 0
            app._next_up_overlay_pending_payload = {"singer": "Ada", "title": "Next", "artist": "Artist", "on_deck": ""}
            self.assertFalse(
                app._consume_next_up_overlay_for_transition(
                    singer,
                    entry,
                    title="Next",
                    artist="Artist",
                    reason=reason,
                )
            )
        self.assertEqual(calls, [])

    def test_song_end_uses_short_outro_not_next_up_countdown(self):
        app = make_app(self.singws)
        calls = []

        class FakeArea:
            def show_next_up_overlay(self, payload, duration):
                calls.append(("next", payload, duration))

            def show_song_outro_vfx(self, singer, title, artist):
                calls.append(("outro", singer, title, artist))
                return True

        app.video_window = SimpleNamespace(video_area=FakeArea())
        app.settings["next_up_overlay_enabled"] = True
        app.settings["next_up_overlay_duration_sec"] = 10
        app._current_karaoke_singer_name = "Ada"
        app._current_karaoke_song_path = "/tmp/current.mp3"
        app.queue = [
            {
                "name": "Ada",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/current.mp3", "artist": "Artist", "title": "Current", "skipped": False},
                    {"song_info": "/tmp/next.mp3", "artist": "Artist", "title": "Next", "skipped": False},
                ],
            },
            {
                "name": "Bo",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/bo.mp3", "artist": "Other", "title": "On Deck", "skipped": False},
                ],
            },
        ]

        self.assertTrue(app._mark_next_up_overlay_pending_after_completion(reason="test_end"))
        self.assertEqual(calls[0][0:2], ("outro", "Ada"))
        self.assertEqual(app._next_up_overlay_pending_payload, {})

        self.assertTrue(app._mark_next_up_overlay_pending_after_completion(reason="test_end_duplicate"))
        self.assertEqual(len(calls), 2)
        self.assertNotIn("next", [call[0] for call in calls])

    def test_karafun_outro_uses_active_metadata_not_synthetic_path(self):
        app = make_app(self.singws)
        app._current_karaoke_singer_display = "Daniel"
        app._current_karaoke_song_path = "karafun_streaming:kf_7271"
        app._current_karaoke_artist = "Bruno Mars"
        app._current_karaoke_title = "Risk It All"

        payload = app._song_outro_payload_from_current()

        self.assertEqual(payload, {
            "singer": "Daniel",
            "artist": "Bruno Mars",
            "title": "Risk It All",
        })

    def test_song_outro_does_not_require_a_next_singer(self):
        app = make_app(self.singws)
        calls = []

        class FakeArea:
            def show_next_up_overlay(self, payload, duration):
                calls.append(("next", payload, duration))

            def show_song_outro_vfx(self, singer, title, artist):
                calls.append(("outro", singer, title, artist))
                return True

        app.video_window = SimpleNamespace(video_area=FakeArea())
        app.settings["next_up_overlay_enabled"] = True
        app._current_karaoke_singer_name = "Ada"
        app._current_karaoke_song_path = "/tmp/current.mp3"
        app.queue = [
            {
                "name": "Ada",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/current.mp3", "artist": "Artist", "title": "Current", "skipped": False},
                ],
            },
        ]

        self.assertTrue(app._mark_next_up_overlay_pending_after_completion(reason="test_end_no_next"))
        self.assertEqual(calls[0][0:2], ("outro", "Ada"))

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
            app.settings = {
                "bg_import_folders": [],
                "simple_audio_mode": False,
                "karaoke_normalize_enabled": True,
                "bg_normalize_enabled": True,
            }

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

    def test_processing_text_auto_dismisses_by_default(self):
        app = make_app(self.singws)
        app.processing_label = FakeStatusLabel()
        app._processing_notification_timer = FakeSingleShotTimer()
        app._processing_notification_text = ""

        self.singws.KaraokeApp._set_processing_text(app, "Import Complete")

        self.assertEqual(app.processing_label.text(), "Import Complete")
        self.assertEqual(app._processing_notification_text, "Import Complete")
        self.assertEqual(app._processing_notification_timer.started, [self.singws.PROCESSING_NOTIFICATION_TIMEOUT_MS])

        self.singws.KaraokeApp._clear_processing_notification(app)

        self.assertEqual(app.processing_label.text(), "")
        self.assertEqual(app._processing_notification_text, "")

    def test_processing_progress_can_opt_out_of_auto_dismiss(self):
        app = make_app(self.singws)
        app.processing_label = FakeStatusLabel()
        app._processing_notification_timer = FakeSingleShotTimer()
        app._processing_notification_text = "old message"

        self.singws.KaraokeApp._set_processing_text(app, "Building search index…", auto_dismiss_ms=None)

        self.assertEqual(app.processing_label.text(), "Building search index…")
        self.assertEqual(app._processing_notification_text, "")
        self.assertEqual(app._processing_notification_timer.started, [])
        self.assertEqual(app._processing_notification_timer.stopped, 1)

    def test_mp3g_queue_duration_reaches_timer_and_transport(self):
        queue_source = inspect.getsource(self.singws.KaraokeApp.play_next_file)
        zip_source = inspect.getsource(self.singws.KaraokeApp.play_mp3g_zip)
        wrapper_source = inspect.getsource(self.singws.KaraokeApp.play_cdg_mp3_dual)

        self.assertIn("self.probe_mp3g_duration(dur_path)", queue_source)
        self.assertGreaterEqual(queue_source.count("duration_seconds=current_song_dur"), 4)
        self.assertIn("duration_seconds=effective_duration", zip_source)
        self.assertIn("duration_seconds=duration_seconds", wrapper_source)

    def test_server_off_background_follows_waitlist_state(self):
        app = make_app(self.singws)
        app._is_requests_accepting_cached = lambda: bool(app.settings["requests_accepting"])
        app._is_waitlist_enabled_cached = lambda: bool(app.settings["use_waiting_for_add"])
        app._default_background_path = lambda: "/default.png"

        with tempfile.TemporaryDirectory() as folder:
            normal = os.path.join(folder, "normal.png")
            waitlist_on = os.path.join(folder, "waitlist-on.png")
            waitlist_off = os.path.join(folder, "waitlist-off.png")
            for path in (normal, waitlist_on, waitlist_off):
                Path(path).touch()
            app.settings.update({
                "background_image_path": normal,
                "background_closed_waitlist_on_image_path": waitlist_on,
                "background_closed_waitlist_off_image_path": waitlist_off,
                "background_slideshow_enabled": False,
            })

            app.settings["requests_accepting"] = True
            app.settings["use_waiting_for_add"] = True
            self.assertEqual(self.singws.KaraokeApp._resolve_idle_background_path(app), normal)

            app.settings["requests_accepting"] = False
            self.assertEqual(self.singws.KaraokeApp._resolve_idle_background_path(app), waitlist_on)

            app.settings["use_waiting_for_add"] = False
            self.assertEqual(self.singws.KaraokeApp._resolve_idle_background_path(app), waitlist_off)

    def test_waitlist_toggle_refreshes_idle_background(self):
        source = inspect.getsource(self.singws.KaraokeApp._set_waitlist_enabled_local)
        self.assertIn("self._apply_idle_background(force=False", source)


if __name__ == "__main__":
    unittest.main()
