"""[MEMORY-SONG]: one cheap line at every song start and end (rss, Python heap blocks, widgets, timers, threads, fds), logging only."""
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-songmem-")
SRC = (Path(__file__).resolve().parent / "0.2.18.1.py").read_text(encoding="utf-8")


class SongMemoryLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location("singws_main_songmem", Path(__file__).resolve().parent / "0.2.18.1.py")
        cls.m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.m
        spec.loader.exec_module(cls.m)

    def test_it_logs_start_and_end_with_deltas(self):
        from PyQt6.QtWidgets import QWidget
        host = QWidget()
        host._current_karaoke_singer_display = "Diamond"
        host._log_song_memory = types.MethodType(self.m.KaraokeApp._log_song_memory, host)
        lines = []
        with mock.patch.object(self.m, "_diag", lambda s, *a, **k: lines.append(s)):
            host._log_song_memory("start")
            host._log_song_memory("end")
        self.assertEqual(len(lines), 2)
        self.assertIn("[MEMORY-SONG] phase=start n=1", lines[0])
        self.assertIn("phase=end n=1", lines[1])
        for key in ("rss_mb=", "d_rss=", "py_blocks_k=", "widgets=", "timers=", "threads=", "fds=", "singer='Diamond'"):
            self.assertIn(key, lines[1])

    def test_it_is_hooked_into_native_and_karafun_song_start_and_end(self):
        self.assertEqual(SRC.count('_song_memory_hook(self, "start")'), 2)
        self.assertEqual(SRC.count('_song_memory_hook(self, "end")'), 2)
        body = SRC[SRC.index("    def _log_song_memory"):SRC.index("    def _log_memory_telemetry")]
        for expensive in ("gc.get_objects", "gc.collect", "tracemalloc"):
            self.assertNotIn(expensive, body)                       # nothing heavy runs at a song change


if __name__ == "__main__":
    unittest.main()
