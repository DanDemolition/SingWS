import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_main_module():
    os.environ["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
    spec = importlib.util.spec_from_file_location("singws_main_songbook_upload", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MAIN = load_main_module()


class SongbookUploadRowsTests(unittest.TestCase):
    def test_rows_from_tracks_dedupes_and_sorts_current_library(self):
        rows = MAIN.SongbookUploadThread._rows_from_tracks([
            {"artist": "Queen", "title": "Somebody To Love"},
            {"artist": "queen", "title": "somebody to love"},
            {"artist": "Adele", "title": "Hello"},
            {"artist": "", "title": "No Artist"},
            {"artist": "No Title", "title": ""},
            "not-a-track",
        ])

        self.assertEqual(rows, [
            ("Adele", "Hello"),
            ("Queen", "Somebody To Love"),
        ])

    def test_collect_rows_prefers_loaded_tracks_over_search_database(self):
        thread = MAIN.SongbookUploadThread(
            "https://wskar.com",
            "wsk",
            "secret",
            [{"artist": "Live Library", "title": "Current Song"}],
        )

        self.assertEqual(thread._collect_rows(), [("Live Library", "Current Song")])


if __name__ == "__main__":
    unittest.main()
