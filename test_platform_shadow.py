"""The platform shadow (platform_shadow.py): Rust's frontmost app / Accessibility trust / keep-awake against Python's, LOG ONLY.

* the setting is off by default and the app calls the hook after its own keep-awake set-up, wrapped;
* compare_state sees each kind of difference and tolerates focus moving during the probe run;
* run_shadow logs [PLATFORM-SHADOW] only for differences or a missing/broken probe, logs each difference once, and never raises;
* a small run against the real probe (needs `cargo build --release -p singws-mac`; skipped otherwise).
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import platform_shadow as ps

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8")
GOOD_RS = {"supported": True, "frontmost_app": "Finder", "ax_trusted": True, "keep_awake_ok": True, "keep_awake_released": True}
GOOD_PY = {"frontmost_app": "Finder", "ax_trusted": True, "keep_awake_active": True}


def fake_probe(directory, reply, exit_code=0):
    path = Path(directory) / "singws-mac-probe"
    path.write_text("#!/bin/sh\nprintf '%s' '" + json.dumps(reply) + f"'\nexit {exit_code}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Setting(unittest.TestCase):
    def test_default_is_on_and_the_app_wires_the_hook(self):
        self.assertRegex(SOURCE, r'"platform_shadow": True,')
        self.assertIn("platform_shadow.start(self.settings", SOURCE)
        self.assertEqual(ps.enabled({}), sys.platform == "darwin")
        self.assertFalse(ps.enabled({"platform_shadow": False}))
        self.assertEqual(ps.enabled({"platform_shadow": True}), sys.platform == "darwin")

    def test_off_starts_nothing(self):
        self.assertIsNone(ps.start({"platform_shadow": False}, None, print))

    def test_the_hook_comes_after_keep_awake_setup_and_is_wrapped(self):
        i = SOURCE.index("platform_shadow.start(self.settings")
        self.assertGreater(i, SOURCE.index("[KEEP-AWAKE] unavailable"))
        window = SOURCE[i - 80:i + 200]
        self.assertIn("try:", window)
        self.assertIn("except Exception:", window)


class Compare(unittest.TestCase):
    def diffs(self, py=None, rs=None, after=None, expected=True):
        py = dict(GOOD_PY, **(py or {}))
        return ps.compare_state(py, dict(py, **(after or {})), dict(GOOD_RS, **(rs or {})), expected)

    def test_agreement_is_silent(self):
        self.assertEqual(self.diffs(), [])

    def test_frontmost_difference(self):
        d = self.diffs(rs={"frontmost_app": "Safari"})
        self.assertEqual(len(d), 1)
        self.assertIn("frontmost_app", d[0])

    def test_focus_moving_during_the_run_is_tolerated(self):
        self.assertEqual(self.diffs(rs={"frontmost_app": "Safari"}, after={"frontmost_app": "Safari"}), [])
        self.assertEqual(self.diffs(rs={"frontmost_app": "Finder"}, after={"frontmost_app": "Safari"}), [])

    def test_ax_trust_difference_either_direction(self):
        self.assertTrue(any("ax_trusted" in x for x in self.diffs(rs={"ax_trusted": False})))
        self.assertTrue(any("ax_trusted" in x for x in self.diffs(py={"ax_trusted": False})))

    def test_ax_trust_changing_during_the_run_is_reported(self):
        self.assertTrue(any("ax_trusted" in x for x in self.diffs(after={"ax_trusted": False})))

    def test_keep_awake_follows_the_setting(self):
        self.assertTrue(any("app_active" in x for x in self.diffs(py={"keep_awake_active": False})))
        self.assertTrue(any("app_active" in x for x in self.diffs(expected=False)))
        self.assertEqual(self.diffs(py={"keep_awake_active": False}, expected=False), [])

    def test_rust_must_take_and_release(self):
        self.assertTrue(any("rust_ok" in x for x in self.diffs(rs={"keep_awake_ok": False})))
        self.assertTrue(any("rust_ok" in x for x in self.diffs(rs={"keep_awake_released": False})))


class RunShadow(unittest.TestCase):
    def setUp(self):
        ps._seen.clear()
        ps._clean_noted = False
        self.lines = []
        self.tmp = tempfile.mkdtemp()

    def run_with(self, reply, **kw):
        probe = fake_probe(self.tmp, reply, kw.pop("exit_code", 0))
        return ps.run_shadow({"keep_mac_awake": True}, None, self.lines.append, probe=probe, py_state=lambda ka: dict(GOOD_PY))

    def test_clean_logs_one_first_check_note_per_launch_and_then_nothing(self):
        self.assertEqual(self.run_with(GOOD_RS), "clean")
        self.assertEqual(len(self.lines), 1)
        self.assertTrue(self.lines[0].startswith("[PLATFORM-SHADOW] first check clean"), self.lines)
        self.assertEqual(self.run_with(GOOD_RS), "clean")
        self.assertEqual(len(self.lines), 1, "the note appears once")

    def test_announce_says_whether_it_is_on_and_whether_the_helper_is_there(self):
        lines = []
        with mock.patch.object(ps, "find_probe", return_value=None):
            self.assertEqual(ps.announce({"platform_shadow": True}, lines.append), "enabled available=0")
        with mock.patch.object(ps, "find_probe", return_value=Path("/x/probe")):
            self.assertEqual(ps.announce({"platform_shadow": True}, lines.append), "enabled available=1")
        self.assertEqual(ps.announce({"platform_shadow": False}, lines.append), "off (platform_shadow=false)")
        self.assertEqual(lines, ["[PLATFORM-SHADOW] enabled available=0", "[PLATFORM-SHADOW] enabled available=1", "[PLATFORM-SHADOW] off (platform_shadow=false)"])

    def test_mismatch_logs_once_per_session(self):
        reply = dict(GOOD_RS, ax_trusted=False)
        self.assertEqual(self.run_with(reply), "mismatch")
        self.assertEqual(len(self.lines), 1)
        self.assertTrue(self.lines[0].startswith("[PLATFORM-SHADOW] MISMATCH ax_trusted"))
        self.assertEqual(self.run_with(reply), "mismatch")
        self.assertEqual(len(self.lines), 1)

    def test_probe_failure_and_garbage_are_swallowed(self):
        self.assertEqual(self.run_with(GOOD_RS, exit_code=3), "skipped")
        self.assertIn("skipped", self.lines[-1])
        probe = Path(self.tmp) / "singws-mac-probe"
        probe.write_text("#!/bin/sh\necho not json\n")
        self.assertEqual(ps.run_shadow({}, None, self.lines.append, probe=probe, py_state=lambda ka: dict(GOOD_PY)), "skipped")

    def test_unsupported_platform_is_skipped(self):
        self.assertEqual(self.run_with(dict(GOOD_RS, supported=False)), "skipped")

    def test_missing_probe_is_skipped(self):
        old = os.environ.get("SINGWS_MAC_PROBE_BIN")
        os.environ["SINGWS_MAC_PROBE_BIN"] = "/nonexistent"
        try:
            orig = ps.find_probe
            ps.find_probe = lambda: None
            self.assertEqual(ps.run_shadow({}, None, self.lines.append), "skipped")
        finally:
            ps.find_probe = orig
            if old is None:
                os.environ.pop("SINGWS_MAC_PROBE_BIN", None)
        self.assertIn("not found", self.lines[-1])

    def test_a_raising_logger_cannot_reach_the_app(self):
        def boom(_):
            raise RuntimeError("log failed")
        probe = fake_probe(self.tmp, dict(GOOD_RS, ax_trusted=False))
        self.assertEqual(ps.run_shadow({}, None, boom, probe=probe, py_state=lambda ka: dict(GOOD_PY)), "mismatch")

    def test_python_state_survives_broken_readers(self):
        def bad():
            raise OSError("no")
        class KA:
            def active(self):
                raise OSError("no")
        with self.assertRaises(OSError):
            ps.python_state(KA(), frontmost=bad)  # the reader errors are the CALLER's to wrap (run_shadow does)
        st = ps.python_state(KA(), frontmost=lambda: "X", ax=lambda: True)
        self.assertEqual(st, {"frontmost_app": "X", "ax_trusted": True, "keep_awake_active": False})


@unittest.skipUnless(sys.platform == "darwin", "macOS only")
class RealProbe(unittest.TestCase):
    def setUp(self):
        self.probe = ps.find_probe()
        target = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-mac"))
        built = Path(target) / "release" / "singws-mac-probe"
        if built.is_file():
            self.probe = built
        if self.probe is None:
            self.skipTest("build it: cargo build --release -p singws-mac")

    def test_probe_json_is_complete_and_releases_keep_awake(self):
        reply = ps.run_probe(self.probe)
        self.assertEqual(set(reply), {"supported", "frontmost_app", "ax_trusted", "keep_awake_ok", "keep_awake_released"})
        self.assertTrue(reply["supported"])
        self.assertTrue(reply["keep_awake_ok"] and reply["keep_awake_released"])
        self.assertIsInstance(reply["frontmost_app"], str)


if __name__ == "__main__":
    unittest.main()
