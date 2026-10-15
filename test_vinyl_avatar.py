"""The hero's spinning record (Show tab redesign): behaviour of the animation and that it paints, headless."""
import importlib.util
import os
import tempfile
import unittest

os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_vinyl", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class VinylAvatarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def make(self, **kw):
        a = self.m.CircularAvatar()
        a.setFixedSize(190, 190)
        a.set_name("Dana")
        a.set_vinyl(True, kw.get("glow", "soft"), kw.get("animate", True), kw.get("playing", lambda: False))
        return a

    def test_it_is_off_unless_asked(self):
        a = self.m.CircularAvatar()
        self.assertIsNone(a._vinyl)

    def test_the_record_only_turns_while_playing_and_coasts_to_a_stop(self):
        v = self.make()._vinyl
        for _ in range(60):                       # 6 s of playing at 10 Hz
            v.step(0.1, True)
        self.assertAlmostEqual(v.velocity, 200.0, delta=3.0)          # 33 1/3 rpm
        turned = v.disc_angle
        for _ in range(100):                      # then 10 s stopped
            v.step(0.1, False)
        self.assertEqual(v.velocity, 0.0)
        frozen = v.disc_angle
        v.step(0.5, False)
        self.assertEqual(v.disc_angle, frozen)    # still once stopped
        self.assertNotEqual(turned, 0.0)

    def test_the_ring_and_halo_keep_animating_when_nothing_plays(self):
        v = self.make()._vinyl
        r0, p0 = v.ring_angle, v.pulse_phase
        v.step(0.5, False)
        self.assertNotEqual(v.ring_angle, r0)
        self.assertNotEqual(v.pulse_phase, p0)
        self.assertEqual(v.disc_angle, 0.0)       # the record itself did not move

    def test_it_paints_and_the_name_circle_stays_put(self):
        a = self.make()
        a.show()
        self.app.processEvents()
        v = a._vinyl
        img0 = a.grab().toImage()
        self.assertFalse(img0.isNull())
        px, py = img0.width() // 2, img0.height() // 2 - int(20 * img0.devicePixelRatio())   # inside the name circle, above the lettering
        centre = img0.pixelColor(px, py)
        self.assertLess(centre.red() + centre.green() + centre.blue(), 200)      # the dark name circle
        for _ in range(8):
            v.step(0.1, True)
        a.update()
        self.app.processEvents()
        img1 = a.grab().toImage()
        self.assertNotEqual(img0, img1)                                         # the record turned
        c1 = img1.pixelColor(px, py)
        self.assertEqual(centre.name(), c1.name())                               # the middle did not rotate with it
        a.hide()

    def test_animation_off_means_no_timer_and_a_static_picture(self):
        a = self.make(animate=False)
        a.show()
        self.app.processEvents()
        self.assertFalse(a._vinyl.timer.isActive())
        a.hide()

    def test_the_timer_only_runs_while_visible(self):
        a = self.make()
        self.assertFalse(a._vinyl.timer.isActive())      # never shown yet
        a.show()
        self.app.processEvents()
        self.assertTrue(a._vinyl.timer.isActive())
        a.hide()
        self.app.processEvents()
        self.assertFalse(a._vinyl.timer.isActive())

    def test_turning_it_off_restores_the_plain_ring(self):
        a = self.make()
        a.set_vinyl(False)
        self.assertIsNone(a._vinyl)
        a.show()
        self.app.processEvents()
        self.assertFalse(a.grab().toImage().isNull())
        a.hide()

    def test_every_glow_level_builds(self):
        for level in ("off", "soft", "medium", "strong", "nonsense"):
            a = self.make(glow=level)
            a.show()
            self.app.processEvents()
            self.assertFalse(a.grab().toImage().isNull())
            a.hide()


if __name__ == "__main__":
    unittest.main()
