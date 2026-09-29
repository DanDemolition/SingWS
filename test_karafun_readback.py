"""karafun_confirmed_readback: a key/tempo read back from KaraFun may only overwrite SingWS's display when
it repeats. Regression 2026-09-29: one early read (sliders not loaded, 0% = tempo 100) flipped the panel to
100 while KaraFun stayed at 95, and the wrong value became the base of the next press."""
import importlib.util
import os
import sys
import unittest

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_readback", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = staticmethod(load_main_module().karafun_confirmed_readback)

    def test_a_read_that_agrees_changes_nothing(self):
        self.assertIsNone(self.f((0, 95), (0, 95), None))

    def test_the_bad_early_read_is_ignored(self):
        # SingWS set 95%; a read caught the unloaded slider (100) once, then the real value came back.
        self.assertIsNone(self.f((0, 95), (0, 100), (0, 95)))

    def test_a_lone_disagreeing_read_is_not_believed(self):
        self.assertIsNone(self.f((0, 95), (0, 100), None))

    def test_a_change_made_inside_karafun_is_believed_when_it_repeats(self):
        self.assertEqual(self.f((0, 95), (2, 95), (2, 95)), (2, 95))
        self.assertEqual(self.f((0, 100), (0, 90), (0, 90)), (0, 90))

    def test_a_failed_first_read_leaves_the_display_alone(self):
        self.assertIsNone(self.f((0, 95), None, None))
        self.assertIsNone(self.f((0, 95), None, (0, 100)))

    def test_two_different_disagreeing_reads_are_not_believed(self):
        self.assertIsNone(self.f((0, 95), (0, 100), (0, 97)))

    def test_works_with_lists(self):
        self.assertEqual(self.f([0, 95], [1, 95], [1, 95]), (1, 95))

    def test_unknown_baseline_needs_a_repeat_too(self):
        self.assertIsNone(self.f(None, (0, 95), None))
        self.assertEqual(self.f(None, (0, 95), (0, 95)), (0, 95))


if __name__ == "__main__":
    unittest.main()
