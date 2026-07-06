"""Tests for the render-thread ticker and the show-screen request QR.

Ticker: the Qt Quick RenderThreadTicker must load, format the queue text
identically to the legacy Ticker, drive its QML properties, defer mid-scroll
queue changes, and clear immediately on an empty queue. Falls back to the
legacy Ticker in VideoWindow if Qt Quick is unavailable (not exercised here;
that path just constructs the old class).

QR: VideoAreaWidget.set_request_qr paints the QR bottom-right, right-aligned
to the ticker countdown timer's right edge (TICKER_SIZE_PRESETS right_margin),
and clears when set to None. The host's _refresh_show_screen_qr gates on the
show_request_qr setting AND requests_accepting.

Needs a QApplication; runs under the offscreen QPA platform.
"""

import importlib.util
import os
import sys
import unittest

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtGui import QPixmap, QColor, QImage, QPainter
from PyQt6.QtCore import Qt

_APP = None


def setUpModule():
    global _APP
    _APP = QApplication.instance() or QApplication(sys.argv)


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_ticker_qr", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_main_module()


def _qtquick_available() -> bool:
    try:
        from PyQt6.QtQuick import QQuickView  # noqa: F401
        return True
    except Exception:
        return False


class _Owner:
    def __init__(self, **settings):
        base = {"ticker_size_index": 2, "ticker_bold": False,
                "ticker_speed_px_per_sec": 78.0}
        base.update(settings)
        self.settings = base

    def _effective_ticker_speed_px_per_sec(self, s):
        return s


@unittest.skipUnless(_qtquick_available(), "PyQt6.QtQuick unavailable")
class RenderThreadTickerTests(unittest.TestCase):
    def _make(self, singers, message="", time_text="3:00"):
        def get_list():
            return (list(singers), message)

        def get_time():
            return time_text

        t = mod.RenderThreadTicker(get_list, parent=None, get_time_left_callback=get_time)
        t._external_settings_owner = _Owner()
        return t

    def test_loads_and_formats_queue_text_like_legacy(self):
        t = self._make(["Alice", "Bob", "Carol"], message="Welcome!")
        t.update_queue_text(force=True)
        expected = "Welcome!   |   1. Alice   |   2. Bob   |   3. Carol"
        self.assertEqual(t._root.property("displayText"), expected)

    def test_size_preset_sets_height_and_font_props(self):
        t = self._make(["A"])
        t.set_size_preset(4)
        h, names_px, timer_px, right_margin, gap, timer_pad = mod.TICKER_SIZE_PRESETS[4]
        self.assertEqual(t.height(), h)
        self.assertEqual(t._root.property("namesPx"), names_px)
        self.assertEqual(t._root.property("timerPx"), timer_px)

    def test_scroll_speed_drives_qml_and_clamps(self):
        t = self._make(["A"])
        t.set_scroll_speed(95.0)
        self.assertAlmostEqual(t._root.property("speedPxPerSec"), 95.0, places=3)
        # Above the max clamp
        t.set_scroll_speed(10_000.0)
        self.assertLessEqual(t._root.property("speedPxPerSec"), mod.TICKER_SPEED_MAX)

    def test_bold_and_color(self):
        t = self._make(["A"])
        t.set_bold(True)
        self.assertTrue(t._root.property("tickerBold"))
        t.set_color("#00FF00")
        self.assertEqual(t._root.property("tickerColor").name().lower(), "#00ff00")

    def test_right_text_updates(self):
        t = self._make(["A"], time_text="2:47")
        t.update_right_text()
        self.assertEqual(t._root.property("rightText"), "2:47")

    def test_mid_scroll_queue_change_is_deferred(self):
        t = self._make(["Alice"], message="")
        t.update_queue_text(force=True)  # displayText = "1. Alice"
        first = t._root.property("displayText")
        # Change the queue while a pass is "running": new text must go to
        # pendingText (deferred to the wrap), not replace displayText live.
        t.get_singer_list_callback = lambda: (["Alice", "Bob"], "")
        t.update_queue_text()
        self.assertEqual(t._root.property("displayText"), first)
        self.assertEqual(t._root.property("pendingText"), "1. Alice   |   2. Bob")

    def test_empty_queue_clears_immediately(self):
        t = self._make(["Alice"], message="")
        t.update_queue_text(force=True)
        t.get_singer_list_callback = lambda: ([], "")
        t.update_queue_text()
        self.assertEqual(t._root.property("displayText"), "")

    def test_start_stop_and_hold(self):
        t = self._make(["A"])
        t.stop_scrolling()
        self.assertFalse(t._root.property("running"))
        t.start_scrolling()
        self.assertTrue(t._root.property("running"))
        t.hold_rendering()
        self.assertTrue(t._root.property("churnHold"))
        t.resume_rendering()
        self.assertFalse(t._root.property("churnHold"))

    def test_force_refresh_reapplies(self):
        t = self._make(["A"], message="Hi")
        t.update_queue_text(force=True)
        # force_refresh_now should re-push even if text unchanged
        t._root.setProperty("displayText", "STALE")
        t.force_refresh_now()
        self.assertEqual(t._root.property("displayText"), "Hi   |   1. A")


class QrPaintPositionTests(unittest.TestCase):
    def setUp(self):
        self._keepalive = []  # retain parents so Qt doesn't GC the child area

    def _area(self, w=1280, h=700, ticker_idx=2):
        owner = _Owner(ticker_size_index=ticker_idx)

        class ParentStub(QWidget):
            pass

        parent = ParentStub()
        parent._external_owner = owner
        parent.resize(w, h + 60)
        area = mod.VideoAreaWidget(parent)
        area.resize(w, h)
        self._keepalive.append((parent, owner, area))
        return area, owner

    def _render(self, area):
        img = QImage(area.size(), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.black)
        p = QPainter(img)
        area.render(p)
        p.end()
        return img

    def _magenta_bbox(self, img, step=2):
        xs, ys = [], []
        for y in range(0, img.height(), step):
            for x in range(0, img.width(), step):
                c = img.pixelColor(x, y)
                if c.red() > 200 and c.blue() > 200 and c.green() < 60:
                    xs.append(x)
                    ys.append(y)
        return (min(xs), max(xs), min(ys), max(ys), len(xs)) if xs else None

    def test_ticker_timer_right_margin_reads_preset(self):
        area, _ = self._area(ticker_idx=3)
        self.assertEqual(area._ticker_timer_right_margin(), mod.TICKER_SIZE_PRESETS[3][3])

    def test_qr_paints_bottom_right_by_timer(self):
        area, _ = self._area(w=1280, h=700, ticker_idx=2)
        qr = QPixmap(300, 300)
        qr.fill(QColor(255, 0, 255))
        area.set_request_qr(qr)
        bbox = self._magenta_bbox(self._render(area))
        self.assertIsNotNone(bbox, "QR not painted")
        minx, maxx, miny, maxy, n = bbox
        right_margin = mod.TICKER_SIZE_PRESETS[2][3]
        # Right edge aligns with the timer's right edge (width - right_margin).
        self.assertAlmostEqual(maxx, area.width() - right_margin, delta=6)
        # Bottom is a small margin above the video area's bottom (above ticker).
        self.assertGreater(maxy, area.height() * 0.6)
        self.assertLess(maxy, area.height())

    def test_qr_clear_removes_it(self):
        area, _ = self._area()
        qr = QPixmap(300, 300)
        qr.fill(QColor(255, 0, 255))
        area.set_request_qr(qr)
        self.assertIsNotNone(self._magenta_bbox(self._render(area)))
        area.set_request_qr(None)
        self.assertIsNone(self._magenta_bbox(self._render(area)))

    def test_qr_skipped_on_tiny_widget(self):
        area, _ = self._area(w=100, h=100)
        qr = QPixmap(300, 300)
        qr.fill(QColor(255, 0, 255))
        area.set_request_qr(qr)
        # Below the 120px guard: nothing painted.
        self.assertIsNone(self._magenta_bbox(self._render(area)))


class ShowScreenQrGatingTests(unittest.TestCase):
    """Host gating: QR shows only when the setting is on AND accepting, with a
    non-empty URL. Uses a bare KaraokeApp + a stub video area."""

    class _StubArea:
        def __init__(self):
            self.calls = []

        def set_request_qr(self, pm):
            self.calls.append(pm)

    class _StubVideoWindow:
        def __init__(self, area):
            self.video_area = area

    def make_app(self, *, enabled=True, accepting=True, url="https://x/tenants/t/"):
        app = mod.KaraokeApp.__new__(mod.KaraokeApp)
        app.settings = {"show_request_qr": enabled, "requests_accepting": accepting}
        app._show_screen_qr_key = None
        area = self._StubArea()
        app.video_window = self._StubVideoWindow(area)
        app._is_requests_accepting_cached = lambda: bool(accepting)
        app._header_qr_url = lambda: url
        app._build_qr_pixmap = lambda u, size=300: self._nonnull_pixmap()
        return app, area

    @staticmethod
    def _nonnull_pixmap():
        pm = QPixmap(10, 10)
        pm.fill(QColor("white"))
        return pm

    def test_shows_when_enabled_and_accepting(self):
        app, area = self.make_app(enabled=True, accepting=True)
        app._refresh_show_screen_qr("test")
        self.assertEqual(len(area.calls), 1)
        self.assertIsNotNone(area.calls[-1])
        self.assertFalse(area.calls[-1].isNull())

    def test_cleared_when_not_accepting(self):
        app, area = self.make_app(enabled=True, accepting=False)
        app._refresh_show_screen_qr("test")
        self.assertEqual(area.calls[-1], None)

    def test_cleared_when_disabled(self):
        app, area = self.make_app(enabled=False, accepting=True)
        app._refresh_show_screen_qr("test")
        self.assertEqual(area.calls[-1], None)

    def test_cleared_when_no_url(self):
        app, area = self.make_app(enabled=True, accepting=True, url="")
        app._refresh_show_screen_qr("test")
        self.assertEqual(area.calls[-1], None)

    def test_cache_skips_redundant_rebuild(self):
        app, area = self.make_app(enabled=True, accepting=True)
        app._refresh_show_screen_qr("first")
        app._refresh_show_screen_qr("second")  # same state -> no new call
        self.assertEqual(len(area.calls), 1)
        # force bypasses the cache
        app._refresh_show_screen_qr("third", force=True)
        self.assertEqual(len(area.calls), 2)


if __name__ == "__main__":
    unittest.main()
