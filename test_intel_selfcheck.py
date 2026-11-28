"""The Intel self-check kit (tools/intel_kit/intel_selfcheck.py): its verdict logic, and its settings and log readers over made-up files. The checks of a real installed app run
on the Mac itself (set SINGWS_INTEL_APP to an app to run them here too)."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before anything loads)
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PATH = Path(__file__).parent / "tools" / "intel_kit" / "intel_selfcheck.py"
spec = importlib.util.spec_from_file_location("intel_selfcheck", PATH)
sc = importlib.util.module_from_spec(spec)
sys.modules["intel_selfcheck"] = sc
spec.loader.exec_module(sc)

GOOD_LOG = """[21:00:00] [INFO] [LAUNCH] {"build_id":"1.0.4.5","executable":"x"}
[21:00:01] [INFO] [BG-ENGINE] Rust background engine ready platform=macOS
[21:00:01] [INFO] [KEEP-AWAKE] macOS App Nap / idle sleep prevention active for this session (held by rust)
[21:00:02] [INFO] [SCREEN] initial topology | screens=Built-in
[21:00:02] [INFO] [SCREEN] refresh rates: Built-in=60.0Hz
[21:00:02] [INFO] [STALL] main-thread watchdog armed threshold=1000ms (light)
[21:00:03] [INFO] [KARAFUN-WEB] engine=rust: the Rust driver runs the player
[21:00:03] [INFO] [QUEUE-STATE-SHADOW] enabled available=1 driver=dry_run
[21:00:03] [INFO] [SONGEND-SHADOW] enabled available=1
[21:00:03] [INFO] [PLATFORM-SHADOW] enabled available=1
[21:00:03] [INFO] [SETTINGS-SHADOW] enabled available=1
[21:30:00] [INFO] [KARAFUN-WEB] prewarm requested reason=queue_add
[21:30:20] [INFO] [KARAFUN-WEB] started after 9.5s title='X' duration=200s
[21:31:00] [INFO] [SHOW-VFX] effect ran 4.20s, 126 frames (30.0 fps) on screen='TV' 60Hz, 2 screens, cap 30 fps
[21:32:00] [INFO] [STALL] GUI thread recovered after ~1500ms
"""


def levels(report):
    return [line[1:5] for line in report.lines if line.startswith("[")]


class VerdictTests(unittest.TestCase):
    def test_the_verdict_is_all_good_only_without_warnings_or_failures(self):
        r = sc.Report()
        r.passed("a")
        self.assertIn("RESULT: ALL GOOD", r.text())
        r.warn("b")
        self.assertIn("NEEDS A LOOK", r.text())
        r.fail("c")
        self.assertIn("PROBLEMS FOUND", r.text())
        self.assertIn("pass=1 warn=1 fail=1", r.text())


class SettingsTests(unittest.TestCase):
    def home(self, settings):
        d = Path(tempfile.mkdtemp())
        if settings is not None:
            (d / "settings.json").write_text(json.dumps(settings))
        return d

    def test_a_migrated_profile_with_everything_on_is_green(self):
        s = {k: True for k in sc.EXPECTED_ON}
        s.update({"songend_gates_default_on_migrated": True, "karafun_web_engine": "rust", "shell_commands": "apply"})
        r = sc.Report()
        sc.check_settings(r, self.home(s))
        self.assertEqual(r.counts["FAIL"] + r.counts["WARN"], 0, r.lines)

    def test_a_switched_off_setting_and_a_missing_migration_are_warned_about(self):
        s = {k: True for k in sc.EXPECTED_ON}
        s["songend_drive_gates"] = False
        r = sc.Report()
        sc.check_settings(r, self.home(s))
        text = "\n".join(r.lines)
        self.assertIn("switched OFF in this profile: songend_drive_gates", text)
        self.assertIn("one-time 1.0.4.5 switch-on has not run", text)

    def test_no_settings_file_and_a_broken_one(self):
        r = sc.Report()
        sc.check_settings(r, self.home(None))
        self.assertEqual(r.counts["WARN"], 1)
        d = self.home(None)
        (d / "settings.json").write_text("{not json")
        r = sc.Report()
        sc.check_settings(r, d)
        self.assertEqual(r.counts["FAIL"], 1)

    def test_the_python_engine_is_a_warning_not_a_failure(self):
        s = {k: True for k in sc.EXPECTED_ON}
        s.update({"songend_gates_default_on_migrated": True, "karafun_web_engine": "python"})
        r = sc.Report()
        sc.check_settings(r, self.home(s))
        self.assertEqual(r.counts["FAIL"], 0)
        self.assertTrue(any("python" in line and line.startswith("[WARN]") for line in r.lines))


class LogTests(unittest.TestCase):
    def home(self, text):
        d = Path(tempfile.mkdtemp())
        (d / "logs").mkdir()
        (d / "logs" / "singws_2026-11-28.log").write_text(text)
        return d

    def test_a_good_launch_and_show_is_green_with_a_summary(self):
        r = sc.Report()
        sc.check_log(r, self.home(GOOD_LOG), "1.0.4.5")
        self.assertEqual(r.counts["FAIL"], 0, r.lines)
        self.assertEqual(r.counts["WARN"], 0, r.lines)
        text = "\n".join(r.lines)
        self.assertIn("1 KaraFun song(s) started", text)
        self.assertIn("pre-warm requests: 1", text)
        self.assertIn("median 30 fps", text)
        self.assertIn("freezes: 1", text)

    def test_the_wrong_build_a_traceback_and_a_refused_song_are_failures(self):
        bad = GOOD_LOG.replace("1.0.4.5", "1.0.4.4") + "Traceback (most recent call last):\n[22:00:00] [INFO] [KARAFUN-WEB] song machine declined: repr of non-ASCII or control text\n"
        r = sc.Report()
        sc.check_log(r, self.home(bad), "1.0.4.5")
        text = "\n".join(r.lines)
        self.assertEqual(r.counts["FAIL"], 3, text)
        self.assertIn("build 1.0.4.4 (expected 1.0.4.5)", text)

    def test_missing_launch_lines_are_warned_and_only_the_last_launch_counts(self):
        old = GOOD_LOG.replace("1.0.4.5", "1.0.4.4") + "Traceback (old launch)\n"
        text = old + '[22:30:00] [INFO] [LAUNCH] {"build_id":"1.0.4.5","executable":"x"}\n'
        r = sc.Report()
        sc.check_log(r, self.home(text), "1.0.4.5")
        self.assertEqual(r.counts["FAIL"], 0, r.lines)       # the old launch's traceback is not this launch's
        self.assertGreaterEqual(r.counts["WARN"], 8)           # the new launch has not written the usual lines yet

    def test_a_song_end_disagreement_and_a_stuck_player_are_warned(self):
        text = GOOD_LOG + "[22:00:00] [INFO] [SONGEND-DRIVE] decision=eos_step python={} rust={} selected=python owner=python-fallback disagreement=1\n" \
                          "[22:01:00] [INFO] [KARAFUN-WEB] page error: Chromium's page stopped; loading it again\n"
        r = sc.Report()
        sc.check_log(r, self.home(text), "1.0.4.5")
        out = "\n".join(r.lines)
        self.assertIn("1 song-end disagreement", out)
        self.assertIn("KaraFun's page stopped: 1 time", out)
        self.assertEqual(r.counts["FAIL"], 0)

    def test_no_log_is_a_warning(self):
        r = sc.Report()
        sc.check_log(r, Path(tempfile.mkdtemp()), "1.0.4.5")
        self.assertEqual((r.counts["WARN"], r.counts["FAIL"]), (1, 0))


class KitTests(unittest.TestCase):
    def test_the_wrapper_and_readme_are_in_the_kit_and_executable(self):
        kit = PATH.parent
        self.assertTrue(os.access(kit / "run_intel_checks.sh", os.X_OK))
        self.assertTrue((kit / "README.txt").is_file())
        self.assertIn("intel_selfcheck.py", (kit / "run_intel_checks.sh").read_text())

    def test_the_song_end_cases_match_the_rust_rules_the_app_ships(self):
        # every op the check asks of the bundled library is one the app wires (songend_drive.py / songend_drive_*), so a renamed op cannot go unnoticed
        ops = {c[0]["op"] for c in sc.SONGEND_CASES}
        src = (Path(__file__).parent / "rust/crates/singws-songend/src/lib.rs").read_text()
        for op in ops:
            self.assertIn(f'Some("{op}")', src, op)

    @unittest.skipUnless(os.environ.get("SINGWS_INTEL_APP"), "set SINGWS_INTEL_APP to an installed app to run the whole check here")
    def test_the_whole_check_runs_against_an_installed_app(self):
        code = sc.main(["--app", os.environ["SINGWS_INTEL_APP"], "--home", tempfile.mkdtemp(), "--no-report-file", "--no-pmset", "--expect-arch", os.environ.get("SINGWS_INTEL_ARCH", sc.native_arch())])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
