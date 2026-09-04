from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from tools.repack_deflate64_archives import repack_one


SEVEN_ZIP = shutil.which("7zz") or "/usr/local/bin/7zz"


class RepackDeflate64ArchivesTests(unittest.TestCase):
    def test_repack_preserves_members_and_backs_up_original(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "library"
            root.mkdir()
            archive = root / "disc" / "song.zip"
            archive.parent.mkdir()
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
                output.writestr("nested/song.mp3", b"audio" * 100)
                output.writestr("nested/song.cdg", b"graphics" * 100)
            original = archive.read_bytes()
            backup_root = Path(td) / "backup"

            backup = repack_one(
                archive,
                library_root=root,
                backup_root=backup_root,
                seven_zip=SEVEN_ZIP,
                temp_root=Path(td),
            )

            self.assertEqual(backup.read_bytes(), original)
            with zipfile.ZipFile(archive) as repaired:
                self.assertEqual(repaired.read("nested/song.mp3"), b"audio" * 100)
                self.assertEqual(repaired.read("nested/song.cdg"), b"graphics" * 100)
                self.assertTrue(all(
                    info.compress_type == zipfile.ZIP_DEFLATED
                    for info in repaired.infolist() if not info.is_dir()
                ))

    def test_optional_cleanup_removes_only_macos_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "library"
            root.mkdir()
            archive = root / "song.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("song.mp3", b"audio")
                output.writestr("song.cdg", b"graphics")
                output.writestr("__MACOSX/._song.mp3", b"metadata")
                output.writestr("folder/._song.cdg", b"metadata")
            repack_one(
                archive,
                library_root=root,
                backup_root=Path(td) / "backup",
                seven_zip=SEVEN_ZIP,
                temp_root=Path(td),
                strip_macos_metadata=True,
            )
            with zipfile.ZipFile(archive) as repaired:
                self.assertEqual(set(repaired.namelist()), {"song.mp3", "song.cdg"})


if __name__ == "__main__":
    unittest.main()
