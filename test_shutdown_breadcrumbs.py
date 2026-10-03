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


class ShutdownProgressBreadcrumbTests(unittest.TestCase):
    """2026-10-02 21:26:59 logged "closing" but never "clean shutdown" (once, cause unknown); these show where a repeat stops."""

    def test_close_event_logs_its_progress(self):
        i = SOURCE.index("def closeEvent(self, event):\n        \"\"\"Close main window -> quit whole app.")
        body = SOURCE[i:i + 9000]
        self.assertLess(body.index("[SHUTDOWN] settings and queue saved"), body.index("[SHUTDOWN] stopping network transports"))
        self.assertLess(body.index("[SHUTDOWN] stopping network transports"), body.index("[SHUTDOWN] network transports stopped"))

    def test_the_two_unbounded_waits_leave_breadcrumbs(self):
        self.assertIn('[SHUTDOWN] waiting for in-flight HTTP requests', SOURCE)
        self.assertIn('[SHUTDOWN] waiting for the poll thread', SOURCE)
    
    def test_no_forced_exit_was_added(self):
        self.assertNotIn("_start_shutdown_watchdog", SOURCE)


if __name__ == "__main__":
    unittest.main()
