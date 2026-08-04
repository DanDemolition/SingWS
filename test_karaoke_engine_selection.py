"""Tests for default/fallback and experimental karaoke engine selection."""

import importlib.util
import os
import sys
import unittest

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")


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
        app._karaoke_engine_session_pref = pref or "ffmpeg"
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

    def test_every_preference_resolves_to_ffmpeg(self):
        # FFmpeg aliases, obsolete gstreamer/auto, and garbage stay on the
        # proven fallback path.
        for pref in ("ffmpeg", "python", "qt", "FFMPEG", " ffmpeg ",
                     "gstreamer", "gst", "auto", "laserdisc", ""):
            resolved, cls = self._app(pref)._select_karaoke_transport_cls()
            self.assertEqual(resolved, "ffmpeg", pref)
            self.assertIs(cls, _FfmpegSentinel, pref)

    def test_mpv_is_explicit_macos_only_selection(self):
        resolved, cls = self._app("mpv")._select_karaoke_transport_cls()
        expected = "mpv" if sys.platform == "darwin" else "ffmpeg"
        self.assertEqual(resolved, expected)
        self.assertIs(cls, _FfmpegSentinel)

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


if __name__ == "__main__":
    unittest.main()
