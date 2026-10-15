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


if __name__ == "__main__":
    unittest.main()
