import importlib.util
import pathlib
import sys
import tempfile
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_library_scan", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LibraryScanWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_full_scan_builds_tracks_without_qt_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "Artist - Title - KV001.zip").write_bytes(b"zip")
            (root / "Band - Video - KV002.mp4").write_bytes(b"mp4")
            (root / "Singer - Tune - KV003.cdg").write_bytes(b"cdg")
            progress = []

            result = self.singws._build_library_scan_result(
                [str(root)],
                False,
                [],
                {"filename_format": "artist-title-disc"},
                progress_cb=progress.append,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["tracks"]), 3)
            self.assertEqual(result["zip_count"], 1)
            self.assertEqual(result["mp4_count"], 1)
            self.assertEqual(result["cdg_count"], 1)
            self.assertTrue(result["reindex_needed"])

    def test_quick_update_reuses_unchanged_directory_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "Artist - Title - KV001.zip").write_bytes(b"zip")
            settings = {"filename_format": "artist-title-disc"}
            first = self.singws._build_library_scan_result([str(root)], False, [], settings)
            self.assertEqual(len(first["tracks"]), 1)

            second = self.singws._build_library_scan_result(
                [str(root)],
                True,
                first["tracks"],
                {
                    "filename_format": "artist-title-disc",
                    "karaoke_scan_dir_sigs": first["dir_sigs"],
                },
            )

            self.assertTrue(second["ok"])
            self.assertEqual(len(second["tracks"]), 1)
            self.assertFalse(second["reindex_needed"])


if __name__ == "__main__":
    unittest.main()
