import importlib.util
import os
from datetime import datetime
import unittest


def load_main_module():
    os.environ["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
    spec = importlib.util.spec_from_file_location("singws_main_history_last_sang", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SingerHistoryLastSangTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_tonight_performance_is_called_out(self):
        now = datetime(2026, 8, 29, 1, 30)
        performed = datetime(2026, 8, 29, 0, 42)
        text = self.singws._history_song_last_sang_text(performed.timestamp(), now=now)
        self.assertEqual(text, "Last sang tonight at 12:42 AM")

    def test_older_performance_includes_date_and_time(self):
        now = datetime(2026, 8, 29, 1, 30)
        performed = datetime(2026, 8, 22, 21, 5)
        text = self.singws._history_song_last_sang_text(performed.timestamp(), now=now)
        self.assertEqual(text, "Last sang Aug 22 at 9:05 PM")

    def test_song_row_includes_last_sang_note(self):
        now = datetime.now()
        song = {
            "artist": "Journey",
            "title": "Don't Stop Believin'",
            "last_performed_at": now.timestamp(),
        }
        text = self.singws.SingerHistorySongsBuildWorker._song_display(song)
        self.assertIn("Last sang tonight at", text)


if __name__ == "__main__":
    unittest.main()
