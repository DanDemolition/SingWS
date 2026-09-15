import json
import os
import tempfile
import unittest
from pathlib import Path

import legacy_import as li


class LegacyImportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.legacy = base / "SingWS"
        self.new = base / "SingWSPro"
        (self.legacy / "logs").mkdir(parents=True)
        (self.legacy / "logs" / "singws.log").write_text("old log")
        (self.legacy / "settings.json").write_text(json.dumps({
            "bg_image": str(self.legacy / "bg.png"),
            "library": ["/Volumes/Karaoke", str(self.legacy / "extra")],
            "other": "/Users/x/SingWSOther",
        }))
        (self.legacy / "singer_history.json").write_text("{}")
        (self.legacy / "loudness.json.flushing").write_text("partial")
        (self.legacy / "sub").mkdir()
        (self.legacy / "sub" / "a.db").write_text("db")
        self.new.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _snapshot(self, root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_prompt_only_for_fresh_pro_folder_with_legacy_data(self):
        self.assertTrue(li.needs_prompt(self.new, self.legacy, env={}))
        self.assertFalse(li.needs_prompt(self.new, self.legacy, env={"SINGWS_HOME": "/tmp/x"}))
        self.assertFalse(li.needs_prompt(self.legacy, self.legacy, env={}))
        li.record_decision(self.new, "declined")
        self.assertFalse(li.needs_prompt(self.new, self.legacy, env={}))

    def test_no_prompt_without_legacy_or_with_existing_settings(self):
        other = Path(self._tmp.name) / "none"
        self.assertFalse(li.needs_prompt(self.new, other, env={}))
        (self.new / "settings.json").write_text("{}")
        self.assertFalse(li.needs_prompt(self.new, self.legacy, env={}))

    def test_import_copies_skips_and_rewrites_without_touching_legacy(self):
        before = self._snapshot(self.legacy)
        n = li.import_legacy_data(self.legacy, self.new)
        self.assertEqual(self._snapshot(self.legacy), before)
        self.assertEqual(n, 3)
        self.assertFalse((self.new / "logs").exists())
        self.assertFalse((self.new / "loudness.json.flushing").exists())
        self.assertTrue((self.new / "sub" / "a.db").exists())
        s = json.loads((self.new / "settings.json").read_text())
        self.assertEqual(s["bg_image"], str(self.new / "bg.png"))
        self.assertEqual(s["library"], ["/Volumes/Karaoke", str(self.new / "extra")])
        self.assertEqual(s["other"], "/Users/x/SingWSOther")
        self.assertFalse(li.needs_prompt(self.new, self.legacy, env={}))

    def test_import_never_overwrites_existing_pro_files(self):
        (self.new / "singer_history.json").write_text('{"pro": true}')
        li.import_legacy_data(self.legacy, self.new)
        self.assertEqual((self.new / "singer_history.json").read_text(), '{"pro": true}')


if __name__ == "__main__":
    unittest.main()
