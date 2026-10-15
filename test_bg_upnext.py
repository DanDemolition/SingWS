"""Show tab redesign: the Up Next list in the background-music card reports drops; the app moves the real playlist."""
import importlib.util
import os
import re
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_bgun", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BgUpNextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def make(self, n=4):
        from PyQt6.QtWidgets import QListWidgetItem
        lst = self.m.BgUpNextList()
        for i in range(n):
            lst.addItem(QListWidgetItem(f"Track {i}"))
        lst.resize(400, 44 * n)
        lst.show()
        self.app.processEvents()
        got = []
        lst.reordered.connect(lambda a, b: got.append((a, b)))
        return lst, got

    def drop(self, lst, src_row, y):
        from PyQt6.QtCore import QMimeData, QPointF, Qt
        from PyQt6.QtGui import QDropEvent
        lst.setCurrentRow(src_row)
        ev = QDropEvent(QPointF(100, y), Qt.DropAction.MoveAction, QMimeData(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        lst.dropEvent(ev)

    def row_y(self, lst, row, frac):
        rect = lst.visualRect(lst.model().index(row, 0))
        return rect.top() + int(rect.height() * frac)

    def test_dropping_on_the_lower_half_of_a_later_row_moves_the_track_after_it(self):
        lst, got = self.make()
        self.drop(lst, 0, self.row_y(lst, 2, 0.8))
        self.assertEqual(got, [(0, 2)])

    def test_dropping_on_the_upper_half_of_a_later_row_moves_the_track_before_it(self):
        lst, got = self.make()
        self.drop(lst, 0, self.row_y(lst, 3, 0.2))
        self.assertEqual(got, [(0, 2)])

    def test_moving_a_track_up(self):
        lst, got = self.make()
        self.drop(lst, 3, self.row_y(lst, 1, 0.2))
        self.assertEqual(got, [(3, 1)])

    def test_dropping_below_the_last_row_moves_to_the_end_of_the_shown_rows(self):
        lst, got = self.make()
        self.drop(lst, 1, lst.height() - 1 + 40)
        self.assertEqual(got, [(1, 3)])

    def test_dropping_where_it_already_is_does_nothing_and_never_reorders_the_widget_itself(self):
        lst, got = self.make()
        self.drop(lst, 1, self.row_y(lst, 1, 0.5))
        self.assertEqual(got, [])
        self.assertEqual([lst.item(i).text() for i in range(4)], [f"Track {i}" for i in range(4)])

    def test_the_app_moves_the_one_bgm_tab_playlist_not_a_copy(self):
        body = SRC[SRC.index("def _bg_upnext_reordered"):SRC.index("def _bg_upnext_menu")]
        self.assertIn("mgr._move_playlist_rows(", body)
        self.assertIn("mgr._playlist_current_row() + 1", body)

    def test_it_is_off_unless_the_redesign_is_on(self):
        self.assertIn("if not self._ui_redesign_on():\n                return", SRC[SRC.index("def _refresh_bg_upnext"):SRC.index("def _bg_upnext_reordered")])
        self.assertRegex(SRC, r"self\.bg_upnext_box\.setVisible\(False\)\n\s+self\._bg_upnext_sig = None\n\s+if on:")


if __name__ == "__main__":
    unittest.main()
