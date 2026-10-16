"""Show tab redesign: the library filter pills hide result rows only; nothing is removed and the search is untouched."""
import importlib.util
import os
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
        try:
            w.hide()
            w.close()
            w.deleteLater()
        except Exception:
            pass
    app.processEvents()


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_lf", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LibraryFilterTests(unittest.TestCase):
    def tearDown(self):
        _close_all_widgets()

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()
        cls.match = staticmethod(cls.m.KaraokeApp._library_row_matches)

    def test_all_matches_everything_even_odd_rows(self):
        self.assertTrue(self.match({"provider": "karafun"}, "all"))
        self.assertTrue(self.match(None, "karafun"))          # a row with no track data is never hidden

    def test_karafun_and_local_split_by_provider_and_catalog_flag(self):
        kf = {"provider": "karafun"}
        cat = {"provider": "local", "karafun_catalog_only": True}
        loc = {"provider": "local", "path": "/a/b.cdg"}
        self.assertTrue(self.match(kf, "karafun") and self.match(cat, "karafun") and not self.match(loc, "karafun"))
        self.assertTrue(self.match(loc, "local") and not self.match(kf, "local") and not self.match(cat, "local"))

    def test_mp4_by_type_or_extension(self):
        self.assertTrue(self.match({"type": "mp4"}, "mp4"))
        self.assertTrue(self.match({"path": "/x/Song.MP4"}, "mp4"))
        self.assertFalse(self.match({"path": "/x/song.cdg", "type": "cdg"}, "mp4"))

    def test_the_bar_changes_mode_by_click_and_reports_it(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        bar = self.m.LibraryFilterBar()
        bar.resize(400, 30)
        got = []
        bar.changed.connect(got.append)
        bar._layout_rects()
        r = [r for k, r in bar._rects if k == "mp4"][0]
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=r.center().toPoint())
        self.assertEqual((bar.mode(), got), ("mp4", ["mp4"]))
        bar.set_mode("mp4")                                   # same mode again: no second signal
        self.assertEqual(got, ["mp4"])

    def test_it_only_hides_rows_and_turning_the_redesign_off_restores_all(self):
        body = SRC[SRC.index("def _apply_library_filter"):SRC.index("def _start_pending_search_if_needed")]
        self.assertIn("setHidden(", body)
        self.assertNotIn("takeItem", body)
        self.assertNotIn(".clear()", body)
        apply_body = SRC[SRC.index("def _apply_ui_show_redesign"):SRC.index("def _apply_redesign_hero_text")]
        self.assertIn('self.library_filter_bar.set_mode("all")', apply_body)


if __name__ == "__main__":
    unittest.main()
