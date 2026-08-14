"""Tests for durable audio-output pinning (AirPlay default-hijack shield).

2026-07-20: adding an AirPlay display made macOS flip the system default
output to the TV. The host's pinned device should shield against that, but
the old GStreamer-era device ids hashed caps/props and churned between
sessions, so the pin silently reset to "default" — which AirPlay then stole.
These tests pin the new behavior: name-keyed ids, recovery by persisted name
instead of silent unpinning, and the name recorded alongside the id.
"""

import importlib.util
import os
import sys
import types
import unittest

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_audio_pin", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AudioOutputPinningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def _app(self, settings=None, cache=None):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = dict(settings or {})
        app._audio_output_cache = list(cache or [])
        app.save_settings = lambda: None
        app._refresh_audio_output_cache = lambda: app._audio_output_cache
        app._iter_audio_sink_devices = lambda: []
        return app

    def _cache(self):
        return [
            {"id": "default", "name": "Default (System)", "kind": "speaker"},
            {"id": "qt_headphones01", "name": "External Headphones", "kind": "headphones"},
            {"id": "qt_speakers03", "name": "MacBook Pro Speakers", "kind": "speaker"},
            {"id": "qt_bravia02", "name": "Sony BRAVIA (AirPlay)", "kind": "display"},
        ]

    def test_valid_pin_is_returned_unchanged(self):
        app = self._app({"audio_output_id": "qt_headphones01"}, self._cache())
        self.assertEqual(app._get_selected_audio_output_id(), "qt_headphones01")

    def test_stale_id_recovers_by_persisted_name(self):
        # The pre-Qt id no longer exists, but the device (by name) does: the
        # pin must transfer to the new id instead of resetting to default.
        app = self._app(
            {"audio_output_id": "dev_deadbeef12345678",
             "audio_output_name": "External Headphones"},
            self._cache(),
        )
        self.assertEqual(app._get_selected_audio_output_id(), "qt_headphones01")
        self.assertEqual(app.settings["audio_output_id"], "qt_headphones01")
        self.assertEqual(app.settings["audio_output_name"], "External Headphones")

    def test_name_recovery_is_case_and_punctuation_tolerant(self):
        app = self._app(
            {"audio_output_id": "dev_stale", "audio_output_name": "external headphones"},
            self._cache(),
        )
        self.assertEqual(app._get_selected_audio_output_id(), "qt_headphones01")

    def test_unplugged_device_keeps_saved_pin_while_playback_can_fallback(self):
        app = self._app(
            {"audio_output_id": "dev_stale", "audio_output_name": "USB Interface"},
            self._cache(),
        )
        self.assertEqual(app._get_selected_audio_output_id(), "dev_stale")
        self.assertEqual(app.settings["audio_output_id"], "dev_stale")
        self.assertEqual(app.settings["audio_output_name"], "USB Interface")

    def test_missing_pin_is_not_persistently_reset_by_legacy_sink_fallback(self):
        app = self._app(
            {"audio_output_id": "dev_stale", "audio_output_name": "USB Interface"},
            self._cache(),
        )
        app._audio_device_missing_notice_shown = False
        app._update_audio_output_button = lambda: None

        class _Factory:
            @staticmethod
            def make(factory, name):
                return factory, name

        old_gst = self.singws.Gst
        self.singws.Gst = types.SimpleNamespace(ElementFactory=_Factory)
        try:
            sink = app._create_audio_sink_for_selected_output("test_sink")
        finally:
            self.singws.Gst = old_gst
        self.assertEqual(sink, ("autoaudiosink", "test_sink"))
        self.assertEqual(app.settings["audio_output_id"], "dev_stale")
        self.assertEqual(app.settings["audio_output_name"], "USB Interface")

    def test_set_audio_output_persists_display_name(self):
        app = self._app({"audio_output_id": "default"}, self._cache())
        app._update_audio_output_button = lambda: None
        app.soundboard_strip = None
        app.bg_music = None
        app._set_audio_output_id("qt_bravia02")
        self.assertEqual(app.settings["audio_output_id"], "qt_bravia02")
        self.assertEqual(app.settings["audio_output_name"], "Sony BRAVIA (AirPlay)")
        app._set_audio_output_id("default")
        self.assertEqual(app.settings["audio_output_name"], "")

    def test_qt_enumeration_ids_are_name_stable(self):
        # Same display name must always map to the same id, independent of
        # device caps/props/order (that churn is what broke the old pins).
        import hashlib
        name = "External Headphones"
        key = self.singws.KaraokeApp._normalized_audio_output_name(name)
        expected = "qt_" + hashlib.sha1(key.encode()).hexdigest()[:16]
        self.assertEqual(key, "externalheadphones")
        self.assertEqual(expected, "qt_" + hashlib.sha1(b"externalheadphones").hexdigest()[:16])

    def test_missing_headphones_fall_back_to_macbook_not_airplay(self):
        cache = self._cache()
        app = self._app(
            {"audio_output_id": "dev_stale", "audio_output_name": "External Headphones"},
            [cache[0], cache[2], cache[3]],
        )
        self.assertEqual(app._safe_local_audio_output_name(), "MacBook Pro Speakers")

    def test_default_prefers_local_output_and_never_display(self):
        app = self._app({"audio_output_id": "default"}, self._cache())
        self.assertIn(
            app._safe_local_audio_output_name(),
            {"External Headphones", "MacBook Pro Speakers"},
        )

    def test_explicit_airplay_pin_is_rejected_for_audio(self):
        app = self._app(
            {"audio_output_id": "qt_bravia02", "audio_output_name": "Sony BRAVIA (AirPlay)"},
            self._cache(),
        )
        self.assertIn(
            app._safe_local_audio_output_name(),
            {"External Headphones", "MacBook Pro Speakers"},
        )


if __name__ == "__main__":
    unittest.main()
