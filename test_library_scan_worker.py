import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

import song_index


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

    def test_progress_identifies_current_library_files_and_elapsed_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "karaoke-one"
            root.mkdir()
            for index in range(240):
                (root / f"Artist - Song {index} - D{index:04d}.zip").touch()
            progress = []

            self.singws._build_library_scan_result(
                [str(root)],
                False,
                [],
                {"filename_format": "artist-title-disc"},
                progress_cb=progress.append,
            )

            self.assertTrue(any("Library 1/1: karaoke-one" in item for item in progress))
            self.assertTrue(any("files" in item and "s" in item for item in progress))

    def test_worker_cancellation_preserves_existing_tracks_and_can_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            song = root / "Artist - Existing - D001.zip"
            song.write_bytes(b"zip")
            initial = self.singws._build_library_scan_result(
                [str(root)], False, [], {"filename_format": "artist-title-disc"}
            )
            worker = self.singws.LibraryScanWorker(
                [str(root)], True, initial["tracks"], {"filename_format": "artist-title-disc"}
            )
            results = []
            worker.finished.connect(results.append)
            worker.cancel()
            worker.run()

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["cancelled"])
            self.assertEqual(results[0]["tracks"], initial["tracks"])

            restarted = self.singws._build_library_scan_result(
                [str(root)], False, [], {"filename_format": "artist-title-disc"}
            )
            self.assertEqual(len(restarted["tracks"]), 1)

    def test_unavailable_root_is_reported_without_dropping_cached_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            cached = [{"path": str(pathlib.Path(tmp) / "offline" / "song.zip"), "title": "Song"}]
            unavailable = pathlib.Path(tmp) / "offline"

            result = self.singws._build_library_scan_result(
                [str(unavailable)],
                True,
                cached,
                {
                    "filename_format": "artist-title-disc",
                    "_scan_preserve_outside_roots": True,
                },
            )

            self.assertEqual(result["tracks"], cached)
            self.assertEqual(result["unavailable_roots"], [self.singws._normalize_library_location_path(unavailable)])

    def test_tracks_json_is_compact_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracks_path = pathlib.Path(tmp) / "tracks.json"
            tracks = [{"path": f"/songs/{index}.zip", "artist": "Artist", "title": f"Song {index}"} for index in range(50)]
            with mock.patch.object(self.singws, "TRACKS_PATH", tracks_path):
                self.singws._save_json_atomic(tracks_path, tracks)

            text = tracks_path.read_text(encoding="utf-8")
            self.assertEqual(json.loads(text), tracks)
            self.assertNotIn("\n  {", text)

    def test_index_reuses_scan_signatures_without_restatting_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracks_path = pathlib.Path(tmp) / "tracks.json"
            db_path = pathlib.Path(tmp) / "songs.db"
            tracks_path.write_text(json.dumps([{
                "path": "/unavailable/song.zip",
                "artist": "Artist",
                "title": "Song",
                "scan_mtime": 123,
                "scan_size": 456,
            }]), encoding="utf-8")

            with mock.patch.object(song_index.os.path, "getmtime", side_effect=AssertionError("unexpected stat")), \
                 mock.patch.object(song_index.os.path, "getsize", side_effect=AssertionError("unexpected stat")):
                rows, _elapsed = song_index.rebuild_from_tracks_json(
                    tracks_path, db_path, verbose=False
                )

            self.assertEqual(rows, 1)

    def test_incremental_index_updates_only_changed_and_removed_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracks_path = pathlib.Path(tmp) / "tracks.json"
            db_path = pathlib.Path(tmp) / "songs.db"
            first_path = "/songs/first.zip"
            removed_path = "/songs/remove.zip"
            tracks_path.write_text(json.dumps([
                {"path": first_path, "artist": "Artist", "title": "Original", "scan_mtime": 1, "scan_size": 2},
                {"path": removed_path, "artist": "Artist", "title": "Remove", "scan_mtime": 1, "scan_size": 2},
            ]), encoding="utf-8")
            song_index.rebuild_from_tracks_json(tracks_path, db_path, verbose=False)
            con = song_index._connect(db_path)
            original_id = con.execute("SELECT id FROM songs WHERE path=?", (first_path,)).fetchone()[0]
            con.close()

            tracks_path.write_text(json.dumps([
                {"path": first_path, "artist": "Artist", "title": "Updated", "scan_mtime": 2, "scan_size": 3},
            ]), encoding="utf-8")
            changed, removed, _elapsed = song_index.update_from_tracks_json(
                tracks_path,
                db_path,
                changed_paths=[first_path],
                removed_paths=[removed_path],
            )

            con = song_index._connect(db_path)
            rows = con.execute("SELECT id, path, title FROM songs").fetchall()
            con.close()
            self.assertEqual((changed, removed), (1, 1))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], original_id)
            self.assertEqual(rows[0]["title"], "Updated")


if __name__ == "__main__":
    unittest.main()
