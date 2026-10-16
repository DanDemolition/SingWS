"""Show tab redesign (hero stage): off by default, wired to the existing handlers, reversible. Source guards only: the layout
itself is checked with screenshots (see HANDOFF)."""
import re
import os
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

    def test_hero_buttons_give_way_to_a_last_and_next_readout(self):
        body = SRC[SRC.index("def _apply_redesign_polish"):SRC.index("def _restyle_bg_main_buttons")]
        self.assertIn("for b in (self.hero_btn_next, self.hero_btn_pause, self.hero_btn_restart, self.hero_btn_stop):", body)
        self.assertIn("b.setVisible(not on)", body)
        self.assertIn("self.hero_lastnext_label.setVisible(on)", body)
        upd = SRC[SRC.index("def _update_last_sung_card"):SRC.index("def _remember_last_sung_from_current")]
        self.assertIn("hero_lastnext_label", upd)            # the readout is fed by the same LAST / NEXT the footer used
        # the buttons are only hidden: the transport row under the preview still has play / pause / restart / stop
        for h in ("_on_play_control_clicked", "toggle_karaoke_pause", "restart_track", "stop_and_clear_now_singing"):
            self.assertIn(f"self.{h}", SRC)

    def test_the_rotation_title_row_is_hidden_with_the_redesign_and_its_facts_move_to_the_summary(self):
        body = SRC[SRC.index("def _apply_ui_show_redesign"):SRC.index("def _apply_redesign_hero_text")]
        self.assertIn("self.rotation_title_label, self.queue_label, self._rotation_divider", body)
        summary = SRC[SRC.index("def _refresh_queue_summary"):SRC.index("    def update_queue_display")]
        for flag in ("LOCKED", "LOOPING"):                     # still shown when they apply
            self.assertIn(flag, summary)
        self.assertNotIn('"CLASSIC"', summary)                 # the mode word is gone

    def test_glass_cards_and_the_page_glow_are_redesign_only_and_reversible(self):
        body = SRC[SRC.index("def _apply_redesign_polish"):SRC.index("def _restyle_bg_main_buttons")]
        self.assertIn("glass_card_css(name)", body)
        self.assertIn("card_css(name)", body)                    # today's card style is what comes back
        self.assertIn("self.update()", body)                       # the window repaints its own backdrop (no extra full-window widget)
        self.assertIn("lw._css_off", body)                       # the two lists get their own old style back

    def test_bottom_nav_is_a_glass_strip_with_dim_labels_and_goes_back_when_off(self):
        body = SRC[SRC.index("def _set_bottom_nav_active"):SRC.index("def _playback_safe_ui_delay_ms")]
        self.assertIn("glass_nav_item_css", body)
        self.assertIn('w.setStyleSheet(css if glass else "")', body)       # off: the labels' own styles are cleared
        self.assertIn("self._bottom_nav_mode = mode", body)                # the active tab survives a switch of the setting
        polish = SRC[SRC.index("def _apply_redesign_polish"):SRC.index("def _restyle_bg_main_buttons")]
        self.assertIn("glass_nav_css(\"operatorBottomNav\")", polish)
        apply_body = SRC[SRC.index("def _apply_ui_show_redesign"):SRC.index("def _apply_redesign_hero_text")]
        self.assertIn("self._set_bottom_nav_active(getattr(self, \"_bottom_nav_mode\", \"main\"))", apply_body)

    def test_hero_progress_bar_is_thicker_with_a_glow_and_gentler_time_labels(self):
        body = SRC[SRC.index("def _restyle_seek_sliders"):SRC.index("def _apply_redesign_polish")]
        self.assertIn('("hero_seek_slider", 6)', body)            # the mockup's 6 px bar
        self.assertIn('("bg_main_seek_slider", 4)', body)
        self.assertIn("glow.setVisible(on)", body)
        self.assertIn("self.hero_seek_glow = SliderGlow(self.hero_seek_slider)", SRC)
        polish = SRC[SRC.index("def _apply_redesign_polish"):SRC.index("def _restyle_bg_main_buttons")]
        self.assertIn("self.hero_seek_elapsed", polish)

    def test_rounder_corners_darker_canvas_and_the_purple_hero_are_redesign_only(self):
        polish = SRC[SRC.index("def _apply_redesign_polish"):SRC.index("def _restyle_bg_main_buttons")]
        self.assertIn('hero_card_css("rotationHeroCard")', polish)
        self.assertIn('deck_css("bgMainCard", radius_px=14, accent=True)', polish)
        live = SRC[SRC.index("def _rotation_hero_live_css(self"):SRC.index("def _rotation_hero_live_css_base")]
        self.assertIn("self._ui_redesign_on()", live)
        canvas = SRC[SRC.index("class CanvasGlowOverlay"):SRC.index("class HeroGlowOverlay")]
        self.assertIn("def paintEvent(self, event):\n        \"\"\"With the redesign on the page gets its deep canvas", SRC)
        self.assertIn("QColor(5, 6, 13)", canvas)                # the mockup's deeper canvas


class BlankHeroTitleGuard(unittest.TestCase):
    def test_with_no_singer_the_redesigned_hero_title_is_blank(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"), encoding="utf-8").read()
        self.assertIn('self.rotation_summary_title.setText("" if self._ui_redesign_on() else "No singer is active")', src)



if __name__ == "__main__":
    unittest.main()
