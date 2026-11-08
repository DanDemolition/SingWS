"""The whole KaraFun worker, orchestration included (tools/karafun_worker_replay.py runs the real method; tools/karafun_worker_parity.py compares it with the Rust worker.rs)."""
import os
import random
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tools"))
import karafun_worker_replay as wr  # noqa: E402

BIN = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")


class RealMethod(unittest.TestCase):
    def test_the_real_method_runs_to_a_queued_song_in_the_replay_world(self):
        loaded = wr.load()
        states = set()
        for seed in range(200):
            r = random.Random(seed)
            out = wr.run(loaded, wr.random_scenario(r), r)
            states.add(out["entry"].get("karafun_submission_state"))
        self.assertIn("karafun_queued", states)
        self.assertIn("karafun_failed", states)

    def test_the_worker_thread_starts_once_and_finish_runs_on_the_ui_thread(self):
        loaded = wr.load()
        for seed in range(300):
            r = random.Random(seed)
            sc = wr.random_scenario(r)
            sc.update(active_ok=True, end_session_at=None)
            sc["settings"]["karafun_auto_queue_enabled"] = True
            out = wr.run(loaded, sc, r)
            names = [c[0] for c in out["calls"]]
            if "thread" in names:
                self.assertEqual(sum(1 for c in out["calls"] if c[0] == "thread" and c[2].get("thread_name") == "karafun-search-play"), 1)
                self.assertEqual(names.index("thread") < names.index("ui_thread", names.index("thread")), True)
                return
        self.fail("no scenario reached the worker thread")


@unittest.skipUnless(BIN.is_file(), "needs the built singws-karafun helper")
class Twin(unittest.TestCase):
    def test_a_short_run_is_identical_on_both_sides(self):
        out = subprocess.run([sys.executable, str(ROOT / "tools/karafun_worker_parity.py"), "--seeds", "120", "--binary", str(BIN)], capture_output=True, text=True, timeout=900)
        self.assertEqual(out.returncode, 0, out.stdout[-900:] + out.stderr[-400:])
        self.assertIn("IDENTICAL", out.stdout)


if __name__ == "__main__":
    unittest.main()
