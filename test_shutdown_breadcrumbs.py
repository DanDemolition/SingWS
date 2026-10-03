"""A normal quit leaves a line in the log, so a run that just stops can be told apart from one that crashed."""
import unittest
from pathlib import Path

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


class ShutdownBreadcrumbTests(unittest.TestCase):
    def test_about_to_quit_is_logged(self):
        self.assertIn('app.aboutToQuit.connect(lambda: _diag(f"[LAUNCH] clean shutdown (aboutToQuit) session={PROCESS_SESSION_UUID}"))', SOURCE)

    def test_close_event_is_logged_after_its_docstring(self):
        i = SOURCE.index("def closeEvent(self, event):\n        \"\"\"Close main window -> quit whole app.")
        body = SOURCE[i:i + 600]
        self.assertLess(body.index('"""', body.index('"""') + 3), body.index('[LAUNCH] main window closing (closeEvent)'))


class ShutdownWatchdogTests(unittest.TestCase):
    """2026-10-02 21:26:59: a quit that never finished. Settings and queue are saved first, then a watchdog guarantees exit."""

    def test_watchdog_starts_after_the_saves_and_before_the_network_teardown(self):
        i = SOURCE.index("def closeEvent(self, event):\n        \"\"\"Close main window -> quit whole app.")
        body = SOURCE[i:i + 9000]
        saved = body.index("[SHUTDOWN] settings and queue saved")
        started = body.index("self._start_shutdown_watchdog()")
        teardown = body.index("self._shutdown_network_transports()")
        self.assertLess(saved, started)
        self.assertLess(started, teardown)

    def test_watchdog_forces_exit_and_says_so(self):
        i = SOURCE.index("def _start_shutdown_watchdog(self):")
        body = SOURCE[i:i + 1600]
        self.assertIn("os._exit(0)", body)
        self.assertIn("[SHUTDOWN] still running", body)
        self.assertIn("daemon=True", body)

    def test_the_two_unbounded_waits_leave_breadcrumbs(self):
        self.assertIn('[SHUTDOWN] waiting for in-flight HTTP requests', SOURCE)
        self.assertIn('[SHUTDOWN] waiting for the poll thread', SOURCE)


if __name__ == "__main__":
    unittest.main()
