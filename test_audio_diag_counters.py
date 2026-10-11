"""The [AUDIO-DIAG] log line format and its host wiring (the BASS audio-callback counters went with BASS in S5)."""
import re
import unittest
from pathlib import Path

from audio_diag import format_audio_diag

ROOT = Path(__file__).resolve().parent


class FormatTests(unittest.TestCase):
    def test_empty_when_nothing_to_report(self):
        self.assertEqual(format_audio_diag(None), "")
        self.assertEqual(format_audio_diag({"master": {"blocks": 0}, "mixer_stalled": 0}), "")

    def test_line_has_the_fields_and_the_tag(self):
        master = {"blocks": 1, "mean_us": 400, "p99_upper_us": 500, "max_us": 400, "over_budget": 0, "gap_max_ms": 0,
                  "gaps_over_100ms": 0, "gaps_over_500ms": 0, "restarts": 0}
        line = format_audio_diag({"master": master, "bass_cpu": 3.14159, "mixer_stalled": 0})
        self.assertTrue(line.startswith("[AUDIO-DIAG] master-dsp"))
        for field in ("blocks=1", "p99<=500us", "over_budget=0", "gap_max=0ms", "bass_cpu=3.1%", "mixer_stalled_polls=0"):
            self.assertIn(field, line)


class HostWiringTests(unittest.TestCase):
    SRC = (ROOT / "0.2.18.1.py").read_text()

    def test_setting_exists_and_defaults_on(self):
        self.assertRegex(self.SRC, r'"audio_diag_counters":\s*True')

    def test_host_logs_once_a_minute_and_can_be_switched_off(self):
        self.assertIn("def _start_audio_diag_counters", self.SRC)
        self.assertIn("timer.setInterval(60000)", self.SRC)
        self.assertIn('parent.settings.get("audio_diag_counters", True)', self.SRC)
        self.assertIn("engine.set_audio_diagnostics(enabled)", self.SRC)
        self.assertIn("format_audio_diag", self.SRC)


if __name__ == "__main__":
    unittest.main()
