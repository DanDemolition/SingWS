import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import importlib.util
import unittest
from pathlib import Path


def load():
    spec = importlib.util.spec_from_file_location("singws_main_defaults", Path(__file__).with_name("0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RustEvidenceDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load()

    def test_fresh_profiles_get_everything_on_and_the_shell_in_dry_run(self):
        d = self.mod.DEFAULTS
        for key in self.mod._RUST_EVIDENCE_FLAGS:
            self.assertIs(d[key], True, key)
        self.assertEqual(d["shell_commands"], "dry_run")
        self.assertIs(d["transport_shadow"], True)
        self.assertIs(d["transport_drive"], True)
        self.assertIs(d["rust_evidence_defaults_migrated"], False)

    def test_a_saved_profile_with_the_old_values_is_switched_on_once(self):
        saved = {k: False for k in self.mod._RUST_EVIDENCE_FLAGS}
        saved["shell_commands"] = "off"
        self.assertTrue(self.mod._migrate_rust_evidence_defaults(saved))
        for key in self.mod._RUST_EVIDENCE_FLAGS:
            self.assertIs(saved[key], True, key)
        self.assertEqual(saved["shell_commands"], "dry_run")
        self.assertIs(saved["rust_evidence_defaults_migrated"], True)

    def test_a_profile_without_shell_commands_gets_dry_run(self):
        saved = {}
        self.assertTrue(self.mod._migrate_rust_evidence_defaults(saved))
        self.assertEqual(saved["shell_commands"], "dry_run")

    def test_after_the_marker_the_operator_choices_are_respected(self):
        saved = {k: False for k in self.mod._RUST_EVIDENCE_FLAGS}
        saved.update({"shell_commands": "off", "rust_evidence_defaults_migrated": True})
        before = dict(saved)
        self.assertFalse(self.mod._migrate_rust_evidence_defaults(saved))
        self.assertEqual(saved, before)

    def test_apply_and_dry_run_are_never_downgraded(self):
        for mode in ("apply", "dry_run", " Apply "):
            saved = {"shell_commands": mode}
            self.mod._migrate_rust_evidence_defaults(saved)
            self.assertEqual(saved["shell_commands"], mode)

    def test_the_first_launch_after_the_upgrade_already_has_the_launch_time_flags_on(self):
        import json
        import tempfile

        old = {"store_shadow": False, "wire_shadow": False, "shell_commands": "off", "api_key": "keep-me"}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            path.write_text(json.dumps(old))
            saved = self.mod.SETTINGS_PATH
            self.mod.SETTINGS_PATH = path
            try:
                app = self.mod.KaraokeApp.__new__(self.mod.KaraokeApp)
                loaded = app.load_settings()
            finally:
                self.mod.SETTINGS_PATH = saved
        self.assertIs(loaded["store_shadow"], True)
        self.assertIs(loaded["wire_shadow"], True)  # read once at launch by _install_wire_shadow(self.settings)
        self.assertEqual(loaded["shell_commands"], "dry_run")
        self.assertEqual(loaded["api_key"], "keep-me")
        self.assertIs(self.mod._STORE_SHADOW_ON, True)  # read once at launch by the store shadow
        self.assertIs(app._rust_defaults_just_migrated, True)  # so __init__ persists the file and logs the migration

    def test_the_load_path_uses_the_migration(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text()
        self.assertIn('if _migrate_rust_evidence_defaults(self.settings) or getattr(self, "_rust_defaults_just_migrated", False):', src)
        self.assertIn("_migrate_rust_evidence_defaults(loaded)", src)


if __name__ == "__main__":
    unittest.main()
