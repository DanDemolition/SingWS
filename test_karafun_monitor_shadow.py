"""KaraFun completion-monitor shadow: the recorder inside the monitor, replayed through the Rust monitor when the loop ends, must (a) say nothing when the
two agree, (b) say which pass differs when they do not, (c) never reach into the monitor, and (d) stay silent for a host without settings."""
import _test_profile  # noqa: F401
import os
import sys
import threading
import unittest
from pathlib import Path

import karafun_monitor_shadow as shadow

ROOT = Path(__file__).resolve().parent
BINARY = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")
SOURCE = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("SINGWS_KARAFUN_BIN", str(BINARY))


def reset():
    shadow._seen.clear()
    shadow._clean_noted = False
    shadow.last_thread = None


@unittest.skipUnless(BINARY.exists(), "singws-karafun is not built (cargo build --release -p singws-karafun)")
class LiveReplayTests(unittest.TestCase):
    def setUp(self):
        reset()
        import karafun_monitor_parity as mp
        self.mp = mp
        self.loaded = mp.load()

    def run_scenario(self, c):
        c = dict(c, shadow_settings={"karafun_monitor_shadow": True})
        r = self.mp.Replay(self.loaded, c)
        out = r.run()
        if shadow.last_thread is not None:
            shadow.last_thread.join(30)
        return out

    def test_the_real_monitor_and_the_rust_monitor_agree_on_many_runs(self):
        mismatches, clean, ran = [], 0, 0
        for seed in range(0, 60):
            reset()
            out = self.run_scenario(self.mp.scenario(seed))
            ran += 1
            mismatches += [l for l in out["log"] if "MISMATCH" in l]
            clean += any("first song clean" in l for l in out["log"])
        self.assertEqual(mismatches, [], mismatches[:3])
        self.assertGreater(clean, 0, "no run reached the replay")

    def test_a_run_that_completes_is_replayed_clean(self):
        c = self.mp.base(events=[(5, "STATE|PLAYING"), (5, "STATE|PLAYING"), (263, "STATE|IDLE")])
        out = self.run_scenario(c)
        self.assertTrue(any("reason=karaFun_idle" in l for l in out["log"]))
        self.assertTrue(any("[KARAFUN-MONITOR-SHADOW] first song clean" in l for l in out["log"]), out["log"][-4:])
        self.assertFalse(any("MISMATCH" in l for l in out["log"]))


@unittest.skipUnless(BINARY.exists(), "singws-karafun is not built")
class CompareTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.log = []

    def request(self):
        cfg = {"title": "Secret Song", "duration": 263, "duration_estimated": False, "playback_assumed": True, "handoff_timed_out_before_play": False,
               "controlled_start": False, "capture_session": False, "recovery_delay": 12, "handoff_recovery_delay": 6, "alert_delay": 40}
        passes = [{"now_before": 100.0 + i * 6, "probe_elapsed": 5.0, "ok": True, "raw": raw, "status_is_manual": False, "seek_offset": 0.0, "display_times": None,
                   "kt_running": False, "kt_dirty": False, "kt_busy": False, "press_ok": True, "press_error": ""}
                  for i, raw in enumerate(["STATE|PLAYING", "STATE|PLAYING", "STATE|PLAYING"])]
        return {"op": "monitor_replay", "cfg": cfg, "now": 100.0, "started": 100.0, "seek_offset": 0.0, "passes": passes}

    def test_agreement_is_silent_except_for_one_clean_line(self):
        self.assertEqual(shadow.replay_and_compare(self.log.append, BINARY, self.request(), []), "clean")
        self.assertEqual(shadow.replay_and_compare(self.log.append, BINARY, self.request(), []), "clean")
        self.assertEqual(len(self.log), 1)

    def test_a_difference_names_the_pass_and_the_actions_only(self):
        # the app claims it completed on pass 1; the Rust monitor (three PLAYING passes) never does
        result = shadow.replay_and_compare(self.log.append, BINARY, self.request(), [(1, "complete")])
        self.assertEqual(result, "mismatch")
        self.assertEqual(self.log, ["[KARAFUN-MONITOR-SHADOW] MISMATCH pass 1: app=['complete'] rust=[]"])
        self.assertNotIn("Secret", "\n".join(self.log))
        n = len(self.log)
        shadow.replay_and_compare(self.log.append, BINARY, self.request(), [(1, "complete")])
        self.assertEqual(len(self.log), n, "the same difference is not repeated")


class RecorderTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_it_is_silent_and_inert_without_a_settings_dict_or_when_off(self):
        app = type("A", (), {})()
        self.assertIs(shadow.start(app, {}, print, started=1.0, now=1.0), shadow.NULL)
        app.settings = {"karafun_monitor_shadow": False}
        self.assertIs(shadow.start(app, {}, print, started=1.0, now=1.0), shadow.NULL)
        for method in (shadow.NULL.note_pass, shadow.NULL.event, shadow.NULL.finish):
            method(1, 2, 3)

    def test_the_recorder_never_raises_into_the_monitor(self):
        rec = shadow.Recorder(None, {}, print, lambda: 0.0, {}, 0.0, 0.0, 0.0, None)
        rec.note_pass(None, None, "not a number", object(), object())      # garbage in: swallowed
        rec.event("press", object(), object())
        rec.finish()
        rec.finish()

    def test_the_announce_line(self):
        log = []
        self.assertTrue(shadow.announce({}, log.append).startswith("enabled available="))
        self.assertIn("off", shadow.announce({"karafun_monitor_shadow": False}, log.append))
        self.assertIn("[KARAFUN-MONITOR-SHADOW]", log[0])


class WiringTests(unittest.TestCase):
    def test_the_monitor_calls_the_recorder_at_the_decision_points(self):
        for needle in ("karafun_monitor_shadow.start(self, entry, _diag, clock=time.monotonic, started=started, now=now_mono)",
                       "_shadow.note_pass(self, entry, probe_started, ok, raw)", '_shadow.event("press", pressed, press_error)', '_shadow.event("alert")',
                       '_shadow.event("complete")', '_shadow.event("kt")', "_shadow.finish()", "target=_monitor_and_shadow"):
            self.assertIn(needle, SOURCE)
        self.assertIn('"karafun_monitor_shadow": True,', SOURCE)
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            self.assertIn("karafun_monitor_shadow.py", (ROOT / spec).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
