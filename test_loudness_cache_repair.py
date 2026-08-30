import json
from pathlib import Path
import tempfile
import unittest

from tools.repair_loudness_cache import repair


class LoudnessCacheRepairTests(unittest.TestCase):
    def test_pending_checkpoint_requires_clean_shutdown(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "loudness.json"
            path.write_text('{}')
            path.with_suffix('.checkpoint.jsonl').write_text('pending result\n')
            with self.assertRaisesRegex(RuntimeError, 'Close SingWS cleanly'):
                repair(path, apply=True)
            self.assertEqual(path.read_text(), '{}')

    def test_repair_preserves_measurements_and_specific_failures(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "loudness.json"
            original = {
                "/songs/good.zip": {"i": -15.2, "peak_db": -1.1, "mode": "full"},
                "/songs/poison.zip": {"failed": True, "reason": "no measurable loudness"},
                "/songs/real.zip": {
                    "failed": True,
                    "failure_version": 2,
                    "reason": "no measurable loudness",
                },
                "/songs/broken.zip": {"failed": True, "reason": "Bad CRC-32"},
                "/songs/video.mp4": {"failed": True, "reason": "no measurable loudness"},
                "/songs/timeout.zip": {
                    "failed": True,
                    "reason": "Turbo helper failed: offline analysis helper response timed out",
                },
            }
            path.write_text(json.dumps(original), encoding="utf-8")

            removed, preserved, backup = repair(path, apply=True)

            self.assertEqual(removed, 4)
            self.assertEqual(preserved, 1)
            self.assertIsNotNone(backup)
            repaired = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(repaired["/songs/good.zip"], original["/songs/good.zip"])
            self.assertNotIn("/songs/poison.zip", repaired)
            self.assertNotIn("/songs/real.zip", repaired)
            self.assertIn("/songs/broken.zip", repaired)
            self.assertNotIn("/songs/video.mp4", repaired)
            self.assertNotIn("/songs/timeout.zip", repaired)
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "loudness.json"
            original = {"/songs/poison.zip": {"failed": True, "reason": "no measurable loudness"}}
            path.write_text(json.dumps(original), encoding="utf-8")
            removed, preserved, backup = repair(path, apply=False)
            self.assertEqual((removed, preserved, backup), (1, 0, None))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)


if __name__ == "__main__":
    unittest.main()
