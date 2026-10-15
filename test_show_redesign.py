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

    def test_queue_cards_swap_in_with_the_setting_and_restore_the_old_delegate(self):
        body = SRC[SRC.index("def _apply_ui_show_redesign"):SRC.index("def _apply_redesign_hero_text")]
        self.assertIn("QueueCardDelegate(", body)
        self.assertIn("RightAlignedMetaDelegate(", body)       # the way back when the setting is off
        self.assertIn("qd.setSpacing(0 if on else 2)", body)    # view spacing would show as a seam inside a card

    def test_queue_card_delegate_paints_from_the_same_roles_and_never_touches_the_model(self):
        cls = SRC[SRC.index("class QueueCardDelegate"):SRC.index("class TwoLineSearchDelegate")]
        for token in ("self.left_role", "self.right_role", "self.status_role", "self.kind_role"):
            self.assertIn(token, cls)
        self.assertNotIn("setData(", cls)
        self.assertNotIn("setRows(", cls)

    def test_polish_pieces_are_gated_and_reversible(self):
        body = SRC[SRC.index("def _apply_redesign_polish"):SRC.index("def _restyle_bg_main_buttons")]
        self.assertIn("self._restyle_seek_sliders(on)", body)
        self.assertIn("self.hero_kicker_dot.setVisible(on)", body)
        self.assertIn("self.hero_glow.setVisible(on)", body)
        for old in ("Clear Queue", "Move Up", "Move Down"):
            self.assertIn(old, body)                      # the original labels are restored when the setting is off
        self.assertIn("self.hero_glow.setVisible(False)", SRC)   # and it starts hidden

    def test_queue_summary_is_redesign_only_and_built_from_the_header_numbers(self):
        body = SRC[SRC.index("def _refresh_queue_summary"):SRC.index("    def update_queue_display")]
        self.assertIn("if not self._ui_redesign_on():", body)
        self.assertIn("_header_summary", body)
        self.assertIn("_queue_eta_minutes", body)
        apply_body = SRC[SRC.index("def _apply_ui_show_redesign"):SRC.index("def _apply_redesign_hero_text")]
        self.assertIn("self.queue_summary_label.setVisible(on)", apply_body)


if __name__ == "__main__":
    unittest.main()
