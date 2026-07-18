"""Focused coverage for the render-thread audience Show Screen VFX layer."""

import importlib.util
import os
import sys
import unittest

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

_APP = None


def setUpModule():
    global _APP
    _APP = QApplication.instance() or QApplication(sys.argv)


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_show_screen_vfx", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_main_module()


class ShowScreenVfxTests(unittest.TestCase):
    def test_qml_has_next_up_countdown_and_stage_effects(self):
        source = mod.QML_SHOW_SCREEN_VFX_SOURCE
        self.assertIn("function showNextUp", source)
        self.assertIn("id: countdownTimer", source)
        self.assertIn("id: spotlights", source)
        self.assertIn("id: confettiBurst", source)
        self.assertIn("id: shockwave", source)
        self.assertIn("function showSingerStart", source)
        self.assertIn("id: singerCountdownTimer", source)
        self.assertIn("id: startCountdownStage", source)
        self.assertIn("id: countdownNumberHit", source)
        self.assertIn("function showSongOutro", source)
        self.assertIn("id: outroPanel", source)
        self.assertIn("id: songOutroSequence", source)
        self.assertIn("signal nextUpCountdownFinished()", source)
        self.assertIn("root.nextUpCountdownFinished()", source)
        spotlights = source[source.index("id: spotlights"):source.index("id: flash")]
        self.assertIn("model: 3", spotlights)
        self.assertIn("RotationAnimator", spotlights)
        self.assertNotIn("XAnimator", spotlights)

    def test_render_thread_layer_accepts_transition_state(self):
        overlay = mod.RenderThreadShowScreenVfx()
        self.addCleanup(overlay.close)
        overlay.resize(960, 540)
        overlay.show()
        overlay.show_next_up({
            "singer": "Maya & Chris",
            "title": "Faithfully",
            "artist": "Journey",
            "on_deck": "Jordan",
        }, 8)
        QTest.qWait(120)
        self.assertTrue(overlay._root.property("active"))
        self.assertEqual(overlay._root.property("singerText"), "Maya & Chris")
        self.assertEqual(overlay._root.property("songText"), "Faithfully")
        self.assertEqual(overlay._root.property("artistText"), "Journey")
        self.assertEqual(overlay._root.property("onDeckText"), "Jordan")
        self.assertEqual(overlay._root.property("countdownValue"), 8)

    def test_singer_start_runs_full_screen_three_two_one_then_explodes(self):
        overlay = mod.RenderThreadShowScreenVfx()
        self.addCleanup(overlay.close)
        overlay.resize(960, 540)
        overlay.show()
        before = int(overlay._root.property("burstSerial") or 0)
        overlay.show_singer_start("Jordan", "Purple Rain", "Prince")
        QTest.qWait(100)
        self.assertTrue(overlay._root.property("startCountdownActive"))
        self.assertEqual(int(overlay._root.property("startCountdownValue") or 0), 3)
        self.assertEqual(int(overlay._root.property("burstSerial") or 0), before)
        self.assertEqual(overlay._root.property("singerText"), "Jordan")
        self.assertEqual(overlay._root.property("songText"), "Purple Rain")
        QTest.qWait(3050)
        self.assertFalse(overlay._root.property("startCountdownActive"))
        self.assertEqual(int(overlay._root.property("startCountdownValue") or 0), 0)
        self.assertEqual(int(overlay._root.property("burstSerial") or 0), before + 1)

    def test_consecutive_singer_countdown_replaces_stale_outro_exit(self):
        overlay = mod.RenderThreadShowScreenVfx()
        self.addCleanup(overlay.close)
        overlay.resize(960, 540)
        overlay.show()
        overlay.show_song_outro("Alice", "First Song", "Artist A")
        QTest.qWait(80)

        # This is the production ordering: dismiss the old transition first,
        # then start the next singer's countdown.
        overlay.hide_transition()
        before = int(overlay._root.property("burstSerial") or 0)
        overlay.show_singer_start("Bob", "Second Song", "Artist B")
        QTest.qWait(1500)

        self.assertTrue(overlay._root.property("active"))
        self.assertTrue(overlay._root.property("startCountdownActive"))
        self.assertEqual(int(overlay._root.property("startCountdownValue") or 0), 1)
        self.assertEqual(overlay._root.property("singerText"), "Bob")
        self.assertEqual(int(overlay._root.property("burstSerial") or 0), before)

        QTest.qWait(750)
        self.assertFalse(overlay._root.property("startCountdownActive"))
        self.assertEqual(int(overlay._root.property("startCountdownValue") or 0), 0)
        self.assertEqual(int(overlay._root.property("burstSerial") or 0), before + 1)

    def test_song_outro_fires_double_burst_and_clears_quickly(self):
        overlay = mod.RenderThreadShowScreenVfx()
        self.addCleanup(overlay.close)
        overlay.resize(960, 540)
        overlay.show()
        before = int(overlay._root.property("burstSerial") or 0)
        overlay.show_song_outro("Jordan", "Purple Rain", "Prince")
        QTest.qWait(120)
        self.assertTrue(overlay._root.property("active"))
        self.assertEqual(overlay._root.property("singerText"), "Jordan")
        self.assertEqual(int(overlay._root.property("burstSerial") or 0), before + 1)
        QTest.qWait(2250)
        self.assertFalse(overlay._root.property("active"))
        self.assertEqual(int(overlay._root.property("burstSerial") or 0), before + 2)

    def test_next_up_countdown_clears_overlay_state_and_restores_qr_layer(self):
        area = mod.VideoAreaWidget()
        self.addCleanup(area.close)
        overlay = mod.RenderThreadShowScreenVfx(area)
        area.set_show_vfx_overlay(overlay)
        area.show_next_up_overlay({"singer": "Maya", "title": "Halo"}, 1)
        self.assertTrue(area._next_up_overlay_payload)
        QTest.qWait(1500)
        self.assertEqual(area._next_up_overlay_payload, {})
        self.assertFalse(overlay._root.property("active"))

    def test_video_area_forwards_events_to_overlay(self):
        calls = []

        class FakeOverlay:
            def setGeometry(self, _rect):
                pass

            def show(self):
                pass

            def raise_(self):
                pass

            def show_next_up(self, payload, duration):
                calls.append(("next", dict(payload), duration))

            def show_singer_start(self, singer, title, artist):
                calls.append(("start", singer, title, artist))

            def show_song_outro(self, singer, title, artist):
                calls.append(("outro", singer, title, artist))

            def hide_transition(self):
                calls.append(("hide",))

        area = mod.VideoAreaWidget()
        self.addCleanup(area.close)
        area.set_show_vfx_overlay(FakeOverlay())
        area.show_next_up_overlay({"singer": "Maya", "title": "Halo"}, 7)
        area.show_singer_start_vfx("Maya", "Halo", "Beyoncé")
        area.show_song_outro_vfx("Maya", "Halo", "Beyoncé")
        area.hide_next_up_overlay(immediate=True, reason="test")
        self.assertEqual(calls[0][0], "next")
        self.assertEqual(calls[1], ("start", "Maya", "Halo", "Beyoncé"))
        self.assertEqual(calls[2], ("outro", "Maya", "Halo", "Beyoncé"))
        self.assertEqual(calls[3], ("hide",))

    def test_disabled_show_vfx_retains_basic_countdown_but_suppresses_other_events(self):
        calls = []

        class FakeOverlay:
            def setGeometry(self, _rect): pass
            def show(self): pass
            def raise_(self): pass
            def set_enabled(self, enabled): calls.append(("enabled", bool(enabled)))
            def show_next_up(self, *_args): calls.append(("next",))
            def show_singer_start(self, *_args): calls.append(("start",))
            def show_song_outro(self, *_args): calls.append(("outro",))
            def hide_transition(self): pass

        area = mod.VideoAreaWidget()
        self.addCleanup(area.close)
        area.set_show_vfx_overlay(FakeOverlay())
        area.set_show_vfx_enabled(False)
        area.show_next_up_overlay({"singer": "Maya", "title": "Halo"}, 7)
        self.assertTrue(area.show_singer_start_vfx("Maya", "Halo", "Beyoncé"))
        self.assertFalse(area.show_song_outro_vfx("Maya", "Halo", "Beyoncé"))
        self.assertEqual(calls, [("enabled", False), ("start",)])
        self.assertFalse(area._show_vfx_enabled)

    def test_vfx_defaults_are_enabled_but_optional(self):
        self.assertTrue(mod.DEFAULTS["show_screen_vfx_enabled"])
        self.assertTrue(mod.DEFAULTS["rotation_vfx_enabled"])


if __name__ == "__main__":
    unittest.main()
