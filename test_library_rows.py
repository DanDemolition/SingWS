"""Show tab redesign: library result rows as cards with a '+' button that runs the existing add flow."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_lr", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LibraryRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def make(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem
        L, R, D = (int(Qt.ItemDataRole.UserRole) + n for n in (20, 21, 22))
        lst = QListWidget()
        for a, t, disc in (("Fleetwood Mac", "Landslide", "SC"), ("Adele", "Hello", "")):
            it = QListWidgetItem(f"{a} • {t}")
            it.setData(L, f"{a} • {t}")
            it.setData(R, "CDG")
            it.setData(D, disc)
            lst.addItem(it)
        added = []
        d = self.m.LibraryCardDelegate(L, R, D, lambda ix: added.append(ix.row()), lst)
        lst.setItemDelegate(d)
        lst.setMouseTracking(True)
        lst.resize(420, 300)
        lst.show()
        self.app.processEvents()
        return lst, d, added

    def click(self, lst, row, at_plus):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        rect = lst.visualItemRect(lst.item(row))
        plus = lst.itemDelegate()._plus_rect(rect)
        pos = plus.center() if at_plus else rect.center()
        QTest.mouseClick(lst.viewport(), Qt.MouseButton.LeftButton, pos=pos)

    def test_the_plus_runs_the_add_callback_for_that_row_only(self):
        lst, d, added = self.make()
        self.click(lst, 1, at_plus=True)
        self.assertEqual(added, [1])

    def test_clicking_the_rest_of_the_row_only_selects_it(self):
        lst, d, added = self.make()
        self.click(lst, 0, at_plus=False)
        self.assertEqual(added, [])
        self.assertEqual(lst.currentRow(), 0)

    def test_rows_paint_without_error_and_have_a_fixed_height(self):
        lst, d, added = self.make()
        self.assertFalse(lst.viewport().grab().toImage().isNull())
        self.assertEqual(lst.visualItemRect(lst.item(0)).height(), d.ROW_H)

    def test_the_plus_uses_the_same_flow_as_a_double_click_and_the_old_rows_come_back(self):
        body = SRC[SRC.index("def _apply_ui_show_redesign"):SRC.index("def _apply_redesign_hero_text")]
        self.assertIn("self._on_search_result_double_clicked(rl.item(ix.row()))", body)
        self.assertIn("TwoLineSearchDelegate(", body)
        self.assertIn("self.library_tabs_label.setVisible(not on)", body)

    def test_company_is_the_canonical_brand_and_karafun_for_the_online_catalogue(self):
        d = self.m.LibraryCardDelegate(0, 0, 0, lambda ix: None, brand_fn=self.m.canonical_disc_brand)
        self.assertEqual(d.company_for("SC8671", "CDG"), "SC")
        self.assertEqual(d.company_for("CC1234", ""), "CC")
        self.assertEqual(d.company_for("KV 43403", ""), "KV")
        self.assertEqual(d.company_for("ZZ208", ""), "ZZ")                 # unknown brand: just its letters
        self.assertEqual(d.company_for("", "KaraFun ONLINE  3:20"), "KaraFun")
        self.assertEqual(d.company_for("", "CDG"), "")                    # nothing known: no pill rather than a guess

    def test_duration_is_read_from_the_end_of_the_row_text_only(self):
        d = self.m.LibraryCardDelegate(0, 0, 0, lambda ix: None)
        self.assertEqual(d.duration_from("SC8671  CDG  3:20"), "3:20")
        self.assertEqual(d.duration_from("KaraFun ONLINE  04:17"), "04:17")
        self.assertEqual(d.duration_from("CDG"), "")
        self.assertEqual(d.duration_from(""), "")

    def test_song_count_is_read_only_and_zero_without_an_index(self):
        import song_index
        self.assertEqual(song_index.song_count(Path(tempfile.mkdtemp()) / "missing.db"), 0)


if __name__ == "__main__":
    unittest.main()
