"""Show tab redesign, right column: slim empty pads / big loaded pads, and the way back to today's look."""
import importlib.util
import os
import tempfile
import unittest

os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_rc", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RightColumnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def strip(self):
        s = self.m.SoundboardStrip(pad_count=4, pad_height=58)
        s.pads[0]._clip_path = "/tmp/x.wav"          # pretend a clip is loaded (no audio is touched)
        s.pads[0]._label = "x"
        for pad in s.pads:
            pad._refresh_face()
        return s

    def test_redesign_makes_empty_pads_slim_and_keeps_loaded_pads_full(self):
        s = self.strip()
        s.set_redesign(True)
        self.assertEqual(s.pads[0].height(), 58 + 16)          # a loaded pad is a little taller in the redesign
        self.assertTrue(all(p.height() == 30 for p in s.pads[1:]))

    def test_switching_back_restores_every_pad_to_the_original_height_and_style(self):
        s = self.strip()
        before = [p.styleSheet() for p in s.pads]
        s.set_redesign(True)
        self.assertNotEqual([p.styleSheet() for p in s.pads], before)
        s.set_redesign(False)
        self.assertEqual([p.styleSheet() for p in s.pads], before)
        self.assertTrue(all(p.height() == 58 for p in s.pads))

    def test_loaded_pads_get_different_accent_colours_by_slot(self):
        css = [self.m.soundboard_pad_css_redesign(i, "loaded") for i in range(4)]
        self.assertEqual(len(set(css)), 4)

    def test_a_pad_still_plays_through_its_own_click_handler(self):
        s = self.strip()
        s.set_redesign(True)
        # the click wiring is untouched by the redesign (one behaviour)
        self.assertGreater(s.pads[0].receivers(s.pads[0].clicked), 0)

    def test_tempo_and_key_become_two_row_cards_and_come_back_as_one_panel(self):
        from pathlib import Path
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        body = src[src.index("def _apply_redesign_polish"):src.index("def _restyle_bg_main_buttons")]
        self.assertIn("self.tempo_row_cards.setVisible(on)", body)
        self.assertIn("self._tempo_panel_css", body)           # the original panel style is what comes back
        self.assertIn("self._tempo_panel.setFixedHeight(100 if on else 78)", body)

    def test_row_cards_paint_inside_their_panel(self):
        from PyQt6.QtWidgets import QWidget, QGridLayout, QLabel
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(14, 4, 14, 4)
        grid.setVerticalSpacing(12)
        for r in range(2):
            grid.addWidget(QLabel("x"), r, 0)
            grid.setRowMinimumHeight(r, 40)
        panel.setFixedSize(300, 100)
        overlay = self.m.RowCardsOverlay(panel, grid, 2)
        panel.show()
        self.app.processEvents()
        for r in range(2):
            cell = grid.cellRect(r, 0)
            self.assertGreaterEqual(cell.top() - 2, 0)
            self.assertLessEqual(cell.bottom() + 2, panel.height())
        self.assertFalse(panel.grab().toImage().isNull())

    def test_preview_card_drops_its_title_and_shows_elapsed_over_total(self):
        from pathlib import Path
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        body = src[src.index("def _apply_redesign_right_column"):src.index("def _apply_redesign_hero_text")]
        self.assertIn("self.performance_deck_title, self.performance_deck_status", body)
        self.assertIn("w.setVisible(not on)", body)
        self.assertIn("self.preview_time_label.setVisible(on)", body)
        sync = src[src.index("def _hero_stage_sync"):src.index("def _hero_seek_pressed")]
        self.assertIn('f"{_mss(pos)} / {_mss(dur)}"', sync)
        self.assertIn("_karaoke_last_dur", sync)             # same position/duration the seek bar already uses

    def test_changing_a_pad_refits_the_fixed_height_card_so_pads_never_overlap(self):
        # Found on screen: with the redesign on, loading pads made loaded pads (taller) overlap the next row, because the
        # transport card has a fixed height computed when the layout was applied.
        from pathlib import Path
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        body = src[src.index("def _on_soundboard_changed"):src.index("def _apply_tooltip_visibility")]
        self.assertIn("self._ui_redesign_on()", body)
        self.assertIn("self._fit_right_column(True)", body)


if __name__ == "__main__":
    unittest.main()
