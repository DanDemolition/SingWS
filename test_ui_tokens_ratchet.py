"""The UI may only get more uniform: literals off the design scales must never increase.

Baselines are the counts from tools/ui_token_audit.py. When a migration step lowers a number, lower the baseline here in
the same commit so the gain is locked in. Adding a new off-scale font size, radius or colour fails this test: use a
value from singws_theme.py (FONT_SCALE, RADIUS_SCALE, VISUAL) instead.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import ui_token_audit  # noqa: E402

BASELINE = {
    "font_off_scale_uses": 11,
    "radius_off_scale_uses": 6,
    "hex_off_palette_uses": 193,
    "inline_stylesheets": 323,
}


class UiTokenRatchetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = ui_token_audit.measure()

    def _check(self, key, what):
        self.assertLessEqual(
            self.m[key], BASELINE[key],
            f"{what} went up ({self.m[key]} > {BASELINE[key]}). Use a value from singws_theme.py instead of a new literal.",
        )

    def test_font_sizes_stay_on_the_scale(self):
        self._check("font_off_scale_uses", "off-scale font-size literals")

    def test_radii_stay_on_the_scale(self):
        self._check("radius_off_scale_uses", "off-scale border-radius literals")

    def test_colours_stay_on_the_palette(self):
        self._check("hex_off_palette_uses", "off-palette hex colour literals")

    def test_inline_stylesheets_do_not_grow(self):
        self._check("inline_stylesheets", "inline setStyleSheet calls")

    def test_text_roles_use_the_font_scale_and_render(self):
        import singws_theme
        for name, (size, weight) in singws_theme.TEXT_ROLES.items():
            self.assertIn(size, singws_theme.FONT_SCALE, f"text role {name} uses an off-scale size")
        css = singws_theme.text_css("title", "text_bright", "padding: 2px;")
        self.assertIn("font-size:14px", css)
        self.assertIn("font-weight:850", css)
        self.assertIn("padding: 2px;", css)
        self.assertTrue(css.startswith("color:#"))

    def test_scales_are_sorted_and_unique(self):
        import singws_theme
        for scale in (singws_theme.FONT_SCALE, singws_theme.RADIUS_SCALE, singws_theme.SPACING_SCALE):
            self.assertEqual(list(scale), sorted(set(scale)))
        self.assertEqual(singws_theme.font_px(15), 16)   # ties round up
        self.assertEqual(singws_theme.font_px(10), 11)
        self.assertEqual(singws_theme.font_px(13), 13)


if __name__ == "__main__":
    unittest.main()
