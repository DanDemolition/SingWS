"""Waitlist redesign (setting ui_waitlist_redesign, off by default): styles and the row painter only; off restores exactly."""
import unittest
from pathlib import Path

SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


class WaitlistRedesignTests(unittest.TestCase):
    def test_off_by_default_with_a_checkbox_and_a_launch_hook(self):
        self.assertIn('"ui_waitlist_redesign": True', SRC)
        self.assertIn('QCheckBox("Use the new Waitlist look")', SRC)
        self.assertIn('if self.settings.get("ui_waitlist_redesign", False):', SRC)

    def test_the_apply_method_only_styles_and_swaps_the_delegate(self):
        body = SRC[SRC.index("    def _apply_ui_waitlist_redesign"):SRC.index("    def _apply_ui_history_redesign")]
        for forbidden in (".clicked.connect", "setRows(", ".setData(", "setModel("):
            self.assertNotIn(forbidden, body)
        self.assertIn("self._waitlist_delegate_off", body)
        self.assertIn("self._waitlist_css_off", body)

    def test_the_delegate_reads_the_row_text_and_never_writes_the_model(self):
        cls = SRC[SRC.index("class WaitlistRowDelegate"):SRC.index("class HistoryRowDelegate")]
        self.assertIn("Qt.ItemDataRole.DisplayRole", cls)
        for forbidden in ("setData(", "setRows("):
            self.assertNotIn(forbidden, cls)

    def test_the_row_pattern_matches_the_text_the_app_produces(self):
        # the real producer's format, checked against the delegate's regex through the class attribute
        import importlib.util, os, sys, tempfile
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
        spec = importlib.util.spec_from_file_location("singws_main_waitlist_redesign", Path(__file__).with_name("0.2.18.1.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        text = ("Singer: Abby    Status: Waitlisted    Queue: #4\nSong: Shake It Off    Artist: Taylor Swift\n"
                "Version: KV 56442    Length: 3:39    Requested: 12:04 AM    Server: 12:04 AM")
        m = mod.WaitlistRowDelegate._FIELDS.match(text)
        self.assertIsNotNone(m)
        self.assertEqual((m.group("singer"), m.group("status"), m.group("queue"), m.group("title"), m.group("artist")),
                         ("Abby", "Waitlisted", "#4", "Shake It Off", "Taylor Swift"))


if __name__ == "__main__":
    unittest.main()
