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


@unittest.skipUnless(BINARY.exists(), "singws-karafun is not built (cargo build --release -p singws-karafun)")
class FlowParityTests(unittest.TestCase):
    """The search ladder and the completion monitor: the real Python statements/method against the Rust ports on scripted answers."""

    def run_tool(self, tool, seeds):
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / tool), "--seeds", str(seeds), "--binary", str(BINARY)], capture_output=True, text=True,
                              env=dict(os.environ, SINGWS_HOME=os.environ.get("SINGWS_HOME", "")))
        self.assertEqual(proc.returncode, 0, proc.stdout[-1500:] + proc.stderr[-500:])
        self.assertIn("IDENTICAL", proc.stdout)

    def test_the_search_ladder_matches_python(self):
        self.run_tool("karafun_ladder_parity.py", 600)

    def test_the_completion_monitor_matches_python(self):
        self.run_tool("karafun_monitor_parity.py", 400)


class TranscribedRuleGuard(unittest.TestCase):
    """The accept/reject rule for a search answer is inline in KaraokeApp._automate_karafun_search_and_play, so the parity tool compares the Rust port with a
    TRANSCRIBED copy. These lines must still be in the Python; if one changed, update tools/karafun_script_parity.py (ref_classify) and pure.rs together."""

    def test_the_inline_rule_is_still_what_was_transcribed(self):
        source = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8")
        for line in (
            'query_has_artist = bool(safe_artist_match) and safe_artist_match.casefold() in query_key',
            'if safe_artist_match and not query_has_artist:',
            'accepted = {"FOUND"}',
            'accepted = {"FOUND", "TITLE_ONLY", "FIRST"}',
            'if len(parts) >= 3 and kind in accepted:',
            'if len(parts) >= 3 and kind in {"FOUND", "TITLE_ONLY", "FIRST"}:',
            'last_error = parts[-1] if parts else "KaraFun search failed"',
            'parts = str(candidate or "").split("|")',
        ):
            self.assertIn(line, source, f"the inline accept/reject rule changed: {line!r}")


if __name__ == "__main__":
    unittest.main()
