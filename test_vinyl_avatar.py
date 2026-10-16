"""The hero's spinning record (Show tab redesign): behaviour of the animation and that it paints, headless."""
import importlib.util
import os
import tempfile
import unittest

os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _close_all_widgets():
    """These tests show real widgets; leave nothing behind (a leftover focus widget made unrelated tests think the host was busy elsewhere)."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in list(app.topLevelWidgets()):
        try:
            w.hide()
            w.close()
            w.deleteLater()
        except Exception:
            pass
    app.processEvents()


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_vinyl", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class VinylAvatarTests(unittest.TestCase):
    def tearDown(self):
        _close_all_widgets()

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

    def test_frame_rates_are_the_cheaper_ones_measured_on_the_dev_mac(self):
        # 60 fps cost about 24 points of one core during a song; 30 fps spinning and 12 fps for the idle ring halve that.
        self.assertEqual((self.m._VinylRenderer.SPIN_MS, self.m._VinylRenderer.IDLE_MS), (33, 83))
        a = self.make(playing=lambda: True)
        a.show()
        self.app.processEvents()
        self.assertEqual(a._vinyl.timer.interval(), 33)


class SliderGlowTests(unittest.TestCase):
    def tearDown(self):
        _close_all_widgets()

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def test_the_glow_follows_the_fill_and_is_absent_at_zero(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QSlider
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(0, 1000)
        sl.setFixedSize(400, 22)
        sl.setStyleSheet("QSlider { background: black; }")
        self.m.SliderGlow(sl)
        sl.show()
        self.app.processEvents()
        def glow_pixels(x):
            img = sl.grab().toImage()
            return sum(1 for y in range(img.height()) if img.pixelColor(x, y).red() > 15 and img.pixelColor(x, y).blue() > 15)
        sl.setValue(0)
        self.app.processEvents()
        self.assertEqual(glow_pixels(100), 0)
        sl.setValue(500)                                    # half way: glow at x=100 (inside the fill), none at x=300
        self.app.processEvents()
        self.assertGreater(glow_pixels(100), 0)
        self.assertEqual(glow_pixels(300), 0)


if __name__ == "__main__":
    unittest.main()
