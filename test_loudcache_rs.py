"""The loudness cache rules, real Python (tools/loudcache_parity.py) vs the Rust singws-loudcache."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
BIN = Path(os.environ.get("CARGO_TARGET_DIR") or Path.home() / ".cache/singws-rust-target-karafun") / "release/singws-loudcache"


@unittest.skipUnless(BIN.is_file(), "needs the built singws-loudcache")
class Twin(unittest.TestCase):
    def test_a_short_run_is_identical_on_both_sides(self):
        out = subprocess.run([sys.executable, str(ROOT / "tools/loudcache_parity.py"), "--seeds", "500"], capture_output=True, text=True, timeout=600, env=dict(os.environ, CARGO_TARGET_DIR=str(BIN.parents[1])))
        self.assertEqual(out.returncode, 0, out.stdout[-900:] + out.stderr[-400:])
        self.assertIn("IDENTICAL", out.stdout)


if __name__ == "__main__":
    unittest.main()
