"""Audience ticker style B (setting ui_ticker_glass, default on): painter and QML tickers draw a glass pill inside the same opaque bar."""
import unittest
from pathlib import Path

SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


class TickerGlassTests(unittest.TestCase):
    def test_default_and_wiring(self):
        self.assertIn('"ui_ticker_glass": True', SRC)
        self.assertIn('QCheckBox("Use the glass pill look for the audience ticker")', SRC)
        self.assertIn("QTimer.singleShot(0, self._apply_ticker_glass)", SRC)

    def test_the_bar_and_window_are_not_changed_only_painted(self):
        paint = SRC[SRC.index("class Ticker(QFrame):"):SRC.index("class DetachedPainterTicker")]
        self.assertIn('painter.fillRect(self.rect(), QColor("black"))', paint)       # the bar stays opaque black
        self.assertIn("glass = bool(_TICKER_GLASS)", paint)
        qml = SRC[SRC.index('QML_TICKER_RT_SOURCE = """'):SRC.index("class RenderThreadTicker")]
        self.assertIn("property bool glass: false", qml)
        self.assertIn("radius: root.glass ? height / 2 : Math.min(13, height / 2)", qml)
        self.assertNotIn("WA_TranslucentBackground", SRC[SRC.index("class DetachedPainterTicker"):SRC.index("class DetachedPainterTicker") + 6000])

    def test_the_rotation_specials_tag_is_a_rounded_pill_only_with_the_glass_look(self):
        body = SRC[SRC.index("class RotationAnnouncementTicker"):SRC.index("class RotationAnimatedBackdrop")]
        self.assertIn("if _TICKER_GLASS:", body)
        self.assertIn("border-radius: 14px", body)
        self.assertIn("font-size: 30px", body)                    # the classic tag is still there for the way back


if __name__ == "__main__":
    unittest.main()
