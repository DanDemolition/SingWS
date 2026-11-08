"""The KaraFun preview geometry: the three Python functions are still where the parity tool reads them, and the Rust twin equals them on a small run."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tools"))
import karafun_geometry_parity as gp  # noqa: E402

BIN = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")


class Source(unittest.TestCase):
    def test_the_functions_and_constants_are_found(self):
        ns = gp.load()
        self.assertEqual(ns["KARAFUN_PREVIEW_OVERLAY_MARGIN"], 14.0)
        self.assertEqual(ns["KARAFUN_FILL_ZOOM_OUT"], 1.08)


@unittest.skipUnless(BIN.is_file(), "needs the built singws-karafun helper")
class WithRust(unittest.TestCase):
    def test_rust_equals_python_on_a_small_run(self):
        self.assertEqual(gp.main(["--seeds", "300", "--binary", str(BIN)]), 0)


if __name__ == "__main__":
    unittest.main()
