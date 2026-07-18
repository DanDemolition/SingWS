import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_multi_library", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MultiLibraryLocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def _location(self, path):
        return {
            "id": self.singws._library_location_id(str(path)),
            "path": self.singws._normalize_library_location_path(str(path)),
        }

    def _settings(self, locations, signatures=None):
        return {
            "filename_format": "artist-title-disc",
            "karaoke_library_locations": locations,
            "karaoke_scan_roots": [item["path"] for item in locations],
            "karaoke_scan_dir_sigs": signatures or {},
            "_scan_preserve_outside_roots": True,
        }

    def test_single_library_setting_migrates_and_duplicate_paths_are_collapsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "library"
            root.mkdir()
            alias = Path(tmp) / "alias"
            alias.symlink_to(root, target_is_directory=True)
            settings = {
                "karaoke_scan_roots": [str(root), str(alias)],
                "karaoke_library_folder": str(root),
            }

            locations, changed = self.singws._migrate_library_locations(settings)

            self.assertTrue(changed)
            self.assertEqual(len(locations), 1)
            self.assertEqual(locations[0]["path"], str(root.resolve()))
            self.assertEqual(settings["karaoke_scan_roots"], [str(root.resolve())])
            self.assertTrue(locations[0]["id"].startswith("library-"))

            settings["karaoke_library_locations"] = []
            settings["karaoke_scan_roots"] = []
            locations, _ = self.singws._migrate_library_locations(settings)
            self.assertEqual(locations, [], "a retired single-path key must not restore a removed location")

    def test_targeted_full_scan_preserves_other_location_and_signatures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / "a"
            root_b = Path(tmp) / "b"
            root_a.mkdir()
            root_b.mkdir()
            song_a = root_a / "Artist - One - A001.zip"
            song_b = root_b / "Artist - Two - B001.zip"
            song_a.write_bytes(b"a")
            song_b.write_bytes(b"b")
            locations = [self._location(root_a), self._location(root_b)]

            first = self.singws._build_library_scan_result(
                [str(root_a)], False, [], self._settings(locations)
            )
            sentinel = self.singws._normalize_library_location_path(str(root_a / "cached-subdirectory"))
            settings = self._settings(locations, {sentinel: [123, 4], **first["dir_sigs"]})
            second = self.singws._build_library_scan_result(
                [str(root_b)], False, first["tracks"], settings
            )

            self.assertEqual({Path(t["path"]).name for t in second["tracks"]}, {song_a.name, song_b.name})
            self.assertIn(sentinel, second["dir_sigs"])
            ids = {t.get("library_location_id") for t in second["tracks"]}
            self.assertEqual(ids, {locations[0]["id"], locations[1]["id"]})

    def test_offline_location_records_survive_and_reconnect_rescan_restores_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / "local"
            root_b = Path(tmp) / "external"
            root_a.mkdir()
            root_b.mkdir()
            (root_a / "Local - Song - L001.zip").write_bytes(b"local")
            external_song = root_b / "Drive - Song - E001.zip"
            external_song.write_bytes(b"external")
            locations = [self._location(root_a), self._location(root_b)]
            settings = self._settings(locations)
            initial = self.singws._build_library_scan_result(
                [str(root_a), str(root_b)], False, [], settings
            )
            self.assertEqual(len(initial["tracks"]), 2)

            disconnected = Path(tmp) / "external-disconnected"
            root_b.rename(disconnected)
            offline_settings = dict(settings)
            offline_settings.pop("_scan_preserve_outside_roots", None)
            offline_update = self.singws._build_library_scan_result(
                [str(root_a), str(root_b)], False, initial["tracks"], offline_settings
            )
            self.assertEqual(len(offline_update["tracks"]), 2)
            self.assertIn(
                self.singws._normalize_library_location_path(str(external_song)),
                {track["path"] for track in offline_update["tracks"]},
            )

            disconnected.rename(root_b)
            new_song = root_b / "Drive - New Song - E002.zip"
            new_song.write_bytes(b"new")
            reconnected = self.singws._build_library_scan_result(
                [str(root_b)], False, offline_update["tracks"], settings
            )
            self.assertEqual(len(reconnected["tracks"]), 3)
            self.assertIn(
                self.singws._normalize_library_location_path(str(new_song)),
                {track["path"] for track in reconnected["tracks"]},
            )

    def test_overlapping_roots_and_aliases_do_not_duplicate_the_same_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "library"
            child = parent / "nested"
            child.mkdir(parents=True)
            song = child / "Artist - Shared - S001.zip"
            song.write_bytes(b"song")
            alias = Path(tmp) / "alias-song.zip"
            os.link(song, alias)
            locations = [self._location(parent), self._location(child), self._location(Path(tmp))]

            result = self.singws._build_library_scan_result(
                [str(parent), str(child), str(Path(tmp))], False, [], self._settings(locations)
            )

            physical = [track for track in result["tracks"] if track["title"] == "Shared"]
            self.assertEqual(len(physical), 1)

    def test_same_metadata_in_different_files_remains_two_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / "a"
            root_b = Path(tmp) / "b"
            root_a.mkdir()
            root_b.mkdir()
            filename = "Artist - Same Song - DISC.zip"
            (root_a / filename).write_bytes(b"version-a")
            (root_b / filename).write_bytes(b"version-b")
            locations = [self._location(root_a), self._location(root_b)]

            result = self.singws._build_library_scan_result(
                [str(root_a), str(root_b)], False, [], self._settings(locations)
            )

            self.assertEqual(len(result["tracks"]), 2)
            self.assertEqual({track["title"] for track in result["tracks"]}, {"Same Song"})

    def test_removal_drops_only_records_not_covered_by_remaining_location(self):
        parent = "/Volumes/Karaoke"
        child = "/Volumes/Karaoke/Keep"
        tracks = [
            {"path": f"{parent}/Remove/song.zip"},
            {"path": f"{child}/song.zip"},
            {"path": "/Other/song.zip"},
        ]

        remaining = self.singws._tracks_after_library_location_removed(
            tracks, parent, [{"path": child}]
        )

        self.assertEqual(
            {track["path"] for track in remaining},
            {f"{child}/song.zip", "/Other/song.zip"},
        )

    def test_ui_exposes_add_status_rescan_and_remove_actions(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('QPushButton("Add Library Location…")', source)
        self.assertIn('QPushButton("Rescan Selected")', source)
        self.assertIn('QPushButton("Remove Selected")', source)
        self.assertIn('status = "Available" if online else "Offline"', source)


if __name__ == "__main__":
    unittest.main()
