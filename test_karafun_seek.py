"""Seeking a KaraFun song from SingWS goes through KaraFun's Skip Forward/Back 10s menu items."""
import re
import unittest
from pathlib import Path

SOURCE = (Path(__file__).parent / "0.2.18.1.py").read_text(encoding="utf-8")


class KarafunSeekWiringTests(unittest.TestCase):
    def test_slider_release_seeks_karafun_when_a_karafun_song_is_active(self):
        body = SOURCE.split("def _on_karaoke_seek_released(self):", 1)[1].split("def ", 1)[0]
        self.assertIn("_karafun_display_times()", body)
        self.assertIn("_karafun_seek_to(", body)

    def test_seek_uses_the_ten_second_menu_items_and_keeps_clear_of_the_end(self):
        body = SOURCE.split("def _karafun_seek_to(self", 1)[1].split("\n    def ", 1)[0]
        self.assertIn('"Skip Forward 10s"', body)
        self.assertIn('"Skip Back 10s"', body)
        self.assertRegex(body, r"float\(duration\) - 8\.0")
        self.assertRegex(body, r"max\(-60, min\(60,")

    def test_seek_moves_the_onscreen_clock_origin(self):
        body = SOURCE.split("def _karafun_seek_to(self", 1)[1].split("\n    def ", 1)[0]
        self.assertIn('entry["karafun_display_started_at"] = min(float(started) - moved, reference)', body)


if __name__ == "__main__":
    unittest.main()
