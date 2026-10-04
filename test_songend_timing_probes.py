"""The song-end steps are timed so a show log can say which one causes the ~450 ms hitch seen on the Intel show Mac
(2026-10-03). The probes log only; they must stay on those steps and must not change what the methods return."""
import ast
import re
import time
import unittest
from pathlib import Path

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")
LABELS = {
    "_handle_media_end_safe": "ui_songend_handler", "_finish_media_end_cleanup": "ui_songend_cleanup",
    "_apply_idle_background": "ui_songend_idle_background", "_mark_next_up_overlay_pending_after_completion": "ui_songend_outro",
    "_refresh_rotation_request_qr": "ui_songend_qr_rotation", "_refresh_show_screen_qr": "ui_songend_qr_show",
    "_recreate_video_surfaces": "ui_songend_video_surfaces", "clear_now_singing": "ui_songend_clear_now_singing",
    "update_bg_button_state": "ui_songend_bg_button", "stop_playback": "ui_songend_stop_playback",
}


class SongEndTimingProbeTests(unittest.TestCase):
    def test_every_song_end_step_is_timed(self):
        for name, label in LABELS.items():
            self.assertRegex(SOURCE, r'@_perf_timed\("%s"\)\n    def %s\(' % (re.escape(label), re.escape(name)), name)

    def test_the_wrapper_returns_the_value_and_reraises(self):
        tree = ast.parse(SOURCE)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_perf_timed")
        logged = []
        ns = {"time": time, "_perf_log_if_slow": lambda name, ms: logged.append((name, ms))}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "probe", "exec"), ns)

        @ns["_perf_timed"]("ui_x")
        def ok(a, b=1):
            return a + b

        @ns["_perf_timed"]("ui_y")
        def boom():
            raise ValueError("kept")
        self.assertEqual(ok(2, b=3), 5)
        with self.assertRaises(ValueError):
            boom()
        self.assertEqual([n for n, _ in logged], ["ui_x", "ui_y"])        # logged even when the step raised


if __name__ == "__main__":
    unittest.main()
