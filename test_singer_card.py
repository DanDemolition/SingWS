"""Karaoke screen 'Now singing' card (setting ui_singer_card, default on): its own see-through, click-through window; display only."""
import unittest
from pathlib import Path

SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


class SingerCardTests(unittest.TestCase):
    def test_default_checkbox_and_hooks(self):
        self.assertIn('"ui_singer_card": True', SRC)
        self.assertIn('QCheckBox("Show a Now Singing card on the karaoke screen at the start of each song")', SRC)
        self.assertEqual(SRC.count("_singer_card_hook(self))"), 2)            # native and KaraFun song starts
        self.assertIn('getattr(app, "_show_singer_card", None)', SRC)         # a stand-in app without it is fine

    def test_it_is_its_own_window_like_the_ticker_and_never_takes_focus_or_clicks(self):
        cls = SRC[SRC.index("class SingerCardWindow"):SRC.index("class VideoWindow")]
        for flag in ("Qt.WindowType.Tool", "FramelessWindowHint", "WindowDoesNotAcceptFocus", "WindowTransparentForInput"):
            self.assertIn(flag, cls)
        self.assertIn("WA_TranslucentBackground", cls)

    def test_it_only_orders_its_own_window_above_the_audience_window_and_fades_out(self):
        body = SRC[SRC.index("    def show_singer_card"):SRC.index("    def showEvent", SRC.index("    def show_singer_card"))]
        self.assertIn("PreviewWindow._order_overlay_above_host(card, self)", body)   # same recipe as the ticker / preview bar
        self.assertIn("windowOpacity", body)
        self.assertIn("seconds", body)
        for forbidden in ("self.raise_()", "activateWindow", "setParent("):
            self.assertNotIn(forbidden, body)                                         # the audience window and its native views are never touched


if __name__ == "__main__":
    unittest.main()
