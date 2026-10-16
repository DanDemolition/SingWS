"""Show tab redesign, right column: slim empty pads / big loaded pads, and the way back to today's look."""
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
    spec = importlib.util.spec_from_file_location("singws_main_rc", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RightColumnTests(unittest.TestCase):
    def tearDown(self):
        _close_all_widgets()

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
        self.assertEqual(s.pads[0].height(), 58 + 16)          # a loaded pad is a little taller in the redesign
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

    def test_tempo_and_key_become_two_row_cards_and_come_back_as_one_panel(self):
        from pathlib import Path
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        body = src[src.index("def _apply_redesign_polish"):src.index("def _restyle_bg_main_buttons")]
        self.assertIn("self.tempo_row_cards.setVisible(on)", body)
        self.assertIn("self._tempo_panel_css", body)           # the original panel style is what comes back
        self.assertIn("self._tempo_panel.setFixedHeight(100 if on else 78)", body)

    def test_row_cards_paint_inside_their_panel(self):
        from PyQt6.QtWidgets import QWidget, QGridLayout, QLabel
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(14, 4, 14, 4)
        grid.setVerticalSpacing(12)
        for r in range(2):
            grid.addWidget(QLabel("x"), r, 0)
            grid.setRowMinimumHeight(r, 40)
        panel.setFixedSize(300, 100)
        overlay = self.m.RowCardsOverlay(panel, grid, 2)
        panel.show()
        self.app.processEvents()
        for r in range(2):
            cell = grid.cellRect(r, 0)
            self.assertGreaterEqual(cell.top() - 2, 0)
            self.assertLessEqual(cell.bottom() + 2, panel.height())
        self.assertFalse(panel.grab().toImage().isNull())

    def test_preview_card_drops_its_title_and_shows_elapsed_over_total(self):
        from pathlib import Path
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        body = src[src.index("def _apply_redesign_right_column"):src.index("def _apply_redesign_hero_text")]
        self.assertIn("self.performance_deck_title, self.performance_deck_status", body)
        self.assertIn("w.setVisible(not on)", body)
        self.assertIn("self.preview_time_label.setVisible(on)", body)
        sync = src[src.index("def _hero_stage_sync"):src.index("def _hero_seek_pressed")]
        self.assertIn('f"{_mss(pos)} / {_mss(dur)}"', sync)
        self.assertIn("_karaoke_last_dur", sync)             # same position/duration the seek bar already uses

    def test_changing_a_pad_refits_the_fixed_height_card_so_pads_never_overlap(self):
        # Found on screen: with the redesign on, loading pads made loaded pads (taller) overlap the next row, because the
        # transport card has a fixed height computed when the layout was applied.
        from pathlib import Path
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        body = src[src.index("def _on_soundboard_changed"):src.index("def _apply_tooltip_visibility")]
        self.assertIn("self._ui_redesign_on()", body)
        self.assertIn("self._fit_right_column(True)", body)

    def test_the_preview_is_inset_in_a_card_when_on_and_exactly_as_before_when_off(self):
        pw = self.m.PreviewWindow()
        off_css = pw.styleSheet()
        margins_off = pw._main_layout.contentsMargins()
        pw.set_card_style(True)
        m = pw._main_layout.contentsMargins()
        self.assertEqual((m.left(), m.top(), m.right(), m.bottom()), (8, 8, 8, 8))     # only the margins around the video change
        self.assertIn("border-radius: 14px", pw.styleSheet())                       # a full 14 px card around the inset video
        pw.set_card_style(False)
        self.assertEqual(pw.styleSheet(), off_css)
        m2 = pw._main_layout.contentsMargins()
        self.assertEqual((m2.left(), m2.top(), m2.right(), m2.bottom()), (margins_off.left(), margins_off.top(), margins_off.right(), margins_off.bottom()))

    def test_the_transport_bar_has_no_card_of_its_own_with_the_redesign(self):
        from pathlib import Path
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        body = src[src.index("def _transport_bar_live_css(self"):src.index("def _transport_bar_live_css_base")]
        self.assertIn("if self._ui_redesign_on():", body)
        self.assertIn("background: transparent; border: none;", body)
        self.assertIn("return self._transport_bar_live_css_base(active, pulse)", body)     # off: the playing glow is untouched

    def test_the_idle_preview_shows_a_gradient_and_label_only_when_nothing_else_is_showing(self):
        from PyQt6.QtGui import QPixmap
        va = self.m.VideoAreaWidget()
        va.resize(300, 180)
        va.show()
        self.app.processEvents()
        plain = va.grab().toImage().pixelColor(150, 90).name()
        self.assertEqual(plain, "#000000")                       # as before: black
        va.idle_placeholder = True
        va.update()
        self.app.processEvents()
        img = va.grab().toImage()
        self.assertNotEqual(img.pixelColor(290, 170).name(), "#000000")      # the gradient is there (bottom right is the purple end)
        pm = QPixmap(300, 180)
        pm.fill()
        va.background_pixmap = pm                                # a real idle background wins over the placeholder
        va.update()
        self.app.processEvents()
        self.assertNotEqual(va.grab().toImage().pixelColor(290, 170).name(), img.pixelColor(290, 170).name())

    def test_the_transport_bar_backdrop_sits_under_the_buttons_and_paints_a_rounded_bar(self):
        from PyQt6.QtCore import QRect
        from PyQt6.QtWidgets import QWidget
        host = QWidget()
        host.resize(300, 100)
        host.setStyleSheet("background: rgb(30,30,60);")
        bd = self.m.BarBackdrop(host, lambda: QRect(20, 30, 260, 40))
        host.show()
        self.app.processEvents()
        img = host.grab().toImage()
        inside, outside = img.pixelColor(150, 50), img.pixelColor(150, 5)
        self.assertNotEqual(inside.name(), outside.name())
        self.assertLess(inside.red() + inside.green() + inside.blue(), outside.red() + outside.green() + outside.blue())   # darker bar

    def test_remembered_sizes_and_fonts_come_back_exactly_when_the_redesign_goes_off(self):
        # Found by diffing every widget's geometry before/after turning the redesign on and off: buttons kept the redesign's size and font.
        from types import SimpleNamespace
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QPushButton, QSizePolicy
        KA = self.m.KaraokeApp
        btn = QPushButton("x")
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        btn.setFont(f)
        btn.setFixedSize(48, 38)
        btn.setMinimumWidth(0)
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        ns = SimpleNamespace(settings={"ui_show_redesign": True})
        ns._ui_redesign_on = lambda: True
        ns.__dict__.setdefault("_redesign_orig_sizes", {})
        KA._redesign_remember(ns, btn)
        btn.setFixedSize(34, 30)
        f2 = btn.font()
        f2.setPointSize(14)
        btn.setFont(f2)
        KA._redesign_restore(ns, btn)
        self.assertEqual((btn.minimumWidth(), btn.maximumWidth(), btn.minimumHeight(), btn.maximumHeight()), (0, 48, 38, 38))
        self.assertEqual((btn.font().pointSize(), btn.font().bold()), (18, True))

    def test_nothing_is_remembered_while_the_redesign_is_off_so_an_early_half_built_state_is_never_the_original(self):
        from types import SimpleNamespace
        from PyQt6.QtWidgets import QPushButton
        KA = self.m.KaraokeApp
        btn = QPushButton("x")
        ns = SimpleNamespace(settings={})
        ns._ui_redesign_on = lambda: False
        KA._redesign_remember(ns, btn)
        self.assertEqual(ns.__dict__.get("_redesign_orig_sizes", {}), {})

    def test_the_preview_overlay_is_opt_in_macos_only_and_puts_everything_back(self):
        from pathlib import Path
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertRegex(src, r'"ui_preview_overlay":\s*False')                       # off by default, and a separate switch from the redesign
        wanted = src[src.index("def _preview_overlay_wanted"):src.index("def _preview_overlay_resettle")]
        self.assertIn("self._ui_redesign_on()", wanted)
        self.assertIn('sys.platform == "darwin"', wanted)
        body = src[src.index("def _apply_preview_overlay"):src.index("def _restyle_seek_sliders")]
        self.assertIn("ov.hide()", body)                                                # off: the overlay goes away...
        self.assertIn("row.insertWidget(i, b)", body)                                   # ...and the buttons return to their original row, in order
        self.assertIn("pw.set_native_round(0.0)", body)                                 # ...and the video is square again
        redesign = src[src.index("def _apply_ui_show_redesign"):src.index("def _apply_redesign_hero_text")]
        self.assertIn("self._apply_preview_overlay()", redesign)                        # turning the whole redesign off also undoes it
        refresh = src[src.index("def _refresh_mpv_video_views"):src.index("def _reveal_mpv_hosts_if_allowed")]
        self.assertIn("self._preview_overlay_resettle()", refresh)                      # re-raised after every native surface change

    def test_the_overlay_is_placed_at_the_bottom_of_the_preview_and_raised(self):
        from PyQt6.QtWidgets import QFrame
        pw = self.m.PreviewWindow()
        pw.resize(400, 300)
        pw.show()
        self.app.processEvents()
        pw.overlay = QFrame(pw)
        pw.overlay.show()
        pw.place_overlay()
        g = pw.overlay.geometry()
        self.assertEqual((g.x(), g.width(), g.height()), (18, 400 - 36, 46))
        self.assertLessEqual(g.bottom(), 300 - 10)


if __name__ == "__main__":
    unittest.main()
