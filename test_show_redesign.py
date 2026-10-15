"""Show tab redesign (hero stage): off by default, wired to the existing handlers, reversible. Source guards only: the layout
itself is checked with screenshots (see HANDOFF)."""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


class ShowRedesignTests(unittest.TestCase):
    def test_the_redesign_is_off_by_default(self):
        self.assertRegex(SRC, r'"ui_show_redesign":\s*False')

    def test_hero_buttons_reuse_the_existing_transport_handlers(self):
        for btn, handler in (("hero_btn_next", "_on_play_control_clicked"), ("hero_btn_pause", "toggle_karaoke_pause"),
                             ("hero_btn_restart", "restart_track"), ("hero_btn_stop", "stop_and_clear_now_singing")):
            self.assertIn(f"self.{btn}.clicked.connect(self.{handler})", SRC)

    def test_hero_seek_goes_through_the_one_existing_release_handler(self):
        body = SRC[SRC.index("def _hero_seek_released"):SRC.index("def _hero_media_badge_text")]
        self.assertIn("self._on_karaoke_seek_released()", body)

    def test_hero_pieces_exist_only_when_the_setting_is_on(self):
        body = SRC[SRC.index("def _apply_ui_show_redesign"):SRC.index("def _apply_redesign_hero_text")]
        self.assertIn("self.hero_stage_extras.setVisible(on)", body)
        self.assertIn("self.last_sung_label.setVisible(not on)", body)
        self.assertIn("self._hero_stage_timer.stop()", body)

    def test_the_settings_checkbox_applies_immediately(self):
        self.assertIn('QCheckBox("Use the new Show tab layout")', SRC)
        self.assertIn("self._apply_ui_show_redesign()", SRC)

    def test_chips_have_a_fixed_height_so_the_radius_is_valid(self):
        # Qt ignores a border-radius larger than half the height; the chip radius is 12, so the height must be >= 24.
        m = re.search(r"c\.setFixedHeight\((\d+)\)", SRC[SRC.index("def _build_hero_stage_extras"):])
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 24)


if __name__ == "__main__":
    unittest.main()
