"""The CD+G visual analysis and the MP4 tail analysis: the app's own transition_analysis functions vs the Rust singws_cdg::visual (tools/cdg_visual_parity.py)."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
TARGET = Path(os.environ.get("CARGO_TARGET_DIR") or Path.home() / ".cache/singws-rust-target-cdg")


@unittest.skipUnless((TARGET / "release/singws-cdg-visual-cli").is_file(), "needs the built singws-cdg-visual-cli")
class Twin(unittest.TestCase):
    def test_a_short_run_is_identical_on_both_sides(self):
        out = subprocess.run([sys.executable, str(ROOT / "tools/cdg_visual_parity.py"), "--seeds", "400"], capture_output=True, text=True, timeout=600, env=dict(os.environ, CARGO_TARGET_DIR=str(TARGET)))
        self.assertEqual(out.returncode, 0, out.stdout[-900:] + out.stderr[-400:])
        self.assertIn("IDENTICAL", out.stdout)


if __name__ == "__main__":
    unittest.main()
