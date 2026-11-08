"""The KaraFun stretches run in order on one entry (tools/karafun_chain_parity.py): what one stretch leaves is what the next is proven on."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
BIN = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")


@unittest.skipUnless(BIN.is_file(), "needs the built singws-karafun helper")
class Chain(unittest.TestCase):
    def test_a_short_chain_is_identical_on_both_sides(self):
        out = subprocess.run([sys.executable, str(ROOT / "tools/karafun_chain_parity.py"), "--seeds", "60", "--binary", str(BIN)], capture_output=True, text=True, timeout=600)
        self.assertEqual(out.returncode, 0, out.stdout[-800:] + out.stderr[-400:])
        self.assertIn("IDENTICAL", out.stdout)


if __name__ == "__main__":
    unittest.main()
