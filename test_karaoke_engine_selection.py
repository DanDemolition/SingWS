"""Tests for default/fallback karaoke engine selection."""

import importlib.util
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")

with open("0.2.18.1.py", "r", encoding="utf-8") as _fh:
    MAIN_SOURCE = _fh.read()


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_engine_select", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FfmpegSentinel:
    pass


class EngineSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def setUp(self):
        self._orig_python = self.singws.PythonKaraokeTransport
        self._orig_engine_override = os.environ.pop("SINGWS_KARAOKE_ENGINE", None)
        self._orig_legacy_build = os.environ.pop("SINGWS_INTEL_LEGACY_BUILD", None)
        self.addCleanup(self._restore)
        self.singws.PythonKaraokeTransport = _FfmpegSentinel

    def _restore(self):
        self.singws.PythonKaraokeTransport = self._orig_python
        os.environ.pop("SINGWS_KARAOKE_ENGINE", None)
        if self._orig_engine_override is not None:
            os.environ["SINGWS_KARAOKE_ENGINE"] = self._orig_engine_override
        os.environ.pop("SINGWS_INTEL_LEGACY_BUILD", None)
        if self._orig_legacy_build is not None:
            os.environ["SINGWS_INTEL_LEGACY_BUILD"] = self._orig_legacy_build

    def _app(self, pref):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = {"karaoke_engine": pref} if pref is not None else {}
        app._karaoke_engine_session_pref = pref or ""
        return app

    def test_default_setting_is_ffmpeg(self):
        self.assertEqual(self.singws.DEFAULTS.get("karaoke_engine"), "ffmpeg")

    def test_gstreamer_symbol_is_gone(self):
        # The removal leaves GstKaraokeTransport defined-but-None so stale refs
        # degrade gracefully; it must never be a real class again.
        self.assertIsNone(self.singws.GstKaraokeTransport)

    def test_missing_setting_selects_ffmpeg(self):
        pref, cls = self._app(None)._select_karaoke_transport_cls()
        self.assertEqual(pref, "ffmpeg")
        self.assertIs(cls, _FfmpegSentinel)

    def test_explicit_and_obsolete_preferences_resolve_to_ffmpeg(self):
        # An explicit FFmpeg pick, its aliases, obsolete gstreamer/auto and
        # garbage all land on the default path.
        for pref in ("ffmpeg", "python", "qt", "FFMPEG", " ffmpeg ",
                     "gstreamer", "gst", "auto", "laserdisc", ""):
            resolved, cls = self._app(pref)._select_karaoke_transport_cls()
            self.assertEqual(resolved, "ffmpeg", pref)
            self.assertIs(cls, _FfmpegSentinel, pref)

    def test_mpv_modes_are_macos_only(self):
        for pref in ("mpv", "mpv-video"):
            resolved, cls = self._app(pref)._select_karaoke_transport_cls()
            expected = pref if sys.platform == "darwin" else "ffmpeg"
            self.assertEqual(resolved, expected, pref)
            self.assertIs(cls, _FfmpegSentinel, pref)

    def test_environment_override_enables_source_smoke_without_saving(self):
        os.environ["SINGWS_KARAOKE_ENGINE"] = "mpv"
        resolved, cls = self._app("ffmpeg")._select_karaoke_transport_cls()
        expected = "mpv" if sys.platform == "darwin" else "ffmpeg"
        self.assertEqual(resolved, expected)
        self.assertIs(cls, _FfmpegSentinel)

    def test_legacy_intel_build_always_uses_ffmpeg_signalsmith(self):
        os.environ["SINGWS_KARAOKE_ENGINE"] = "mpv"
        os.environ["SINGWS_INTEL_LEGACY_BUILD"] = "1"
        resolved, cls = self._app("mpv")._select_karaoke_transport_cls()
        self.assertEqual(resolved, "ffmpeg")
        self.assertIs(cls, _FfmpegSentinel)

    def test_engine_missing_returns_none_cls(self):
        self.singws.PythonKaraokeTransport = None
        pref, cls = self._app("ffmpeg")._select_karaoke_transport_cls()
        self.assertEqual(pref, "ffmpeg")
        self.assertIsNone(cls)

    def test_startup_banner_reports_the_real_engine(self):
        # The banner was a hardcoded "FFmpeg/Qt" string, so a session actually
        # running mpv logged FFmpeg and looked like the setting had reverted.
        self.assertNotIn('logging.info("- Karaoke engine: FFmpeg/Qt', MAIN_SOURCE)
        self.assertNotIn('logging.info("- engine: FFmpeg/Qt', MAIN_SOURCE)
        if sys.platform != "darwin":
            self.skipTest("mpv labels are macOS-only")
        import json
        import pathlib
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "settings.json"
            for pref, expected in (
                ("mpv-video", "mpv video + SingWS audio engine"),
                ("mpv", "mpv (audio and video)"),
                ("ffmpeg", "FFmpeg/Qt (GStreamer removed)"),
                ("", "FFmpeg/Qt (GStreamer removed)"),
            ):
                path.write_text(json.dumps({"karaoke_engine": pref}), encoding="utf-8")
                with mock.patch.object(self.singws, "SETTINGS_PATH", path):
                    self.assertEqual(
                        self.singws._configured_karaoke_engine_label(), expected, pref
                    )

    def test_mpv_stays_opt_in(self):
        # The offset does now reach the in-process backend (see
        # test_iina_plugin_maps_the_offset_onto_audio_delay), but the
        # follower-based backend still cannot apply it, and mpv has not yet run
        # a full show. Until it has, nothing may make mpv the default or
        # migrate saved settings onto it.
        self.assertEqual(self.singws.DEFAULTS.get("karaoke_engine"), "ffmpeg")
        self.assertNotIn("mpv_default_engine_migrated", MAIN_SOURCE)
        self.assertNotIn('self.settings["karaoke_engine"] = "mpv-video"', MAIN_SOURCE)
        with open("mpv_karaoke_transport.py", "r", encoding="utf-8") as fh:
            transport_source = fh.read()
        self.assertIn("def set_video_offset_ms", transport_source)

    def test_engine_chooser_stays_in_settings(self):
        # Removing the chooser once left a bad show with no way back to the
        # proven engine. The escape hatch must exist, and each checkbox must
        # read the saved value by testing for its own engine string.
        self.assertIn('mpv_engine_cb = QCheckBox("Use the mpv video engine")', MAIN_SOURCE)
        self.assertIn(
            'mpv_engine_cb.setChecked(saved_engine in ("mpv", "mpv-video"))', MAIN_SOURCE
        )
        self.assertIn('mpv_keep_audio_cb.setChecked(saved_engine == "mpv-video")', MAIN_SOURCE)
        self.assertIn('engine = "ffmpeg"', MAIN_SOURCE)



class MpvBackendSelectionTests(unittest.TestCase):
    """A build ships exactly one mpv backend. Picking the wrong one -- or
    hard-coding either -- makes mpv unavailable and silently drops every song
    onto the FFmpeg engine with the bundled media stack unused."""

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_prefers_iina_only_when_its_bridge_is_present(self):
        import pathlib
        import tempfile
        import types
        from unittest import mock

        fake = types.ModuleType("mpv_playback_iina")
        fake.MpvPlaybackPlugin = object

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            fake._runtime_root = lambda: root
            with mock.patch.dict(sys.modules, {"mpv_playback_iina": fake}):
                # No bridge beside the module -> must fall back.
                _, name = self.singws.KaraokeApp._load_mpv_playback_backend()
                self.assertEqual(name, "homebrew")

                # Bridge present -> IINA is the shipped stack.
                (root / "libsingws_mpv_bridge.dylib").write_bytes(b"")
                cls_, name = self.singws.KaraokeApp._load_mpv_playback_backend()
                self.assertEqual(name, "iina")
                self.assertIs(cls_, fake.MpvPlaybackPlugin)

    def test_falls_back_when_iina_module_is_absent(self):
        from unittest import mock
        real_import = __import__

        def no_iina(name, *args, **kwargs):
            if name == "mpv_playback_iina":
                raise ImportError("not in this build")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=no_iina):
            _, name = self.singws.KaraokeApp._load_mpv_playback_backend()
        self.assertEqual(name, "homebrew")

    def test_backend_is_not_hard_coded_at_the_call_site(self):
        self.assertIn("self._load_mpv_playback_backend()", MAIN_SOURCE)
        core = MAIN_SOURCE[MAIN_SOURCE.index("def _ensure_mpv_karaoke_core"):]
        core = core[:core.index("def _attach_mpv_video_follower")]
        self.assertNotIn("from mpv_playback import", core)


class CdgVisualOffsetTests(unittest.TestCase):
    """The CDG calibration was a silent no-op on mpv: the host pushed it on
    every song start and slider move, and MpvKaraokeTransport threw it away.
    Lyrics ran ~750ms out with the Display tab doing nothing."""

    def _transport(self, plugin):
        import mpv_karaoke_transport as mkt
        t = mkt.MpvKaraokeTransport.__new__(mkt.MpvKaraokeTransport)
        t.plugin = plugin
        return t

    def test_offset_is_forwarded_to_a_capable_backend(self):
        class Capable:
            def __init__(self): self.got = []
            def setVideoOffsetMs(self, ms): self.got.append(ms)
        plugin = Capable()
        t = self._transport(plugin)
        t.set_video_offset_ms(750)
        t.set_video_offset_ms(-250)
        self.assertEqual(plugin.got, [750, -250])

    def test_follower_backend_warns_once_and_never_silently_discards(self):
        import io, contextlib
        t = self._transport(object())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            t.set_video_offset_ms(750)
            t.set_video_offset_ms(750)
        out = buf.getvalue()
        self.assertEqual(out.count("cannot apply"), 1, out)
        self.assertIn("+750ms", out)

    def test_zero_offset_on_an_incapable_backend_is_silent(self):
        import io, contextlib
        t = self._transport(object())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            t.set_video_offset_ms(0)
        self.assertEqual(buf.getvalue(), "")

    def test_setter_is_no_longer_an_unconditional_no_op(self):
        with open("mpv_karaoke_transport.py", "r", encoding="utf-8") as fh:
            src = fh.read()
        body = src[src.index("def set_video_offset_ms"):]
        body = body[:body.index("def fade_out")]
        self.assertIn("setVideoOffsetMs", body)
        self.assertNotIn("mpv followers use the audible engine clock directly", body)

    def test_full_mpv_path_applies_the_offset_at_song_start(self):
        # Separate from the Python-transport path; it previously applied none.
        start = MAIN_SOURCE.index("def _start_mpv_karaoke_transport")
        end = MAIN_SOURCE.index("def _attach_mpv_video_follower")
        block = MAIN_SOURCE[start:end]
        self.assertIn("_effective_cdg_timing_offset_ms()", block)
        self.assertIn("transport.set_video_offset_ms(off)", block)

    def test_iina_plugin_maps_the_offset_onto_audio_delay(self):
        with open("mpv_playback_iina.py", "r", encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("def setVideoOffsetMs", src)
        self.assertIn("singws_bridge_set_audio_delay", src)
        # Same sign, direct mapping: SingWS ms -> mpv seconds.
        self.assertIn("self._video_offset_ms / 1000.0", src)


class CdgTimingBaselinePerEngineTests(unittest.TestCase):
    """The two engines need different CDG baselines and separate fine tuning.

    One shared key plus an FFmpeg-only baseline meant a value dialled in on mpv
    was really cancelling a baseline that did not apply there -- and it stayed
    applied after switching back, silently de-calibrating FFmpeg by the same
    amount.
    """

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def setUp(self):
        self._orig_override = os.environ.pop("SINGWS_KARAOKE_ENGINE", None)
        self._orig_legacy = os.environ.pop("SINGWS_INTEL_LEGACY_BUILD", None)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.pop("SINGWS_KARAOKE_ENGINE", None)
        if self._orig_override is not None:
            os.environ["SINGWS_KARAOKE_ENGINE"] = self._orig_override
        os.environ.pop("SINGWS_INTEL_LEGACY_BUILD", None)
        if self._orig_legacy is not None:
            os.environ["SINGWS_INTEL_LEGACY_BUILD"] = self._orig_legacy

    def _host(self, **settings):
        """Minimal stand-in: these resolvers only touch settings + the pref."""
        app = self.singws.KaraokeApp
        host = mock.Mock(spec=[])
        host.settings = dict(settings)
        host._karaoke_engine_session_pref = settings.pop("_session_pref", None)
        for name in (
            "_cdg_timing_engine",
            "_cdg_timing_offset_key",
            "_cdg_timing_base_offset_ms",
            "_effective_cdg_timing_offset_ms",
        ):
            setattr(host, name, getattr(app, name).__get__(host, app))
        return host

    def test_baselines_are_distinct(self):
        self.assertNotEqual(
            self.singws.FFMPEG_CDG_BASE_OFFSET_MS,
            self.singws.MPV_CDG_BASE_OFFSET_MS,
        )

    def test_ffmpeg_keeps_its_own_baseline_and_key(self):
        host = self._host(karaoke_engine="ffmpeg", cdg_timing_offset_ms=25)
        self.assertEqual(host._cdg_timing_engine(), "ffmpeg")
        self.assertEqual(host._cdg_timing_offset_key(), "cdg_timing_offset_ms")
        self.assertEqual(
            host._effective_cdg_timing_offset_ms(),
            self.singws.FFMPEG_CDG_BASE_OFFSET_MS + 25,
        )

    @mock.patch("sys.platform", "darwin")
    def test_mpv_uses_the_mpv_baseline_and_key(self):
        host = self._host(karaoke_engine="mpv", cdg_timing_offset_mpv_ms=25)
        self.assertEqual(host._cdg_timing_engine(), "mpv")
        self.assertEqual(host._cdg_timing_offset_key(), "cdg_timing_offset_mpv_ms")
        self.assertEqual(
            host._effective_cdg_timing_offset_ms(),
            self.singws.MPV_CDG_BASE_OFFSET_MS + 25,
        )

    @mock.patch("sys.platform", "darwin")
    def test_calibrating_one_engine_cannot_move_the_other(self):
        # The regression this split exists to prevent.
        settings = {"cdg_timing_offset_ms": 0, "cdg_timing_offset_mpv_ms": -750}
        mpv = self._host(karaoke_engine="mpv", **settings)
        ffmpeg = self._host(karaoke_engine="ffmpeg", **settings)
        self.assertEqual(
            ffmpeg._effective_cdg_timing_offset_ms(),
            self.singws.FFMPEG_CDG_BASE_OFFSET_MS,
        )
        self.assertNotEqual(
            mpv._effective_cdg_timing_offset_ms(),
            ffmpeg._effective_cdg_timing_offset_ms(),
        )

    @mock.patch("sys.platform", "darwin")
    def test_mpv_video_follower_reads_the_ffmpeg_key(self):
        # That path cannot apply an offset at all, so it must not get a
        # second, separately-calibrated value the operator never sees.
        host = self._host(karaoke_engine="mpv-video")
        self.assertEqual(host._cdg_timing_offset_key(), "cdg_timing_offset_ms")

    @mock.patch("sys.platform", "linux")
    def test_mpv_is_macos_only(self):
        host = self._host(karaoke_engine="mpv")
        self.assertEqual(host._cdg_timing_engine(), "ffmpeg")

    def test_split_migration_uses_the_historical_baseline_not_the_live_one(self):
        # The migration reconstructs an effective offset that was dialled in
        # while the shared baseline was 600. Reading FFMPEG_CDG_BASE_OFFSET_MS
        # there would re-interpret saved settings every time that constant
        # moves -- and it has now moved to 750.
        # Anchor on the migration itself: the key also appears in DEFAULTS.
        start = MAIN_SOURCE.index(
            'if not bool(self.settings.get("cdg_timing_engine_split_migrated"'
        )
        block = MAIN_SOURCE[start:start + 2000]
        self.assertIn("LEGACY_SHARED_BASE_MS = 600", block)
        self.assertNotIn(
            "FFMPEG_CDG_BASE_OFFSET_MS + saved_fine", block,
            "the split migration must not read the live FFmpeg baseline",
        )

    def test_legacy_intel_build_pins_the_ffmpeg_baseline(self):
        os.environ["SINGWS_INTEL_LEGACY_BUILD"] = "1"
        host = self._host(karaoke_engine="mpv")
        self.assertEqual(host._cdg_timing_engine(), "ffmpeg")


class PaintedOverlayVsMpvSurfaceTests(unittest.TestCase):
    """Painted show overlays must win over mpv's native child surface.

    Qt composites a WA_NativeWindow child above everything its parent widget
    paints, so the singer-start / outro transitions VideoAreaWidget draws in
    paintEvent were invisible for the whole of playback once the mpv host was
    revealed. No test covered the ordering, so nothing caught it.
    """

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def _host(self, *, playing=True, has_plugin=True):
        app = self.singws.KaraokeApp
        host = mock.Mock(spec=[])
        host._mpv_playback = object() if has_plugin else None
        host._mpv_output_host = mock.Mock(spec=["setVisible", "raise_"])
        host._mpv_preview_host = mock.Mock(spec=["setVisible", "raise_"])
        host._last_karaoke_engine = "mpv"
        host.karaoke_transport = object() if playing else None
        host._current_karaoke_mode = "cdg"
        for name in ("_set_mpv_hosts_visible", "_mpv_hosts_should_show",
                     "_reveal_mpv_hosts_if_allowed",
                     "_suppress_mpv_hosts_for_overlay"):
            setattr(host, name, getattr(app, name).__get__(host, app))
        return host

    def _visible_calls(self, host):
        return [c.args[0] for c in host._mpv_output_host.setVisible.call_args_list]

    def test_overlay_hides_the_native_surface(self):
        host = self._host()
        host._suppress_mpv_hosts_for_overlay("transition", True)
        self.assertEqual(self._visible_calls(host)[-1], False)
        host._mpv_preview_host.setVisible.assert_called_with(False)

    def test_playback_start_cannot_reveal_over_a_running_overlay(self):
        # The exact regression: transport.started fired mid-transition and
        # painted the video straight over it.
        host = self._host()
        host._suppress_mpv_hosts_for_overlay("transition", True)
        host._reveal_mpv_hosts_if_allowed()
        self.assertEqual(self._visible_calls(host)[-1], False)

    def test_surface_returns_when_the_overlay_clears(self):
        host = self._host()
        host._suppress_mpv_hosts_for_overlay("transition", True)
        host._suppress_mpv_hosts_for_overlay("transition", False)
        self.assertEqual(self._visible_calls(host)[-1], True)

    def test_two_overlays_both_have_to_clear(self):
        # Output and preview each run their own transition timer.
        host = self._host()
        host._suppress_mpv_hosts_for_overlay("output", True)
        host._suppress_mpv_hosts_for_overlay("preview", True)
        host._suppress_mpv_hosts_for_overlay("preview", False)
        self.assertEqual(self._visible_calls(host)[-1], False)
        host._suppress_mpv_hosts_for_overlay("output", False)
        self.assertEqual(self._visible_calls(host)[-1], True)

    def test_clearing_while_idle_does_not_resurrect_the_surface(self):
        # Between songs the surface must stay hidden so the idle background,
        # which is also painted by VideoAreaWidget, remains visible.
        host = self._host(playing=False)
        host._suppress_mpv_hosts_for_overlay("transition", True)
        host._suppress_mpv_hosts_for_overlay("transition", False)
        self.assertEqual(self._visible_calls(host)[-1], False)

    def test_no_mpv_plugin_is_a_no_op(self):
        host = self._host(has_plugin=False)
        host._suppress_mpv_hosts_for_overlay("transition", True)
        host._mpv_output_host.setVisible.assert_not_called()

    def test_started_signal_is_routed_through_the_gate(self):
        # A direct _set_mpv_hosts_visible(True) here would bypass suppression.
        start = MAIN_SOURCE.index("def _start_mpv_karaoke_transport")
        end = MAIN_SOURCE.index("def _attach_mpv_video_follower")
        block = MAIN_SOURCE[start:end]
        self.assertIn("transport.started.connect(self._reveal_mpv_hosts_if_allowed)", block)
        self.assertNotIn("lambda: self._set_mpv_hosts_visible(True)", block)

    def test_fallback_transition_drives_the_suppression(self):
        self.assertIn("self._set_overlay_suppresses_mpv(True)", MAIN_SOURCE)
        self.assertIn("self._set_overlay_suppresses_mpv(False)", MAIN_SOURCE)


if __name__ == "__main__":
    unittest.main()
