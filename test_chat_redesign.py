"""Chat page redesign (setting ui_chat_redesign, off by default): styles only; off restores exactly."""
import unittest
from pathlib import Path

SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


class ChatRedesignTests(unittest.TestCase):
    def test_off_by_default_with_a_checkbox_and_a_launch_hook(self):
        self.assertIn('"ui_chat_redesign": True', SRC)
        self.assertIn('QCheckBox("Use the new Chat look")', SRC)
        self.assertIn('if self.settings.get("ui_chat_redesign", False):', SRC)

    def test_the_apply_method_only_styles(self):
        body = SRC[SRC.index("    def _apply_ui_chat_redesign"):SRC.index("    def _apply_ui_waitlist_redesign")]
        for forbidden in (".clicked.connect", ".textChanged.connect", ".setData(", "setModel(", ".send"):
            self.assertNotIn(forbidden, body)
        self.assertIn("self._chat_css_off", body)                       # the way back
        self.assertIn("css.get(\"tabbar\"", body)


if __name__ == "__main__":
    unittest.main()
