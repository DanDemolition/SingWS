"""Python MpvKaraokeTransport vs the Rust protocol machine, scripted scenarios (tools/karaoke_transport_parity.py). Skips when the Rust CLI is not built:
CARGO_TARGET_DIR=~/.cache/singws-rust-target-karaoke-proto cargo build --release -p singws-karaoke-proto  (in rust/)."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import karaoke_transport_parity as par  # noqa: E402

CASES = par.ROOT / "docs/design/karaoke_transport_cases.json"
CLI = par.find_cli(None)


@unittest.skipUnless(os.path.exists(CLI), "singws-karaoke-proto-cli is not built")
class ParityTests(unittest.TestCase):
    def test_every_scenario_is_identical(self):
        cases = json.loads(CASES.read_text())
        rust = par.run_rust(CLI, CASES)
        for c in cases:
            with self.subTest(c["name"]):
                self.assertEqual(par.run_python(c), rust[c["name"]])

    def test_the_scenarios_are_not_vacuous(self):
        log = par.run_python(next(c for c in json.loads(CASES.read_text()) if c["name"] == "steady_play"))
        texts = [t for _, t in log]
        for needle in ("v.play", "p.play", "event:started"):
            self.assertIn(needle, texts)


if __name__ == "__main__":
    unittest.main()
