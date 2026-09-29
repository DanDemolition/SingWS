"""KaraFun video-window (Dual Renderer) handling.

Regression 2026-09-29: KaraFun's video button is a TOGGLE. SingWS could not see a renderer that macOS had put in
its own Space (System Events only lists the current Space), pressed the button three times blind and closed the
window it had just opened; the audience screen stayed black for about a minute with the music playing.
"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_renderer", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def cg(name="Dual Renderer", owner="KaraFun", onscreen=True, layer=8, w=1920, h=1049):
    return {"kCGWindowOwnerName": owner, "kCGWindowName": name, "kCGWindowIsOnscreen": onscreen,
            "kCGWindowLayer": layer, "kCGWindowBounds": {"Width": w, "Height": h}}


class RendererWindowListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_main_module()

    def test_finds_the_renderer_in_any_space_including_offscreen_ones(self):
        found = self.m.karafun_dual_renderer_windows(_source=[
            cg(onscreen=False, layer=0, h=1080), cg(name='Results for "x"', onscreen=True, layer=0),
            cg(owner="SingWS", name="Dual Renderer"), cg(name="")])
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["onscreen"])                 # an off-screen (other Space) renderer still counts

    def test_none_of_them_means_absent_not_unknown(self):
        self.assertEqual(self.m.karafun_dual_renderer_windows(_source=[cg(name="Results for x")]), [])

    def test_a_broken_source_is_unknown_not_absent(self):
        self.assertIsNone(self.m.karafun_dual_renderer_windows(_source=[object()]))

    def test_the_real_call_returns_a_list_or_unknown(self):
        real = self.m.karafun_dual_renderer_windows()
        self.assertTrue(real is None or isinstance(real, list))


class PressDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = staticmethod(load_main_module().karafun_renderer_press_decision)

    def test_never_presses_while_a_renderer_exists(self):
        self.assertEqual(self.d([{"onscreen": False}], 0, None, 20.0, 1.0), "wait")
        self.assertEqual(self.d([{"onscreen": True}], 1, 30.0, 40.0, 2.0), "wait")

    def test_an_unreachable_renderer_falls_back_after_eight_seconds_not_thirty_eight(self):
        self.assertEqual(self.d([{"onscreen": False}], 0, None, 20.0, 7.9), "wait")
        self.assertEqual(self.d([{"onscreen": False}], 0, None, 20.0, 8.0), "fallback")

    def test_presses_once_when_it_is_really_absent_and_the_song_is_playing(self):
        self.assertEqual(self.d([], 0, None, 2.0), "wait")     # song not playing yet
        self.assertEqual(self.d([], 0, None, 3.0), "press")

    def test_waits_after_a_press_and_only_repeats_if_still_absent_ten_seconds_later(self):
        self.assertEqual(self.d([], 1, 3.0, 20.0), "wait")
        self.assertEqual(self.d([], 1, 9.9, 20.0), "wait")
        self.assertEqual(self.d([], 1, 10.0, 20.0), "press")

    def test_at_most_two_presses_then_fallback(self):
        self.assertEqual(self.d([], 2, 10.0, 40.0), "wait")
        self.assertEqual(self.d([], 2, 12.0, 40.0), "fallback")

    def test_unknown_window_list_presses_at_most_once(self):
        self.assertEqual(self.d(None, 0, None, 5.0), "press")
        self.assertEqual(self.d(None, 1, 11.0, 20.0), "wait")
        self.assertEqual(self.d(None, 1, 12.0, 20.0), "fallback")


class WiringTests(unittest.TestCase):
    def test_the_retry_loop_uses_the_all_space_check_and_the_old_blind_three_press_is_gone(self):
        src = Path("0.2.18.1.py").read_text(encoding="utf-8")
        block = src[src.index("def _ensure_renderer_windowed"):src.index("def _begin_capture")]
        self.assertIn("karafun_dual_renderer_windows()", block)
        self.assertIn("karafun_renderer_press_decision(", block)
        self.assertNotIn("press_state[\"count\"] < 3", block)
        self.assertNotIn("(1/3)", block)


if __name__ == "__main__":
    unittest.main()
