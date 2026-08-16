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

    def test_scan_ignores_all_hidden_dotfiles_and_purges_old_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            visible = root / "Artist - Song - D001.zip"
            hidden = root / ". Artist - Phantom - D002.mp4"
            visible.write_bytes(b"zip")
            hidden.write_bytes(b"AppleDouble")
            settings = {"filename_format": "artist-title-disc"}

            full = self.singws._build_library_scan_result(
                [str(root)], False, [], settings)
            self.assertEqual(
                [pathlib.Path(t["path"]).name for t in full["tracks"]],
                [visible.name],
            )

            quick = self.singws._build_library_scan_result(
                [str(root)], True,
                full["tracks"] + [{"path": str(hidden), "title": "Phantom"}],
                {
                    "filename_format": "artist-title-disc",
                    "karaoke_scan_dir_sigs": full["dir_sigs"],
                },
            )
            self.assertEqual(
                [pathlib.Path(t["path"]).name for t in quick["tracks"]],
                [visible.name],
            )

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


class EmptyRootMassDeletionGuardTests(unittest.TestCase):
    """2026-07-19 show outage: a configured root that scans empty while songs
    were previously indexed there (stale /Volumes mount husk) must preserve
    the old records instead of silently deleting the whole library."""

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def _old_tracks(self, root: pathlib.Path, count: int = 4):
        # Recorded libraries store canonical (realpath'd) paths; mirror that,
        # otherwise macOS /var vs /private/var symlinks skew the fixtures.
        canonical = pathlib.Path(str(root).replace("/var/", "/private/var/", 1)
                                 if str(root).startswith("/var/") else str(root))
        return [
            {
                "path": str(canonical / f"Artist{i} - Song{i} - KV{i:03d}.zip"),
                "type": "zip",
                "artist": f"Artist{i}",
                "title": f"Song{i}",
            }
            for i in range(count)
        ]

    def test_empty_root_with_prior_tracks_preserves_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            husk = pathlib.Path(tmp) / "StaleMount"
            husk.mkdir()  # exists but has no files: hollow mount point
            old = self._old_tracks(husk)

            result = self.singws._build_library_scan_result(
                [str(husk)], False, old,
                {"filename_format": "artist-title-disc",
                 "_scan_preserve_outside_roots": True},
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["removed_count"], 0)
            self.assertEqual(len(result["tracks"]), len(old))
            self.assertEqual(len(result["suspect_empty_roots"]), 1)
            self.assertIn("looked empty", result["summary_text"])

    def test_quick_mode_empty_root_also_preserves_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            husk = pathlib.Path(tmp) / "StaleMount"
            husk.mkdir()
            old = self._old_tracks(husk)

            result = self.singws._build_library_scan_result(
                [str(husk)], True, old,
                {"filename_format": "artist-title-disc"},
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["removed_count"], 0)
            self.assertEqual(len(result["tracks"]), len(old))
            self.assertEqual(len(result["suspect_empty_roots"]), 1)

    def test_truly_empty_new_root_is_not_suspect(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = pathlib.Path(tmp) / "BrandNew"
            fresh.mkdir()
            result = self.singws._build_library_scan_result(
                [str(fresh)], False, [],
                {"filename_format": "artist-title-disc"},
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["suspect_empty_roots"], [])
            self.assertEqual(len(result["tracks"]), 0)

    def test_partial_root_content_still_tracks_real_removals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "Library"
            root.mkdir()
            (root / "Artist0 - Song0 - KV000.zip").write_bytes(b"zip")
            old = self._old_tracks(root, count=3)  # two of three no longer exist

            result = self.singws._build_library_scan_result(
                [str(root)], False, old,
                {"filename_format": "artist-title-disc"},
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["suspect_empty_roots"], [])
            self.assertEqual(len(result["tracks"]), 1)
            self.assertEqual(result["removed_count"], 3 - 1)

    def test_intentional_location_removal_still_drops_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "Removed"
            old = self._old_tracks(root)
            remaining = self.singws._tracks_after_library_location_removed(
                old, str(root), []
            )
            self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
