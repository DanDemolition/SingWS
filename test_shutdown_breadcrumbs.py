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


if __name__ == "__main__":
    unittest.main()
