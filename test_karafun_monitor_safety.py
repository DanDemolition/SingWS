"""Replay KaraFun state changes without importing the app or controlling macOS."""
import ast
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


@lru_cache(maxsize=1)
def monitor_code():
    tree = ast.parse(Path("0.2.18.1.py").read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "_start_karafun_completion_monitor")
    return compile(ast.Module(body=[method], type_ignores=[]), "monitor-replay", "exec")


class MonitorReplay:
    def __init__(self, events, *, duration=263, estimated=False, callbacks_immediate=True):
        self.now = 100.0
        self.events = iter(events)
        self.messages = []
        self.timers = []
        self.workers = []
        self.callbacks = []
        self.scripts = []
        self.entry = {"title": "Mr. Highway's Thinking About the End",
                      "duration": duration, "duration_estimated": estimated,
                      "karafun_playback_assumed": True,
                      "karafun_handoff_timed_out_before_play": True,
                      "karafun_result_activation_point": (389, 217)}
        self.active = {"entry": self.entry}
        self.host = SimpleNamespace(
            _active_external_karafun=self.active,
            _run_karafun_applescript_sync=self.probe,
            _karafun_clock_seconds=self.clock_seconds,
            _run_on_ui_thread=(lambda fn: fn()) if callbacks_immediate else self.callbacks.append,
            _macos_native_double_click=mock.Mock(return_value=True),
            _karafun_press_play_control=mock.Mock(return_value=(True, "")),
            _finish_external_karafun_playback=mock.Mock(),
            _set_karafun_entry_status=mock.Mock(),
            KARAFUN_PLAYBACK_RECOVERY_DELAY_S=12,
            KARAFUN_HANDOFF_TIMEOUT_RECOVERY_DELAY_S=6,
            KARAFUN_PLAYBACK_ALERT_DELAY_S=40,
        )
        namespace = {
            "uuid": SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="monitor-token")),
            "time": SimpleNamespace(monotonic=lambda: self.now, sleep=self.sleep),
            "threading": SimpleNamespace(
                Thread=lambda target, **kw: SimpleNamespace(start=lambda: self.workers.append(target)),
                Timer=self.timer),
            "_diag": self.messages.append,
        }
        exec(monitor_code(), namespace)
        namespace['_start_karafun_completion_monitor'](self.host, self.entry)

    @staticmethod
    def clock_seconds(value):
        try:
            m, s = value.split(":")
            return int(m) * 60 + int(s)
        except (ValueError, TypeError):
            return None

    def sleep(self, seconds):
        self.now += seconds

    def timer(self, seconds, callback):
        return SimpleNamespace(start=lambda: self.timers.append((self.now + seconds, callback)))

    def probe(self, script, **kw):
        self.scripts.append("\n".join(script))
        try:
            elapsed, raw = next(self.events)
        except StopIteration:
            self.host._active_external_karafun = None
            return False, "", "replay finished"
        self.now += elapsed
        return (False, "", "timeout") if raw is None else (True, raw, "")

    def run(self):
        self.workers[0]()
        return self


class KaraFunMonitorSafetyTests(unittest.TestCase):
    def test_actual_show_readings_neither_retry_nor_complete_song_early(self):
        r = MonitorReplay([(10, ""), (5.2, "STATE|PLAYING"), (5.1, "STATE|PLAYING"),
                           (5.2, "STATE|PLAYING"), (4.9, "STATE|IDLE")]).run()
        r.host._macos_native_double_click.assert_not_called()
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_failed_and_unknown_probes_cannot_retry_or_complete_even_after_duration(self):
        for value in (None, ""):
            with self.subTest(value=value):
                r = MonitorReplay([(400, value)]).run()
                r.host._macos_native_double_click.assert_not_called()
                r.host._finish_external_karafun_playback.assert_not_called()

    def test_explicit_idle_before_any_playback_still_gets_one_recovery(self):
        r = MonitorReplay([(15, "STATE|IDLE"), (15, "STATE|IDLE"),
                           (1, "STATE|PLAYING"), (1, "STATE|PLAYING")]).run()
        r.host._macos_native_double_click.assert_called_once_with(389, 217)
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_one_playing_hint_prevents_later_idle_from_restarting_song(self):
        r = MonitorReplay([(2, "STATE|PLAYING"), (15, "STATE|IDLE")]).run()
        r.host._macos_native_double_click.assert_not_called()
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_repeated_early_idle_does_not_complete_a_known_long_song(self):
        r = MonitorReplay([(5, "STATE|PLAYING"), (5, "STATE|PLAYING"),
                           (5, "STATE|IDLE"), (5, "STATE|IDLE"), (5, "STATE|IDLE")]).run()
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_playing_at_duration_end_never_completes_until_explicit_idle(self):
        r = MonitorReplay([(5, "STATE|PLAYING"), (5, "STATE|PLAYING"),
                           (265, "STATE|PLAYING")]).run()
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_lost_status_after_confirmed_playback_and_elapsed_duration_cannot_complete(self):
        r = MonitorReplay([(5, "STATE|PLAYING"), (5, "STATE|PLAYING"),
                           (300, None)]).run()
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_conflicting_idle_and_playing_signals_cannot_complete(self):
        r = MonitorReplay([(5, "STATE|PLAYING"), (5, "STATE|PLAYING"),
                           (300, "STATE|IDLE\nSTATE|PLAYING")]).run()
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_idle_near_expected_end_completes_without_a_second_slow_probe(self):
        r = MonitorReplay([(5, "STATE|PLAYING"), (5, "STATE|PLAYING"),
                           (263, "STATE|IDLE")]).run()
        r.host._finish_external_karafun_playback.assert_called_once_with(
            "complete", expected_active=r.active)
        self.assertEqual(len(r.scripts), 3)
        self.assertTrue(any("reason=karaFun_idle" in m for m in r.messages))

    def test_unknown_duration_idle_requires_manual_completion(self):
        for duration, estimated in [(0, False), (240, True)]:
            with self.subTest(duration=duration, estimated=estimated):
                r = MonitorReplay([(5, "STATE|PLAYING"), (5, "STATE|PLAYING"),
                                   (300, "STATE|IDLE")], duration=duration, estimated=estimated).run()
                r.host._finish_external_karafun_playback.assert_not_called()

    def test_advancing_clock_near_end_then_idle_can_complete_without_duration_metadata(self):
        r = MonitorReplay([(5, "4:10\n4:23\nSTATE|PLAYING"),
                           (5, "4:20\n4:23\nSTATE|PLAYING"),
                           (3, "STATE|IDLE")], duration=0).run()
        r.host._finish_external_karafun_playback.assert_called_once_with(
            "complete", expected_active=r.active)
        self.assertEqual(len(r.scripts), 3)

    def test_static_duration_labels_cannot_complete_unknown_length_track(self):
        r = MonitorReplay([(5, "4:23\n4:23\nSTATE|PLAYING"),
                           (5, "4:23\n4:23\nSTATE|PLAYING"),
                           (5, "STATE|IDLE")], duration=0).run()
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_old_near_end_clock_is_not_evidence_for_a_later_idle_reading(self):
        r = MonitorReplay([(5, "4:10\n4:23\nSTATE|PLAYING"),
                           (5, "4:20\n4:23\nSTATE|PLAYING"),
                           (30, "STATE|IDLE")], duration=0).run()
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_watchdog_reminder_waits_for_confirmed_playback_duration(self):
        r = MonitorReplay([(20, "STATE|PLAYING"), (20, "STATE|PLAYING")]).run()
        r.host._active_external_karafun = r.active
        r.now, callback = r.timers.pop(0)
        callback()
        self.assertTrue(r.timers)
        self.assertGreater(r.timers[0][0], r.now)
        r.host._set_karafun_entry_status.assert_not_called()
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_duration_watchdog_cannot_complete_when_accessibility_is_stuck(self):
        r = MonitorReplay([])
        r.now, callback = r.timers.pop(0)
        callback()
        r.host._finish_external_karafun_playback.assert_not_called()
        r.host._set_karafun_entry_status.assert_called_once()

    def test_watchdog_cannot_force_completion_after_old_grace_budget_expires(self):
        r = MonitorReplay([])
        for _ in range(12):
            self.assertTrue(r.timers, "A still-playing song must not exhaust a completion grace budget")
            r.now, callback = r.timers.pop(0)
            r.entry["karafun_last_playing_ts"] = r.now
            callback()
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_stale_queued_completion_and_watchdog_cannot_touch_replacement_session(self):
        r = MonitorReplay([(5, "STATE|PLAYING"), (5, "STATE|PLAYING"),
                           (263, "STATE|IDLE")], callbacks_immediate=False).run()
        self.assertEqual(len(r.callbacks), 1)
        r.host._active_external_karafun = {"entry": r.entry}
        r.callbacks.pop(0)()
        r.now, callback = r.timers.pop(0)
        callback()
        r.callbacks.pop(0)()
        r.host._finish_external_karafun_playback.assert_not_called()

    def test_empty_queue_is_never_used_as_an_idle_signal(self):
        r = MonitorReplay([(1, "")]).run()
        self.assertNotIn('n contains "your queue is empty"', r.scripts[0])


if __name__ == "__main__":
    unittest.main()
