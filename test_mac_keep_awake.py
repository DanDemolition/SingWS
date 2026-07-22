"""Tests for the macOS keep-awake assertion (idle App Nap / sleep prevention).

Added after a tester's logs showed the idle app "freezing" between songs on
macOS (App Nap + idle sleep). Verifies the assertion is fail-safe everywhere,
idempotent, releasable, and wired into the app defaults.
"""

import importlib.util
import os
import sys
import unittest

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")

from mac_keep_awake import KeepAwake


class KeepAwakeTests(unittest.TestCase):
    def test_lifecycle_is_safe_and_idempotent(self):
        k = KeepAwake("unit-test")
        self.assertFalse(k.active())
        first = k.begin()
        # On macOS the assertion should hold; elsewhere begin() returns False
        # and stays inactive — either way it must never raise.
        self.assertEqual(k.active(), first)
        # begin() again is idempotent (no second token, no raise).
        self.assertEqual(k.begin(), first)
        k.end()
        self.assertFalse(k.active())
        # end() when not held is safe.
        k.end()
        self.assertFalse(k.active())

    def test_set_enabled_toggles(self):
        k = KeepAwake()
        on = k.set_enabled(True)
        self.assertEqual(k.active(), on)
        self.assertFalse(k.set_enabled(False))
        self.assertFalse(k.active())

    @unittest.skipUnless(sys.platform == "darwin", "macOS-only assertion")
    def test_holds_real_assertion_on_macos(self):
        k = KeepAwake("macos-test")
        self.assertTrue(k.begin(), "NSProcessInfo activity should begin on macOS")
        self.assertTrue(k.active())
        k.end()
        self.assertFalse(k.active())

    def test_default_setting_is_on(self):
        spec = importlib.util.spec_from_file_location("singws_main_keepawake", "0.2.18.1.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.assertTrue(module.DEFAULTS.get("keep_mac_awake"))


if __name__ == "__main__":
    unittest.main()
