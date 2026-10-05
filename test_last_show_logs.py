"""Bug reports: every build sends the log of the LAST SHOW (one run of the app, even across midnight) to the developer through the
SingWS server. The destination is fixed on the server and no mail credentials exist in the app."""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


def load_main_module():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    spec = importlib.util.spec_from_file_location("singws_last_show_logs", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["singws_last_show_logs"] = module
    spec.loader.exec_module(module)
    return module


LAUNCH = '[{t}] [INFO] [LAUNCH] {{"build_id":"1.0.0.8","executable":"/Applications/SingWS.app/Contents/MacOS/SingWS"}}\n'


def run(start, minutes, label):
    """A run of the app: a launch line, a line every minute, and a closing line."""
    h, m = divmod(start, 60)
    lines = [LAUNCH.format(t=f"{h % 24:02d}:{m:02d}:00")]
    for i in range(1, minutes + 1):
        h, m = divmod(start + i, 60)
        lines.append(f"[{h % 24:02d}:{m:02d}:00] [INFO] {label} minute {i}\n")
    return lines


class LastShowSliceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_main_module()

    def text(self, lines, picked):
        first, end, _ = picked
        return "".join(lines[first:end])

    def test_the_last_long_run_is_the_show_not_the_short_reopen_after_it(self):
        lines = run(21 * 60 + 34, 250, "SHOW") + run(25 * 60 + 41, 1, "REOPEN")      # a 4 h show, then a 1 min relaunch to export logs
        picked = self.m._last_show_slice(lines)
        body = self.text(lines, picked)
        self.assertIn("SHOW minute 250", body)
        self.assertNotIn("REOPEN", body)
        self.assertGreaterEqual(picked[2], 240 * 60)                 # crossed midnight and still counted as one run

    def test_earlier_shows_are_not_included(self):
        lines = run(20 * 60, 120, "NIGHT-ONE") + run(30 * 60, 90, "NIGHT-TWO")
        body = self.text(lines, self.m._last_show_slice(lines))
        self.assertIn("NIGHT-TWO", body)
        self.assertNotIn("NIGHT-ONE", body)

    def test_when_every_run_is_short_the_latest_run_is_used(self):
        lines = run(600, 3, "A") + run(700, 2, "B")
        body = self.text(lines, self.m._last_show_slice(lines))
        self.assertIn("B minute 2", body)
        self.assertNotIn("A minute", body)

    def test_continuation_lines_stay_with_their_run(self):
        lines = run(600, 30, "SHOW")
        lines.insert(10, "    Traceback (most recent call last):\n")
        body = self.text(lines, self.m._last_show_slice(lines))
        self.assertIn("Traceback", body)

    def test_a_log_with_no_launch_line_is_used_whole(self):
        lines = [f"[10:{i:02d}:00] [INFO] plain {i}\n" for i in range(30)]
        self.assertEqual(self.m._last_show_slice(lines)[:2], (0, 30))

    def test_an_empty_log_is_handled(self):
        self.assertEqual(self.m._last_show_slice([]), (0, 0, 0.0))


class LastShowPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_main_module()

    def test_the_package_is_one_sanitized_log_of_the_last_show_across_the_midnight_files(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            older_show = logs / "singws_2026-10-03.log.2026-10-03"
            older_show.write_text("".join(run(21 * 60, 100, "OLDSHOW")), encoding="utf-8")
            before_midnight = logs / "singws_2026-10-04.log.2026-10-04"
            before_midnight.write_text("".join(run(21 * 60 + 34, 146, "SHOW") ).replace("minute 3\n", "minute 3 api_key=topsecret\n"), encoding="utf-8")
            after_midnight = logs / "singws_2026-10-04.log"      # sorts BEFORE the rotated file by name, but is newer
            after_midnight.write_text("[00:00:01] [INFO] SHOW after midnight\n[01:40:52] [INFO] SHOW end\n", encoding="utf-8")
            now = time.time()
            for path, age in ((older_show, 3 * 86400), (before_midnight, 7200), (after_midnight, 60)):
                os.utime(path, (now - age, now - age))
            (logs / "settings.json").write_text("private state", encoding="utf-8")
            with mock.patch.object(self.m, "LOGS_DIR", logs), mock.patch.object(self.m, "flush_log_queue"):
                package, files, error = self.m.prepare_log_email_package()
            self.assertEqual(error, "")
            with zipfile.ZipFile(package) as zf:
                self.assertEqual(len(zf.namelist()), 1)
                body = zf.read(zf.namelist()[0]).decode()
            self.assertIn("SHOW minute 146", body)
            self.assertLess(body.index("SHOW minute 146"), body.index("SHOW after midnight"))     # chronological across the rotation
            self.assertIn("SHOW end", body)
            self.assertNotIn("OLDSHOW", body)
            self.assertNotIn("private state", body)
            self.assertNotIn("topsecret", body)
            self.assertIn("api_key=***", body)
            self.assertTrue(body.startswith("# SingWS last show log:"))

    def test_no_log_files_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(self.m, "LOGS_DIR", Path(td)), mock.patch.object(self.m, "flush_log_queue"):
            package, files, error = self.m.prepare_log_email_package()
        self.assertIsNone(package)
        self.assertIn("No SingWS log files", error)


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body


class SendToDeveloperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_main_module()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pkg = Path(self.tmp.name) / "bundle.zip"
        self.pkg.write_bytes(b"PK\x03\x04 test")
        self.settings = {"base_url": "https://wskar.com", "user": "wsk", "api_key": "KEY123"}

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, response=None, settings=None, **kw):
        with mock.patch.object(self.m.requests, "post", return_value=response or FakeResponse(200, {"ok": True, "stored": True, "emailed": True})) as post:
            result = self.m.send_log_package_to_developer(self.settings if settings is None else settings, self.pkg, **kw)
        return result, post

    def test_the_logs_go_to_the_servers_support_endpoint_with_the_venue_key(self):
        (ok, msg), post = self.send(reason="crash", window="21:34 - 01:40 (246 min)")
        self.assertTrue(ok)
        url = post.call_args.args[0]
        self.assertEqual(url, "https://wskar.com/api/v1/support_logs.php")
        self.assertEqual(post.call_args.kwargs["headers"]["X-API-Key"], "KEY123")
        data = post.call_args.kwargs["data"]
        self.assertEqual((data["user"], data["reason"], data["session_window"]), ("wsk", "crash", "21:34 - 01:40 (246 min)"))
        self.assertEqual(data["app_version"], self.m.APP_VERSION)
        self.assertEqual(post.call_args.kwargs["files"]["logs"][0], "bundle.zip")

    def test_the_app_never_chooses_or_sends_a_recipient(self):
        (_ok, _msg), post = self.send()
        sent = json.dumps({k: v for k, v in post.call_args.kwargs["data"].items()}).lower()
        self.assertNotIn("@", sent)
        for field in ("to", "recipient", "email"):
            self.assertNotIn(field, post.call_args.kwargs["data"])

    def test_it_says_so_when_the_server_could_not_send_the_email(self):
        (ok, msg), _ = self.send(FakeResponse(200, {"ok": True, "stored": True, "emailed": False}))
        self.assertTrue(ok)
        self.assertIn("saved", msg.lower())
        self.assertIn("could not be sent", msg)

    def test_a_refused_key_and_a_rate_limit_have_plain_messages(self):
        (ok, msg), _ = self.send(FakeResponse(401, {"ok": False, "error": "unauthorized"}))
        self.assertFalse(ok)
        self.assertIn("API key", msg)
        (ok, msg), _ = self.send(FakeResponse(429, {"ok": False, "error": "rate_limited"}))
        self.assertFalse(ok)
        self.assertIn("Try again later", msg)

    def test_without_a_server_connection_nothing_is_sent(self):
        (ok, msg), post = self.send(settings={"base_url": "", "user": "", "api_key": ""})
        self.assertFalse(ok)
        self.assertIn("Settings > Network", msg)
        post.assert_not_called()

    def test_a_network_error_is_reported_not_raised(self):
        with mock.patch.object(self.m.requests, "post", side_effect=OSError("offline")):
            ok, msg = self.m.send_log_package_to_developer(self.settings, self.pkg)
        self.assertFalse(ok)
        self.assertIn("offline", msg)


class SettingsAndDefaultsTests(unittest.TestCase):
    def test_the_settings_screen_has_no_recipient_or_mail_login_fields(self):
        start = SOURCE.index('_adv_actions_card = _section_card(tab_advanced, "Logs & Crash Reporting"')
        card = SOURCE[start:SOURCE.index("# Push each tab's content to the top", start)]
        for gone in ("Send logs to", "SMTP host", "App password", "log_email_edit", "smtp_"):
            self.assertNotIn(gone, card)
        self.assertIn("Send Last Show's Logs", card)
        self.assertIn('self.settings.get("crash_auto_send_logs", True)', card)

    def test_the_crash_auto_send_is_on_unless_switched_off(self):
        auto = SOURCE[SOURCE.index("def maybe_auto_send_crash_logs"):SOURCE.index("# --- Logging Setup ---")]
        self.assertIn('settings.get("crash_auto_send_logs", True)', auto)

    def test_old_mail_settings_are_removed_from_the_saved_file(self):
        self.assertIn('_legacy_log_mail_keys = ("crash_log_email_to"', SOURCE)
        self.assertIn('"log_smtp_password"', SOURCE[SOURCE.index("_legacy_log_mail_keys"):SOURCE.index("_legacy_removed = ")])
        self.assertIn("if library_settings_changed or _legacy_removed:", SOURCE)

    def test_the_venue_api_key_field_is_always_masked(self):
        start = SOURCE.index('key_edit = QLineEdit(self.settings.get("api_key", ""))')
        field = SOURCE[start:start + 400]
        self.assertIn("key_edit.setEchoMode(QLineEdit.EchoMode.Password)", field)
        self.assertNotIn("Normal", field)                       # and there is no reveal toggle for it

    def test_no_smtp_sending_code_is_left_in_the_app(self):
        self.assertNotIn("smtplib", SOURCE)
        self.assertNotIn("send_log_package_via_smtp", SOURCE)


if __name__ == "__main__":
    unittest.main()
