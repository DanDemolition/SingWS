"""Source guard: the Python lines that rust/crates/singws-karafun/src/startflow.rs transcribes. If one changes, the Rust rule must be re-checked."""
import unittest
from pathlib import Path

SRC = (Path(__file__).parent / "0.2.18.1.py").read_text(encoding="utf-8")

LINES = [
    'initial_probe_state = (\n                        managed_start_state if managed_handoff else "PLAYING"',
    'managed_state_unknown = bool(\n                    fast_start and managed_handoff and initial_probe_state == "UNKNOWN"',
    "if (not (ok and initial_probe_state == \"PLAYING\")\n                        and not managed_state_unknown\n                        and not entry.get(\"karafun_explicit_play_sent\")):",
    "verified_playing = bool(\n                    ok and initial_probe_state == \"PLAYING\" and not fast_start",
    "max_probe_attempts = 12 if karafun_was_running else 30",
    'auto_adjusted = bool(adjust_ok and done == f"ADJUSTED|{key_steps}|{tempo_steps}")',
    'key_ok = requested_key == 0 or "|true|" in lowered_adjusted',
    'tempo_ok = requested_tempo == 100 or lowered_adjusted.endswith("|true")',
]


class StartflowSourceGuard(unittest.TestCase):
    def test_transcribed_python_is_unchanged(self):
        for line in LINES:
            self.assertIn(line, SRC)


if __name__ == "__main__":
    unittest.main()
