"""The KaraFun setup at the top of the worker: the segment of 0.2.18.1.py is still the shape the replay harness reads, and the Rust setup equals it on a small run."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tools"))
import karafun_setup_replay as fr  # noqa: E402

BIN = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")


class Segment(unittest.TestCase):
    def test_the_segment_is_found_with_exactly_the_outside_names_the_harness_provides(self):
        setup, n = fr.load()   # asserts the start statement, the end statement and the free names
        self.assertGreaterEqual(n, 10)

    def test_a_scenario_runs_and_records(self):
        import random
        setup, _ = fr.load()
        r = random.Random(1)
        out = fr.run(setup, fr.random_scenario(r), r)
        self.assertIn("ok", out["result"])
        self.assertTrue(out["calls"] or not out["result"]["ok"])


@unittest.skipUnless(BIN.is_file(), "needs the built singws-karafun helper")
class WithRust(unittest.TestCase):
    def test_rust_equals_python_on_a_small_run(self):
        import karafun_setup_parity as fp
        self.assertEqual(fp.main(["--seeds", "300", "--binary", str(BIN)]), 0)


if __name__ == "__main__":
    unittest.main()
