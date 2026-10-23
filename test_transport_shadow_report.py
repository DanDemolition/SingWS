import importlib.util
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("tsr", Path(__file__).with_name("tools") / "transport_shadow_report.py")
tsr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tsr)

CLEAN = """[21:00:00] [INFO] [TRANSPORT-SHADOW] enabled available=1
[21:04:59] [INFO] [TRANSPORT-SHADOW] session 1 started source=native:cdg external=0 duration=200.0
[21:05:00] [INFO] [TRANSPORT-SHADOW] cmd session=1 {"BeginPlayback": 1}
[21:05:01] [INFO] [TRANSPORT-SHADOW] cmd session=1 {"Ignore": "uncorroborated idle"}
[21:08:30] [INFO] [TRANSPORT-SHADOW] seek session=1 source=native:cdg from=120.0 to=180.0 duration=200.0 remaining=20.0 near_end=1
[21:09:00] [INFO] [TRANSPORT-SHADOW] session 1 summary events=5 total_mismatches=0
[21:10:00] [INFO] [TRANSPORT-SHADOW] session 2 started source=native:mp4 external=0 duration=180.0
[21:15:00] [INFO] [TRANSPORT-SHADOW] session 2 summary events=4 total_mismatches=0
[21:16:00] [INFO] [TRANSPORT-SHADOW] session 3 started source=karafun external=1 duration=240.0
[21:19:59] [INFO] [TRANSPORT-SHADOW] finish session=3 source=karafun action=complete manual=1
[21:20:00] [INFO] [TRANSPORT-SHADOW] session 3 summary events=4 total_mismatches=0
"""
DIRTY = CLEAN + "[21:20:00] [INFO] [TRANSPORT-SHADOW] MISMATCH app auto-completed session 3; machine had not completed it\n"


def write(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
    f.write(text)
    f.close()
    return f.name


class ReportTests(unittest.TestCase):
    def test_counts_sessions_ignores_and_mismatches(self):
        r = tsr.summarise(write(DIRTY))
        self.assertEqual(r["sessions"], 3)
        self.assertEqual(len(r["mismatches"]), 1)
        self.assertEqual(r["ignores"]["uncorroborated idle"], 1)
        self.assertEqual(r["commands"]["BeginPlayback"], 1)
        self.assertEqual(r["coverage"], set(tsr.REQUIRED_COVERAGE))

    def test_acceptance_needs_three_clean_shows_and_no_dirty_ones(self):
        clean = [tsr.summarise(write(CLEAN)) for _ in range(3)]
        self.assertEqual(tsr.verdict(clean), (3, 3, True))
        self.assertEqual(tsr.verdict(clean[:2])[2], False)                       # only two shows
        self.assertEqual(tsr.verdict(clean + [tsr.summarise(write(DIRTY))])[2], False)  # one show with a disagreement

    def test_a_log_without_shadow_lines_is_not_a_watched_show(self):
        r = tsr.summarise(write("[21:00:00] [INFO] nothing here\n"))
        self.assertEqual(r["sessions"], 0)
        self.assertEqual(tsr.verdict([r]), (0, 0, False))

    def test_three_clean_shows_without_required_coverage_do_not_pass(self):
        legacy = """[21:00:00] [INFO] [TRANSPORT-SHADOW] enabled available=1
[21:09:00] [INFO] [TRANSPORT-SHADOW] session 1 summary events=5 total_mismatches=0
"""
        clean = [tsr.summarise(write(legacy)) for _ in range(3)]
        self.assertEqual(tsr.verdict(clean), (3, 3, False))


if __name__ == "__main__":
    unittest.main()
