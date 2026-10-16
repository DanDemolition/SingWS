"""Singer History redesign (setting ui_history_redesign, off by default): styles and row painter only; off restores exactly."""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


class HistoryRedesignTests(unittest.TestCase):
    def test_off_by_default_with_a_settings_checkbox_and_a_launch_hook(self):
        self.assertRegex(SRC, r'"ui_history_redesign":\s*False')
        self.assertIn('QCheckBox("Use the new Singer History look")', SRC)
        self.assertIn('if self.settings.get("ui_history_redesign", False):', SRC)

    def test_the_apply_method_only_styles_and_swaps_delegates(self):
        body = SRC[SRC.index("    def _apply_ui_history_redesign"):SRC.index("    def _apply_ui_bgm_redesign")]
        for forbidden in (".clicked.connect", "setRows(", ".setData(", "setModel("):
            self.assertNotIn(forbidden, body)
        self.assertIn("self._history_delegates_off", body)        # the way back
        self.assertIn("self._history_css_off", body)

    def test_the_row_delegate_reads_the_display_text_and_never_the_model(self):
        cls = SRC[SRC.index("class HistoryRowDelegate"):SRC.index("class SingerHistorySongListModel")]
        self.assertIn("Qt.ItemDataRole.DisplayRole", cls)
        for forbidden in ("setData(", "setRows(", "model().set"):
            self.assertNotIn(forbidden, cls)

    def test_admin_tools_fold_away_only_in_the_redesign(self):
        self.assertIn("self.singer_history_admin_toggle", SRC)
        body = SRC[SRC.index("    def _apply_ui_history_redesign"):SRC.index("    def _apply_ui_bgm_redesign")]
        self.assertIn("self.singer_history_cleanup_meta_label.setVisible(True)", body)   # off: the original label is back
        self.assertIn("b.setVisible(True)", body)                                       # off: the red buttons are back


if __name__ == "__main__":
    unittest.main()
