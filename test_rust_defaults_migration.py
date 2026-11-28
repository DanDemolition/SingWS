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

    def test_fresh_profiles_get_everything_on_and_the_shell_applying(self):
        d = self.mod.DEFAULTS
        for key in self.mod._RUST_EVIDENCE_FLAGS:
            self.assertIs(d[key], True, key)
        self.assertEqual(d["shell_commands"], "apply")
        self.assertIs(d["transport_shadow"], True)
        self.assertIs(d["transport_drive"], True)
        self.assertIs(d["rust_evidence_defaults_migrated"], False)

    def test_a_saved_profile_with_the_old_values_is_switched_on_once(self):
        saved = {k: False for k in self.mod._RUST_EVIDENCE_FLAGS}
        saved["shell_commands"] = "off"
        self.assertTrue(self.mod._migrate_rust_evidence_defaults(saved))
        for key in self.mod._RUST_EVIDENCE_FLAGS:
            self.assertIs(saved[key], True, key)
        self.assertEqual(saved["shell_commands"], "apply", "off became dry_run and then apply in the same pass")
        self.assertIs(saved["rust_evidence_defaults_migrated"], True)

    def test_the_rust_karafun_engine_is_the_default_and_a_saved_shadow_moves_once(self):
        self.assertEqual(self.mod.DEFAULTS["karafun_web_engine"], "rust")
        self.assertIs(self.mod.DEFAULTS["karafun_web_engine_rust_default_migrated"], False)
        saved = {"karafun_web_engine": "shadow"}
        self.mod._migrate_rust_evidence_defaults(saved)
        self.assertEqual(saved["karafun_web_engine"], "rust")
        self.assertIs(saved["karafun_web_engine_rust_default_migrated"], True)
        saved["karafun_web_engine"] = "shadow"  # chosen again afterwards: kept
        self.mod._migrate_rust_evidence_defaults(saved)
        self.assertEqual(saved["karafun_web_engine"], "shadow")

    def test_a_python_choice_and_a_missing_value_through_the_engine_migration(self):
        off = {"karafun_web_engine": "python"}
        self.mod._migrate_rust_evidence_defaults(off)
        self.assertEqual(off["karafun_web_engine"], "python")
        fresh = {}
        self.mod._migrate_rust_evidence_defaults(fresh)
        self.assertEqual(fresh.get("karafun_web_engine"), "rust")

    def test_a_profile_without_shell_commands_gets_apply(self):
        saved = {}
        self.assertTrue(self.mod._migrate_rust_evidence_defaults(saved))
        self.assertEqual(saved["shell_commands"], "apply")

    def test_after_the_marker_the_operator_choices_are_respected(self):
        saved = {k: False for k in self.mod._RUST_EVIDENCE_FLAGS}
        saved.update({"shell_commands": "off", "rust_evidence_defaults_migrated": True, "waitlist_snapshot_default_on_migrated": True,
                      "karafun_web_engine_rust_default_migrated": True,
                      "shell_commands_apply_default_migrated": True, "songend_gates_default_on_migrated": True})
        before = dict(saved)
        self.assertFalse(self.mod._migrate_rust_evidence_defaults(saved))
        self.assertEqual(saved, before)

    def test_the_waitlist_snapshot_migration_leaves_the_evidence_choices_alone(self):
        saved = {k: False for k in self.mod._RUST_EVIDENCE_FLAGS}
        saved.update({"shell_commands": "off", "rust_evidence_defaults_migrated": True, "waitlist_snapshot": False,
                      "karafun_web_engine_rust_default_migrated": True, "shell_commands_apply_default_migrated": True})
        self.assertTrue(self.mod._migrate_rust_evidence_defaults(saved))      # only the one new one-time step runs
        self.assertIs(saved["waitlist_snapshot"], True)
        self.assertTrue(all(saved[k] is False for k in self.mod._RUST_EVIDENCE_FLAGS), "the operator's evidence choices stay as they were")
        self.assertEqual(saved["shell_commands"], "off")

    def test_apply_is_never_downgraded_and_dry_run_moves_to_apply_once(self):
        for mode in ("apply", " Apply "):
            saved = {"shell_commands": mode}
            self.mod._migrate_rust_evidence_defaults(saved)
            self.assertEqual(saved["shell_commands"], mode)
        saved = {"shell_commands": "dry_run"}
        self.mod._migrate_rust_evidence_defaults(saved)
        self.assertEqual(saved["shell_commands"], "apply")
        self.assertIs(saved["shell_commands_apply_default_migrated"], True)
        saved["shell_commands"] = "dry_run"        # chosen again afterwards: kept
        self.mod._migrate_rust_evidence_defaults(saved)
        self.assertEqual(saved["shell_commands"], "dry_run")
        # a profile that turned the commands off after the first migration keeps them off
        off = {"rust_evidence_defaults_migrated": True, "shell_commands": "off"}
        self.mod._migrate_rust_evidence_defaults(off)
        self.assertEqual(off["shell_commands"], "off")

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
        self.assertEqual(loaded["shell_commands"], "apply")
        self.assertEqual(loaded["api_key"], "keep-me")
        self.assertIs(self.mod._STORE_SHADOW_ON, True)  # read once at launch by the store shadow
        self.assertIs(app._rust_defaults_just_migrated, True)  # so __init__ persists the file and logs the migration

    def test_the_load_path_uses_the_migration(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text()
        self.assertIn('if _migrate_rust_evidence_defaults(self.settings) or getattr(self, "_rust_defaults_just_migrated", False):', src)
        self.assertIn("_migrate_rust_evidence_defaults(loaded)", src)


if __name__ == "__main__":
    unittest.main()
