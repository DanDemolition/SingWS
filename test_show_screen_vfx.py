"""Focused coverage for the render-thread audience Show Screen VFX layer."""

import importlib.util
import os
import random
import sys
import unittest
from pathlib import Path

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
    def test_selected_transition_set_is_complete_and_shuffle_bagged(self):
        expected = {
            "curtain_reveal", "moving_spotlights", "neon_scan", "spinning_record",
            "confetti_drop", "audio_pulse", "camera_iris", "marquee_lightbulbs",
            "color_ribbon_wipe", "star_tunnel", "mosaic_tile_reveal",
            "waveform_sweep", "jukebox_flip", "laser_fan", "film_countdown",
            "polaroid_drop",
        }
        self.assertEqual(set(mod.SHOW_TRANSITION_EFFECTS), expected)
        self.assertEqual(set(mod.DEFAULTS["show_transition_effects"]), expected)
        bag = mod.ShowTransitionShuffleBag(mod.SHOW_TRANSITION_EFFECTS, random.Random(17))
        first = [bag.next() for _ in expected]
        second = [bag.next() for _ in expected]
        self.assertEqual(set(first), expected)
        self.assertEqual(set(second), expected)
        self.assertNotEqual(first[-1], second[0])

    def test_outro_and_next_singer_get_separate_transition_draws(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        singer_start = source[source.index("def _trigger_show_screen_singer_start_vfx"):]
        singer_start = singer_start[:singer_start.index("def _next_show_transition_style")]
        outro = source[source.index("def _mark_next_up_overlay_pending_after_completion"):]
        outro = outro[:outro.index("def _consume_next_up_overlay_for_transition")]

        self.assertIn("style = self._next_show_transition_style()", singer_start)
        self.assertIn("style = self._next_show_transition_style()", outro)
        self.assertNotIn('state["_pending_show_transition_style"] = style', outro)

    def test_all_selected_styles_live_in_the_existing_single_qml_surface(self):
        source = mod.QML_SHOW_SCREEN_VFX_SOURCE
        for effect in mod.SHOW_TRANSITION_EFFECTS:
            self.assertIn(f'root.transitionStyle === "{effect}"', source)
        self.assertEqual(source.count("id: styleLayer"), 1)
        self.assertNotIn("QQuickView", source)

    def test_host_quick_surface_is_natively_reasserted_for_each_effect(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        overlay = source[source.index("class RenderThreadShowScreenVfx"):]
        overlay = overlay[:overlay.index("class VideoAreaWidget")]
        self.assertIn("def _raise_native_surface", overlay)
        self.assertIn("self._container.raise_()", overlay)
        self.assertNotIn("addSubview_positioned_relativeTo_", overlay)
        self.assertNotIn("NSWindowAbove", overlay)
        self.assertIn("def _schedule_surface_reassert", overlay)
        self.assertEqual(overlay.count("self._schedule_surface_reassert()"), 3)

    def test_host_preview_uses_and_preserves_same_quick_transition_layer(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        preview_init = source[source.index("self.preview_window = PreviewWindow()"):]
        preview_init = preview_init[:preview_init.index("# Karaoke transport card")]
        self.assertIn("RenderThreadShowScreenVfx(self.preview_window.video_area)", preview_init)
        self.assertIn("set_show_vfx_overlay(preview_vfx)", preview_init)

        preview_class = source[source.index("class PreviewWindow"):]
        preview_class = preview_class[:preview_class.index("class ", 10)]
        self.assertIn('old_overlay = getattr(old, "_show_vfx_overlay", None)', preview_class)
        self.assertIn("new_area.set_show_vfx_overlay(old_overlay)", preview_class)

    def test_shuffled_style_is_not_hidden_behind_the_countdown_veil(self):
        source = mod.QML_SHOW_SCREEN_VFX_SOURCE
        style_layer = source[source.index("id: styleLayer"):]
        style_layer = style_layer[:style_layer.index('visible: root.active && root.effectsEnabled')]
        countdown = source[source.index("id: startCountdownStage"):]
        countdown = countdown[:countdown.index('text: "GET READY')]
        spotlights = source[source.index("id: spotlights"):]
        spotlights = spotlights[:spotlights.index("clip: true")]
        confetti = source[source.index("id: confettiBurst"):]
        confetti = confetti[:confetti.index("visible: root.effectsEnabled")]

        self.assertIn("z: 22", style_layer)
        self.assertIn("z: 22", spotlights)
        self.assertIn("z: 22", confetti)
        self.assertIn('color: "#52070612"', countdown)
        self.assertNotIn('color: "#f2070612"', countdown)

    def test_shared_explosion_only_runs_for_the_confetti_style(self):
        source = mod.QML_SHOW_SCREEN_VFX_SOURCE
        self.assertIn(
            'readonly property bool burstStyle: transitionStyle === "confetti_drop"',
            source,
        )
        singer_sequence = source[source.index("id: singerStartSequence"):]
        singer_sequence = singer_sequence[:singer_sequence.index("id: songOutroSequence")]
        outro_sequence = source[source.index("id: songOutroSequence"):]
        outro_sequence = outro_sequence[:outro_sequence.index("id: overlayExit")]
        for sequence in (singer_sequence, outro_sequence):
            self.assertIn("root.burstStyle ?", sequence)
            self.assertNotIn("target: shockwave; from: 0.0; to: 1.0", sequence)

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
        spotlights = source[source.index("id: spotlights"):source.index("id: styleLayer")]
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
        overlay.show_singer_start("Jordan", "Purple Rain", "Prince", "camera_iris")
        QTest.qWait(100)
        self.assertTrue(overlay._root.property("startCountdownActive"))
        self.assertEqual(int(overlay._root.property("startCountdownValue") or 0), 3)
        self.assertEqual(int(overlay._root.property("burstSerial") or 0), before)
        self.assertEqual(overlay._root.property("singerText"), "Jordan")
        self.assertEqual(overlay._root.property("songText"), "Purple Rain")
        self.assertEqual(overlay._root.property("transitionStyle"), "camera_iris")
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

    def test_immediate_hide_cancels_active_singer_animation(self):
        overlay = mod.RenderThreadShowScreenVfx()
        self.addCleanup(overlay.close)
        overlay.resize(960, 540)
        overlay.show()
        overlay.show_singer_start("FrankieRod", "Beyond the Realms of Death", "Judas Priest")
        QTest.qWait(900)
        self.assertTrue(overlay._root.property("active"))

        overlay.hide_transition(immediate=True)
        QTest.qWait(1500)

        self.assertFalse(overlay._root.property("active"))
        self.assertFalse(overlay._root.property("startCountdownActive"))

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

            def show_singer_start(self, singer, title, artist, style):
                calls.append(("start", singer, title, artist, style))

            def show_song_outro(self, singer, title, artist, style):
                calls.append(("outro", singer, title, artist, style))

            def hide_transition(self, immediate=False):
                calls.append(("hide", bool(immediate)))

        area = mod.VideoAreaWidget()
        self.addCleanup(area.close)
        area.set_show_vfx_overlay(FakeOverlay())
        area.show_next_up_overlay({"singer": "Maya", "title": "Halo"}, 7)
        area.show_singer_start_vfx("Maya", "Halo", "Beyoncé")
        area.show_song_outro_vfx("Maya", "Halo", "Beyoncé")
        area.hide_next_up_overlay(immediate=True, reason="test")
        self.assertEqual(calls[0][0], "next")
        self.assertEqual(calls[1], ("start", "Maya", "Halo", "Beyoncé", "moving_spotlights"))
        self.assertEqual(calls[2], ("outro", "Maya", "Halo", "Beyoncé", "moving_spotlights"))
        self.assertEqual(calls[3], ("hide", True))

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
            def hide_transition(self, immediate=False): pass

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

    def test_intel_fallback_keeps_explosions_and_uses_applause_copy(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('self._show_fallback_transition("APPLAUSE!", singer, title, artist)', source)
        self.assertIn('text: "APPLAUSE!"', source)
        self.assertIn("self._fallback_transition_frame_timer.setInterval(33)", source)
        self.assertIn("for i in range(30):", source)
        self.assertIn("painter.drawEllipse(QPointF(cx, cy), wave_radius, wave_radius)", source)
        self.assertIn('"NOW SINGING", singer, title, artist, duration_ms=3600', source)
        self.assertIn('"duration_ms": max(500, int(duration_ms))', source)
        self.assertIn("painter.setOpacity(1.0 - (exit_progress * exit_progress))", source)
        self.assertIn("burst_t = min(1.0, elapsed / 1.05)", source)
        self.assertIn('area.set_show_vfx_enabled(bool(settings.get("show_screen_vfx_enabled", True)))', source)
        runtime = source[source.index("def _apply_runtime_media_settings"):]
        runtime = runtime[:runtime.index("def ", 10)]
        self.assertNotIn("area.set_show_vfx_enabled(False)", runtime)


if __name__ == "__main__":
    unittest.main()
