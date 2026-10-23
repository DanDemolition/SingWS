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
