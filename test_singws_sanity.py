import importlib.util
import tempfile
import unittest
from pathlib import Path


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JsonStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_missing_and_corrupt_json_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            corrupt = Path(tmp) / "corrupt.json"
            corrupt.write_text("{not json", encoding="utf-8")

            self.assertEqual(self.singws._load_json_file(missing, [], expected_type=list), [])
            self.assertEqual(self.singws._load_json_file(corrupt, {"ok": True}, expected_type=dict), {"ok": True})

    def test_atomic_json_save_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"
            payload = {"queue": [{"name": "Ada", "songs": []}]}

            self.singws._save_json_atomic(target, payload)

            self.assertEqual(self.singws._load_json_file(target, {}, expected_type=dict), payload)


if __name__ == "__main__":
    unittest.main()
