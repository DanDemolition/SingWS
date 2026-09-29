"""LocationFixCollector: the decision logic for CoreLocation searches (no macOS objects needed).

Regression: on an Intel Mac (no GPS, Wi-Fi positioning) location detection failed no matter
how many times it was tried, because the first transient "location unknown" error ended the
search, the first (often cached or coarse) fix was accepted, and 12 seconds was too short.
"""
import importlib.util
import os
import sys
import unittest

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_locfix", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LocationFixCollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.C = load_main_module().LocationFixCollector

    def test_a_good_fix_stops_the_search(self):
        c = self.C()
        self.assertTrue(c.add_fix("here", 35.0, 1.0))
        self.assertEqual(c.result(), ("here", ""))

    def test_a_rough_fix_keeps_the_search_going_but_is_kept(self):
        c = self.C()
        self.assertFalse(c.add_fix("coarse", 1200.0, 1.0))      # not good enough to stop
        self.assertEqual(c.result(), ("coarse", ""))            # but accepted at the deadline

    def test_the_most_accurate_fresh_fix_wins(self):
        c = self.C()
        c.add_fix("coarse", 1500.0, 1.0)
        c.add_fix("better", 250.0, 2.0)
        c.add_fix("worse-again", 900.0, 3.0)
        self.assertEqual(c.result(), ("better", ""))

    def test_cached_old_fixes_are_ignored(self):
        c = self.C()
        self.assertFalse(c.add_fix("last-known", 20.0, 600.0))   # accurate but ten minutes old
        fix, err = c.result()
        self.assertIsNone(fix)
        self.assertIn("Timed out", err)

    def test_invalid_accuracy_is_ignored(self):
        c = self.C()
        self.assertFalse(c.add_fix("bogus", -1.0, 0.5))
        self.assertFalse(c.add_fix("junk", "n/a", 0.5))
        self.assertIsNone(c.result()[0])

    def test_a_useless_fix_is_reported_not_used(self):
        c = self.C()
        c.add_fix("country-level", 80000.0, 1.0)
        fix, err = c.result()
        self.assertIsNone(fix)
        self.assertIn("rough", err)

    def test_location_unknown_does_not_end_the_search(self):
        c = self.C()
        self.assertFalse(c.add_error(0, "kCLErrorDomain code=0 unknown"))     # kCLErrorLocationUnknown
        self.assertFalse(c.add_error(2, "kCLErrorDomain code=2 network"))      # kCLErrorNetwork
        self.assertTrue(c.add_fix("fix", 60.0, 1.0))                            # a fix still arrives later
        self.assertEqual(c.result(), ("fix", ""))

    def test_a_transient_error_with_no_fix_says_so_at_the_deadline(self):
        c = self.C()
        c.add_error(0, "kCLErrorDomain code=0 unknown")
        fix, err = c.result()
        self.assertIsNone(fix)
        self.assertIn("code=0", err)          # the friendly-message mapper turns this into "try again"

    def test_denied_is_final(self):
        c = self.C()
        self.assertTrue(c.add_error(1, "kCLErrorDomain code=1 denied"))
        fix, err = c.result()
        self.assertIsNone(fix)
        self.assertIn("denied", err)

    def test_a_fix_wins_over_a_later_fatal_error(self):
        c = self.C()
        c.add_fix("fix", 40.0, 1.0)
        c.add_error(1, "denied later")
        self.assertEqual(c.result(), ("fix", ""))


if __name__ == "__main__":
    unittest.main()
