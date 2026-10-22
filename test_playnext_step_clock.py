"""Song-start timing probe: log-only, one line, only when the start is slow."""
import importlib.util
import os
import time
import unittest


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_stepclock", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StepClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def test_slow_start_logs_one_line_naming_the_slow_step(self):
        lines = []
        self.m._diag = lambda msg: lines.append(msg)
        c = self.m._StepClock("PLAYNEXT-STEPS", threshold_ms=30)
        c.mark("fast")
        time.sleep(0.06)
        c.mark("file_exists")
        c.report("tail")
        self.assertEqual(len(lines), 1)
        self.assertIn("[PLAYNEXT-STEPS] steps total=", lines[0])
        self.assertIn("file_exists=", lines[0])
        self.assertNotIn("fast=", lines[0])            # steps under 5 ms stay out of the line

    def test_a_fast_start_logs_nothing(self):
        lines = []
        self.m._diag = lambda msg: lines.append(msg)
        c = self.m._StepClock("PLAYNEXT-STEPS", threshold_ms=500)
        c.mark("x")
        c.report()
        self.assertEqual(lines, [])

    def test_play_next_marks_each_step(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"), encoding="utf-8").read()
        body = src[src.index("    def play_next_file"):src.index("    def play_cdg_mp3_dual")]
        for step in ("flush_remote_adds", "cancel_cleanup", "snapshot_rotation", "refresh_metadata", "file_exists", "duration"):
            self.assertIn(f'_pn_clock.mark("{step}")', body)
        self.assertIn("_pn_clock.report(", body)


if __name__ == "__main__":
    unittest.main()
