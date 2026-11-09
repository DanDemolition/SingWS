"""phrase_detect's waveform analysis vs the Rust singws-phrase, within tolerance (tools/phrase_parity.py)."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
TARGET = Path(os.environ.get("CARGO_TARGET_DIR") or Path.home() / ".cache/singws-rust-target-karafun")


@unittest.skipUnless((TARGET / "release/singws-phrase").is_file(), "needs the built singws-phrase")
class Twin(unittest.TestCase):
    def test_agreement_stays_above_the_threshold(self):
        out = subprocess.run([sys.executable, str(ROOT / "tools/phrase_parity.py"), "--cases", "60"], capture_output=True, text=True, timeout=900, env=dict(os.environ, CARGO_TARGET_DIR=str(TARGET)))
        self.assertEqual(out.returncode, 0, out.stdout[-900:] + out.stderr[-400:])
        self.assertIn("AGREE", out.stdout)


if __name__ == "__main__":
    unittest.main()
