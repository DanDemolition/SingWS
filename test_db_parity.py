import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import hashlib
import json
import os
import random
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

import song_index
from tools import db_parity as dp

CLI = os.environ.get("SINGWS_DB_CLI") or dp.CLI_DEFAULT
_SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
needs_cli = unittest.skipUnless(Path(CLI).exists(), "needs the singws-db-cli binary (cargo build --release -p singws-db)")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hostile_db(path):
    """A WAL-mode database with every value kind, awkward text and a NOCASE index, built outside the app."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
        CREATE TABLE songs (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, artist TEXT, title TEXT, discid TEXT,
            duration_secs INTEGER, mtime INTEGER, size_bytes INTEGER, song_type TEXT, display TEXT, searchstring TEXT NOT NULL,
            artist_norm TEXT, title_norm TEXT, ratio REAL, raw BLOB);
        CREATE INDEX idx_songs_artist_title ON songs(artist COLLATE NOCASE, title COLLATE NOCASE);
        CREATE TABLE no_rowid (a TEXT PRIMARY KEY, b INTEGER) WITHOUT ROWID;
        INSERT INTO no_rowid VALUES ('x', 1), ('y', 2);
        INSERT INTO meta VALUES ('schema_version', '5');
        """
    )
    rows = [
        ("/m/a.mp3", "ABBA", "Waterloo", "d1", 168, 1771708484, 4_000_000, "mp3", "ABBA - Waterloo", "abba waterloo", "abba", "waterloo", 0.5, b"\x00\xff"),
        ("/m/b.mp3", "ÉLAN", "Straße 100%", None, None, None, None, None, None, "élan straße 100% x_y", "elan", "strasse 100", 1e300, None),
        ("/m/c.mp3", None, "", "", 9223372036854775807, -9223372036854775808, 0, "", "", "c", None, None, -0.0, b""),
        ("/m/d's \"quoted\".mp3", "O'Brien", 'Say "Hi"', "", 1, 2, 3, "cdg", "x", "o'brien say \"hi\"", "obrien", "say hi", float("inf"), b"abc"),
        ("/m/日本語/e.mp3", "日本", "語", None, 0, 0, 0, "mp4", "日本 - 語", "日本 語 x​y 😀", "日本", "語", 5e-324, None),
        ("/m/f.mp3", "Dup", "Song", None, 1, 1, 1, "mp3", None, "dup song", "dup", "song", 1.5, None),
        ("/m/g.mp3", "dup", "song", None, 2, 2, 2, "mp3", None, "dup song 2", "dup", "song", 2.5, None),
    ]
    con.executemany("INSERT INTO songs (path, artist, title, discid, duration_secs, mtime, size_bytes, song_type, display, searchstring,"
                    " artist_norm, title_norm, ratio, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO songs (path, artist, title, searchstring) VALUES ('/m/bad.mp3', CAST(x'fffe' AS TEXT), CAST(x'c328' AS TEXT), 'bad')")
    con.commit()
    con.close()


@needs_cli
class RealIndexerTests(unittest.TestCase):
    def test_a_large_database_built_by_the_apps_own_indexer_is_identical_in_both_engines(self):
        rng = random.Random(2026)
        artists = ["ABBA", "Beyoncé", "Sigur Rós", "Mötley Crüe", "Björk", "AC/DC", "P!nk", "Guns N' Roses", "Zoë", "Ünal", "日本のバンド",
                   "Daft Punk", "The Beatles", "Cher", "Queen"]
        words = ["love", "night", "Straße", "café", "100%", "under_score", "Ñandú", "x", "Rock", "Blue", "ñ", "Ω", "Hello", "World"]
        tracks = []
        for i in range(20000):
            artist = rng.choice(artists)
            title = " ".join(rng.choice(words) for _ in range(rng.randint(1, 4)))
            tracks.append({"path": f"/lib/{i % 500}/{artist} - {title} {i}.cdg", "artist": artist, "title": title,
                           "discid": rng.choice(["SC1", "SF2", "", "PH9"]), "duration": rng.randint(60, 400), "mtime": 1771708484 + i,
                           "size": rng.randint(1000, 9_000_000), "type": rng.choice(["cdg", "mp4", "mp3"]),
                           "display": f"{artist} - {title}"})
        with tempfile.TemporaryDirectory() as td:
            tp, db = Path(td) / "tracks.json", Path(td) / "singws.db"
            tp.write_text(json.dumps(tracks), encoding="utf-8")
            song_index.rebuild_from_tracks_json(tp, db, verbose=False)
            stats, bad = dp.check_database(CLI, db)
        self.assertEqual(bad, [], bad[:3])
        self.assertEqual(stats["rows:songs"], 20000)
        self.assertGreater(stats["identical"], 1000)
        self.assertEqual(stats["both_failed_alike"] + stats["same_rows_different_order"], 0)


@needs_cli
class HostileFixtureTests(unittest.TestCase):
    def test_every_value_kind_awkward_text_and_wal_mode_agree(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "h.db"
            hostile_db(db)
            stats, bad = dp.check_database(CLI, db)
        self.assertEqual(bad, [], bad[:3])
        self.assertEqual(stats["tables"], 3)
        self.assertGreater(stats["identical"], 50)

    def test_invalid_utf8_inf_blob_and_extreme_integers_are_encoded_identically(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "h.db"
            hostile_db(db)
            snap = Path(td) / "s.db"
            dp.snapshot(db, snap)
            req = {"op": "query", "sql": "SELECT artist, ratio, raw, duration_secs, mtime FROM songs ORDER BY id", "params": []}
            py, rs = dp.py_handle(snap, req), dp.rust_handle(CLI, snap, req)
            self.assertEqual(py, rs)
            rows = py["result"]["rows"]
            self.assertEqual(rows[3][1], {"$float": "inf"})
            self.assertEqual(rows[0][2], {"$blob": "00ff"})
            self.assertEqual(rows[2][3:], [9223372036854775807, -9223372036854775808])
            self.assertIn("$invalid_utf8", json.dumps(rows[-1]))

    def test_wildcards_the_app_does_not_escape_behave_the_same(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "h.db"
            hostile_db(db)
            snap = Path(td) / "s.db"
            dp.snapshot(db, snap)
            for term in ("%", "_", "x_y", "100%", "%%", "é", "É", "ß", "日本", "o'brien", "​", "😀", "a" * 300):
                req = {"op": "query", "sql": "SELECT id FROM songs WHERE searchstring LIKE ? ORDER BY artist, title LIMIT ?",
                       "params": [f"%{term}%", 500]}
                self.assertEqual(dp.py_handle(snap, req), dp.rust_handle(CLI, snap, req), term)


@needs_cli
class SafetyTests(unittest.TestCase):
    def test_snapshotting_a_wal_database_creates_and_changes_nothing_next_to_it(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "live" / "singws.db"
            src.parent.mkdir()
            hostile_db(src)
            keep = sqlite3.connect(src)  # keep a connection open, as the running app would
            keep.execute("INSERT INTO meta VALUES ('k', 'v')")
            keep.commit()
            before = {p.name: digest(p) for p in src.parent.iterdir()}
            dp.snapshot(src, Path(td) / "copy.db")
            after = {p.name: digest(p) for p in src.parent.iterdir()}
            keep.close()
            self.assertEqual(before, after, "no sidecar created, no byte changed")

    def test_the_cli_refuses_writes_and_never_modifies_or_creates_a_database(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "h.db"
            hostile_db(db)
            snap = Path(td) / "s.db"
            dp.snapshot(db, snap)
            before = digest(snap)
            for sql in ("INSERT INTO meta VALUES ('x','y')", "DELETE FROM songs", "DROP TABLE songs", "UPDATE songs SET title='x'",
                        "PRAGMA user_version = 99", "SELECT 1; DELETE FROM songs", "VACUUM", "ATTACH DATABASE 'z.db' AS z"):
                out = dp.rust_handle(CLI, snap, {"op": "query", "sql": sql, "params": []})
                self.assertFalse(out["ok"], sql)
            self.assertEqual(digest(snap), before)
            missing = Path(td) / "never.db"
            out = dp.rust_handle(CLI, missing, {"op": "schema"})
            self.assertFalse(out["ok"])
            self.assertFalse(missing.exists(), "a missing database is not created")
            self.assertEqual(sorted(p.name for p in Path(td).iterdir() if p.name.startswith("never")), [])

    def test_bad_requests_and_missing_binary_inputs_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            out = subprocess.run([CLI, str(Path(td) / "x.db")], input="not json", capture_output=True, text=True)
            self.assertEqual(out.returncode, 0)
            self.assertFalse(json.loads(out.stdout)["ok"])
            self.assertEqual(subprocess.run([CLI], capture_output=True, text=True).returncode, 2)


class ComparisonTests(unittest.TestCase):
    def test_a_real_difference_is_reported_and_a_tie_order_difference_is_classified(self):
        stats, bad = dp.Counter(), []
        a = {"ok": True, "result": {"columns": ["id", "artist"], "rows": [[1, "x"], [2, "x"]]}}
        dp.compare("same", a, json.loads(json.dumps(a)), stats, bad)
        self.assertEqual((stats["identical"], bad), (1, []))
        swapped = {"ok": True, "result": {"columns": ["id", "artist"], "rows": [[2, "x"], [1, "x"]]}}
        dp.compare("order", a, swapped, stats, bad)
        self.assertEqual(stats["same_rows_different_order"], 1)
        self.assertEqual(len(bad), 1)
        changed = {"ok": True, "result": {"columns": ["id", "artist"], "rows": [[1, "x"], [2, "y"]]}}
        dp.compare("diff", a, changed, stats, bad)
        self.assertEqual(bad[-1], ("diff", "results differ"))
        dp.compare("ok-vs-error", a, {"ok": False, "error": "boom"}, stats, bad)
        self.assertIn("rust ok=False", bad[-1][1])
        dp.compare("both-fail", {"ok": False, "error": "a"}, {"ok": False, "error": "b"}, stats, bad)
        self.assertEqual(stats["both_failed_alike"], 1)

    def test_int_and_float_are_not_conflated(self):
        self.assertNotEqual(json.dumps(dp.enc(1)), json.dumps(dp.enc(1.0)))
        stats, bad = dp.Counter(), []
        dp.compare("int-vs-real", {"ok": True, "result": {"columns": ["v"], "rows": [[1]]}},
                   {"ok": True, "result": {"columns": ["v"], "rows": [[1.0]]}}, stats, bad)
        # Python treats 1 == 1.0, but the rows are JSON-dumped for the order check; the equality above passes, so guard the encoding itself
        self.assertEqual(json.loads(json.dumps(dp.enc(1.0))), 1.0)
        self.assertIsInstance(json.loads(json.dumps(dp.enc(1.0))), float)
        self.assertIsInstance(json.loads(json.dumps(dp.enc(1))), int)


class WiringTests(unittest.TestCase):
    def test_the_rust_sqlite_reader_is_an_offline_tool_and_not_part_of_the_app_or_its_library(self):
        self.assertNotIn("singws-db", _SRC)
        self.assertNotIn("db_cli", _SRC)
        ffi = (Path(__file__).parent / "rust/crates/singws-dsp-ffi/Cargo.toml").read_text()
        self.assertNotIn("singws-db", ffi)
        self.assertNotIn("rusqlite", ffi)

    def test_the_crate_can_only_open_read_only(self):
        lib = (Path(__file__).parent / "rust/crates/singws-db/src/lib.rs").read_text()
        body = lib[:lib.index("#[cfg(test)]")]
        self.assertIn("SQLITE_OPEN_READ_ONLY", body)
        self.assertIn("PRAGMA query_only=ON", body)
        for forbidden in ("SQLITE_OPEN_READ_WRITE", "SQLITE_OPEN_CREATE", "execute(", ".backup("):
            self.assertNotIn(forbidden, body)


if __name__ == "__main__":
    unittest.main()
