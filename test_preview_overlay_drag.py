"""The transport bar over the host preview: draggable inside the picture, snaps back to its default place, more see-through."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import importlib.util
import os
import unittest


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_ovdrag", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class OverlayDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def test_position_round_trips_through_fractions_and_survives_a_resize(self):
        from PyQt6.QtCore import QPoint, QRect, QSize
        m = self.m
        bounds, size = QRect(100, 200, 500, 300), QSize(480, 46)
        tl = QPoint(110, 260)
        frac = m.overlay_frac_from_pos(tl, bounds, size)
        self.assertEqual(m.overlay_pos_from_frac(frac, bounds, size), tl)
        bigger = QRect(100, 200, 700, 420)                         # the preview grows: the bar keeps its relative place
        x, y = m.overlay_pos_from_frac(frac, bigger, QSize(680, 46)).x(), m.overlay_pos_from_frac(frac, bigger, QSize(680, 46)).y()
        self.assertGreater(y, 260)
        self.assertGreaterEqual(x, bigger.left())

    def test_fractions_are_clamped_inside_the_picture(self):
        from PyQt6.QtCore import QPoint, QRect, QSize
        bounds, size = QRect(0, 0, 500, 300), QSize(480, 46)
        self.assertEqual(self.m.overlay_frac_from_pos(QPoint(-50, 9999), bounds, size), (0.0, 1.0))

    def test_near_the_default_place_snaps_far_from_it_does_not(self):
        from PyQt6.QtCore import QPoint
        default = QPoint(110, 454)
        self.assertTrue(self.m.overlay_snap(QPoint(120, 440), default))       # 24 px away: snaps back
        self.assertFalse(self.m.overlay_snap(QPoint(110, 300), default))      # well away: stays where it was dropped
        self.assertTrue(self.m.overlay_snap(default, default))

    def test_the_bar_is_more_see_through_than_before(self):
        self.assertLess(self.m.PreviewControlsWindow.BAR.alpha(), 184)

    def test_drag_snap_and_reset_are_wired(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"), encoding="utf-8").read()
        cls = src[src.index("class PreviewControlsWindow"):src.index("class SliderGlow")]
        for name in ("mousePressEvent", "mouseMoveEvent", "mouseReleaseEvent", "mouseDoubleClickEvent", "contextMenuEvent"):
            self.assertIn(f"def {name}", cls)
        self.assertIn("overlay_reset_place", cls)
        self.assertIn("preview_overlay_frac", src)                 # remembered between launches

    def test_the_bar_hides_unless_the_pointer_is_over_the_preview(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"), encoding="utf-8").read()
        poll = src[src.index("    def poll_overlay_hover"):src.index("    def overlay_dragged_to")]
        self.assertIn("QCursor.pos()", poll)
        self.assertIn("preview_overlay_autohide", poll)
        self.assertIn("ov.dragging or ov.menu_open", poll)             # never vanishes under the pointer that is dragging it or under its own menu
        self.assertIn("fade_to(0.0)", poll)
        self.assertIn("hover.timeout.connect(pw.poll_overlay_hover)", src)
        place = src[src.index("    def place_overlay"):src.index("    def _overlay_geometry")]
        self.assertIn("_overlay_revealed", place)                        # the 1.5 s guard may not bring a hidden bar back


if __name__ == "__main__":
    unittest.main()
