"""Show tab redesign: the header's four status pills."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_hc", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HeaderChipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def test_a_chip_sizes_to_its_text_and_paints(self):
        chip = self.m.StatusChip("Requests open")
        w1 = chip.width()
        chip.set_state("Requests open until the very end of the night", None)
        self.assertGreater(chip.width(), w1)
        self.assertFalse(chip.grab().toImage().isNull())

    def test_setting_the_same_state_again_does_not_repaint_or_resize(self):
        from PyQt6.QtGui import QColor
        chip = self.m.StatusChip("Online", QColor(52, 224, 138))
        w = chip.width()
        chip.set_state("Online", QColor(52, 224, 138))
        self.assertEqual(chip.width(), w)

    def test_pills_replace_the_single_status_pill_only_with_the_redesign_on(self):
        body = SRC[SRC.index("def _apply_ui_show_redesign"):SRC.index("def _apply_redesign_hero_text")]
        self.assertIn("self.header_status_group.setVisible(not on)", body)
        self.assertIn("self.header_chip_group.setVisible(on)", body)

    def test_the_pills_read_the_same_state_as_the_single_pill(self):
        body = SRC[SRC.index("def _refresh_header_chips"):SRC.index("    def _refresh_header_status")]
        for token in ('"requests_accepting"', '"use_waiting_for_add"', "_server_connection_state", "_header_summary"):
            self.assertIn(token, body)


class SpacedCapsLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def render(self, label):
        label.setFixedHeight(20)
        label.resize(label.sizeHint().width() + 4, 20)
        label.show()
        self.app.processEvents()
        return label.grab().toImage()

    def test_spacing_zero_is_exactly_an_ordinary_label(self):
        from PyQt6.QtWidgets import QLabel
        css = "color: rgb(200,100,50); font-size: 11px; font-weight: 700;"
        plain, spaced = QLabel("SONG LIBRARY"), self.m.SpacedCapsLabel("SONG LIBRARY")
        plain.setStyleSheet(css)
        spaced.setStyleSheet(css)
        a, b = self.render(plain), self.render(spaced)
        self.assertEqual(a.size(), b.size())
        self.assertEqual(a, b)

    def test_spacing_makes_the_label_wider_and_changes_how_it_draws(self):
        lab = self.m.SpacedCapsLabel("SONG LIBRARY")
        lab.setStyleSheet("color: rgb(200,100,50); font-size: 11px; font-weight: 700;")
        w0 = lab.sizeHint().width()
        img0 = self.render(lab)
        lab.set_spacing(1.1)
        self.assertGreater(lab.sizeHint().width(), w0)
        self.assertNotEqual(self.render(lab), img0)

    def test_the_spacing_survives_a_restyle_which_a_qfont_spacing_did_not(self):
        lab = self.m.SpacedCapsLabel("TEMPO")
        lab.setStyleSheet("color: rgb(127,132,150); font-size: 11px;")
        lab.set_spacing(1.1)
        before = lab.sizeHint().width()
        lab.setStyleSheet("color: rgb(127,132,150); font-size: 11px; font-weight: 700;")   # what the redesign does when it restyles
        self.app.processEvents()
        self.assertGreater(lab.sizeHint().width(), QFontMetricsWidth(lab, "TEMPO"))
        self.assertGreater(before, 0)

    def test_the_labels_that_carry_it_are_the_spaced_kind_and_it_is_off_without_the_redesign(self):
        src = SRC[SRC.index("def _apply_redesign_polish"):SRC.index("def _restyle_bg_main_buttons")]
        self.assertIn("w.set_spacing(px if on else 0.0)", src)
        for name in ("library_title_label", "rotation_queue_section_label", "karaoke_tempo_label", "karaoke_key_label", "soundboard_caption", "bg_upnext_title", "rotation_summary_kicker"):
            self.assertIn(f"self.{name} = SpacedCapsLabel(", SRC)


def QFontMetricsWidth(label, text):
    from PyQt6.QtGui import QFontMetrics
    return QFontMetrics(label.font()).horizontalAdvance(text)


if __name__ == "__main__":
    unittest.main()
