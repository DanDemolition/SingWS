"""Four KaraFun automation methods: the real methods are still where the replay harness copies them from, and the Rust twin equals them on a small run."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tools"))
import karafun_methods_parity as mp  # noqa: E402
import karafun_methods_replay as mr  # noqa: E402

BIN = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")


class Source(unittest.TestCase):
    def test_the_methods_and_constants_are_found(self):
        mod, consts = mr.load()
        self.assertEqual(consts["KARAFUN_COLD_SETTLE_S"], 2.0)
        self.assertEqual(consts["KARAFUN_READY_TIMEOUT_S"], 60.0)


@unittest.skipUnless(BIN.is_file(), "needs the built singws-karafun helper")
class WithRust(unittest.TestCase):
    def test_rust_equals_python_on_a_small_run(self):
        self.assertEqual(mp.main(["--seeds", "150", "--binary", str(BIN)]), 0)


if __name__ == "__main__":
    unittest.main()
