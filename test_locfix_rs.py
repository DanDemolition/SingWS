"""LocationFixCollector: the app's own class vs the Rust singws-misc (tools/locfix_parity.py)."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
TARGET = Path(os.environ.get("CARGO_TARGET_DIR") or Path.home() / ".cache/singws-rust-target-karafun")


@unittest.skipUnless((TARGET / "release/singws-misc").is_file(), "needs the built singws-misc")
class Twin(unittest.TestCase):
    def test_a_run_is_identical_on_both_sides(self):
        out = subprocess.run([sys.executable, str(ROOT / "tools/locfix_parity.py"), "--seeds", "1500"], capture_output=True, text=True, timeout=300, env=dict(os.environ, CARGO_TARGET_DIR=str(TARGET)))
        self.assertEqual(out.returncode, 0, out.stdout[-900:] + out.stderr[-400:])
        self.assertIn("IDENTICAL", out.stdout)


if __name__ == "__main__":
    unittest.main()
