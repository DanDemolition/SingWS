"""Dialogs redesign (setting ui_dialogs_redesign, off by default): the shared dialog stylesheet only grows when the flag is on."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-dlgredesign-")
SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


class DialogsRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location("singws_main_dlgredesign", Path(__file__).with_name("0.2.18.1.py"))
        cls.m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.m
        spec.loader.exec_module(cls.m)

    def test_off_by_default_and_wired(self):
        self.assertIn('"ui_dialogs_redesign": False', SRC)
        self.assertIn("_set_dialog_redesign(bool(self.settings.get(\"ui_dialogs_redesign\", False)))", SRC)
        self.assertFalse(self.m._DIALOG_REDESIGN)

    def test_the_flag_adds_to_the_stylesheet_and_off_leaves_it_exactly_as_it_was(self):
        m = self.m
        m._set_dialog_redesign(False)
        off = m.dialog_stylesheet(None, "#101020", "#ffffff")
        self.assertEqual(off, m._dialog_stylesheet_base(None, "#101020", "#ffffff"))
        m._set_dialog_redesign(True)
        try:
            on = m.dialog_stylesheet(None, "#101020", "#ffffff")
        finally:
            m._set_dialog_redesign(False)
        self.assertTrue(on.startswith(off))
        self.assertGreater(len(on), len(off))
        self.assertIn("QDialogButtonBox QPushButton:default", on)


if __name__ == "__main__":
    unittest.main()
