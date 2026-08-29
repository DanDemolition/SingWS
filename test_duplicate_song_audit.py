import tempfile
import unittest
from pathlib import Path
import zipfile

import duplicate_song_audit as audit


class DuplicateSongAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, name, audio=b"audio", cdg=b"lyrics"):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("song.mp3", audio)
            zf.writestr("song.cdg", cdg)
        return path

    def test_identical_audio_and_cdg_recommends_one_keeper(self):
        first = self.archive("Song.zip")
        copied = self.archive("Song Copy.zip")
        result = audit.audit_zip_duplicates([copied, first])
        group = result["groups"][0]
        self.assertTrue(group["eligible"])
        self.assertEqual(group["keeper"], str(first))
        self.assertEqual(set(group["paths"]), {str(first), str(copied)})

    def test_same_audio_with_different_cdg_is_review_only(self):
        first = self.archive("A.zip", cdg=b"lyrics-a")
        second = self.archive("B.zip", cdg=b"lyrics-b")
        result = audit.audit_zip_duplicates([first, second])
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["kind"], "identical_audio_different_cdg")
        self.assertFalse(result["groups"][0]["eligible"])

    def test_crc_candidates_are_verified_by_sha256(self):
        first = self.archive("A.zip", audio=b"one")
        second = self.archive("B.zip", audio=b"two")
        result = audit.audit_zip_duplicates([first, second])
        self.assertEqual(result["groups"], [])

    def test_move_is_recoverable_and_never_removes_keeper_implicitly(self):
        first = self.archive("A.zip")
        second = self.archive("B.zip")
        result = audit.move_to_recovery([second], self.root / "recovery")
        self.assertTrue(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue(Path(result["moved"][0]["destination"]).exists())


if __name__ == "__main__":
    unittest.main()
