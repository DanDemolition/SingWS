"""logpack_shadow: the log-only comparison of the app's last-show log package with the Rust singws-logpack helper. The Python package is always the one used."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import importlib.util
import json
import os
import stat
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import logpack_shadow as ls

CLI = os.environ.get("SINGWS_LOGPACK_CLI") or os.path.join(
    os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-logpack")), "release", "singws-logpack")
SECRET = "SUPER-SECRET-VALUE-123"
LAUNCH = '[{t}] [INFO] [LAUNCH] {{"build_id":"1.0.2.4"}}\n'


def load_main_module():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    spec = importlib.util.spec_from_file_location("singws_logpack_shadow_app", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["singws_logpack_shadow_app"] = module
    spec.loader.exec_module(module)
    return module


def fake_helper(tmp, reply, exit_code=0):
    path = Path(tmp) / "singws-logpack"
    path.write_text("#!/bin/sh\ncat >/dev/null\ncat <<'EOF'\n" + json.dumps(reply) + "\nEOF\nexit " + str(exit_code) + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


PY = {"text": "# header\nline\n", "window": "21:00:00 - 22:00:00 (60 min)", "used": ["a.log"], "extras": []}
RS = {"text": "# header\nline\n", "window": "21:00:00 - 22:00:00 (60 min)", "used": ["a.log"], "extras": [], "error": ""}
PAYLOAD = {"days": 7, "now": 1.0, "files": [], "extras": []}


class Compare(unittest.TestCase):
    def test_identical_is_clean(self):
        self.assertEqual(ls.compare(PY, RS), [])

    def test_text_difference_is_reported_without_content(self):
        d = ls.compare(dict(PY, text="a " + SECRET), dict(RS, text="a " + SECRET.lower()))
        self.assertEqual(len(d), 1)
        self.assertIn("first_diff_at=2", d[0])
        self.assertNotIn(SECRET, d[0])
        self.assertNotIn(SECRET.lower(), d[0])

    def test_missing_text_on_one_side(self):
        d = ls.compare(dict(PY, text=None), RS)
        self.assertTrue(d and d[0].startswith("text: app=none"))

    def test_window_files_and_extras(self):
        self.assertTrue(any(x.startswith("window") for x in ls.compare(PY, dict(RS, window="x"))))
        self.assertTrue(any(x.startswith("files") for x in ls.compare(PY, dict(RS, used=["b.log"]))))
        d = ls.compare(dict(PY, extras=[SECRET]), dict(RS, extras=["other"]))
        self.assertTrue(any(x.startswith("extras") for x in d))
        self.assertFalse(any(SECRET in x for x in d))

    def test_a_helper_error_is_a_difference(self):
        self.assertTrue(ls.compare(PY, dict(RS, error="no_text:a.log"))[0].startswith("error"))

    def test_none_equals_empty_window(self):
        self.assertEqual(ls.compare(dict(PY, window=None), dict(RS, window="")), [])


class RunShadow(unittest.TestCase):
    def setUp(self):
        ls._seen.clear()
        self.lines = []
        self.tmp = tempfile.mkdtemp()

    def test_clean_logs_nothing(self):
        self.assertEqual(ls.run_shadow(PAYLOAD, PY, self.lines.append, helper=fake_helper(self.tmp, RS)), "clean")
        self.assertEqual(self.lines, [])

    def test_mismatch_logs_once_per_session_and_never_the_text(self):
        reply = dict(RS, text=SECRET)
        h = fake_helper(self.tmp, reply)
        self.assertEqual(ls.run_shadow(PAYLOAD, dict(PY, text="x"), self.lines.append, helper=h), "mismatch")
        self.assertEqual(len(self.lines), 1)
        self.assertTrue(self.lines[0].startswith("[LOGPACK-SHADOW] MISMATCH text:"))
        self.assertNotIn(SECRET, self.lines[0])
        ls.run_shadow(PAYLOAD, dict(PY, text="x"), self.lines.append, helper=h)
        self.assertEqual(len(self.lines), 1)

    def test_helper_failure_and_garbage_are_swallowed_and_logged(self):
        self.assertEqual(ls.run_shadow(PAYLOAD, PY, self.lines.append, helper=fake_helper(self.tmp, RS, 3)), "skipped")
        self.assertIn("skipped", self.lines[-1])
        bad = Path(self.tmp) / "singws-logpack"
        bad.write_text("#!/bin/sh\ncat >/dev/null\necho not json\n")
        self.assertEqual(ls.run_shadow(PAYLOAD, PY, self.lines.append, helper=bad), "skipped")

    def test_missing_helper_is_skipped_quietly(self):
        with mock.patch.object(ls, "find_helper", return_value=None):
            self.assertEqual(ls.run_shadow(PAYLOAD, PY, self.lines.append), "skipped")
        self.assertEqual(self.lines, [])

    def test_a_raising_logger_cannot_reach_the_app(self):
        def boom(_):
            raise RuntimeError("log failed")
        self.assertEqual(ls.run_shadow(PAYLOAD, dict(PY, text="x"), boom, helper=fake_helper(self.tmp, RS)), "mismatch")

    def test_the_payload_reaches_the_helper_on_stdin(self):
        out = Path(self.tmp) / "stdin.json"
        h = Path(self.tmp) / "singws-logpack"
        h.write_text(f"#!/bin/sh\ncat > '{out}'\ncat <<'EOF'\n{json.dumps(RS)}\nEOF\n")
        h.chmod(0o755)
        ls.run_shadow({"days": 7, "now": 5.0, "files": [], "extras": []}, PY, self.lines.append, helper=h)
        self.assertEqual(json.loads(out.read_text())["now"], 5.0)


class Start(unittest.TestCase):
    def test_off_and_default(self):
        self.assertTrue(ls.enabled({}))
        self.assertTrue(ls.enabled(None))
        self.assertFalse(ls.enabled({"logpack_shadow": False}))
        self.assertIsNone(ls.start({"logpack_shadow": False}, print, logs_dir=".", days=7, now=1.0, texts={}, extra_texts=[], py={}))

    def test_no_helper_means_no_thread(self):
        with mock.patch.object(ls, "find_helper", return_value=None):
            self.assertIsNone(ls.start({}, print, logs_dir=".", days=7, now=1.0, texts={}, extra_texts=[], py={}))

    def test_bad_logs_dir_is_swallowed(self):
        with mock.patch.object(ls, "find_helper", return_value=Path("/bin/cat")):
            self.assertIsNone(ls.start({}, print, logs_dir="/nonexistent-dir-xyz", days=7, now=1.0, texts={}, extra_texts=[], py={}))

    def test_snapshot_lists_regular_files_with_the_text_the_app_read(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.log").write_text("x")
            (Path(d) / "sub").mkdir()
            seen = {}
            with mock.patch.object(ls, "find_helper", return_value=Path("/bin/cat")), \
                    mock.patch.object(ls.threading, "Thread", side_effect=lambda **kw: seen.update(kw) or mock.Mock()):
                ls.start({}, print, logs_dir=d, days=7, now=2.5, texts={"a.log": "x"}, extra_texts=["crash"], py={"text": None})
            payload = seen["args"][0]
            self.assertEqual([(f["name"], f["text"]) for f in payload["files"]], [("a.log", "x")])
            self.assertEqual(payload["extras"], [{"name": "extra", "text": "crash"}])
            self.assertEqual(payload["now"], 2.5)


class InTheApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_main_module()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name) / "logs"
        self.logs.mkdir()
        lines = [LAUNCH.format(t="21:00:00")] + [f"[{21 + i // 60:02d}:{i % 60:02d}:30] [INFO] minute {i} token={SECRET}\n" for i in range(1, 50)]
        (self.logs / "singws_2026-10-29.log").write_text("".join(lines))
        self._old = self.m.LOGS_DIR
        self.m.LOGS_DIR = self.logs

    def tearDown(self):
        self.m.LOGS_DIR = self._old
        self.tmp.cleanup()

    def test_the_setting_defaults_on(self):
        self.assertIs(self.m.DEFAULTS["logpack_shadow"], True)

    def test_the_package_is_unchanged_and_the_shadow_gets_what_the_app_used(self):
        calls = []
        with mock.patch.object(ls, "start", side_effect=lambda *a, **k: calls.append((a, k))):
            built = self.m._build_last_show_package(7)
        self.assertIsNone(built["error"] or None)
        with zipfile.ZipFile(built["package"]) as zf:
            text = zf.read(next(n for n in zf.namelist() if n.startswith("singws_last_show_"))).decode()
        self.assertNotIn(SECRET, text)
        (args, kw), = calls
        self.assertEqual(kw["py"]["text"], text)
        self.assertEqual(kw["py"]["used"], ["singws_2026-10-29.log"])
        self.assertIn("singws_2026-10-29.log", kw["texts"])
        self.assertEqual(kw["days"], 7)
        self.assertAlmostEqual(kw["now"], time.time(), delta=60)

    def test_quoted_secrets_are_redacted(self):
        f = self.m._sanitize_log_text
        self.assertEqual(f("password = 'hunter 2' next"), "password = '***' next")
        self.assertEqual(f('{"api_key": "abc", "u": 1}'), '{"api_key": "***", "u": 1}')
        self.assertEqual(f("secret='open\nnext"), "secret='***\nnext")
        self.assertEqual(f('"token": 123}'), '"token": ***')
        self.assertEqual(f("user=bob pin=1234"), "user=bob pin=***")

    def test_a_failing_shadow_cannot_change_or_break_the_package(self):
        with mock.patch.object(ls, "start", side_effect=RuntimeError("boom")):
            built = self.m._build_last_show_package(7)
        self.assertTrue(built["package"] and Path(built["package"]).exists())
        self.assertEqual(built["error"], "")

    def test_nothing_is_attempted_when_the_package_fails(self):
        (self.logs / "singws_2026-10-29.log").unlink()
        with mock.patch.object(ls, "start") as start:
            built = self.m._build_last_show_package(7)
        self.assertTrue(built["error"])
        start.assert_not_called()

    @unittest.skipUnless(os.access(CLI, os.X_OK), "singws-logpack is not built")
    def test_the_real_helper_agrees_with_the_real_package(self):
        crash = Path(self.tmp.name) / "crash.log"
        crash.write_text(f"Traceback\npassword: {SECRET}\n")
        captured = {}
        with mock.patch.object(ls, "start", side_effect=lambda *a, **k: captured.update(k)):
            self.m._build_last_show_package(7, crash_log=crash)
        files = [{"name": n, "mtime": t, "text": captured["texts"].get(n)} for n, t in ls.list_logs(self.logs)]
        payload = {"days": 7, "now": captured["now"], "files": files, "extras": [{"name": "extra", "text": t} for t in captured["extra_texts"]]}
        lines = []
        ls._clean_noted = False
        self.assertEqual(ls.run_shadow(payload, captured["py"], lines.append, helper=Path(CLI)), "clean", lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("[LOGPACK-SHADOW] first check clean"), lines)


class Announce(unittest.TestCase):
    def test_announce_says_whether_it_is_on_and_whether_the_helper_is_there(self):
        from unittest import mock
        lines = []
        with mock.patch.object(ls, "find_helper", return_value=None):
            self.assertEqual(ls.announce({"logpack_shadow": True}, lines.append), "enabled available=0")
        with mock.patch.object(ls, "find_helper", return_value=Path("/x/h")):
            self.assertEqual(ls.announce({"logpack_shadow": True}, lines.append), "enabled available=1")
        self.assertEqual(ls.announce({"logpack_shadow": False}, lines.append), "off (logpack_shadow=false)")
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(x.startswith("[LOGPACK-SHADOW] ") for x in lines))


if __name__ == "__main__":
    unittest.main()

