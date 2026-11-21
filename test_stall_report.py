import _test_profile  # noqa: F401
import contextlib
import io
import os
import tempfile
import unittest

from tools import stall_report as sr

LINES = [
    "[20:56:28] [INFO] [LAUNCH] {\"build_id\": \"1.0.4.1\"}",
    "[20:56:34] [INFO] [PERF-DIAG] app_startup took 6369ms",
    "[20:56:34] [INFO] [STALL] GUI thread recovered after ~3474ms",
    "[21:10:00] [INFO] [PERF-DIAG] ui_songstart_play_next took 683ms",
    "[21:10:00] [INFO] [STALL] GUI thread recovered after ~1228ms",
    "[21:10:30] [INFO] [PERF-DIAG] ui_update_queue_display took 25ms",
    "[21:10:30] [INFO] [STALL] GUI thread recovered after ~150ms",
    "[21:11:00] [INFO] [STALL] GUI thread recovered after ~250ms",
    "[23:59:59] [INFO] [STALL] GUI thread recovered after ~600ms",
    "[00:00:05] [INFO] [PERF-DIAG] server_sync took 1235ms",
    "[00:00:05] [INFO] [STALL] GUI thread recovered after ~2634ms",
    "not a log line",
]


class StallReportTests(unittest.TestCase):
    def run_report(self, lines=LINES):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return sr.analyse(sr.read([path])), path

    def test_counts_percentiles_and_the_midnight_rollover(self):
        r, _ = self.run_report()
        self.assertEqual(r["stalls"], 6)
        self.assertEqual(r["total_ms"], 3474 + 1228 + 150 + 250 + 600 + 2634)
        self.assertEqual(r["max"], 3474)
        self.assertEqual((r["over_500"], r["over_1000"]), (4, 3))
        self.assertEqual(r["launches"], 1)
        # the 00:00:05 stall comes after the 23:59:59 one: the clock went past midnight
        times = [b[0] for b in r["biggest"]]
        self.assertEqual(max(times) - min(times), 24 * 3600 + 5 - (21 * 3600 + 10 * 60), "the late stall is a day-lifted time, not an earlier one")

    def test_the_biggest_stalls_skip_the_launch_minute_and_name_the_operations_beside_them(self):
        r, _ = self.run_report()
        first = r["biggest"][0]
        self.assertEqual(first[1], 2634)
        self.assertEqual(first[2], [("server_sync", 1235)])
        self.assertNotIn(3474, [x for _, x, _ in r["biggest"]], "the launch stall is left out")
        self.assertEqual(r["biggest"][1][2], [("ui_songstart_play_next", 683)])

    def test_operations_by_total_time_and_beside_counts(self):
        r, _ = self.run_report()
        self.assertEqual(r["by_op"]["server_sync"], [1, 1235, 1235])
        self.assertEqual(r["beside"]["ui_update_queue_display"], 1)

    def test_the_text_and_the_main_entry(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(LINES) + "\n")
            text = sr.render(sr.analyse(sr.read([path])))
            self.assertIn("6 stalls", text)
            self.assertIn("server_sync", text)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(sr.main([path]), 0)
            self.assertIn("slow operations by total time", out.getvalue())
            self.assertEqual(sr.main([os.path.join(d, "missing.log")]), 2)

    def test_an_empty_log_does_not_crash(self):
        r, _ = self.run_report(["[10:00:00] [INFO] [X] nothing"])
        self.assertEqual((r["stalls"], r["max"], r["p50"]), (0, 0, 0))
        self.assertIn("0 stalls", sr.render(r))


if __name__ == "__main__":
    unittest.main()
