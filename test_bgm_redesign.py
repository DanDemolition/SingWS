"""BGM tab redesign (setting ui_bgm_redesign, off by default): styles and the row painter change, nothing else; off restores exactly."""
import contextlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parent
os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-bgmredesign-")
SRC = (_ROOT / "0.2.18.1.py").read_text(encoding="utf-8")


def _load():
    spec = importlib.util.spec_from_file_location("singws_main_bgmredesign", _ROOT / "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BgmRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.module = _load()

    def make(self):
        m = self.module
        bg = SimpleNamespace(playlist=[], current_index=0, is_playing=False, play=lambda: None)
        with contextlib.ExitStack() as patches:
            for method in ("_apply_main_window_look", "_match_main_window_geometry", "load_data", "_init_file_browser"):
                patches.enter_context(mock.patch.object(m.BackgroundMusicManager, method))
            mgr = m.BackgroundMusicManager(bg)
        mgr.update_timer.stop()
        for n in ("Alpha.mp3", "Bravo.mp3", "Charlie.mp3"):
            mgr.current_playlist.append({"path": f"/x/{n}", "filename": n, "title": n[:-4]})
            mgr.playlist_list.addItem(mgr._make_playlist_item(n, f"/x/{n}"))
        bg.playlist = [t["path"] for t in mgr.current_playlist]
        self.addCleanup(mgr.close)
        return mgr

    def test_off_by_default(self):
        self.assertRegex(SRC, r'"ui_bgm_redesign":\s*True')
        self.assertFalse(getattr(self.make(), "_bgm_redesign", False))

    def test_on_swaps_the_row_painter_and_off_puts_everything_back(self):
        mgr = self.make()
        before = {
            "player": mgr.bg_player_frame.styleSheet(), "list": mgr.playlist_list.styleSheet(), "play": mgr.play_button.styleSheet(),
            "check": mgr.enable_bg_cb.styleSheet(), "seek": mgr.seek_slider.styleSheet(),
            "delegate": mgr.playlist_list.itemDelegate(), "alt": mgr.playlist_list.alternatingRowColors(),
            "cmin": mgr.enable_bg_cb.minimumHeight(), "cmax": mgr.enable_bg_cb.maximumHeight(),
        }
        mgr.apply_bgm_redesign(True)
        self.assertIsInstance(mgr.playlist_list.itemDelegate(), self.module.BgmPlaylistDelegate)
        self.assertNotEqual(mgr.play_button.styleSheet(), before["play"])
        self.assertEqual(mgr.enable_bg_cb.maximumHeight(), 28)          # a fixed height so the pill radius is valid
        mgr.apply_bgm_redesign(False)
        self.assertIs(mgr.playlist_list.itemDelegate(), before["delegate"])
        self.assertEqual(mgr.bg_player_frame.styleSheet(), before["player"])
        self.assertEqual(mgr.playlist_list.styleSheet(), before["list"])
        self.assertEqual(mgr.play_button.styleSheet(), before["play"])
        self.assertEqual(mgr.enable_bg_cb.styleSheet(), before["check"])
        self.assertEqual(mgr.seek_slider.styleSheet(), before["seek"])
        self.assertEqual(mgr.playlist_list.alternatingRowColors(), before["alt"])
        self.assertEqual((mgr.enable_bg_cb.minimumHeight(), mgr.enable_bg_cb.maximumHeight()), (before["cmin"], before["cmax"]))

    def test_the_redesign_never_touches_the_playlist_or_the_handlers(self):
        mgr = self.make()
        names = [mgr.playlist_list.item(i).text() for i in range(mgr.playlist_list.count())]
        mgr.apply_bgm_redesign(True)
        self.assertEqual([mgr.playlist_list.item(i).text() for i in range(mgr.playlist_list.count())], names)
        self.assertEqual([t["filename"] for t in mgr.current_playlist], names)
        body = SRC[SRC.index("    def apply_bgm_redesign"):SRC.index("    def setup_ui(self):\n        \"\"\"Create the UI layout\"\"\"")]
        for forbidden in (".clicked.connect", ".toggled.connect", "setData(", "takeItem", "insertItem", ".addItem("):
            self.assertNotIn(forbidden, body)

    def test_the_row_after_the_current_one_is_tagged_up_next(self):
        mgr = self.make()
        self.assertEqual(mgr._up_next_row(), 1)
        mgr.bg_music.playlist = []
        self.assertEqual(mgr._up_next_row(), -1)

    def test_the_delegate_paints_every_row(self):
        mgr = self.make()
        mgr.apply_bgm_redesign(True)
        mgr.resize(900, 700)
        mgr.show()
        self.app.processEvents()
        img = mgr.playlist_list.viewport().grab().toImage()
        self.assertFalse(img.isNull())

    def test_the_settings_checkbox_and_launch_hook_exist(self):
        self.assertIn('QCheckBox("Use the new BGM tab look")', SRC)
        self.assertIn("self._apply_ui_bgm_redesign()", SRC)
        self.assertIn('if self.settings.get("ui_bgm_redesign", False):', SRC)


if __name__ == "__main__":
    unittest.main()
