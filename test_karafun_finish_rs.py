"""The `_finish` closure of the KaraFun worker: it is still the shape the replay harness reads, and the Rust finish equals it on a small run."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tools"))
import karafun_finish_replay as fr  # noqa: E402

BIN = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")


class Closure(unittest.TestCase):
    def test_the_closure_is_found_with_exactly_the_outside_names_the_harness_provides(self):
        self.assertTrue(callable(fr.load()))

    def test_a_successful_result_queues_the_entry_and_a_failed_one_marks_it_failed(self):
        import random
        outer = fr.load()
        sc = fr.random_scenario(random.Random(3))
        sc.update(current=True, result={"ok": True, "message": "ADJUSTED|false|false"})
        self.assertEqual(fr.run(outer, sc)["entry"]["karafun_submission_state"], "karafun_queued")
        sc.update(result={"ok": False, "message": "boom"})
        self.assertEqual(fr.run(outer, sc)["entry"]["karafun_submission_state"], "karafun_failed")
        sc.update(current=False)
        self.assertEqual(fr.run(outer, sc)["calls"], [])


@unittest.skipUnless(BIN.is_file(), "needs the built singws-karafun helper")
class WithRust(unittest.TestCase):
    def test_rust_equals_python_on_a_small_run(self):
        import karafun_finish_parity as fp
        self.assertEqual(fp.main(["--seeds", "300", "--binary", str(BIN)]), 0)


if __name__ == "__main__":
    unittest.main()
