"""A search the operator has typed past must stop scanning (2026-10-01 show: six searches in 8 s took 1-4 s each, each
one waiting behind a scan whose results nobody wanted). An aborted search returns [] and is never cached."""
import tempfile
import time
import unittest
from pathlib import Path

import song_index

ROWS = 40_000


def build(tmp):
    db = Path(tmp) / "songs.db"
    con = song_index._connect(db)
    song_index.init_schema(con)
    data = []
    for i in range(ROWS):
        artist, title = f"Artist{i % 997}", f"Tune {i:06d}"
        data.append((f"/lib/{i}.zip", artist, title, f"{artist} {title}".lower()))
    con.executemany("INSERT INTO songs(path, artist, title, searchstring) VALUES (?,?,?,?)", data)
    con.commit()
    con.close()
    return db


class SearchAbortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = build(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_normal_search_is_unchanged(self):
        rows = song_index.search_songs("tune 001234", dbfile=self.db, fuzzy=False)
        self.assertEqual([r["title"] for r in rows], ["Tune 001234"])
        polled = []
        rows2 = song_index.search_songs("tune 004321", dbfile=self.db, fuzzy=False, should_abort=lambda: polled.append(1) or False)
        self.assertEqual([r["title"] for r in rows2], ["Tune 004321"])
        self.assertTrue(polled, "SQLite should poll the callback during a long scan")

    def test_an_aborted_strict_scan_stops_early_and_is_not_cached(self):
        calls = []
        started = time.monotonic()
        rows = song_index.search_songs("zzqqxx", dbfile=self.db, fuzzy=False, should_abort=lambda: calls.append(1) or True)
        self.assertEqual(rows, [])
        self.assertEqual(len(calls), 1, "the first poll must end the scan")
        self.assertLess(time.monotonic() - started, 0.5)
        # the same query without an abort must still really search, not return a cached empty list
        rows = song_index.search_songs("tune 000077", dbfile=self.db, fuzzy=False, should_abort=lambda: True)
        self.assertEqual(rows, [])
        rows = song_index.search_songs("tune 000077", dbfile=self.db, fuzzy=False)
        self.assertEqual([r["title"] for r in rows], ["Tune 000077"])

    def test_an_aborted_fuzzy_scan_returns_nothing_and_is_not_cached(self):
        query = "artst4 tne 000005"                         # typos: strict finds nothing, so the fuzzy scan runs
        self.assertEqual(song_index.search_songs(query, dbfile=self.db, fuzzy=True, should_abort=lambda: True), [])
        fresh = song_index.search_songs(query, dbfile=self.db, fuzzy=True)
        self.assertGreater(len(fresh), 0)

    def test_without_the_argument_behaviour_is_as_before(self):
        a = song_index.search_songs("artist12 tune", dbfile=self.db, fuzzy=False, limit=50)
        b = song_index.search_songs("artist12 tune", dbfile=self.db, fuzzy=False, limit=50, should_abort=None)
        self.assertEqual(len(a), len(b))
        self.assertGreater(len(a), 0)


class ThreadWiringTests(unittest.TestCase):
    def test_the_search_thread_passes_its_interruption_flag(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        body = source[source.index("class SongSearchThread"):source.index("_RETIRED_SEARCH_THREADS = []")]
        self.assertEqual(body.count("should_abort=self.isInterruptionRequested"), 2)


if __name__ == "__main__":
    unittest.main()
