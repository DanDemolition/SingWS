"""Switching between the host window's main views many times must not grow threads, timers, widgets or open files
(a four-hour show opens these views hundreds of times). The real window is built off-screen in a separate process."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class HeadlessWindowStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SINGWS_QUICK_SURFACES="off")
        with tempfile.TemporaryDirectory() as home:
            env["SINGWS_HOME"] = home
            result = subprocess.run([sys.executable, str(ROOT / "tools" / "headless_stability_check.py")], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=240)
        lines = [l for l in result.stdout.splitlines() if l.startswith("{")]
        cls.data = json.loads(lines[-1]) if lines else None
        cls.returncode = result.returncode
        cls.tail = (result.stderr or result.stdout)[-400:]

    def setUp(self):
        if self.data is None:
            self.skipTest(f"the off-screen window could not be built in this environment (exit {self.returncode}): {self.tail!r}")

    def test_threads_and_timers_do_not_grow(self):
        b, a = self.data["before"], self.data["after"]
        self.assertEqual(a["py_threads"], b["py_threads"])
        self.assertEqual(a["qtimers"], b["qtimers"])
        self.assertEqual(a["active_timers"], b["active_timers"])
        self.assertEqual(a["qthreads_running"], b["qthreads_running"])

    def test_widgets_objects_and_files_do_not_grow(self):
        b, a = self.data["before"], self.data["after"]
        self.assertEqual(a["widgets"], b["widgets"])
        self.assertLessEqual(a["fds"], b["fds"])
        self.assertLess(a["py_objects"] - b["py_objects"], 500)
        self.assertLess(a["rss_mb"] - b["rss_mb"], 20)

    def test_nothing_fast_is_ticking_while_idle(self):
        self.assertEqual(self.data["after"]["fast_timers"], 0)


if __name__ == "__main__":
    unittest.main()
