"""A window resize reformats queue song rows; the CUE/NOW/SKIP status tags must survive it."""
import importlib.util
import os
import tempfile
import types
import unittest

os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class ReformatKeepsStatusTagsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location(
            "singws_main_reformat_tags", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def test_status_prefix_survives_reformat(self):
        from PyQt6.QtCore import Qt
        m = self.m
        R = int(Qt.ItemDataRole.UserRole) + 21
        cases = ["CUE  03:20", "NOW  03:20", "SKIP  03:20", "PLAYING KARAFUN  03:20",
                 "CUE  PLAYING KARAFUN  03:20", "CUE", "03:20"]
        rows = [{"kind": "song", "entry": {"display_name": "A - B", "duration": 200},
                 "roles": {R: r}, "text": "x"} for r in cases]
        model = m.QueueListModel()
        model.syncRows(rows)
        fake = types.SimpleNamespace(
            results_list=types.SimpleNamespace(count=lambda: 0),
            queue_display_model=model,
            _build_queue_song_row_text=lambda e: ("v", "t", "l", "03:20"),
        )
        m.KaraokeApp._reformat_lists_now(fake)
        got = [model.index(i, 0).data(R) for i in range(len(cases))]
        self.assertEqual(got, ["CUE  03:20", "NOW  03:20", "SKIP  03:20", "PLAYING KARAFUN  03:20",
                               "CUE  PLAYING KARAFUN  03:20", "CUE  03:20", "03:20"])


if __name__ == "__main__":
    unittest.main()
