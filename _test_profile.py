"""Isolate the process from the operator's live profile before a test loads the app.

The app resolves its data root (logs, settings, queue, history, loudness cache, request-sync state) from SINGWS_HOME at
import time and otherwise uses ~/SingWS. Running a test module directly (``python -m unittest test_x``) bypasses
tools/run_tests.sh, which used to be the only thing that set it, so test runs wrote into the live show log and rewrote live
state files. Importing this module first gives the process a private, auto-deleted profile unless the caller (a test that
needs its own directory, or run_tests.sh) already chose one.
"""
import atexit
import os
import shutil
import tempfile

if not os.environ.get("SINGWS_HOME"):
    _HOME = tempfile.mkdtemp(prefix="singws-test-home-")
    os.environ["SINGWS_HOME"] = _HOME
    atexit.register(shutil.rmtree, _HOME, ignore_errors=True)


# A blocking modal dialog can never be answered in a headless test run: an unhandled exception in a Qt slot makes the app's own error handler call
# QMessageBox.critical() and the whole run then waits forever (seen 2026-10-26, 30 minutes of nothing). Tests that CHECK a dialog patch it themselves
# (mock.patch replaces these); everything else gets a non-blocking answer instead of a hang.
try:
    from PyQt6.QtWidgets import QMessageBox as _QMessageBox

    def _no_modal(*_args, **_kwargs):
        return _QMessageBox.StandardButton.Ok

    for _name in ("critical", "warning", "information", "question", "about", "aboutQt"):
        setattr(_QMessageBox, _name, staticmethod(_no_modal))
except Exception:  # PyQt is not needed by every test module
    pass
