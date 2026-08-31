"""Crash reporting must preserve the original error without unsafe Qt dialogs."""
import subprocess
import sys
import unittest
from pathlib import Path


class ExceptionHandlerTests(unittest.TestCase):
    def check_handler(self, scenario):
        # Separate processes let us exercise no application, QCoreApplication,
        # and QApplication without changing another suite's Qt singleton.
        # Extract only the real handler so tests cannot send mail or load data.
        script = r'''
import ast
import contextlib
import io
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication, QMessageBox

scenario = sys.argv[1]
tree = ast.parse(Path("0.2.18.1.py").read_text())
handler = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "exception_handler")
reporter = mock.Mock(return_value=Path("/scratch/crash.log"))
sender = mock.Mock()
namespace = dict(sys=sys, SingWSLogger=SimpleNamespace(log_crash=reporter), maybe_auto_send_crash_logs=sender)
exec(compile(ast.Module(body=[handler], type_ignores=[]), "0.2.18.1.py", "exec"), namespace)
app = None
if scenario == "core":
    app = QCoreApplication([])
elif scenario not in {"no_app", "unhandled"}:
    app = QApplication([])
if scenario == "disk_failure":
    reporter.side_effect = OSError("disk full")
if scenario == "upload_failure":
    sender.side_effect = RuntimeError("report packaging failed")

if scenario == "unhandled":
    # No QMessageBox mock: this must exit with a Python error, not SIGABRT.
    sys.excepthook = namespace["exception_handler"]
    raise RuntimeError("original failure")

error = RuntimeError("original failure")
stderr = io.StringIO()
with mock.patch.object(QMessageBox, "critical") as dialog, contextlib.redirect_stderr(stderr):
    def report():
        namespace["exception_handler"](type(error), error, None)
    if scenario == "thread":
        worker = threading.Thread(target=report)
        worker.start()
        worker.join(timeout=3)
        assert not worker.is_alive()
    elif scenario == "closing":
        with mock.patch.object(QApplication, "closingDown", return_value=True):
            report()
    else:
        report()
    if scenario in {"no_app", "core", "thread", "closing"}:
        dialog.assert_not_called()
    else:
        dialog.assert_called_once()
        message = dialog.call_args.args[2]
        if scenario == "disk_failure":
            assert "could not be saved" in message, message
        else:
            assert "/scratch/crash.log" in message, message
assert "RuntimeError: original failure" in stderr.getvalue(), stderr.getvalue()
reporter.assert_called_once()
if scenario == "disk_failure":
    sender.assert_not_called()
else:
    sender.assert_called_once()
'''
        result = subprocess.run(
            [sys.executable, "-c", script, scenario],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=15,
        )
        if scenario == "unhandled":
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("RuntimeError: original failure", result.stderr)
            self.assertNotIn("QWidget: Must construct", result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_gui_application_never_opens_a_widget(self):
        self.check_handler("no_app")

    def test_core_only_helper_never_opens_a_widget(self):
        self.check_handler("core")

    def test_background_thread_never_opens_a_widget(self):
        self.check_handler("thread")

    def test_application_shutdown_never_opens_a_widget(self):
        self.check_handler("closing")

    def test_gui_error_still_shows_saved_report(self):
        self.check_handler("gui")

    def test_unwritable_report_does_not_hide_original_error(self):
        self.check_handler("disk_failure")

    def test_failed_upload_does_not_hide_original_error(self):
        self.check_handler("upload_failure")

    def test_unhandled_startup_error_exits_without_native_abort(self):
        self.check_handler("unhandled")


if __name__ == "__main__":
    unittest.main()
