"""Focused coverage for the render-thread Singer Rotation presentation."""

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtTest import QTest

_APP = None


def setUpModule():
    global _APP
    _APP = QApplication.instance() or QApplication(sys.argv)


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_rotation_render_thread", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_main_module()


class RenderThreadRotationTests(unittest.TestCase):
    def test_now_singing_surface_has_live_motion(self):
        source = mod.QML_NOW_SINGING_SOURCE
        self.assertIn('text: "NOW SINGING  •  LIVE"', source)
        self.assertIn("ScaleAnimator { target: eqBar", source)
        self.assertIn("id: headerSheen", source)
        self.assertIn("id: singerChangeFlash", source)
        self.assertIn("id: singerShockwaves", source)
        self.assertIn("id: singerBurst", source)
        self.assertIn("onSingerBurstSerialChanged", source)
        self.assertIn("function shouldPulseCountdown()", source)
        self.assertIn("model: 12", source)
        self.assertNotIn("singerPopupAnimation.restart()", source)

        card = mod.RenderThreadNowSingingCard()
        self.addCleanup(card.close)
        card.resize(620, 154)
        card.show()
        card.set_state("Maya & Chris", "Jordan", "03:12")
        QTest.qWait(650)
        self.assertEqual(card._root.property("singerText"), "Maya & Chris")
        self.assertEqual(card._root.property("nextSingerText"), "Jordan")
        self.assertEqual(card._root.property("countdownText"), "03:12")

    def test_qml_uses_render_thread_vertical_animator(self):
        self.assertIn("YAnimator {", mod.QML_ROTATION_RAIL_SOURCE)
        self.assertIn("easing.type: Easing.Linear", mod.QML_ROTATION_RAIL_SOURCE)

    def test_scroll_has_smooth_conveyor_depth_effects(self):
        source = mod.QML_ROTATION_RAIL_SOURCE
        self.assertNotIn("id: conveyorDrift", source)
        self.assertNotIn("target: driftLayer", source)
        self.assertIn("id: scrollLightRibbons", source)
        self.assertIn("id: topScrollFeather", source)
        self.assertIn("id: bottomScrollFeather", source)
        self.assertIn("id: scrollEdgeGlint", source)
        self.assertIn("visible: root.effectsEnabled", source)

    def test_rotation_layout_has_no_burn_in_orbit(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertNotIn("rotation_burn_in_shift_enabled", source)
        self.assertNotIn("def _advance_burn_in_shift(self):", source)
        self.assertNotIn("_burn_in_shift_timer", source)

    def test_rotation_uses_raw_cdg_backdrop_without_show_composite(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('"rotation_cdg_backdrop_enabled": True', source)
        self.assertIn("class RotationCdgBackdrop(QWidget):", source)
        self.assertIn("self._cdg_backdrop_timer.start(125)", source)
        self.assertIn("central_layout.setCurrentWidget(safe_area)", source)
        self.assertIn('== "cdg"', source)
        self.assertIn('getattr(plugin, "grabFrame", None)', source)
        self.assertIn("KeepAspectRatioByExpanding", source)
        self.assertIn("painter.setOpacity(self._opacity)", source)
        backdrop = source[source.index("class RotationCdgBackdrop"):source.index("class RotationView")]
        self.assertNotIn("loadBackgroundVideo", backdrop)

    def test_vfx_stay_in_the_qt_quick_scene(self):
        source = mod.QML_ROTATION_RAIL_SOURCE
        self.assertIn("property bool effectsEnabled: true", source)
        self.assertNotIn("ScaleAnimator { target: numberBadge", source)
        self.assertNotIn("OpacityAnimator { target: leadGlow", source)
        self.assertIn("id: sheen", source)
        self.assertIn("model: 4", source)

    def test_rotation_vfx_can_be_disabled_without_disabling_scroll(self):
        rail = mod.RenderThreadRotationRail()
        self.addCleanup(rail.close)
        rail.set_effects_enabled(False)
        self.assertFalse(rail._root.property("effectsEnabled"))
        self.assertTrue(rail._root.property("running"))

        card = mod.RenderThreadNowSingingCard()
        self.addCleanup(card.close)
        card.resize(620, 154)
        card.show()
        card.set_effects_enabled(False)
        before = int(card._root.property("singerBurstSerial") or 0)
        card.set_state("Jordan", "Maya", "02:10")
        QTest.qWait(80)
        self.assertEqual(card._root.property("singerText"), "Jordan")
        self.assertEqual(int(card._root.property("singerBurstSerial") or 0), before)

    def test_structured_items_are_sent_to_qml(self):
        rail = mod.RenderThreadRotationRail()
        self.addCleanup(rail.close)
        items = [
            {"number": "1", "singer": "Alice", "song": "Journey • Faithfully"},
            {"number": "2", "singer": "Bob", "song": "Queen • Somebody to Love"},
        ]
        rail.set_items(items, force=True)
        self.assertEqual(json.loads(rail._root.property("itemsJson")), items)

    def test_growing_overflowing_queue_updates_after_short_transition(self):
        rail = mod.RenderThreadRotationRail()
        self.addCleanup(rail.close)
        rail.resize(500, 240)
        rail.show()
        initial = [
            {"number": str(i + 1), "singer": f"Singer {i}", "song": f"Artist • Song {i}"}
            for i in range(8)
        ]
        grown = initial + [
            {"number": str(i + 1), "singer": f"Singer {i}", "song": f"Artist • Song {i}"}
            for i in range(8, 15)
        ]
        rail.set_items(initial, force=True)
        QTest.qWait(80)
        self.assertTrue(rail._root.property("overflow"))
        rail.set_items(grown)
        QTest.qWait(650)
        self.assertEqual(len(json.loads(rail._root.property("itemsJson"))), 15)
        self.assertEqual(rail._root.property("updatePopupText"), "+7 SINGERS JOINED")

    def test_rotation_view_populates_cards_and_count(self):
        view = mod.RotationView()
        self.addCleanup(view.close)
        queue = [{
            "name": "Alice",
            "songs": [{"song_info": ("/music/faithfully.mp3",), "skipped": False}],
        }]
        tracks = [{"path": "/music/faithfully.mp3", "display": "Journey - Faithfully"}]
        view.update_rotation(queue, tracks, "Alice")
        if view.rotation_rail is not None:
            cards = json.loads(view.rotation_rail._root.property("itemsJson"))
            # The rail gained artist/duet/combined when the rotation screen
            # became a table (# | SINGER | SONG | ARTIST). Assert the fields
            # the delegate actually binds rather than the whole dict, so
            # adding another column does not break this again.
            self.assertEqual(len(cards), 1)
            card = cards[0]
            self.assertEqual(card["number"], "1")
            self.assertEqual(card["singer"], "Alice")
            self.assertEqual(card["duet"], False)
            # This fixture's entry cannot be split, so song keeps the combined
            # display string and artist stays empty.
            self.assertEqual(card["song"], "Journey • Faithfully")
            self.assertEqual(card["artist"], "")
            self.assertEqual(card["combined"], "Journey • Faithfully")
        else:
            self.assertEqual(view.list_widget.count(), 1)
            self.assertEqual(
                view.list_widget.item(0).text(),
                "1. Alice  •  Journey • Faithfully",
            )
        self.assertEqual(view.queue_count_label.text(), "1 SINGER")

    def test_rotation_view_close_hides_and_retains_native_surfaces(self):
        owner = QMainWindow()
        owner.settings = {}
        owner.save_settings = lambda: None
        owner._app_closing = False
        owner._rotation_view_user_opened = True
        view = mod.RotationView(owner)
        owner.rotation_view = view
        view.show()
        QTest.qWait(40)

        now_surface = view.now_singing_surface
        rail_surface = view.rotation_rail
        view.close()
        QTest.qWait(40)

        self.assertFalse(view.isVisible())
        self.assertIs(owner.rotation_view, view)
        self.assertIs(view.now_singing_surface, now_surface)
        self.assertIs(view.rotation_rail, rail_surface)
        self.assertFalse(owner._rotation_view_user_opened)

        view.show()
        QTest.qWait(40)
        self.assertTrue(view.isVisible())

        owner._app_closing = True
        view.close()
        owner.close()

    def test_karafun_streaming_card_uses_artist_and_title_not_provider_id(self):
        view = mod.RotationView()
        self.addCleanup(view.close)
        queue = [{
            "name": "Maya",
            "songs": [{
                "song_info": "karafun_streaming:kf_7271",
                "provider": "karafun_streaming",
                "provider_track_id": "kf_7271",
                "artist": "Alain Souchon",
                "title": "Allô Maman bobo",
                "display_name": "Alain Souchon - Allô Maman bobo - KaraFun",
                "skipped": False,
            }],
        }]
        view.update_rotation(queue, [], "Maya")
        if view.rotation_rail is not None:
            rendered_song = json.loads(
                view.rotation_rail._root.property("itemsJson")
            )[0]["song"]
        else:
            self.assertEqual(view.list_widget.count(), 1)
            rendered_song = view.list_widget.item(0).text()
        self.assertIn("Alain Souchon • Allô Maman bobo", rendered_song)
        self.assertNotIn("kf_7271", rendered_song)


if __name__ == "__main__":
    unittest.main()
