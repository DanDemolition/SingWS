"""Show tab redesign, right column: slim empty pads / big loaded pads, and the way back to today's look."""
import importlib.util
import os
import tempfile
import unittest

os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_rc", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RightColumnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def strip(self):
        s = self.m.SoundboardStrip(pad_count=4, pad_height=58)
        s.pads[0]._clip_path = "/tmp/x.wav"          # pretend a clip is loaded (no audio is touched)
        s.pads[0]._label = "x"
        for pad in s.pads:
            pad._refresh_face()
        return s

    def test_redesign_makes_empty_pads_slim_and_keeps_loaded_pads_full(self):
        s = self.strip()
        s.set_redesign(True)
        self.assertEqual(s.pads[0].height(), 58)
        self.assertTrue(all(p.height() == 30 for p in s.pads[1:]))

    def test_switching_back_restores_every_pad_to_the_original_height_and_style(self):
        s = self.strip()
        before = [p.styleSheet() for p in s.pads]
        s.set_redesign(True)
        self.assertNotEqual([p.styleSheet() for p in s.pads], before)
        s.set_redesign(False)
        self.assertEqual([p.styleSheet() for p in s.pads], before)
        self.assertTrue(all(p.height() == 58 for p in s.pads))

    def test_loaded_pads_get_different_accent_colours_by_slot(self):
        css = [self.m.soundboard_pad_css_redesign(i, "loaded") for i in range(4)]
        self.assertEqual(len(set(css)), 4)

    def test_a_pad_still_plays_through_its_own_click_handler(self):
        s = self.strip()
        s.set_redesign(True)
        # the click wiring is untouched by the redesign (one behaviour)
        self.assertGreater(s.pads[0].receivers(s.pads[0].clicked), 0)


if __name__ == "__main__":
    unittest.main()
