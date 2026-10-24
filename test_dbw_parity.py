import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import random
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools import dbw_parity as dp

CLI = os.environ.get("SINGWS_DBW_CLI") or dp.DBW_DEFAULT
DB_CLI = os.environ.get("SINGWS_DB_CLI") or dp.DB_DEFAULT
needs_cli = unittest.skipUnless(Path(CLI).exists() and Path(DB_CLI).exists(),
                                "needs singws-dbw-cli and singws-db-cli (cargo build --release -p singws-dbw -p singws-db)")


@needs_cli
class WriterParityTests(unittest.TestCase):
    def setUp(self):
        dp.failures.clear()
        dp.counts.clear()

    def test_the_whole_differential_is_clean_on_a_small_run(self):
        self.assertEqual(dp.main(["--skip-unicode", "--cases", "10", "--seed", "7", "--cli", CLI, "--db-cli", DB_CLI]), 0, dp.failures[:5])
        self.assertGreater(dp.counts.get("marker ops", 0), 0)
        self.assertGreater(dp.counts.get("db compared (rebuild)", 0), 0)

    def test_the_comparison_notices_a_one_cell_difference(self):
        """Guards against a vacuous harness: a database that differs in one value must be reported."""
        rng = random.Random(1)
        with tempfile.TemporaryDirectory() as d:
            tracks = dp.make_tracks(rng, d, 8)
            tp = Path(d) / "tracks.json"
            dp.write_tracks(tp, tracks)
            a, b = str(Path(d) / "a.db"), str(Path(d) / "b.db")
            dp.py_rebuild(tp, a)
            shutil.copy(a, b)
            dp.compare_dbs("identical copies", DB_CLI, a, b)
            self.assertEqual(dp.failures, [])
            con = sqlite3.connect(b)
            con.execute("UPDATE songs SET artist_norm = artist_norm || 'x' WHERE id = (SELECT MIN(id) FROM songs)")
            con.commit()
            con.close()
            dp.compare_dbs("tampered copy", DB_CLI, a, b)
            self.assertEqual(len(dp.failures), 1)
            self.assertIn("tampered copy", dp.failures[0])
            dp.failures.clear()
            con = sqlite3.connect(b)
            con.execute("DROP INDEX idx_songs_title_norm")
            con.commit()
            con.close()
            dp.compare_dbs("dropped index", DB_CLI, a, b)
            self.assertEqual(len(dp.failures), 1, "a schema difference is reported too")

    def test_the_cli_refuses_the_live_profile_and_anything_outside_temp(self):
        live = os.path.expanduser("~/SingWS/singws.db")
        existed = os.path.exists(live)
        mtime = os.path.getmtime(live) if existed else None
        for path in (live, "/usr/local/x.db", "/etc/x.db"):
            r = dp.dbw(CLI, {"op": "index_init", "db": path})
            self.assertFalse(r["ok"], path)
            self.assertTrue(r["error"].startswith("refused:"), r)
        self.assertEqual(os.path.exists(live), existed)
        if existed:
            self.assertEqual(os.path.getmtime(live), mtime, "the live database was not touched")

    def test_the_text_helpers_agree_with_python_on_the_exhaustive_corner_cases(self):
        rng = random.Random(3)
        dp.check_text(CLI, rng, 500, skip_unicode=True)
        self.assertEqual(dp.failures, [])


if __name__ == "__main__":
    unittest.main()
