"""KaraFun's video window is parked at a screen edge so it never covers the host or audience screens, and a cold KaraFun
gets long enough to start playing (2026-10-01 show)."""
import ast
import random
import subprocess
import sys
import unittest
from pathlib import Path

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


def namespace():
    tree = ast.parse(SOURCE)
    names = {"KARAFUN_PARK_SLIVER_PX", "KARAFUN_PARK_FRAME_TIMEOUT_S"}
    funcs = {"_karafun_rect_overlap", "karafun_visible_area", "karafun_needs_parking", "karafun_park_position", "karafun_move_window_script"}
    body = [n for n in tree.body if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets))
            or (isinstance(n, ast.FunctionDef) and n.name in funcs)]
    ns = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), "park", "exec"), ns)
    return ns


NS = namespace()
park = NS["karafun_park_position"]
needs = NS["karafun_needs_parking"]
visible = NS["karafun_visible_area"]
SLIVER = NS["KARAFUN_PARK_SLIVER_PX"]

# The 2026-10-01 venue arrangement: laptop screen, and the TV to its right but lower.
LAPTOP = (0, 0, 1680, 1050)
TV = (1680, 447, 1920, 1080)


class GeometryTests(unittest.TestCase):
    def test_the_venue_layout_parks_below_the_laptop_screen(self):
        self.assertEqual(park(1680, 932, [LAPTOP, TV], preferred=0), (0, 1050 - SLIVER))

    def test_the_parked_window_touches_only_a_strip_of_one_screen(self):
        x, y = park(1680, 932, [LAPTOP, TV], preferred=0)
        rect = (x, y, 1680, 932)
        self.assertEqual(visible(rect, [TV]), 0)
        self.assertLessEqual(visible(rect, [LAPTOP]), SLIVER * 1680)
        self.assertGreater(visible(rect, [LAPTOP]), 0, "some of it must stay on a screen so it keeps rendering")
        self.assertFalse(needs(rect, [LAPTOP, TV]))

    def test_where_it_opened_and_where_it_was_left_by_hand_both_need_parking(self):
        self.assertTrue(needs((0, 25, 1680, 932), [LAPTOP, TV]))          # maximised over the host screen
        self.assertTrue(needs((1623, 929, 1680, 932), [LAPTOP, TV]))      # the earlier manual spot, still mostly on the TV

    def test_the_host_screen_is_preferred_but_not_required(self):
        self.assertEqual(park(1680, 932, [LAPTOP, TV], preferred=0)[0], 0)
        x, y = park(1680, 932, [LAPTOP, TV], preferred=1)
        self.assertEqual(visible((x, y, 1680, 932), [LAPTOP]), 0)         # sliver went on the TV when asked

    def test_a_single_screen(self):
        screen = (0, 0, 1440, 900)
        x, y = park(1200, 800, [screen], preferred=0)
        self.assertEqual((x, y), (0, 900 - SLIVER))

    def test_when_the_bottom_edge_is_taken_by_another_screen_the_right_edge_is_used(self):
        a, below = (0, 0, 1000, 1000), (0, 1000, 1000, 1000)
        self.assertEqual(park(1000, 1000, [a, below], preferred=0), (1000 - SLIVER, 0))

    def test_when_the_bottom_and_right_are_taken_the_left_edge_is_used(self):
        a, below, right = (0, 0, 1000, 1000), (0, 1000, 1000, 1000), (1000, 0, 1000, 1000)
        self.assertEqual(park(1000, 1000, [a, below, right], preferred=0), (-1000 + SLIVER, 0))

    def test_empty_input(self):
        self.assertIsNone(park(0, 100, [LAPTOP]))
        self.assertIsNone(park(100, 100, []))

    def test_any_answer_never_touches_a_second_screen(self):
        rnd = random.Random(7)
        for _ in range(400):
            n = rnd.randint(1, 3)
            screens, x = [], 0
            for _i in range(n):
                w, h = rnd.choice([(1440, 900), (1680, 1050), (1920, 1080), (2560, 1440)])
                screens.append((x, rnd.randint(-300, 500), w, h))
                x += w
            width, height = rnd.randint(400, 1900), rnd.randint(300, 1100)
            pos = park(width, height, screens, preferred=rnd.randint(0, n - 1))
            if pos is None:
                continue
            rect = (pos[0], pos[1], width, height)
            touched = [s for s in screens if visible(rect, [s]) > 0]
            self.assertEqual(len(touched), 1, (screens, rect))
            self.assertLessEqual(visible(rect, touched), SLIVER * max(width, height))


class ScriptTests(unittest.TestCase):
    def test_the_move_script_names_the_window_and_the_position(self):
        source = "\n".join(NS["karafun_move_window_script"](0, 1047))
        self.assertIn('if windowName is "Dual Renderer" then', source)
        self.assertIn("set position of w to {0, 1047}", source)

    @unittest.skipUnless(sys.platform == "darwin", "osacompile is macOS only")
    def test_the_move_script_compiles(self):
        source = "\n".join(NS["karafun_move_window_script"](-1650, 25))
        result = subprocess.run(["osacompile", "-o", "/dev/null"], input=source, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)


class WiringTests(unittest.TestCase):
    def test_it_is_on_by_default_and_has_a_switch(self):
        self.assertIn('"karafun_park_video_window": True', SOURCE)
        self.assertIn('karafun_park_cb.toggled.connect(lambda checked: _save_karafun_choice("karafun_park_video_window", checked))', SOURCE)

    def test_parking_runs_on_the_first_frame_and_on_every_guard_tick(self):
        self.assertIn('QTimer.singleShot(250, lambda: self._park_karafun_video_window("first_frame"))', SOURCE)
        guard = SOURCE[SOURCE.index("def _keep_audience_in_front():"):SOURCE.index("guard.timeout.connect(_keep_audience_in_front)")]
        self.assertIn('self._park_karafun_video_window("guard")', guard)
        self.assertIn("self._check_parked_video_alive()", guard)

    def test_parking_work_never_runs_on_the_gui_thread(self):
        body = SOURCE[SOURCE.index("def _park_karafun_video_window("):SOURCE.index("def _check_parked_video_alive(")]
        self.assertIn('threading.Thread(target=worker, daemon=True, name="karafun-park-window").start()', body)
        self.assertIn("karafun_dual_renderer_windows()", body[body.index("def worker():"):])

    def test_a_silent_capture_puts_the_window_back(self):
        body = SOURCE[SOURCE.index("def _check_parked_video_alive("):SOURCE.index("def _stop_karafun_dual_renderer_capture(")]
        self.assertIn("KARAFUN_PARK_FRAME_TIMEOUT_S", body)
        self.assertIn('state["_karafun_park_disabled"] = True', body)
        self.assertIn("karafun_move_window_script(*home)", body)

    def test_frames_are_timestamped_and_state_resets_with_the_capture(self):
        self.assertIn("self._karafun_last_frame_at = seen_at", SOURCE)
        stop = SOURCE[SOURCE.index("def _stop_karafun_dual_renderer_capture("):][:600]
        for key in ("_karafun_parked_at", "_karafun_park_disabled", "_karafun_video_window_home", "_karafun_last_frame_at"):
            self.assertIn(key, stop)

    def test_a_cold_start_gets_a_longer_playback_check(self):
        self.assertIn("max_probe_attempts = 12 if karafun_was_running else 30", SOURCE)
        self.assertIn("for probe_attempt in range(max_probe_attempts):", SOURCE)


if __name__ == "__main__":
    unittest.main()
