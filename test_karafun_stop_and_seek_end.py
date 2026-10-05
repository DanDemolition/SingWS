"""2026-10-05, a single KaraFun test song: after skipping ahead inside KaraFun the song ended earlier than SingWS expected, so the end was
never trusted, Play said "Current song kept", Stop (which only knew the local player) did nothing and Skip does not end a song either.
(1) seeks now move the end timing; (2) Stop ends an active KaraFun song."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


def method(name, ns_extra=None):
    tree = ast.parse(SOURCE)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == name:
                    sub.decorator_list = [d for d in sub.decorator_list if getattr(d, "id", "") == "staticmethod"]
                    ns = {"_diag": lambda *a, **k: None}
                    ns.update(ns_extra or {})
                    exec(compile(ast.Module(body=[sub], type_ignores=[]), name, "exec"), ns)
                    return ns[name]
    raise AssertionError(name)


class SeekAwareEndTimingTests(unittest.TestCase):
    def setUp(self):
        self.remaining = method("_karafun_fallback_remaining")

    def test_without_a_seek_it_is_the_plain_wall_clock(self):
        self.assertEqual(self.remaining(217, 100.4, 0.0), 117)
        self.assertEqual(self.remaining(217, 100.4, None), 117)
        self.assertEqual(self.remaining(217, 100, "junk"), 117)

    def test_skipping_forward_makes_the_song_end_sooner(self):
        # 217 s song, skipped ahead 150 s, 67 s of wall time later it is over
        self.assertEqual(self.remaining(217, 67, 150.0), 0)

    def test_skipping_back_makes_the_song_end_later(self):
        self.assertEqual(self.remaining(217, 217, -60.0), 60)       # the 2026-10-04 test: remaining was -61 without this

    def test_the_end_is_reached_exactly_when_the_song_really_ends(self):
        # +150 s forward seek: expected_end_reached needs remaining <= 5 which now happens at the true end
        self.assertLessEqual(self.remaining(217, 62, 150.0), 5)
        self.assertGreater(self.remaining(217, 40, 150.0), 5)


class SeekWiringTests(unittest.TestCase):
    def test_the_monitor_and_the_watchdog_use_the_seek_aware_remaining(self):
        begin = SOURCE.index("    def _start_karafun_completion_monitor(")
        monitor = SOURCE[begin:SOURCE.index("\n    def ", begin + 10)]
        self.assertGreaterEqual(monitor.count('entry.get("karafun_seek_offset_s")'), 3)
        self.assertNotIn("fallback_duration - int(time.monotonic() - fallback_origin)", monitor)
        self.assertNotIn("fallback_duration - (time.monotonic() - playback_confirmed_at)", monitor)

    def test_a_seek_records_its_offset_and_rearms_the_end_watchdog(self):
        start = SOURCE.index("    def _karafun_seek_to(")
        seek = SOURCE[start:SOURCE.index("    def _toggle_karafun_playback", start)]
        self.assertIn('entry["karafun_seek_offset_s"] = float(entry.get("karafun_seek_offset_s") or 0.0) + moved', seek)
        self.assertIn('token == entry.get("karafun_completion_monitor")', seek)
        self.assertIn("rearm()", seek)

    def test_the_rearm_hook_lives_on_the_app_not_in_the_saved_queue_entry(self):
        self.assertIn("self._karafun_duration_rearm = (monitor_token, _rearm_duration_watchdog)", SOURCE)
        self.assertNotIn('entry["karafun_duration_rearm"]', SOURCE)

    def test_finishing_a_song_clears_the_offset_and_the_hook(self):
        start = SOURCE.index("    def _finish_external_karafun_playback(")
        finish = SOURCE[start:start + 3000]
        self.assertIn('entry.pop("karafun_seek_offset_s", None)', finish)
        self.assertIn("self._karafun_duration_rearm = (None, None)", finish)


class StopWithKaraFunTests(unittest.TestCase):
    def run_stop(self, entry, times):
        finished = []
        host = SimpleNamespace(
            _karafun_display_times=lambda: times(),
            _finish_external_karafun_playback=lambda action, expected_active=None: finished.append((action, expected_active)))
        active = {"entry": entry}
        method("_stop_external_karafun_from_button")(host, active)
        return finished, active

    def test_a_song_that_is_over_counts_as_completed(self):
        finished, active = self.run_stop({"karafun_status": "playing"}, lambda: (215.0, 217.0))
        self.assertEqual(finished, [("complete", active)])

    def test_a_song_stopped_part_way_goes_back_to_the_queue(self):
        finished, active = self.run_stop({"karafun_status": "playing"}, lambda: (90.0, 217.0))
        self.assertEqual(finished, [("return_to_queue", active)])

    def test_a_song_whose_end_could_not_be_verified_counts_as_completed(self):
        finished, active = self.run_stop({"karafun_status": "manual"}, lambda: (10.0, 217.0))
        self.assertEqual(finished, [("complete", active)])

    def test_unknown_timing_falls_back_to_returning_it(self):
        finished, active = self.run_stop({"karafun_status": "playing"}, lambda: None)
        self.assertEqual(finished[0][0], "return_to_queue")

    def test_a_failing_clock_read_still_ends_the_session(self):
        def boom():
            raise RuntimeError("no clock")
        finished, active = self.run_stop({}, boom)
        self.assertEqual(finished, [("return_to_queue", active)])

    def test_the_stop_button_handler_ends_an_active_karafun_song_before_anything_else(self):
        start = SOURCE.index("    def stop_and_clear_now_singing(")
        handler = SOURCE[start:SOURCE.index("    def _manual_stop_fade_ms", start)]
        branch = handler.index("self._stop_external_karafun_from_button(karafun_active)")
        self.assertLess(branch, handler.index("_manual_stop_in_progress"))
        self.assertLess(branch, handler.index("_confirm_audio_interrupt"))
        self.assertIn('karafun_active = getattr(self, "_active_external_karafun", None)', handler[:branch])


class ReplayTheTestSongTests(unittest.TestCase):
    """The real monitor code, replayed: a 217 s song, KaraFun reports IDLE at the moment it really ends."""
    def replay(self, events, offset=None):
        from test_karafun_monitor_safety import MonitorReplay
        r = MonitorReplay(events, duration=217, capture=True)
        if offset is not None:
            r.entry["karafun_seek_offset_s"] = offset
        return r.run()

    def test_after_skipping_ahead_the_song_is_ended_when_KaraFun_goes_idle(self):
        # 10 s of playing, a +150 s skip, KaraFun goes idle after 62 more seconds (wall 72 s of a 217 s song)
        r = self.replay([(5, "STATE|PLAYING"), (5, "STATE|PLAYING"), (62, "STATE|IDLE")], offset=150.0)
        r.host._finish_external_karafun_playback.assert_called_once_with("complete", expected_active=r.active)

    def test_the_same_idle_without_a_seek_is_still_not_trusted(self):
        r = self.replay([(5, "STATE|PLAYING"), (5, "STATE|PLAYING"), (62, "STATE|IDLE")])
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_after_skipping_back_an_early_idle_is_not_a_finished_song(self):
        # skipped back 60 s: at wall 215 s the song still has about 62 s to run
        r = self.replay([(5, "STATE|PLAYING"), (5, "STATE|PLAYING"), (205, "STATE|IDLE")], offset=-60.0)
        r.host._finish_external_karafun_playback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
