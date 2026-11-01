"""The library-scan shadow (scan_shadow.py) and the Rust scan's parity with the app's own scan.

* the setting: "python" is the default, "shadow" runs, "rust" is accepted but never becomes the source of truth;
* only the scan's own settings are ever sent to the helper (no key, PIN or URL);
* compare_results sees every kind of difference and nothing else; run_shadow logs [SCAN-SHADOW] verdicts and survives a broken helper;
* the app's scan worker calls the shadow AFTER it has its own result, and the default keeps the shadow off;
* a small fixed-seed run of tools/scan_parity.py (needs `cargo build --release -p singws-scan -p singws-db -p singws-dbw`; skipped otherwise).
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path

import scan_shadow

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8")


def _result(tracks=None, **extra):
    base = {"ok": True, "quick_mode": False, "roots": ["/m"], "requested_roots": ["/m"], "unavailable_roots": [], "overlapping_roots_skipped": 0,
            "dir_sigs": {}, "tracks_added": 0, "zip_count": 0, "mp4_count": 0, "cdg_count": 0, "tracks_reused": 0, "tracks_changed": 0,
            "files_seen": 0, "added_count": 0, "removed_count": 0, "index_changed_paths": [], "index_removed_paths": [], "reindex_needed": True,
            "suspect_empty_roots": [], "summary_text": "x", "scan_time": 1.0, "tracks": tracks or []}
    base.update(extra)
    return base


def _fake_helper(directory, reply, exit_code=0):
    path = Path(directory) / "singws-scan-cli"
    path.write_text("#!/bin/sh\ncat >/dev/null\nprintf '%s' '" + json.dumps(reply) + f"'\nexit {exit_code}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Setting(unittest.TestCase):
    def test_default_is_python_and_the_app_ships_it_off(self):
        self.assertEqual(scan_shadow.effective_engine({}), "python")
        self.assertEqual(scan_shadow.effective_engine({"scan_engine": "garbage"}), "python")
        self.assertRegex(SOURCE, r'"scan_engine": "shadow",')

    def test_shadow_runs_and_rust_is_not_wired(self):
        self.assertEqual(scan_shadow.effective_engine({"scan_engine": "shadow"}), "shadow")
        self.assertEqual(scan_shadow.effective_engine({"scan_engine": "rust"}), "python")
        self.assertEqual(scan_shadow.normalize_engine("RUST"), "rust")

    def test_off_means_no_thread_and_no_helper_lookup(self):
        calls = []
        orig = scan_shadow.find_helper
        scan_shadow.find_helper = lambda: calls.append(1)
        try:
            self.assertIsNone(scan_shadow.start(_result(), ["/m"], False, [], {"scan_engine": "python"}, lambda m: None))
            self.assertIsNone(scan_shadow.start(_result(), ["/m"], False, [], {"scan_engine": "rust"}, lambda m: None))
        finally:
            scan_shadow.find_helper = orig
        self.assertEqual(calls, [])

    def test_rust_value_is_noted_once(self):
        scan_shadow._warned_rust = False
        lines = []
        scan_shadow.start(_result(), [], False, [], {"scan_engine": "rust"}, lines.append)
        scan_shadow.start(_result(), [], False, [], {"scan_engine": "rust"}, lines.append)
        self.assertEqual(len([l for l in lines if "not wired" in l]), 1)


class Request(unittest.TestCase):
    def test_only_the_scan_settings_are_sent(self):
        snapshot = {"api_key": "SECRET", "host_controls_pin": "1234", "base_url": "https://x", "filename_format": "artist-title",
                    "karaoke_library_locations": [{"path": "/m"}], "karaoke_scan_dir_sigs": {"/m": [1, 2]}}
        req = scan_shadow.build_request(["/m", ""], False, [{"path": "/m/a.cdg"}], snapshot)
        text = json.dumps(req)
        for secret in ("SECRET", "1234", "https://x"):
            self.assertNotIn(secret, text)
        self.assertEqual(req["roots"], ["/m"])
        self.assertEqual(req["settings"]["filename_format"], "artist-title")
        self.assertEqual(set(req["settings"]) - set(scan_shadow.SETTING_KEYS), set())


class Compare(unittest.TestCase):
    TRACK = {"path": "/m/a.cdg", "type": "cdg", "artist": "A", "title": "T", "disc_id": "KV", "duration": None, "display": "A - T - KV"}

    def test_identical_ignores_scan_time(self):
        a, b = _result([self.TRACK]), _result([dict(self.TRACK)], scan_time=99.0)
        self.assertEqual(scan_shadow.compare_results(a, b), [])

    def test_each_kind_of_difference_is_seen(self):
        other = dict(self.TRACK, path="/m/b.cdg")
        changed = dict(self.TRACK, title="Other")
        cases = [
            (_result([self.TRACK]), _result([]), "only_in_app"),
            (_result([]), _result([other]), "only_in_rust"),
            (_result([self.TRACK]), _result([changed]), "differing"),
            (_result([self.TRACK, other]), _result([other, self.TRACK]), "different order"),
            (_result([], files_seen=1), _result([], files_seen=2), "files_seen"),
            (_result([], roots=["/a"]), _result([], roots=["/b"]), "roots"),
        ]
        for py, rs, needle in cases:
            self.assertTrue(any(needle in d for d in scan_shadow.compare_results(py, rs)), needle)


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lines = []
        self.old_env = os.environ.get("SINGWS_SCAN_BIN")
        self.addCleanup(lambda: os.environ.pop("SINGWS_SCAN_BIN", None) if self.old_env is None else os.environ.__setitem__("SINGWS_SCAN_BIN", self.old_env))

    def run_with(self, reply, result=None, exit_code=0, request=None):
        os.environ["SINGWS_SCAN_BIN"] = str(_fake_helper(self.tmp.name, reply, exit_code))
        return scan_shadow.run_shadow(result or _result(), request or {"op": "scan", "quick_mode": False}, self.lines.append)

    def test_clean(self):
        self.assertEqual(self.run_with({"ok": True, "result": dict(_result(), index_rows=0)}), "clean")
        self.assertTrue(any("[SCAN-SHADOW] clean" in l for l in self.lines))

    def test_mismatch_is_logged_with_the_first_differences(self):
        self.assertEqual(self.run_with({"ok": True, "result": _result(files_seen=5)}), "mismatch")
        self.assertTrue(any("MISMATCH" in l for l in self.lines))
        self.assertTrue(any("files_seen" in l for l in self.lines))

    def test_every_failure_is_swallowed(self):
        self.assertEqual(self.run_with({"ok": False, "error": "decline: quick"}), "skipped")
        self.assertEqual(self.run_with({"ok": True, "result": {}}, exit_code=3), "skipped")
        os.environ["SINGWS_SCAN_BIN"] = "/nonexistent/helper"
        self.assertEqual(scan_shadow.run_shadow(_result(), {"quick_mode": False}, self.lines.append), "skipped")
        self.assertEqual(scan_shadow.run_shadow(_result(), {"quick_mode": True}, self.lines.append), "skipped")
        self.assertEqual(scan_shadow.run_shadow({"ok": False}, {"quick_mode": False}, self.lines.append), "skipped")
        self.assertEqual(scan_shadow.run_shadow(_result(), {"quick_mode": False}, lambda m: 1 / 0), "skipped")

    def test_start_runs_on_a_thread_when_enabled(self):
        os.environ["SINGWS_SCAN_BIN"] = str(_fake_helper(self.tmp.name, {"ok": True, "result": dict(_result(), index_rows=0)}))
        thread = scan_shadow.start(_result(), ["/m"], False, [], {"scan_engine": "shadow"}, self.lines.append)
        self.assertIsNotNone(thread)
        thread.join(30)
        self.assertTrue(any("clean" in l for l in self.lines), self.lines)
        self.assertTrue(thread.daemon)


class Wiring(unittest.TestCase):
    def test_the_worker_calls_the_shadow_after_its_own_result_exists(self):
        run = SOURCE[SOURCE.index("class LibraryScanWorker"):]
        run = run[:run.index("def _display_for_track")]
        self.assertIn("scan_shadow.start(result, self.roots, self.quick_mode, self.old_tracks, self.settings_snapshot, logging.info)", run)
        self.assertLess(run.index("_build_library_scan_result("), run.index("scan_shadow.start("))
        self.assertLess(run.index("scan_shadow.start("), run.index("self.finished.emit(result)"))

    def test_the_specs_bundle_the_module(self):
        for name in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = (ROOT / name).read_text()
            self.assertIn('"scan_shadow.py"', text)
            self.assertIn("'scan_shadow'", text)


from tools import scan_parity  # noqa: E402


@unittest.skipUnless(Path(scan_parity.CLI).exists() and Path(scan_parity.DBCLI).exists(), "needs singws-scan-cli and singws-db-cli")
class RustScanParity(unittest.TestCase):
    def test_rust_scan_equals_the_apps_scan(self):
        self.assertEqual(scan_parity.main(["--cases", "30", "--seed", "3", "--index-cases", "4"]), 0, scan_parity.failures[:3])
        self.assertGreater(scan_parity.counts.get("scan compared", 0), 0)
        self.assertGreater(scan_parity.counts.get("zip", 0), 0)


class Announce(unittest.TestCase):
    def test_announce_names_the_engine_and_the_helper(self):
        from unittest import mock
        lines = []
        with mock.patch.object(scan_shadow, "find_helper", return_value=None):
            self.assertEqual(scan_shadow.announce({"scan_engine": "shadow"}, lines.append), "engine=shadow available=0")
        with mock.patch.object(scan_shadow, "find_helper", return_value=Path("/x/h")):
            self.assertEqual(scan_shadow.announce({"scan_engine": "shadow"}, lines.append), "engine=shadow available=1")
        self.assertEqual(scan_shadow.announce({"scan_engine": "python"}, lines.append), "engine=python")
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(x.startswith("[SCAN-SHADOW] ") for x in lines))


class AppAnnounceHook(unittest.TestCase):
    def test_the_launch_announcement_uses_the_defaults_for_keys_not_yet_filled_in(self):
        src = (Path(__file__).resolve().parent / "0.2.18.1.py").read_text()
        i = src.index("for _mod_name in (")
        self.assertIn("{**DEFAULTS,", src[i:i + 700])


if __name__ == "__main__":
    unittest.main()
