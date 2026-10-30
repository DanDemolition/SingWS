"""Two small show-log fixes from 2026-10-29 (Intel, library on an external drive): reading the NEXT song file ahead on a worker thread (`_prewarm_read_file`,
`KaraokeApp._prewarm_next_song`, setting `prewarm_next_song_file`) and the light always-on GUI stall watchdog (`stall_watchdog_light`).
Real code under test: the module-level reader and the real method (bound onto a stand-in). The watchdog is checked against the source: it needs a live QApplication to run."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import re
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

import test_shell_queue_commands as base

m = base.Harness([]).m


class PrewarmReaderTests(unittest.TestCase):
    def test_it_reads_a_cdg_and_its_paired_mp3_and_logs_one_line(self):
        with tempfile.TemporaryDirectory() as d:
            cdg, mp3 = os.path.join(d, "a.cdg"), os.path.join(d, "a.MP3")
            Path(cdg).write_bytes(b"x" * 3000)
            Path(mp3).write_bytes(b"y" * 5000)
            lines = []
            real = m._diag
            m._diag = lines.append
            try:
                m._prewarm_read_file(cdg)
            finally:
                m._diag = real
            self.assertEqual(len(lines), 1)
            self.assertIn("read 7 KB", lines[0])  # 8000 bytes -> 7 KB (floor)

    def test_it_stops_at_the_limit(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "big.mp4")
            Path(f).write_bytes(b"z" * 3 * 1024 * 1024)
            lines = []
            real = m._diag
            m._diag = lines.append
            try:
                m._prewarm_read_file(f, max_bytes=1024 * 1024)
            finally:
                m._diag = real
            self.assertIn("read 1024 KB", lines[0])

    def test_a_missing_or_unreadable_file_never_raises(self):
        m._prewarm_read_file("/nonexistent/x.cdg")
        m._prewarm_read_file("")


class PrewarmMethodTests(unittest.TestCase):
    def app(self, **settings):
        a = types.SimpleNamespace(settings=dict({"prewarm_next_song_file": True}, **settings))
        a._first_active_entry_for_singer = lambda s: s["songs"][0] if s.get("songs") else None
        a._is_external_karafun_entry = lambda e: bool(isinstance(e, dict) and e.get("external"))
        a._song_info_primary_path = types.MethodType(m.KaraokeApp._song_info_primary_path, a)
        a._prewarm_next_song = types.MethodType(m.KaraokeApp._prewarm_next_song, a)
        return a

    def run_with(self, a, singer):
        seen = []
        real = m._prewarm_read_file
        m._prewarm_read_file = lambda p, *x: seen.append(p)
        try:
            a._prewarm_next_song(singer)
            for t in threading.enumerate():
                if t.name == "song-prewarm":
                    t.join(2)
        finally:
            m._prewarm_read_file = real
        return seen

    def singer(self, path, **entry):
        return {"name": "Ann", "songs": [dict({"song_info": (path, 0, 100)}, **entry)]}

    def test_it_warms_the_next_singers_first_song_once_per_path(self):
        a = self.app()
        s = self.singer("/Volumes/X/a.cdg")
        self.assertEqual(self.run_with(a, s), ["/Volumes/X/a.cdg"])
        self.assertEqual(self.run_with(a, s), [])  # same path again: nothing
        self.assertEqual(self.run_with(a, self.singer("/Volumes/X/b.zip")), ["/Volumes/X/b.zip"])

    def test_it_does_nothing_when_off_for_karafun_or_for_no_singer_or_non_paths(self):
        s = self.singer("/Volumes/X/a.cdg")
        self.assertEqual(self.run_with(self.app(prewarm_next_song_file=False), s), [])
        self.assertEqual(self.run_with(self.app(), self.singer("karafun_streaming:1", external=True)), [])
        self.assertEqual(self.run_with(self.app(), None), [])
        self.assertEqual(self.run_with(self.app(), {"name": "Bo", "songs": []}), [])
        self.assertEqual(self.run_with(self.app(), self.singer("relative/a.cdg")), [])

    def test_the_card_refresh_calls_it(self):
        src = Path(m.__file__).read_text()
        self.assertRegex(src, r"next_entry = self\._next_singer_entry\(\)\n\s+self\._prewarm_next_song\(next_entry\)")


class LightWatchdogSourceTests(unittest.TestCase):
    src = Path(m.__file__).read_text()

    def test_defaults_are_on(self):
        self.assertEqual(m.DEFAULTS["stall_watchdog_light"], True)
        self.assertEqual(m.DEFAULTS["prewarm_next_song_file"], True)

    def test_without_perf_debug_it_runs_light_and_off_switch_returns(self):
        body = self.src.split("def _install_main_thread_watchdog", 1)[1].split("\n# Settings defaults", 1)[0]
        self.assertRegex(body, r'"stall_watchdog_light", True\)\):\s+return\s+light = True')
        self.assertIn("threshold_ms = max(int(threshold_ms), 1000)", body)
        self.assertIn("hb_timer.start(500 if light else 50)", body)
        self.assertIn("time.sleep(0.1 if light else 0.03)", body)
        # the signal-based stack dump is on in either mode; the event filter stays opt-in
        self.assertIn('"stall_stack_capture", True', body)
        self.assertIn('"stall_event_attribution", False', body)


if __name__ == "__main__":
    unittest.main()
