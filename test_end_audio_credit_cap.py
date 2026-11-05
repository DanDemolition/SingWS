"""The most trailing silence the app credits when it works out where a karaoke song's audio ends (2026-10-29 "Disillusioned": 17.9 s credited, background music
faded in over the song's last 18 seconds)."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import unittest
from unittest import mock

import test_show_critical_regressions as base


class EndAudioCreditCap(unittest.TestCase):
    bare_app = base.ShowCriticalRegressionTests.bare_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()

    def apply(self, duration, trailing, source="scan"):
        app = self.bare_app()
        app._karaoke_audio_end_path = "/t/x.mp3"
        lines = []
        with mock.patch.object(self.singws, "_diag", lambda text, *a, **k: lines.append(str(text))):
            app._apply_audio_end_floor("/t/x.mp3", duration, trailing, source)
        return app, lines

    def test_the_cap_is_eight_seconds(self):
        self.assertEqual(self.singws.AUDIO_END_MAX_CREDITED_SILENCE_S, 8.0)

    def test_a_very_long_silent_tail_is_credited_only_up_to_the_cap(self):
        app, lines = self.apply(371.28, 17.9)
        self.assertAlmostEqual(app._karaoke_audio_end_s, 371.28 - 8.0, places=2)
        self.assertTrue(any("capped to 8.0s" in x for x in lines), lines)

    def test_a_short_tail_is_credited_in_full_and_not_reported_as_capped(self):
        app, lines = self.apply(200.0, 5.5)
        self.assertAlmostEqual(app._karaoke_audio_end_s, 194.5, places=2)
        self.assertFalse(any("capped" in x for x in lines), lines)

    def test_a_tail_exactly_at_the_cap_is_not_capped(self):
        app, lines = self.apply(200.0, 8.0)
        self.assertAlmostEqual(app._karaoke_audio_end_s, 192.0, places=2)
        self.assertFalse(any("capped" in x for x in lines), lines)

    def test_the_library_cache_path_is_capped_too(self):
        app, _ = self.apply(300.0, 20.0, source="library-cache")
        self.assertAlmostEqual(app._karaoke_audio_end_s, 292.0, places=2)

    def test_no_tail_still_plays_the_whole_file(self):
        app, _ = self.apply(300.0, 0.0)
        self.assertIsNone(app._karaoke_audio_end_s)


if __name__ == "__main__":
    unittest.main()


class AudioRunsToEnd(EndAudioCreditCap):
    """trailing == 0.0 is ambiguous; only a scan that PROVED the last half second audible makes the file end the verified audio end."""

    def apply_end(self, duration, trailing, **kw):
        app = self.bare_app()
        app._karaoke_audio_end_path = "/t/x.mp3"
        lines = []
        with mock.patch.object(self.singws, "_diag", lambda text, *a, **k: lines.append(str(text))):
            app._apply_audio_end_floor("/t/x.mp3", duration, trailing, "scan", **kw)
        return app, lines

    def test_zero_trailing_without_proof_stays_unverified(self):
        app, lines = self.apply_end(200.0, 0.0)
        self.assertIsNone(app._karaoke_audio_end_s)
        self.assertTrue(any("no trailing silence found" in x for x in lines), lines)

    def test_zero_trailing_with_proof_makes_the_file_end_the_audio_end(self):
        app, lines = self.apply_end(200.0, 0.0, runs_to_end=True)
        self.assertEqual(app._karaoke_audio_end_s, 200.0)
        self.assertTrue(any("audio runs to the end of the file" in x for x in lines), lines)

    def test_proof_is_ignored_when_there_is_real_trailing_silence(self):
        app, _ = self.apply_end(200.0, 5.5, runs_to_end=True)
        self.assertAlmostEqual(app._karaoke_audio_end_s, 194.5, places=2)

    def test_proof_needs_a_known_duration(self):
        app, _ = self.apply_end(0.0, 0.0, runs_to_end=True)
        self.assertIsNone(app._karaoke_audio_end_s)
