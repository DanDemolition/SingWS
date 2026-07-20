"""Tests for the host-selectable live karaoke engine (karaoke_engine setting)."""

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


class _GstSentinel:
    pass


class _FfmpegSentinel:
    pass


class EngineSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def setUp(self):
        self._orig_gst = self.singws.GstKaraokeTransport
        self._orig_python = self.singws.PythonKaraokeTransport
        self.addCleanup(self._restore)

    def _restore(self):
        self.singws.GstKaraokeTransport = self._orig_gst
        self.singws.PythonKaraokeTransport = self._orig_python

    def _app(self, pref):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = {"karaoke_engine": pref} if pref is not None else {}
        return app

    def test_default_setting_present_and_auto(self):
        self.assertEqual(self.singws.DEFAULTS.get("karaoke_engine"), "auto")

    def test_auto_prefers_gstreamer_when_available(self):
        self.singws.GstKaraokeTransport = _GstSentinel
        self.singws.PythonKaraokeTransport = _FfmpegSentinel
        pref, cls = self._app(None)._select_karaoke_transport_cls()
        self.assertEqual(pref, "auto")
        self.assertIs(cls, _GstSentinel)

    def test_auto_falls_back_to_ffmpeg_without_gstreamer(self):
        self.singws.GstKaraokeTransport = None
        self.singws.PythonKaraokeTransport = _FfmpegSentinel
        pref, cls = self._app("auto")._select_karaoke_transport_cls()
        self.assertEqual(pref, "auto")
        self.assertIs(cls, _FfmpegSentinel)

    def test_ffmpeg_pin_selects_python_transport(self):
        self.singws.GstKaraokeTransport = _GstSentinel
        self.singws.PythonKaraokeTransport = _FfmpegSentinel
        for alias in ("ffmpeg", "python", "qt", "FFMPEG", " ffmpeg "):
            pref, cls = self._app(alias)._select_karaoke_transport_cls()
            self.assertEqual(pref, "ffmpeg")
            self.assertIs(cls, _FfmpegSentinel)

    def test_gstreamer_pin_selects_gst_transport(self):
        self.singws.GstKaraokeTransport = _GstSentinel
        self.singws.PythonKaraokeTransport = _FfmpegSentinel
        for alias in ("gstreamer", "gst"):
            pref, cls = self._app(alias)._select_karaoke_transport_cls()
            self.assertEqual(pref, "gstreamer")
            self.assertIs(cls, _GstSentinel)

    def test_unavailable_pin_falls_back_to_auto(self):
        self.singws.GstKaraokeTransport = None
        self.singws.PythonKaraokeTransport = _FfmpegSentinel
        pref, cls = self._app("gstreamer")._select_karaoke_transport_cls()
        self.assertEqual(pref, "auto")
        self.assertIs(cls, _FfmpegSentinel)

    def test_garbage_pref_falls_back_to_auto(self):
        self.singws.GstKaraokeTransport = _GstSentinel
        self.singws.PythonKaraokeTransport = _FfmpegSentinel
        pref, cls = self._app("laserdisc")._select_karaoke_transport_cls()
        self.assertEqual(pref, "auto")
        self.assertIs(cls, _GstSentinel)

    def test_both_engines_missing_returns_none_cls(self):
        self.singws.GstKaraokeTransport = None
        self.singws.PythonKaraokeTransport = None
        pref, cls = self._app("auto")._select_karaoke_transport_cls()
        self.assertEqual(pref, "auto")
        self.assertIsNone(cls)


if __name__ == "__main__":
    unittest.main()
