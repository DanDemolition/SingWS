"""duplicate_song_audit.audit_zip_duplicates vs the Rust singws-scan op `dupes` (tools/dupes_parity.py)."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
TARGET = Path(os.environ.get("CARGO_TARGET_DIR") or Path.home() / ".cache/singws-rust-target-scan")


@unittest.skipUnless((TARGET / "release/singws-scan-cli").is_file(), "needs the built singws-scan-cli")
class Twin(unittest.TestCase):
    def test_a_short_run_is_identical_on_both_sides(self):
        out = subprocess.run([sys.executable, str(ROOT / "tools/dupes_parity.py"), "--cases", "40"], capture_output=True, text=True, timeout=600, env=dict(os.environ, CARGO_TARGET_DIR=str(TARGET)))
        self.assertEqual(out.returncode, 0, out.stdout[-900:] + out.stderr[-400:])
        self.assertIn("IDENTICAL", out.stdout)


if __name__ == "__main__":
    unittest.main()
