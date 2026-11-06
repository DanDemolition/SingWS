"""The Rust copy of the KaraFun search script builder (rust/crates/singws-karafun) must follow the Python (0.2.18.1.py).
- the generated script_lines.rs must match the Python source (tools/gen_karafun_script_rs.py --check): changing the Python script without
  regenerating fails here;
- when the Rust CLI is built, every corpus case must give the same title, literal and script lines (tools/karafun_script_parity.py)."""
import _test_profile  # noqa: F401
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BINARY = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")


class KarafunScriptRustTests(unittest.TestCase):
    def test_generated_rust_lines_match_the_python_source(self):
        proc = subprocess.run([sys.executable, str(ROOT / "tools/gen_karafun_script_rs.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    @unittest.skipUnless(BINARY.exists(), "singws-karafun is not built (cargo build --release -p singws-karafun)")
    def test_rust_builder_matches_python_on_the_corpus(self):
        proc = subprocess.run([sys.executable, str(ROOT / "tools/karafun_script_parity.py"), "--seeds", "300", "--binary", str(BINARY)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout[-1500:] + proc.stderr[-500:])
        self.assertIn("IDENTICAL", proc.stdout)


if __name__ == "__main__":
    unittest.main()
