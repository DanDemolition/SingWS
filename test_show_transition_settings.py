"""1.0.4.0: the show-screen transitions run at a 30 fps cap and draw directly (the 720-line offscreen layer is opt-in), after the 2026-11-19 show found them SLOWER with the layer."""
import _test_profile  # noqa: F401
import unittest

from test_recent_regressions import load_main_module


class ShowTransitionSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_main_module()

    def test_the_defaults_are_a_30_fps_cap_and_no_offscreen_layer(self):
        self.assertEqual(self.m.DEFAULTS["show_transition_fps_cap"], 30)
        self.assertEqual(self.m.DEFAULTS["show_transition_render_lines"], 0)

    def test_the_swap_interval_gives_the_cap_on_each_display_speed(self):
        f = self.m._transition_swap_interval
        self.assertEqual(f(60.0, 30), 2)
        self.assertEqual(f(59.94, 30), 2)
        self.assertEqual(f(120.0, 30), 4)
        self.assertEqual(f(120.0, 60), 2)
        self.assertEqual(f(60.0, 60), 1)
        self.assertEqual(f(60.0, 0), 1, "0 = no cap")
        self.assertEqual(f(30.0, 30), 1)
        self.assertEqual(f(24.0, 30), 1, "never below every vblank")
        self.assertEqual(f(0.0, 30), 1)

    def test_configure_reads_clamps_and_survives_bad_values(self):
        m = self.m
        try:
            m._configure_show_transitions({"show_transition_fps_cap": 60, "show_transition_render_lines": 720})
            self.assertEqual((m._SHOW_TRANSITION_FPS_CAP, m._SHOW_TRANSITION_RENDER_LINES), (60, 720))
            m._configure_show_transitions({"show_transition_fps_cap": 9999, "show_transition_render_lines": -5})
            self.assertEqual((m._SHOW_TRANSITION_FPS_CAP, m._SHOW_TRANSITION_RENDER_LINES), (240, 0))
            m._configure_show_transitions({"show_transition_fps_cap": "fast", "show_transition_render_lines": None})
            self.assertEqual((m._SHOW_TRANSITION_FPS_CAP, m._SHOW_TRANSITION_RENDER_LINES), (30, 0))
            m._configure_show_transitions({})
            self.assertEqual((m._SHOW_TRANSITION_FPS_CAP, m._SHOW_TRANSITION_RENDER_LINES), (30, 0))
        finally:
            m._configure_show_transitions({})

    def test_the_qml_layer_is_off_unless_render_lines_is_set(self):
        qml = self.m.QML_SHOW_SCREEN_VFX_SOURCE
        self.assertIn("property int renderLines: 0", qml)
        self.assertIn("(renderLines > 0 && height > renderLines) ? renderLines / height : 1.0", qml)
        self.assertNotIn("height > 720 ? 720 / height", qml)
        self.assertIn("layer.enabled: active && renderScale < 1.0", qml)


if __name__ == "__main__":
    unittest.main()
