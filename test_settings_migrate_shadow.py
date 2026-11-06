"""settings_migrate_shadow: the log-only comparison of the app's startup settings migrations with the Rust singws-settingsmig helper. Python's settings are always the ones used."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import importlib.util
import json
import os
import random
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import settings_migrate_shadow as sm

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
CLI = os.environ.get("SINGWS_SETTINGSMIG_CLI") or os.path.join(
    os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-settingsmig")), "release", "singws-settingsmig")
SECRET = "SUPER-SECRET-VALUE-123"


def load_main_module():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    spec = importlib.util.spec_from_file_location("singws_settings_migrate_shadow_app", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["singws_settings_migrate_shadow_app"] = module
    spec.loader.exec_module(module)
    return module


def fake_helper(tmp, body):
    path = Path(tmp) / "fake-settingsmig"
    path.write_text("#!/bin/sh\ncat >/dev/null\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Basics(unittest.TestCase):
    def setUp(self):
        sm._seen.clear()
        sm._clean_noted = False

    def test_default_on_and_off_switch(self):
        self.assertTrue(sm.enabled({}))
        self.assertTrue(sm.enabled(None))
        self.assertFalse(sm.enabled({"settings_migrate_shadow": False}))

    def test_only_migration_keys_are_sent_never_secrets(self):
        s = {"venue_api_key": SECRET, "pin": "1234", "karaoke_library_locations": ["/x"], "cdg_timing_offset_ms": 5, "shell_commands": "off"}
        out = sm.subset(s)
        self.assertEqual(out, {"cdg_timing_offset_ms": 5, "shell_commands": "off"})
        self.assertNotIn(SECRET, json.dumps(out))

    def test_subset_keeps_the_dicts_own_order(self):
        s = {"mp4_timing_offset_mpv_ms": 1, "junk": 2, "cdg_timing_offset_ms": 3}
        self.assertEqual(list(sm.subset(s)), ["mp4_timing_offset_mpv_ms", "cdg_timing_offset_ms"])

    def test_compare_reports_key_names_not_values(self):
        py = {"cdg_timing_offset_ms": SECRET, "transport_shadow": True}
        rs = {"settings": {"cdg_timing_offset_ms": 0, "transport_shadow": True}, "changed": True}
        diffs = sm.compare(py, True, rs)
        self.assertEqual(diffs, ["keys: cdg_timing_offset_ms"])
        self.assertNotIn(SECRET, " ".join(diffs))

    def test_compare_missing_key_on_either_side_and_order_and_changed(self):
        self.assertEqual(sm.compare({"safe_mode": 1}, True, {"settings": {}, "changed": True}), ["keys: safe_mode"])
        self.assertEqual(sm.compare({}, True, {"settings": {"safe_mode": 1}, "changed": True}), ["keys: safe_mode"])
        self.assertEqual(sm.compare({"transport_shadow": 1, "safe_mode": 2}, True, {"settings": {"safe_mode": 2, "transport_shadow": 1}, "changed": True})[0][:6], "order:")
        self.assertEqual(sm.compare({}, True, {"settings": {}, "changed": False}), ["changed: app=True rust=False"])
        self.assertEqual(sm.compare({}, True, {"settings": {}, "changed": False}, compare_changed=False), [])

    def test_equal_value_of_a_different_type_is_a_difference(self):
        self.assertEqual(sm.compare({"safe_mode": 1}, False, {"settings": {"safe_mode": 1.0}, "changed": False}), ["keys: safe_mode"])

    def test_announce_lines(self):
        lines = []
        self.assertIn("enabled available=", sm.announce({}, lines.append))
        self.assertIn("off (settings_migrate_shadow=false)", sm.announce({"settings_migrate_shadow": False}, lines.append))
        self.assertTrue(all(x.startswith("[SETTINGS-MIGRATE-SHADOW]") for x in lines))


class WithFakeHelper(unittest.TestCase):
    def setUp(self):
        sm._seen.clear()
        sm._clean_noted = False
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lines = []

    def run_with(self, body, before=None, after=None, changed=True, compare_changed=True):
        helper = fake_helper(self.tmp.name, body)
        return sm.run_shadow(before or {}, after or {}, changed, compare_changed, self.lines.append, helper=helper)

    def test_agreement_is_clean_and_noted_once(self):
        body = "echo '{\"settings\": {}, \"changed\": true}'"
        self.assertEqual(self.run_with(body), "clean")
        self.assertEqual(self.run_with(body), "clean")
        self.assertEqual([x for x in self.lines if "first check clean" in x].__len__(), 1)

    def test_difference_is_logged_once_with_names_only(self):
        body = "echo '{\"settings\": {\"cdg_timing_offset_ms\": 0}, \"changed\": true}'"
        self.assertEqual(self.run_with(body, after={"cdg_timing_offset_ms": SECRET}), "mismatch")
        self.assertEqual(self.run_with(body, after={"cdg_timing_offset_ms": SECRET}), "mismatch")
        mism = [x for x in self.lines if "MISMATCH" in x]
        self.assertEqual(len(mism), 1)
        self.assertIn("keys: cdg_timing_offset_ms", mism[0])
        self.assertNotIn(SECRET, " ".join(self.lines))

    def test_decline_helper_failure_and_garbage_are_skipped_not_mismatches(self):
        self.assertEqual(self.run_with("echo '{\"decline\": \"decline: integer outside 64 bits\"}'"), "skipped")
        self.assertEqual(self.run_with("exit 3"), "skipped")
        self.assertEqual(self.run_with("echo not-json"), "skipped")
        self.assertFalse(any("MISMATCH" in x for x in self.lines))
        self.assertTrue(all(x.startswith("[SETTINGS-MIGRATE-SHADOW] skipped") for x in self.lines))

    def test_missing_helper_is_silent(self):
        before = os.environ.get("SINGWS_SETTINGSMIG_BIN")
        os.environ["SINGWS_SETTINGSMIG_BIN"] = "/nonexistent/helper"
        try:
            if sm.find_helper() is None:  # no bundled helper in this tree either
                self.assertEqual(sm.run_shadow({}, {}, True, True, self.lines.append), "skipped")
                self.assertEqual(self.lines, [])
                self.assertIsNone(sm.start({}, self.lines.append, before={}, after={}, changed=True))
        finally:
            if before is None:
                os.environ.pop("SINGWS_SETTINGSMIG_BIN", None)
            else:
                os.environ["SINGWS_SETTINGSMIG_BIN"] = before

    def test_start_returns_none_when_switched_off(self):
        os.environ["SINGWS_SETTINGSMIG_BIN"] = str(fake_helper(self.tmp.name, "echo '{}'"))
        try:
            self.assertIsNone(sm.start({"settings_migrate_shadow": False}, self.lines.append, before={}, after={}, changed=True))
        finally:
            os.environ.pop("SINGWS_SETTINGSMIG_BIN", None)


@unittest.skipUnless(os.path.exists(CLI), "needs the Rust helper: CARGO_TARGET_DIR=~/.cache/singws-rust-target-settingsmig cargo build --release -p singws-settingsmig (in rust/)")
class RealBlockAgainstRealHelper(unittest.TestCase):
    """The REAL migration block extracted from 0.2.18.1.py against the REAL helper, through the shadow's own comparison."""

    @classmethod
    def setUpClass(cls):
        import settings_migrate_parity as par
        cls.par = par
        cls.oracle = staticmethod(par.real_migration(load_main_module()))

    def setUp(self):
        sm._seen.clear()
        sm._clean_noted = False

    def check(self, case):
        after, changed = self.oracle(case)
        lines = []
        out = sm.run_shadow(sm.subset(case), sm.subset(after), changed, True, lines.append, helper=Path(CLI))
        if self.par.has_decline_value(case):  # a value the helper deliberately refuses to guess (an integer beyond 64 bits, non-ASCII digits)
            self.assertIn(out, ("clean", "skipped"), (case, lines))
            self.assertFalse(any("MISMATCH" in x for x in lines), (case, lines))
        else:
            self.assertEqual(out, "clean", (case, lines))

    def test_scripted_cases(self):
        for case in self.par.scripted():
            if not self.par.has_big_int(case):
                self.check(case)

    def test_random_cases(self):
        n = 0
        for seed in range(120):
            case = self.par.random_case(random.Random(seed))
            if self.par.has_big_int(case):
                continue
            self.check(case)
            n += 1
        self.assertGreater(n, 80)

    def test_an_already_migrated_profile_is_a_clean_no_op(self):
        self.check({m: True for m in self.par.MARKERS})


class Wiring(unittest.TestCase):
    def test_the_app_calls_the_shadow_between_the_migrations_and_the_defaults_fill(self):
        src = Path("0.2.18.1.py").read_text(encoding="utf-8")
        hook = src.index("settings_migrate_shadow.start(")
        self.assertLess(src.index("_migrate_shadow_before = dict(self.settings)"), src.index("for obsolete_key in"))
        self.assertLess(src.index("end_silence_threshold_2_5_migrated\"] = True"), hook)
        self.assertLess(hook, src.index("for k, v in DEFAULTS.items():", hook))
        self.assertIn('"settings_migrate_shadow": True,', src)
        self.assertIn('"settings_migrate_shadow", "songend_shadow", "karafun_script_shadow")', src)  # announced once per launch, in the shadows' announce loop

    def test_both_specs_bundle_the_module_and_the_optional_helper(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            t = Path(spec).read_text(encoding="utf-8")
            self.assertIn('"settings_migrate_shadow.py"', t)
            self.assertIn("'settings_migrate_shadow'", t)
            self.assertIn("singws_settingsmig", t)

    def test_both_build_scripts_build_the_helper(self):
        for f in ("build_singws_mac_arm64.sh", "build_singws_mac_intel.sh"):
            self.assertIn("build_settingsmig.sh", Path(f).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
