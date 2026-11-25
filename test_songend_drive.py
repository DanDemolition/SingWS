"""The song-end drive: Rust answers the pre-start question only when it agrees with Python (songend_drive.py)."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import types
import unittest
from pathlib import Path
from unittest import mock

import songend_drive as drive
import test_show_critical_regressions as base

NS = 1_000_000_000
FLAGS = dict(crossfade=True, prefired=False, has_playlist=True, bg_playing=False, bg_enabled=True, bg_autoplay=True)


def python_rule(remaining_ns, lead_s, crossfade=True, prefired=False, has_playlist=True, bg_playing=False, bg_enabled=True, bg_autoplay=True):
    window = int(max(2.0, min(8.0, lead_s)) * NS)
    return bool(crossfade and remaining_ns <= window and remaining_ns > 150_000_000 and not prefired and has_playlist and not bg_playing and bg_enabled and bg_autoplay)


class SongEndDrive(unittest.TestCase):
    def decide(self, python_due, remaining_ns, lead_s=5.0, rust="real", **flags):
        args = dict(FLAGS, **flags)
        if rust == "real":
            return drive.decide_prestart(python_due, remaining_ns, lead_s, log=None, **args)
        with mock.patch.object(drive, "rust_prestart_due", return_value=rust):
            return drive.decide_prestart(python_due, remaining_ns, lead_s, log=None, **args)

    def test_agreement_uses_rust_and_a_difference_keeps_python(self):
        self.assertEqual(self.decide(True, 3 * NS, rust=True), (True, False, "rust"))
        self.assertEqual(self.decide(False, 3 * NS, rust=False), (False, False, "rust"))
        self.assertEqual(self.decide(False, 3 * NS, rust=True), (False, True, "python-fallback"))
        self.assertEqual(self.decide(True, 3 * NS, rust=False), (True, True, "python-fallback"))

    def test_rust_that_cannot_answer_never_changes_the_decision(self):
        self.assertEqual(self.decide(True, 3 * NS, rust=None), (True, False, "python"))
        self.assertEqual(self.decide(False, 3 * NS, rust=None), (False, False, "python"))
        with mock.patch.object(drive, "rust_prestart_due", side_effect=RuntimeError("boom")):
            self.assertEqual(drive.decide_prestart(True, 3 * NS, 5.0, log=None, **FLAGS), (True, False, "python"))

    def test_nothing_is_asked_earlier_than_the_last_eight_and_a_half_seconds(self):
        with mock.patch.object(drive, "rust_prestart_due") as asked:
            self.assertEqual(drive.decide_prestart(False, 60 * NS, 5.0, log=None, **FLAGS), (False, False, "python"))
            asked.assert_not_called()

    def test_the_disagreement_and_the_firing_are_logged_but_idle_ticks_are_not(self):
        lines = []
        with mock.patch.object(drive, "rust_prestart_due", return_value=False):
            drive.decide_prestart(True, 3 * NS, 5.0, log=lines.append, **FLAGS)
        self.assertEqual(len(lines), 1)
        self.assertIn("disagreement=1", lines[0])
        lines.clear()
        with mock.patch.object(drive, "rust_prestart_due", return_value=False):
            drive.decide_prestart(False, 3 * NS, 5.0, log=lines.append, **FLAGS)
        self.assertEqual(lines, [])

    @unittest.skipIf(drive._library() is None, "libsingws_dsp_ffi without singws_songend_json is not built here")
    def test_the_real_rust_rule_matches_python_over_a_grid(self):
        import itertools
        bools = (False, True)
        for remaining, lead in itertools.product((0, 100_000_000, 150_000_000, 150_000_001, 2 * NS, 2 * NS + 1, 3 * NS, 5 * NS, 5 * NS + 1, 8 * NS, 8 * NS + 1, 9 * NS),
                                                (0.5, 2.0, 3.2, 5.0, 8.0, 12.0)):
            for crossfade, prefired, playlist, playing, enabled, autoplay in itertools.product(bools, repeat=6):
                flags = dict(crossfade=crossfade, prefired=prefired, has_playlist=playlist, bg_playing=playing, bg_enabled=enabled, bg_autoplay=autoplay)
                expected = python_rule(remaining, lead, **flags)
                got = drive.rust_prestart_due(remaining, lead, **flags) if remaining <= drive.ASK_WITHIN_NS else expected
                self.assertEqual(got, expected, (remaining, lead, flags))

    @classmethod
    def main_module(cls):
        if not hasattr(cls, "_main"):
            cls._main = base.load_main_module()
        return cls._main

    def test_floors_agree_use_rust_and_a_difference_keeps_python(self):
        with mock.patch.object(drive, "_ask", return_value=(256_760_000_000, True)):
            self.assertEqual(drive.decide_effective_duration(256_760_000_000, 234_250_000_000, 256.76), 256_760_000_000)
        with mock.patch.object(drive, "_ask", return_value=(1, True)):
            self.assertEqual(drive.decide_effective_duration(256_760_000_000, 234_250_000_000, 256.76), 256_760_000_000)
        with mock.patch.object(drive, "_ask", return_value=(None, False)):
            self.assertEqual(drive.decide_effective_duration(7, 7, 0.0), 7)
        with mock.patch.object(drive, "_ask", side_effect=RuntimeError("boom")):
            self.assertEqual(drive.decide_effective_duration(7, 7, 0.0), 7)
            self.assertEqual(drive.decide_audio_end(192.0, 200.0, 20.0, False), 192.0)
        with mock.patch.object(drive, "_ask", return_value=(192.0, True)):
            self.assertEqual(drive.decide_audio_end(192.0, 200.0, 20.0, False), 192.0)
        with mock.patch.object(drive, "_ask", return_value=(150.0, True)):
            lines = []
            self.assertEqual(drive.decide_audio_end(192.0, 200.0, 20.0, False, log=lines.append), 192.0)
            self.assertIn("disagreement=1", lines[0])
        with mock.patch.object(drive, "_ask", return_value=(None, True)):
            self.assertIsNone(drive.decide_audio_end(None, 200.0, 0.0, False))

    @unittest.skipIf(drive._library() is None, "libsingws_dsp_ffi without singws_songend_json is not built here")
    def test_the_real_rust_floors_match_the_real_python_methods_over_a_grid(self):
        import itertools
        mod = self.main_module()
        KA = mod.KaraokeApp
        with mock.patch.object(mod, "_diag", lambda *a, **k: None):
            for dur, audio in itertools.product((None, 0, 1, 234_250_000_000, 256_760_000_000, 300_000_000_000), (-1.0, 0.0, 0.5, 234.25, 256.76, 300.0, 400.123)):
                fake = types.SimpleNamespace(_karaoke_audio_duration_s=audio, _karaoke_duration_floor_logged=False)
                expected = KA._effective_karaoke_duration_ns_python(fake, dur)
                got, ok = drive._ask({"op": "effective_duration", "dur_ns": dur, "audio_s": float(audio)})
                self.assertTrue(ok)
                self.assertEqual(got, expected, (dur, audio))
            for duration, trailing, runs in itertools.product((0.0, 5.0, 200.0, 262.36), (-3.0, 0.0, 0.4, 3.0, 8.0, 8.01, 20.0, 300.0), (False, True)):
                fake = types.SimpleNamespace(_karaoke_audio_end_path="p", _karaoke_audio_end_s="unset", _karaoke_audio_duration_s=None)
                KA._apply_audio_end_floor_python(fake, "p", duration, trailing, "test", runs)
                expected = None if fake._karaoke_audio_end_s in (None,) else fake._karaoke_audio_end_s
                got, ok = drive._ask({"op": "audio_end", "duration": float(duration), "trailing": float(trailing), "runs_to_end": runs})
                self.assertTrue(ok)
                self.assertEqual(got, expected, (duration, trailing, runs))

    def test_the_floors_drive_is_off_by_default_and_wired_in_the_app(self):
        src = (Path(__file__).parent / "0.2.18.1.py").read_text()
        self.assertIn('"songend_drive_floors": False', src)
        self.assertGreaterEqual(src.count('__dict__.get("settings") or {}).get("songend_drive_floors", False)'), 2)
        self.assertIn("songend_drive.decide_effective_duration(", src)
        self.assertIn("songend_drive.decide_audio_end(", src)

    def test_the_app_keeps_python_in_charge_and_the_setting_is_off_by_default(self):
        src = (Path(__file__).parent / "0.2.18.1.py").read_text()
        self.assertIn('"songend_drive_prestart": False', src)
        body = src[src.index("    def update_time_left(self):"):][:6000]
        self.assertIn('self.settings.get("songend_drive_prestart", False)', body)
        self.assertIn("songend_drive.decide_prestart(", body)
        self.assertIn("if _prestart_due:", body)

    def test_the_installers_bundle_the_module(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = Path(__file__).with_name(spec).read_text()
            self.assertIn('"songend_drive.py"', text)
            self.assertIn("'songend_drive'", text)


if __name__ == "__main__":
    unittest.main()
