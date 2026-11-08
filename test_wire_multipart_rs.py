"""Multipart uploads: the Rust singws-wire vs requests (tools/wire_multipart_parity.py)."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
CLI = Path.home() / ".cache/singws-rust-target/release/singws-wire-cli"


@unittest.skipUnless(CLI.is_file(), "needs the built singws-wire-cli")
class Twin(unittest.TestCase):
    def test_a_short_run_is_identical_on_both_sides(self):
        out = subprocess.run([sys.executable, str(ROOT / "tools/wire_multipart_parity.py"), "--cases", "300"], capture_output=True, text=True, timeout=300)
        self.assertEqual(out.returncode, 0, out.stdout[-900:] + out.stderr[-400:])
        self.assertIn("IDENTICAL", out.stdout)


if __name__ == "__main__":
    unittest.main()
