"""Tests for the song search inside an individual singer's history view."""

import importlib.util
import os
import sys
import unittest

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_history_search", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SearchBox:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text


def _songs():
    return [
        ("neil young|heart of gold", {"artist": "Neil Young", "title": "Heart Of Gold", "disc_id": "CC 101"}),
        ("avenged sevenfold|almost easy", {"artist": "Avenged Sevenfold", "title": "Almost Easy", "disc_id": "KV 200"}),
        ("mxpx|responsibility", {"artist": "MxPx", "title": "Responsibility", "disc_id": "KV 300"}),
        ("cher|believe", {"artist": "Cher", "title": "Believe", "disc_id": "SC 400", "duet_display": "Cher & Dan"}),
    ]


class SingerHistorySongSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def _app(self, query=""):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.singer_history_song_search = _SearchBox(query)
        app._singer_history_song_total_unfiltered = 0
        app._singer_history_song_filter_active = False
        return app

    def _titles(self, items):
        return [song.get("title") for _key, song in items]

    def test_empty_query_returns_all_songs(self):
        app = self._app("")
        self.assertEqual(len(app._filter_singer_history_songs(_songs())), 4)

    def test_title_substring_match_is_case_insensitive(self):
        app = self._app("ALMOST")
        self.assertEqual(self._titles(app._filter_singer_history_songs(_songs())), ["Almost Easy"])

    def test_artist_and_multi_token_match(self):
        app = self._app("neil gold")
        self.assertEqual(self._titles(app._filter_singer_history_songs(_songs())), ["Heart Of Gold"])

    def test_disc_id_and_duet_fields_are_searchable(self):
        app = self._app("sc 400")
        self.assertEqual(self._titles(app._filter_singer_history_songs(_songs())), ["Believe"])
        app = self._app("duet dan")
        self.assertEqual(self._titles(app._filter_singer_history_songs(_songs())), [])
        app = self._app("cher dan")
        self.assertEqual(self._titles(app._filter_singer_history_songs(_songs())), ["Believe"])

    def test_no_match_returns_empty(self):
        app = self._app("zebra")
        self.assertEqual(app._filter_singer_history_songs(_songs()), [])

    def test_meta_text_reflects_filter_state(self):
        app = self._app("kv")
        app._singer_history_song_total_unfiltered = 40
        app._singer_history_song_filter_active = True
        self.assertEqual(app._singer_history_song_meta_text(2), "2 of 40 entries")
        self.assertEqual(app._singer_history_song_meta_text(30, 20), "30 of 40 entries (20 shown)")
        app._singer_history_song_filter_active = False
        app._singer_history_song_total_unfiltered = 2
        self.assertEqual(app._singer_history_song_meta_text(2), "2 entries")

    def test_malformed_rows_are_skipped_not_fatal(self):
        app = self._app("gold")
        rows = _songs() + ["not-a-tuple", ("key-only",)]
        self.assertEqual(self._titles(app._filter_singer_history_songs(rows)), ["Heart Of Gold"])


if __name__ == "__main__":
    unittest.main()
