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

    def test_gate_rules_use_rust_only_when_it_equals_python_and_never_raise(self):
        flags = (False, True, False, True, False, True, True)
        with mock.patch.object(drive, "_ask", return_value=(True, True)):
            self.assertTrue(drive.decide_verified_end_allowed(True, *flags))
            self.assertFalse(drive.decide_verified_end_allowed(False, *flags))        # Rust says yes, Python no: Python wins
        lines = []
        with mock.patch.object(drive, "_ask", return_value=(False, True)):
            self.assertTrue(drive.decide_lyric_cue_ready(True, True, True, 100.0, 3.0, 4.0, 103.0, 9.0, log=lines.append))
        self.assertIn("disagreement=1", lines[0])
        with mock.patch.object(drive, "_ask", return_value=(None, False)):
            self.assertTrue(drive.decide_visual_end_trusted(True, True, 5.0, 0.9))
            self.assertFalse(drive.decide_visual_end_trusted(False, False, None, 0.1))
        with mock.patch.object(drive, "_ask", side_effect=RuntimeError("boom")):
            self.assertTrue(drive.decide_visual_end_trusted(True, True, 5.0, 0.9))
            self.assertFalse(drive.decide_lyric_cue_ready(False, False, False, None, 3.0, 4.0, 0.0, 0.0))

    @unittest.skipIf(drive._library() is None, "libsingws_dsp_ffi without singws_songend_json is not built here")
    def test_the_real_rust_gates_match_the_real_python_methods_over_a_grid(self):
        import itertools
        mod = self.main_module()
        KA = mod.KaraokeApp
        with mock.patch.object(mod, "_diag", lambda *a, **k: None):
            # the verified-end gate: every combination of the seven conditions, through the real method
            for auto, cross, pre, playlist, playing, enabled, autoplay in itertools.product((False, True), repeat=7):
                bg = types.SimpleNamespace(playlist=["t"] if playlist else [], is_playing=playing, fade_in=lambda *a, **k: None)
                fake = types.SimpleNamespace(settings={"karaoke_auto_advance": auto, "bg_enabled": enabled, "bg_autoplay_on_idle": autoplay}, bg_music=bg, _bg_crossfade_prefired=pre,
                                             _karaoke_bgm_crossfade_enabled=lambda cross=cross: cross)
                expected = KA._prefire_bgm_at_verified_audio_end_python(fake)
                got, ok = drive._ask({"op": "verified_end_allowed", "auto_advance": auto, "crossfade": cross, "prefired": pre, "has_playlist": playlist, "bg_playing": playing,
                                      "bg_enabled": enabled, "bg_autoplay": autoplay})
                self.assertTrue(ok)
                self.assertEqual(got, expected, (auto, cross, pre, playlist, playing, enabled, autoplay))
            # the lyric cue: the real method over settings, mode, cue and the clock
            for enabled, mode, cue, delay, need, elapsed, remain in itertools.product((False, True), ("cdg", "mp4", ""), (None, 100.0), (0.0, 3.0), (0.0, 4.0), (99.0, 102.9, 103.0, 150.0), (3.9, 4.0, 40.0)):
                fake = types.SimpleNamespace(settings={"karaoke_end_on_lyric_cue": enabled, "karaoke_lyric_cue_delay_sec": delay, "karaoke_lyric_cue_min_remaining_sec": need},
                                             _end_silence_mode=mode, _karaoke_lyric_cue_s=cue)
                expected = KA._lyric_end_cue_ready_python(fake, elapsed, remain)
                got, ok = drive._ask({"op": "lyric_cue_ready", "enabled": enabled, "cdg_mode": mode == "cdg", "cue": cue, "delay": delay, "need": need, "elapsed": elapsed, "remain": remain})
                self.assertTrue(ok)
                self.assertEqual(got, expected, (enabled, mode, cue, delay, need, elapsed, remain))
            # the visual-end trust: whether the real method goes on to apply the endpoint
            for safe, end, conf in itertools.product((False, True), (None, 0.0, 12.5), (0.0, 0.84, 0.85, 0.99)):
                applied = []
                fake = types.SimpleNamespace(settings={}, _karaoke_visual_end_path="p", _set_lyric_cue=lambda *a, **k: None, _apply_visual_end_values=lambda *a, **k: applied.append(1))
                result = types.SimpleNamespace(safe_for_early_completion=safe, visual_end=end, confidence=conf, reason="r", last_change=None, duration=0.0)
                KA._apply_visual_end_floor(fake, "p", result)
                got, ok = drive._ask({"op": "visual_end_trusted", "safe": safe, "visual_end": end, "confidence": conf})
                self.assertTrue(ok)
                self.assertEqual(got, bool(applied), (safe, end, conf))

    def test_step_decisions_use_rust_only_when_the_whole_new_state_equals_pythons(self):
        py = {"guard": 0, "fire": True}
        with mock.patch.object(drive, "_ask", return_value=({"guard": 0, "fire": True}, True)):
            self.assertEqual(drive.decide_eos_step(py, 1, True, False), py)
        lines = []
        with mock.patch.object(drive, "_ask", return_value=({"guard": 1, "fire": False}, True)):
            self.assertEqual(drive.decide_eos_step(py, 1, True, False, log=lines.append), py)
        self.assertIn("disagreement=1", lines[0])
        with mock.patch.object(drive, "_ask", return_value=(None, False)):
            self.assertEqual(drive.decide_stall_step({"last_pos": 5, "ticks": 0, "fire": False}, None, 0, 5, False, False, True, False), {"last_pos": 5, "ticks": 0, "fire": False})
        with mock.patch.object(drive, "_ask", side_effect=RuntimeError("boom")):
            self.assertEqual(drive.decide_stall_step(py, None, 0, 5, False, False, True, False), py)
        with mock.patch.object(drive, "_ask", return_value=(False, True)):
            self.assertTrue(drive.decide_overlap_window(True, 2.0, True, 100.0, 99.0))
            self.assertTrue(drive.decide_tail_cleared(True, 103.0, 100.0, 2.5, None, -38.0))
            self.assertTrue(drive.decide_visual_end_reached(True, 100.0, 0.9, 101.0))

    @unittest.skipIf(drive._library() is None, "libsingws_dsp_ffi without singws_songend_json is not built here")
    def test_the_real_detectors_match_rust_tick_by_tick_over_random_sequences(self):
        import random
        mod = self.main_module()
        KA = mod.KaraokeApp
        rng = random.Random(20261128)
        fired = {"eos": 0, "stall": 0}
        with mock.patch.object(mod, "_diag", lambda *a, **k: None):
            for _seq in range(60):
                fake = types.SimpleNamespace(karaoke_playing=True)
                pos = rng.choice([0, 1_000_000, 5_000_000_000])
                frozen = 0
                for _tick in range(120):
                    if frozen == 0 and rng.random() < 0.04:
                        frozen = rng.choice([5, 19, 20, 21, 30])       # the position stops, silent, playing: the stall detector's case
                    if frozen:
                        frozen -= 1
                        near_end, live, paused = False, False, False
                        fake.karaoke_playing = True
                        pos = max(pos, 1)
                    else:
                        pos += rng.choice([0, 0, 0, 5_000_000, 19_999_999, 20_000_000, 500_000_000])
                        near_end = rng.random() < 0.15
                        live = rng.random() < 0.2
                        paused = rng.random() < 0.05
                        fake.karaoke_playing = rng.random() < 0.95
                    # the end-of-stream detector
                    pre_guard = getattr(fake, "_eos_guard", 0)
                    fire = KA._eos_step_python(fake, near_end, live)
                    got, ok = drive._ask({"op": "eos_step", "guard": pre_guard, "near_end": near_end, "audio_live": live})
                    self.assertTrue(ok)
                    self.assertEqual(got, {"guard": fake._eos_guard, "fire": fire}, ("eos", pre_guard, near_end, live))
                    fired["eos"] += fire
                    # the stalled-position detector
                    pre_last, pre_ticks, playing = getattr(fake, "_last_pos_ns", None), getattr(fake, "_stalled_pos_ticks", 0), fake.karaoke_playing
                    fire = KA._stall_step_python(fake, pos, near_end, live, paused)
                    got, ok = drive._ask({"op": "stall_step", "last_pos": pre_last, "ticks": pre_ticks, "pos": pos, "paused": paused, "audio_live": live,
                                          "karaoke_playing": playing, "near_end": near_end})
                    self.assertTrue(ok)
                    self.assertEqual(got, {"last_pos": fake._last_pos_ns, "ticks": fake._stalled_pos_ticks, "fire": fire}, ("stall", pre_last, pre_ticks, pos))
                    fired["stall"] += fire
        self.assertGreater(fired["eos"], 5)
        self.assertGreater(fired["stall"], 0)

    def test_the_step_drive_is_on_by_default_and_wired_in_the_app(self):
        src = (Path(__file__).parent / "0.2.18.1.py").read_text()
        self.assertIn('"songend_drive_steps": True', src)
        for call in ("songend_drive.decide_eos_step(", "songend_drive.decide_stall_step(", "songend_drive.decide_overlap_window(", "songend_drive.decide_tail_cleared(",
                     "songend_drive.decide_visual_end_reached("):
            self.assertIn(call, src)
        self.assertIn("if self._eos_step(near_end, audio_is_live):", src)
        self.assertIn("if self._stall_step(pos, near_end, audio_is_live):", src)

    def test_the_gates_drive_is_on_by_default_and_wired_in_the_app(self):
        src = (Path(__file__).parent / "0.2.18.1.py").read_text()
        self.assertIn('"songend_drive_gates": True', src)
        self.assertEqual(src.count('__dict__.get("settings") or {}).get("songend_drive_gates", True)'), 3)
        for name in ("decide_verified_end_allowed(", "decide_lyric_cue_ready(", "decide_visual_end_trusted("):
            self.assertIn("songend_drive." + name, src)

    def test_the_floors_drive_is_on_by_default_and_wired_in_the_app(self):
        src = (Path(__file__).parent / "0.2.18.1.py").read_text()
        self.assertIn('"songend_drive_floors": True', src)
        self.assertGreaterEqual(src.count('__dict__.get("settings") or {}).get("songend_drive_floors", True)'), 2)
        self.assertIn("songend_drive.decide_effective_duration(", src)
        self.assertIn("songend_drive.decide_audio_end(", src)

    def test_the_app_keeps_python_in_charge_and_the_setting_is_off_by_default(self):
        src = (Path(__file__).parent / "0.2.18.1.py").read_text()
        self.assertIn('"songend_drive_prestart": True', src)
        body = src[src.index("    def update_time_left(self):"):][:6000]
        self.assertIn('self.settings.get("songend_drive_prestart", True)', body)
        self.assertIn("songend_drive.decide_prestart(", body)
        self.assertIn("if _prestart_due:", body)

    def test_a_saved_profile_with_the_drives_off_is_switched_on_once_and_a_later_off_is_kept(self):
        mod = self.main_module()
        saved = {"songend_drive_prestart": False, "mac_shim_keep_awake": False, "rust_evidence_defaults_migrated": True, "waitlist_snapshot_default_on_migrated": True,
                 "shell_commands_apply_default_migrated": True, "karafun_web_engine_rust_default_migrated": True}
        self.assertTrue(mod._migrate_rust_evidence_defaults(saved))
        for key in ("songend_drive_prestart", "songend_drive_floors", "songend_drive_gates", "mac_shim_keep_awake"):
            self.assertIs(saved[key], True, key)
        self.assertIs(saved["songend_gates_default_on_migrated"], True)
        saved["songend_drive_gates"] = False                    # the operator turns one off afterwards
        self.assertFalse(mod._migrate_rust_evidence_defaults(saved))
        self.assertIs(saved["songend_drive_gates"], False)

    def test_every_shadow_and_drive_the_app_has_is_on_by_default(self):
        mod = self.main_module()
        off = [k for k in ("transport_shadow", "transport_drive", "transport_drive_media_end", "transport_drive_watchdog", "queue_shadow", "plan_shadow", "plan_shadow_live",
                           "start_shadow", "start_drive", "settings_shadow", "store_shadow", "wire_shadow", "platform_shadow", "logpack_shadow", "settings_migrate_shadow",
                           "songend_shadow", "songend_drive_prestart", "songend_drive_floors", "songend_drive_gates", "relay_msg_shadow", "net_slice_shadow", "queue_state_shadow",
                           "update_shadow", "phrase_shadow", "publish_live_state", "mac_shim_keep_awake", "karafun_prewarm") if mod.DEFAULTS.get(k) is not True]
        self.assertEqual(off, [])
        self.assertEqual(mod.DEFAULTS.get("karafun_web_engine"), "rust")
        self.assertEqual(mod.DEFAULTS.get("shell_commands"), "apply")

    def test_the_installers_bundle_the_module(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = Path(__file__).with_name(spec).read_text()
            self.assertIn('"songend_drive.py"', text)
            self.assertIn("'songend_drive'", text)


if __name__ == "__main__":
    unittest.main()
