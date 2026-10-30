"""Stall stacks are written by faulthandler's signal handler running on the main thread, never by walking the main thread's frames from the watchdog thread
(that crashed the app on 2026-08-09). A real watchdog is installed in a child process, the GUI thread is blocked, and the dump file is read back."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8")

CHILD = textwrap.dedent('''
    import importlib.util, os, sys, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
    spec = importlib.util.spec_from_file_location("singws_stall_child", "0.2.18.1.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication(sys.argv)
    lines = []
    mod._diag = lambda text, *a, **k: lines.append(str(text))
    owner = QWidget()
    owner.settings = {"performance_debug_enabled": True, "stall_stack_capture": %(capture)s, "stall_system_snapshot": False, "stall_event_attribution": False}
    mod._install_main_thread_watchdog(owner, threshold_ms=150)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.6:
        app.processEvents(); time.sleep(0.01)
    def blocked_in_this_function_for_the_test():
        time.sleep(1.0)
    blocked_in_this_function_for_the_test()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.6:
        app.processEvents(); time.sleep(0.01)
    owner._mt_watch_stop = True
    print("\\n".join(lines))
''')


class SafeStallStacks(unittest.TestCase):
    def run_child(self, capture):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, SINGWS_HOME=home, QT_QPA_PLATFORM="offscreen", SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS="1")
            out = subprocess.run([sys.executable, "-c", CHILD % {"capture": "True" if capture else "False"}], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            dump = Path(home) / "logs" / "singws_stall_stacks.log"
            return out, (dump.read_text(encoding="utf-8", errors="replace") if dump.exists() else "")

    def test_a_blocked_gui_thread_gets_an_all_thread_stack_in_the_dump_file(self):
        out, dump = self.run_child(True)
        self.assertEqual(out.returncode, 0, out.stderr[-600:])
        self.assertIn("GUI thread blocked", out.stdout)
        self.assertRegex(out.stdout, r"all-thread stacks written to singws_stall_stacks\.log as dump #1")
        self.assertIn("stall dump #1", dump)
        self.assertIn("blocked_in_this_function_for_the_test", dump)
        self.assertRegex(dump, r"(Current thread|Thread) 0x")

    def test_with_the_setting_off_nothing_is_dumped_and_the_app_survives(self):
        out, dump = self.run_child(False)
        self.assertEqual(out.returncode, 0, out.stderr[-600:])
        self.assertIn("stack capture off", out.stdout)
        self.assertNotIn("stall dump #", dump)

    def test_the_old_cross_thread_frame_walk_is_gone(self):
        start = SOURCE.index("def _install_main_thread_watchdog")
        end = SOURCE.index("# Settings defaults", start)
        body = SOURCE[start:end]
        self.assertNotIn("_current_frames", body)
        self.assertNotIn("format_stack", body)
        self.assertIn("faulthandler.register", body)
        self.assertIn("pthread_kill(main_ident", body)

    def test_the_signal_is_only_sent_after_the_handler_is_registered(self):
        start = SOURCE.index("def _request_stall_dump")
        body = SOURCE[start:start + 1200]
        self.assertLess(body.index('if not stall_dump["ready"]'), body.index("pthread_kill"))

    def test_capture_defaults_on_and_a_one_time_migration_turns_it_on_for_old_profiles(self):
        self.assertIn('"stall_stack_capture": True,', SOURCE)
        migration = SOURCE[SOURCE.index('if not bool(self.settings.get("stall_stack_capture_safe_migrated", False))'):]
        self.assertIn('self.settings["stall_stack_capture"] = True', migration[:600])
        self.assertIn('self.settings["stall_stack_capture_safe_migrated"] = True', migration[:600])
        old = SOURCE[SOURCE.index('if not bool(self.settings.get("show_safe_stall_diagnostics_migrated", False))'):][:900]
        self.assertNotIn('self.settings["stall_stack_capture"] = False', old)


if __name__ == "__main__":
    unittest.main()
