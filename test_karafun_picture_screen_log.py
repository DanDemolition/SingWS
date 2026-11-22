"""The KaraFun picture's placement is logged with the screen it went to (2026-11-21 Intel show: the picture/audio on an AirPlay screen could not be judged from the log)."""
import re
import unittest
from pathlib import Path

SRC = Path("karafun_web_host.py").read_text(encoding="utf-8")


class PictureScreenLog(unittest.TestCase):
    def test_placing_the_picture_names_the_screen(self):
        body = SRC[SRC.index("    def _place_picture"):SRC.index("    def _start_frames")]
        self.assertRegex(body, r"the picture is placed on screen \{self\._audience_screen_name\(\)!r\}")
        self.assertIn("the picture is hidden", body)

    def test_the_screen_name_never_raises(self):
        class Host:
            app = object()
        ns = {}
        m = re.search(r"    def _audience_screen_name.*?(?=\n    def )", SRC, re.S)
        exec("\n".join(l[4:] for l in m.group(0).splitlines()), ns)
        self.assertEqual(ns["_audience_screen_name"](Host()), "none")


if __name__ == "__main__":
    unittest.main()
