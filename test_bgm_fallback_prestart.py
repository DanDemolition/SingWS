"""The fallback BGM pre-start (used when a song's audio end could not be verified) is a setting, default 5 s, clamped to 2..8 s."""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


class FallbackPrestartTests(unittest.TestCase):
    def test_default_is_five_seconds(self):
        self.assertRegex(SRC, r'"bgm_fallback_prestart_sec":\s*5\.0')

    def test_window_comes_from_the_setting_and_is_clamped(self):
        m = re.search(r"crossfade_window_ns = int\(max\(2\.0, min\(8\.0, _lead_s\)\) \* 1_000_000_000\)", SRC)
        self.assertIsNotNone(m, "the pre-start window must be clamped to 2..8 s")
        self.assertIn('self.settings.get("bgm_fallback_prestart_sec", 5.0)', SRC)

    def test_old_fixed_window_is_gone(self):
        self.assertNotIn("crossfade_window_ns = 3_200_000_000", SRC)


if __name__ == "__main__":
    unittest.main()
