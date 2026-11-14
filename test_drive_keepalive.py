"""The external-drive keepalive: a sleeping USB drive froze James's GUI for 5-7 s at song start (2026-11-13), so a thread reads a few KB from every external
library volume every 25 s (uncached), and says so when a read was slow."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import importlib.util
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_keepalive", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


M = load_main_module()


class VolumeRoot(unittest.TestCase):
    def test_only_external_volumes_count(self):
        self.assertEqual(M._volume_root("/Volumes/Transcend/KARAOKE LIBRARY/KV/song.cdg"), "/Volumes/Transcend")
        self.assertEqual(M._volume_root("/Volumes/Transcend/song.cdg"), "/Volumes/Transcend")
        self.assertEqual(M._volume_root("/Users/daniel/Music/song.cdg"), "")
        self.assertEqual(M._volume_root("/Volumes/Transcend"), "")
        self.assertEqual(M._volume_root(""), "")
        self.assertEqual(M._volume_root(None), "")


class Reads(unittest.TestCase):
    def test_a_small_read_returns_its_time_and_a_missing_file_raises(self):
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(os.urandom(100_000)); path = fh.name
        try:
            self.assertGreaterEqual(M._drive_keepalive_read(path), 0.0)
            with tempfile.NamedTemporaryFile(delete=False) as tiny:
                tiny.write(b"abc"); small = tiny.name
            self.assertGreaterEqual(M._drive_keepalive_read(small), 0.0)     # a file smaller than one block
            os.unlink(small)
        finally:
            os.unlink(path)
        with self.assertRaises(OSError):
            M._drive_keepalive_read(path)


class Loop(unittest.TestCase):
    def run_loop(self, candidates, reader, seconds=0.5, interval=0.05):
        stop = threading.Event()
        logs = []
        with unittest.mock.patch.object(M, "_drive_keepalive_read", reader):
            thread = threading.Thread(target=M._drive_keepalive_loop, args=(stop, candidates, interval, logs.append), daemon=True)
            thread.start()
            time.sleep(seconds)
            stop.set(); thread.join(2)
        self.assertFalse(thread.is_alive())
        return logs

    def test_it_reads_every_volume_repeatedly_and_stays_quiet_when_the_drive_answers_fast(self):
        seen = []
        logs = self.run_loop(lambda: {"/Volumes/A": "/Volumes/A/x.cdg", "/Volumes/B": "/Volumes/B/y.cdg"}, lambda p: (seen.append(p), 3.0)[1])
        self.assertIn("/Volumes/A/x.cdg", seen)
        self.assertIn("/Volumes/B/y.cdg", seen)
        self.assertGreater(seen.count("/Volumes/A/x.cdg"), 2)
        self.assertEqual(logs, [])

    def test_a_slow_answer_is_logged_as_the_drive_waking_up(self):
        logs = self.run_loop(lambda: {"/Volumes/A": "/Volumes/A/x.cdg"}, lambda p: 4200.0)
        self.assertTrue(logs and "/Volumes/A" in logs[0] and "asleep" in logs[0] and "4200" in logs[0])

    def test_an_unplugged_drive_is_dropped_and_the_loop_keeps_running(self):
        calls = {"n": 0}
        def reader(path):
            calls["n"] += 1
            raise OSError("gone")
        self.run_loop(lambda: {"/Volumes/A": "/Volumes/A/x.cdg"}, reader, seconds=0.4)
        self.assertGreater(calls["n"], 1)       # it asks the candidates again and tries a new file

    def test_a_failing_candidate_lookup_never_ends_the_thread(self):
        def boom():
            raise RuntimeError("tracks changing")
        self.run_loop(boom, lambda p: 1.0, seconds=0.3)


class Wiring(unittest.TestCase):
    def test_it_is_on_by_default_and_started_after_launch(self):
        self.assertTrue(M.DEFAULTS["keep_library_drives_awake"])
        self.assertEqual(M.DEFAULTS["drive_keepalive_seconds"], 25)
        src = open("0.2.18.1.py", encoding="utf-8").read()
        self.assertIn("QTimer.singleShot(12000, self._start_drive_keepalive)", src)

    def test_candidates_pick_one_track_per_external_volume(self):
        class App:
            tracks = [{"path": "/Users/d/a.cdg"}, {"path": "/Volumes/T/KV/a.cdg"}, {"path": "/Volumes/T/KV/b.cdg"}, {"path": "/Volumes/U/c.cdg"}, "junk"]
        found = M.KaraokeApp._drive_keepalive_candidates(App())
        self.assertEqual(found, {"/Volumes/T": "/Volumes/T/KV/a.cdg", "/Volumes/U": "/Volumes/U/c.cdg"})


if __name__ == "__main__":
    import unittest.mock
    unittest.main()
