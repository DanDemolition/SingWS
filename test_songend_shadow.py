"""songend_shadow: the opt-in, log-only comparison of where the app starts the background music at a song's end with the Rust song-end model driven through an idealised run."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import os
import sys
import types
import unittest
from pathlib import Path

import songend_shadow as se

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
BIN = os.environ.get("SINGWS_SONGEND_BIN") or os.path.join(
    os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-songend-main")), "release", "singws-songend")
HAVE = os.path.exists(BIN)
DEFAULT_SETTINGS = {"seamless_transitions_enabled": True, "karaoke_bgm_crossfade_enabled": True, "karaoke_trim_verified_tail": True, "bg_enabled": True,
                    "bg_autoplay_on_idle": True, "bg_volume": 0.8, "karaoke_auto_advance": False, "bgm_overlap_before_audio_end_sec": 2.0,
                    "bgm_fallback_prestart_sec": 5.0, "end_silence_trim_threshold_sec": 2.5}


class Pure(unittest.TestCase):
    def setUp(self):
        se._clean_noted = False

    def test_off_by_default(self):
        self.assertFalse(se.enabled({}))
        self.assertFalse(se.enabled(None))
        self.assertTrue(se.enabled({"songend_shadow": True}))

    def test_reason_categories_ignore_the_started_or_not_wording(self):
        self.assertEqual(se.reason_of(["[CROSSFADE] BG pre-start fired (5.0s remaining)"]), "prestart")
        self.assertEqual(se.reason_of(["[CROSSFADE] BG fade started at verified audio end; x"]), "verified_end")
        self.assertEqual(se.reason_of(["[BG-HANDOFF] fade_in started reason=karaoke_end duration_ms=1000"]), "handoff:karaoke_end")
        self.assertEqual(se.reason_of(["[BG-HANDOFF] fade_in did not start reason=karaoke_end"]), "handoff:karaoke_end")
        self.assertEqual(se.reason_of([]), "other")

    def test_spec_from_a_verified_audio_end(self):
        spec = se.build_spec({"settings": DEFAULT_SETTINGS, "media_dur_s": 200.0, "audio_end_s": 193.0, "audio_duration_s": 200.0, "visual_end_s": 195.0})
        self.assertEqual(spec["arm"]["cached"][se.FAKE_LIB], {"duration": 200.0, "audio_end": 193.0})
        self.assertEqual(spec["arm"]["cached"][se.FAKE_VIS], {"visual_end": 195.0, "visual_confidence": 1.0})
        self.assertEqual(spec["library_path"], se.FAKE_LIB)

    def test_spec_when_the_audio_runs_to_the_end_of_the_file(self):
        spec = se.build_spec({"settings": {}, "media_dur_s": 200.0, "audio_end_s": 200.0, "audio_duration_s": 200.0})
        self.assertEqual(spec["arm"], {"scan_probe": 200.0, "scan_trailing": 0.0, "scan_audible_end": True})
        self.assertEqual(spec["library_path"], "")

    def test_spec_when_the_audio_end_is_unverified(self):
        spec = se.build_spec({"settings": {}, "media_dur_s": 100.0, "audio_end_s": None, "audio_duration_s": None})
        self.assertEqual(spec["arm"], {"scan_probe": "raise", "scan_trailing": "raise"})
        spec = se.build_spec({"settings": {}, "media_dur_s": 100.0, "audio_end_s": None, "audio_duration_s": 100.0})
        self.assertEqual(spec["arm"], {"scan_probe": 100.0, "scan_trailing": 0.0, "scan_audible_end": False})

    def test_only_the_models_settings_are_sent(self):
        spec = se.build_spec({"settings": {**DEFAULT_SETTINGS, "venue_api_key": "SECRET"}, "media_dur_s": 100.0})
        self.assertNotIn("venue_api_key", spec["settings"])

    def test_compare_tolerance_and_reason(self):
        self.assertEqual(se.compare(191.0, "verified_end", {"pos_s": 191.0, "reason": "verified_end"}), [])
        self.assertEqual(se.compare(191.0, "verified_end", {"pos_s": 192.4, "reason": "verified_end"}), [])
        self.assertEqual(len(se.compare(191.0, "verified_end", {"pos_s": 193.0, "reason": "verified_end"})), 1)
        self.assertEqual(len(se.compare(191.0, "verified_end", {"pos_s": 191.0, "reason": "prestart"})), 1)
        self.assertIn("never started", se.compare(191.0, "prestart", None)[0])

    def test_announce_lines(self):
        lines = []
        self.assertIn("off (songend_shadow=false)", se.announce({}, lines.append))
        self.assertIn("enabled available=", se.announce({"songend_shadow": True}, lines.append))
        self.assertTrue(all(x.startswith("[SONGEND-SHADOW]") for x in lines))

    def test_nothing_is_recorded_or_compared_while_off(self):
        app = types.SimpleNamespace(settings={}, __dict__={})
        app = types.SimpleNamespace(settings={})
        se.note_song_start(app, "cdg")
        self.assertNotIn("_songend_shadow_rec", app.__dict__)
        self.assertIsNone(se.note_music_start(app, "prestart"))


@unittest.skipUnless(HAVE, "needs the Rust model: CARGO_TARGET_DIR=~/.cache/singws-rust-target-songend-main cargo build --release -p singws-songend (in rust/)")
class WithTheModel(unittest.TestCase):
    def setUp(self):
        se._clean_noted = False
        os.environ["SINGWS_SONGEND_BIN"] = BIN
        self.addCleanup(os.environ.pop, "SINGWS_SONGEND_BIN", None)

    def spec(self, **kw):
        return se.build_spec({"settings": DEFAULT_SETTINGS, "mode": "cdg", "media_dur_s": 200.0, "audio_end_s": 193.0, "audio_duration_s": 200.0, "visual_end_s": 195.0, **kw})

    def test_verified_end_starts_the_music_overlap_before_the_audio_end(self):
        r = se.predict(self.spec())
        self.assertEqual(r["pos_s"], 191.0)
        self.assertEqual(r["reason"], "verified_end")

    def test_unverified_end_uses_the_fallback_prestart(self):
        r = se.predict(se.build_spec({"settings": DEFAULT_SETTINGS, "mode": "cdg", "media_dur_s": 200.0}))
        self.assertEqual(r["reason"], "prestart")
        self.assertAlmostEqual(r["pos_s"], 195.0, delta=0.5)

    def test_a_longer_prestart_setting_moves_the_prediction_earlier(self):
        s = {**DEFAULT_SETTINGS, "bgm_fallback_prestart_sec": 8.0}
        r = se.predict(se.build_spec({"settings": s, "mode": "cdg", "media_dur_s": 200.0}))
        self.assertAlmostEqual(r["pos_s"], 192.0, delta=0.5)

    def test_run_shadow_clean_and_mismatch(self):
        lines = []
        facts = {"settings": DEFAULT_SETTINGS, "mode": "cdg", "media_dur_s": 200.0, "audio_end_s": 193.0, "audio_duration_s": 200.0, "visual_end_s": 195.0}
        self.assertEqual(se.run_shadow(facts, 191.0, "verified_end", lines.append, number=1), "clean")
        self.assertTrue(any("first check clean" in x for x in lines))
        self.assertEqual(se.run_shadow(facts, 187.0, "verified_end", lines.append, number=2), "mismatch")
        mism = [x for x in lines if "MISMATCH" in x]
        self.assertEqual(len(mism), 1)
        self.assertIn("song 2", mism[0])
        self.assertIn("app=187.0s", mism[0])

    def test_notes_from_a_stub_app_end_to_end(self):
        lines = []
        transport = types.SimpleNamespace(query_times_ns=lambda: (200 * se.NS, int(191.2 * se.NS)))
        app = types.SimpleNamespace(settings={**DEFAULT_SETTINGS, "songend_shadow": True}, karaoke_transport=transport, bg_music=types.SimpleNamespace(playlist=["a"]))
        app.__dict__.update({"_karaoke_audio_end_s": 193.0, "_karaoke_audio_duration_s": 200.0, "_karaoke_visual_end_s": 195.0, "_bgm_session_started": True})
        se.note_song_start(app, "cdg")
        thread = se.note_music_start(app, "verified_end", lines.append)
        thread.join(20)
        self.assertTrue(any("first check clean" in x for x in lines), lines)
        self.assertIsNone(se.note_music_start(app, "prestart", lines.append))  # only the FIRST start of a song counts

    def test_legacy_detector_songs_are_not_compared(self):
        transport = types.SimpleNamespace(query_times_ns=lambda: (200 * se.NS, 100 * se.NS))
        app = types.SimpleNamespace(settings={**DEFAULT_SETTINGS, "songend_shadow": True, "karaoke_trim_verified_tail": False}, karaoke_transport=transport)
        se.note_song_start(app, "cdg")
        self.assertIsNone(se.note_music_start(app, "prestart"))


@unittest.skipUnless(HAVE, "needs the Rust model (see above)")
class DriverMatchesTheHarness(unittest.TestCase):
    """The idealised driver and the REAL Python song-end code (the harness) start the music at the same position for the same reason on every uneventful scenario."""

    def test_uneventful_scenarios_are_identical(self):
        import songend_predict_parity as pp
        self.assertEqual(pp.main(["--seeds", "40", "--binary", BIN]), 0)


class Wiring(unittest.TestCase):
    def test_the_app_hooks_the_song_start_and_the_three_music_starts_and_ships_the_flag_off(self):
        src = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('"songend_shadow": False,', src)
        self.assertIn("_songend_shadow_note(self, None, mode)", src)
        self.assertIn('_songend_shadow_note(self, "verified_end")', src)
        self.assertIn('_songend_shadow_note(self, "prestart")', src)
        self.assertIn('_songend_shadow_note(self, "handoff:" + str(resume_reason))', src)
        for call in ('_songend_shadow_note(self, "verified_end")', '_songend_shadow_note(self, "prestart")', '_songend_shadow_note(self, "handoff:" + str(resume_reason))'):
            i = src.index(call)
            self.assertIn("fade_in(", src[i:i + 400], call)   # the note sits right before the fade_in it describes

    def test_both_specs_and_build_scripts_know_the_helper(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            t = Path(spec).read_text(encoding="utf-8")
            self.assertIn('"songend_shadow.py"', t)
            self.assertIn("singws_songend", t)
        for f in ("build_singws_mac_arm64.sh", "build_singws_mac_intel.sh"):
            self.assertIn("build_songend.sh", Path(f).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
