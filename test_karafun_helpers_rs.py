"""The worker's nested helpers (once-only BGM fade and early handoff, the native double-click retry, the learning watcher): the closures are still the shape the replay harness
reads, the learning rule behaves, and the Rust helpers equal them."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tools"))
import karafun_helpers_replay as fr  # noqa: E402

BIN = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")


class Closures(unittest.TestCase):
    def test_the_closures_are_found_with_exactly_the_outside_names_the_harness_provides(self):
        self.assertTrue(callable(fr.load()))

    def test_the_bgm_fade_and_the_early_handoff_act_once(self):
        helpers = fr.load()
        sc = {"settings": {"karafun_native_double_click": False}, "entry": {}, "play_p": 0.5, "ops": [["bgm", "a"], ["bgm", "b"], ["early", "a"], ["early", "b"]]}
        out = fr.run(helpers, sc, __import__("random").Random(1))
        self.assertEqual(sum(1 for c in out["calls"] if c[0] == "fade_bg"), 1)
        self.assertEqual(sum(1 for c in out["calls"] if c[0] == "handoff_show_screen"), 1)

    def test_a_native_click_that_started_the_song_is_learned_for_next_time(self):
        helpers = fr.load()
        answers = [{"kind": "session_current", "v": True}, {"kind": "menu_state", "v": "PLAYING"}, {"kind": "session_current", "v": True}, {"kind": "session_current", "v": True}]
        sc = {"settings": {"karafun_native_double_click": False}, "entry": {"karafun_native_retry_sent": True}, "play_p": 1.0, "ops": [["watch", [1, 2]]]}
        out = fr.run(helpers, sc, answers=answers)
        self.assertTrue(out["settings"]["karafun_native_double_click"])
        self.assertTrue(any("learned: native" in l for l in out["log"]))


@unittest.skipUnless(BIN.is_file(), "needs the built singws-karafun helper")
class WithRust(unittest.TestCase):
    def test_rust_equals_python_on_a_small_run(self):
        import karafun_helpers_parity as fp
        self.assertEqual(fp.main(["--seeds", "400", "--binary", str(BIN)]), 0)


if __name__ == "__main__":
    unittest.main()
