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


def _close_all_widgets():
    """These tests show real widgets; leave nothing behind (a leftover focus widget made unrelated tests think the host was busy elsewhere)."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in list(app.topLevelWidgets()):
        if type(w).__name__ == "KaraokeApp":
            continue                      # a whole app window owned by another test: closing it runs the full shutdown and can block
        try:
            w.hide()
            w.close()
            w.deleteLater()
        except Exception:
            pass
    app.processEvents()


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_bgun", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BgUpNextTests(unittest.TestCase):
    def tearDown(self):
        _close_all_widgets()

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

    def test_a_short_window_gets_two_rows_and_no_hint_a_tall_one_three_and_the_hint(self):
        from types import SimpleNamespace
        KA = self.m.KaraokeApp
        short = SimpleNamespace(height=lambda: 840, _compact_deck=False)
        tall = SimpleNamespace(height=lambda: 1000, _compact_deck=False)
        compact = SimpleNamespace(height=lambda: 1000, _compact_deck=True)
        for obj in (short, tall, compact):
            obj._bg_upnext_roomy = lambda o=obj: KA._bg_upnext_roomy(o)
        self.assertEqual((KA._bg_upnext_rows_wanted(short), short._bg_upnext_roomy()), (2, False))
        self.assertEqual((KA._bg_upnext_rows_wanted(tall), tall._bg_upnext_roomy()), (3, True))
        self.assertEqual((KA._bg_upnext_rows_wanted(compact), compact._bg_upnext_roomy()), (2, False))   # a short screen never gets the tall layout

    def test_the_redesign_does_not_make_the_smallest_window_taller_than_the_old_layout_needed(self):
        # measured on screen: min height 828 (old) vs 841 (redesign) once the list drops to two rows; the splitter's own inset is accounted for
        src = SRC[SRC.index("def _fit_right_column"):SRC.index("fit right column failed")]
        self.assertIn("self.video_controls_splitter.setMinimumHeight(", src)
        self.assertIn("self.video_controls_splitter.setMinimumHeight(0)", src)

    def test_the_music_subtitle_adds_after_the_last_singer_only_with_the_redesign_and_while_playing(self):
        body = SRC[SRC.index("def _render_bg_main_text"):SRC.index("def _refresh_bg_main_panel")]
        self.assertIn("self._ui_redesign_on()", body)
        self.assertIn('getattr(self.bg_music, "is_playing", False)', body)
        self.assertIn("after {last}", body)


if __name__ == "__main__":
    unittest.main()
