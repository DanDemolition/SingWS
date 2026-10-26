import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import os
import threading
import time
import unittest
from pathlib import Path

import native_memory as nm

MB = 1024 * 1024


def doc(pid=4242, **cats):
    return json.dumps({"processes": [{"name": "SingWS", "pid": pid, "footprint": sum(d + s for d, s in cats.values()) * MB,
                                      "categories": {k: {"dirty": d * MB, "swapped": s * MB, "clean": 5 * MB, "regions": 3} for k, (d, s) in cats.items()}}]})


class ParseTests(unittest.TestCase):
    def test_sizes_are_dirty_plus_swapped_and_clean_pages_are_not_counted(self):
        total, cats = nm.parse_footprint(doc(**{"Malloc Large": (100, 20), "IOSurface": (50, 0), "__TEXT": (0, 0)}), 4242)
        self.assertEqual(cats["Malloc Large"][0], 120 * MB)
        self.assertEqual(cats["IOSurface"][0], 50 * MB)
        self.assertNotIn("__TEXT", cats)  # nothing dirty or swapped
        self.assertEqual(total, 170 * MB)

    def test_a_different_pid_or_garbage_is_not_accepted(self):
        self.assertIsNone(nm.parse_footprint(doc(pid=1, A=(1, 0)), 4242))
        self.assertIsNone(nm.parse_footprint("not json", 4242))
        self.assertIsNone(nm.parse_footprint("{}", 4242))

    def test_line_ranks_by_size_and_shows_the_change_since_the_last_probe(self):
        _, first = nm.parse_footprint(doc(**{"Malloc Large": (100, 0), "IOSurface": (50, 0), "Small": (1, 0)}), 4242)
        total, second = nm.parse_footprint(doc(**{"Malloc Large": (160, 0), "IOSurface": (40, 0), "Small": (1, 0)}), 4242)
        line = nm.format_line("song_end_3", total, second, first, top_n=2)
        self.assertEqual(line, "[MEMORY-NATIVE] label=song_end_3 footprint_mb=201 top=Malloc Large 160 (+60) | IOSurface 40 (-10)")
        self.assertIn("top=Malloc Large 100 | IOSurface 50", nm.format_line("launch", total, first, None, top_n=2))  # no delta on the first probe


class ProbeTests(unittest.TestCase):
    def make(self, texts, **kw):
        lines, calls = [], []
        probe = nm.NativeMemoryProbe(lines.append, pid=4242, interval=kw.pop("interval", 3600), min_gap=kw.pop("min_gap", 0.0), **kw)
        seq = iter(texts)

        def fake():
            calls.append(threading.current_thread().name)
            nxt = next(seq)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        probe._footprint_text = fake
        return probe, lines, calls

    def test_a_sample_logs_one_line_and_remembers_the_categories(self):
        probe, lines, _ = self.make([doc(A=(10, 0)), doc(A=(25, 0))])
        self.assertTrue(probe.sample("a"))
        self.assertTrue(probe.sample("b"))
        self.assertIn("A 10", lines[0])
        self.assertIn("A 25 (+15)", lines[1])

    def test_request_returns_at_once_and_the_work_happens_on_the_probe_thread(self):
        probe, lines, calls = self.make([doc(A=(1, 0))] * 5)
        probe.start()
        deadline = time.time() + 5
        while len(lines) < 1 and time.time() < deadline:
            time.sleep(0.02)
        t0 = time.perf_counter()
        probe.request("song_start_1")
        self.assertLess(time.perf_counter() - t0, 0.05)
        while len(lines) < 2 and time.time() < deadline:
            time.sleep(0.02)
        probe.stop()
        self.assertGreaterEqual(len(lines), 2, lines)
        self.assertIn("label=launch", lines[0])
        self.assertIn("label=song_start_1", lines[1])
        self.assertTrue(all(c == "native-memory-probe" for c in calls), calls)

    def test_three_failures_in_a_row_turn_it_off_and_say_so_once_each_time(self):
        probe, lines, _ = self.make([RuntimeError("rc=73"), RuntimeError("rc=73"), RuntimeError("rc=73"), doc(A=(1, 0))])
        for _ in range(3):
            self.assertFalse(probe.sample("x"))
        self.assertTrue(probe.disabled)
        self.assertEqual(len(lines), 2)  # first failure and the final "probe off"
        self.assertIn("probe off for this session", lines[-1])
        self.assertFalse(probe.sample("y"))  # disabled: no more runs
        self.assertEqual(len(lines), 2)

    def test_a_success_resets_the_failure_count(self):
        probe, lines, _ = self.make([RuntimeError("a"), RuntimeError("b"), doc(A=(1, 0)), RuntimeError("c"), RuntimeError("d")])
        for _ in range(5):
            probe.sample("x")
        self.assertFalse(probe.disabled)

    def test_requests_close_together_are_spaced_by_the_minimum_gap(self):
        probe, lines, _ = self.make([doc(A=(1, 0))] * 4, min_gap=0.3)
        probe.start()
        deadline = time.time() + 5
        while len(lines) < 1 and time.time() < deadline:
            time.sleep(0.02)
        t0 = time.time()
        probe.request("a")
        while len(lines) < 2 and time.time() < deadline:
            time.sleep(0.02)
        probe.stop()
        self.assertGreaterEqual(time.time() - t0, 0.2)

    @unittest.skipUnless(os.path.exists(nm.FOOTPRINT), "macOS footprint tool not present")
    def test_the_real_tool_works_on_this_process(self):
        lines = []
        probe = nm.NativeMemoryProbe(lines.append)
        self.assertTrue(probe.sample("real"), lines)
        self.assertRegex(lines[0], r"^\[MEMORY-NATIVE\] label=real footprint_mb=\d+ top=")


class WiringGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = Path(__file__).with_name("0.2.18.1.py").read_text()

    def test_default_on_and_started_with_the_memory_telemetry_off_the_gui_thread(self):
        self.assertIn('"native_memory_probe": True', self.src)
        start = self.src.index("def _start_memory_telemetry(self):")
        self.assertIn("self._start_native_memory_probe()", self.src[start:start + 400])
        self.assertIn("native_memory.NativeMemoryProbe(_diag)", self.src)

    def test_each_song_start_and_end_asks_for_a_sample(self):
        self.assertIn('probe.request(f"song_{phase}_{n}")', self.src)

    def test_the_module_ships_in_both_installers(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = Path(__file__).with_name(spec).read_text()
            self.assertIn('"native_memory.py"', text, spec)
            self.assertIn("'native_memory'", text, spec)


if __name__ == "__main__":
    unittest.main()
