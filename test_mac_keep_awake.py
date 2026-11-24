"""Tests for the macOS keep-awake assertion (idle App Nap / sleep prevention).

Added after a tester's logs showed the idle app "freezing" between songs on
macOS (App Nap + idle sleep). Verifies the assertion is fail-safe everywhere,
idempotent, releasable, and wired into the app defaults.
"""

import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import importlib.util
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")

import mac_keep_awake
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

    def test_rust_backend_is_opt_in_and_falls_back_to_pyobjc(self):
        class FakeLib:
            def __init__(self, begins):
                self.begins = begins
                self.calls = []

            def singws_macshim_keep_awake(self, op, reason):
                self.calls.append(op)
                return self.begins if op == 1 else 0

        # off by default: the Rust library is never asked
        self.assertFalse(mac_keep_awake._prefer_rust)
        lib = FakeLib(1)
        with mock.patch.object(mac_keep_awake, "_rust", return_value=lib):
            k = KeepAwake("t")
            k.begin()
            k.end()
            self.assertEqual(lib.calls, [])
            # opted in and Rust holds it: backend is rust, end releases through Rust
            mac_keep_awake.prefer_rust(True)
            try:
                k = KeepAwake("t")
                self.assertTrue(k.begin())
                self.assertEqual((k.backend(), k.active()), ("rust", True))
                self.assertTrue(k.begin())                     # idempotent: no second begin call
                self.assertEqual(lib.calls, [1])
                k.end()
                self.assertEqual((k.backend(), k.active()), ("none", False))
                self.assertEqual(lib.calls, [1, 0])
                k.end()                                        # not held: nothing more is sent
                self.assertEqual(lib.calls, [1, 0])
            finally:
                mac_keep_awake.prefer_rust(False)

    @unittest.skipUnless(sys.platform == "darwin", "macOS-only assertion")
    def test_a_rust_that_cannot_start_leaves_the_pyobjc_assertion_in_place(self):
        class Refusing:
            def singws_macshim_keep_awake(self, op, reason):
                return 0

        mac_keep_awake.prefer_rust(True)
        try:
            with mock.patch.object(mac_keep_awake, "_rust", return_value=Refusing()):
                k = KeepAwake("fallback")
                self.assertTrue(k.begin())
                self.assertEqual(k.backend(), "pyobjc")
                k.end()
            with mock.patch.object(mac_keep_awake, "_rust", return_value=None):          # no library at all
                k = KeepAwake("fallback")
                self.assertTrue(k.begin())
                self.assertEqual(k.backend(), "pyobjc")
                k.end()
        finally:
            mac_keep_awake.prefer_rust(False)

    def test_the_rust_switch_is_off_by_default_and_wired_in_the_app(self):
        src = open("0.2.18.1.py", encoding="utf-8").read()
        self.assertIn('"mac_shim_keep_awake": False', src)
        self.assertIn('mac_keep_awake.prefer_rust(bool(self.settings.get("mac_shim_keep_awake", False)))', src)

    def test_default_setting_is_on(self):
        spec = importlib.util.spec_from_file_location("singws_main_keepawake", "0.2.18.1.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.assertTrue(module.DEFAULTS.get("keep_mac_awake"))


if __name__ == "__main__":
    unittest.main()
