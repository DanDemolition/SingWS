"""Tests for karaoke engine selection after GStreamer removal.

GStreamer is gone; the FFmpeg/Qt PythonKaraokeTransport is the sole engine.
`_select_karaoke_transport_cls` always resolves to the FFmpeg engine, and a
stale `karaoke_engine` setting of gstreamer/auto is accepted and mapped to it
rather than erroring.
"""

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
        self.addCleanup(self._restore)
        self.singws.PythonKaraokeTransport = _FfmpegSentinel

    def _restore(self):
        self.singws.PythonKaraokeTransport = self._orig_python

    def _app(self, pref):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = {"karaoke_engine": pref} if pref is not None else {}
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
        # ffmpeg aliases, obsolete gstreamer/auto, and garbage all map to the
        # sole engine — no preference can conjure GStreamer back.
        for pref in ("ffmpeg", "python", "qt", "FFMPEG", " ffmpeg ",
                     "gstreamer", "gst", "auto", "laserdisc", ""):
            resolved, cls = self._app(pref)._select_karaoke_transport_cls()
            self.assertEqual(resolved, "ffmpeg", pref)
            self.assertIs(cls, _FfmpegSentinel, pref)

    def test_engine_missing_returns_none_cls(self):
        self.singws.PythonKaraokeTransport = None
        pref, cls = self._app("ffmpeg")._select_karaoke_transport_cls()
        self.assertEqual(pref, "ffmpeg")
        self.assertIsNone(cls)


if __name__ == "__main__":
    unittest.main()
