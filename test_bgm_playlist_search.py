"""BGM playlist: search box, Queue Next and Move to End (2026-10-10). Builds the real manager UI offscreen."""
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
_HOME = tempfile.mkdtemp(prefix="singws-bgsearch-")
os.environ["SINGWS_HOME"] = _HOME


def _load():
    spec = importlib.util.spec_from_file_location("singws_main_bgsearch", _ROOT / "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


NAMES = ["Alpha One.mp3", "Bravo Two.mp3", "Charlie Three.mp3", "Delta Four.mp3", "Echo Five.mp3", "Foxtrot Six.mp3"]


class PlaylistSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.module = _load()

    def make(self, current_index=1):
        m = self.module
        bg = SimpleNamespace(playlist=[], current_index=current_index, is_playing=True, play=lambda: None)
        with contextlib.ExitStack() as patches:
            for method in ("_apply_main_window_look", "_match_main_window_geometry", "load_data", "_init_file_browser"):
                patches.enter_context(mock.patch.object(m.BackgroundMusicManager, method))
            patches.enter_context(mock.patch.object(m.BackgroundMusicManager, "save_current_playlist"))
            mgr = m.BackgroundMusicManager(bg)
        mgr.update_timer.stop()
        mgr.save_current_playlist = lambda: None
        for n in NAMES:
            mgr.current_playlist.append({"path": f"/x/{n}", "filename": n, "title": n[:-4]})
            mgr.playlist_list.addItem(mgr._make_playlist_item(n, f"/x/{n}"))
        bg.playlist = [t["path"] for t in mgr.current_playlist]
        mgr._invalidate_highlight_cache()
        mgr.highlight_current_track()
        self.addCleanup(mgr.close)
        return mgr, bg

    def visible(self, mgr):
        return [mgr.playlist_list.item(i).text() for i in range(mgr.playlist_list.count()) if not mgr.playlist_list.item(i).isHidden()]

    def order(self, mgr):
        return [mgr.playlist_list.item(i).text() for i in range(mgr.playlist_list.count())]

    def test_played_rows_stay_hidden_without_a_search(self):
        mgr, _ = self.make(current_index=1)
        self.assertEqual(self.visible(mgr), NAMES[2:])

    def test_search_filters_upcoming_rows_only(self):
        mgr, _ = self.make(current_index=1)
        mgr.playlist_search.setText("o")  # matches Alpha One / Bravo Two / Echo Five / Foxtrot Six, ...
        mgr._apply_playlist_filter()
        shown = self.visible(mgr)
        self.assertNotIn("Alpha One.mp3", shown)   # played, so never resurrected by a search
        self.assertNotIn("Bravo Two.mp3", shown)   # the current track
        self.assertEqual(shown, [n for n in NAMES[2:] if "o" in n.lower()])
        self.assertIn("of 6 tracks", mgr.playlist_count.text())

    def test_multiword_search_and_clear_restores_the_view(self):
        mgr, _ = self.make(current_index=0)
        mgr.playlist_search.setText("echo five")
        mgr._apply_playlist_filter()
        self.assertEqual(self.visible(mgr), ["Echo Five.mp3"])
        mgr.playlist_search.clear()
        mgr._apply_playlist_filter()
        self.assertEqual(self.visible(mgr), NAMES[1:])
        self.assertEqual(mgr.playlist_count.text(), "6 tracks")

    def test_search_survives_the_current_track_advancing(self):
        mgr, bg = self.make(current_index=0)
        mgr.playlist_search.setText("e")
        mgr._apply_playlist_filter()
        bg.current_index = 2
        mgr.highlight_current_track()
        self.assertEqual(self.visible(mgr), [n for n in NAMES[3:] if "e" in n.lower()])
        bg.current_index = 0   # stepping back must not un-hide non-matching rows
        mgr.highlight_current_track()
        self.assertEqual(self.visible(mgr), [n for n in NAMES[1:] if "e" in n.lower()])

    def test_queue_next_puts_the_selection_right_after_the_current_track_in_order(self):
        mgr, bg = self.make(current_index=1)
        for name in ("Foxtrot Six.mp3", "Delta Four.mp3"):
            mgr.playlist_list.item(NAMES.index(name)).setSelected(True)
        mgr.queue_selected_next()
        self.assertEqual(self.order(mgr), ["Alpha One.mp3", "Bravo Two.mp3", "Delta Four.mp3", "Foxtrot Six.mp3", "Charlie Three.mp3", "Echo Five.mp3"])
        self.assertEqual([t["filename"] for t in mgr.current_playlist], self.order(mgr))
        self.assertEqual(bg.playlist, [t["path"] for t in mgr.current_playlist])
        self.assertEqual(bg.current_index, 1)                      # the playing track did not move
        self.assertEqual(bg.playlist[bg.current_index], "/x/Bravo Two.mp3")
        self.assertEqual(self.visible(mgr)[:2], ["Delta Four.mp3", "Foxtrot Six.mp3"])

    def test_queue_next_works_from_a_filtered_view(self):
        mgr, bg = self.make(current_index=0)
        mgr.playlist_search.setText("foxtrot")
        mgr._apply_playlist_filter()
        self.assertEqual(self.visible(mgr), ["Foxtrot Six.mp3"])
        mgr.playlist_list.item(5).setSelected(True)
        mgr.queue_selected_next()
        self.assertEqual(self.order(mgr)[:3], ["Alpha One.mp3", "Foxtrot Six.mp3", "Bravo Two.mp3"])
        self.assertEqual(self.visible(mgr), ["Foxtrot Six.mp3"])   # filter still applied afterwards
        self.assertEqual(bg.current_index, 0)

    def test_played_and_current_rows_cannot_be_moved(self):
        mgr, bg = self.make(current_index=2)
        before = self.order(mgr)
        for row in (0, 1, 2):
            mgr.playlist_list.item(row).setSelected(True)
        mgr.queue_selected_next()
        mgr.move_selected_to_end()
        self.assertEqual(self.order(mgr), before)
        self.assertEqual(bg.current_index, 2)

    def test_move_to_end(self):
        mgr, bg = self.make(current_index=0)
        mgr.playlist_list.item(2).setSelected(True)
        mgr.playlist_list.item(3).setSelected(True)
        mgr.move_selected_to_end()
        self.assertEqual(self.order(mgr), ["Alpha One.mp3", "Bravo Two.mp3", "Echo Five.mp3", "Foxtrot Six.mp3", "Charlie Three.mp3", "Delta Four.mp3"])
        self.assertEqual(bg.current_index, 0)

    def test_context_menu_and_search_box_are_wired(self):
        mgr, _ = self.make()
        self.assertEqual(mgr.playlist_list.contextMenuPolicy(), self.module.Qt.ContextMenuPolicy.CustomContextMenu)
        self.assertTrue(mgr.playlist_search.isClearButtonEnabled())
        mgr.playlist_search.setText("delta")
        self.assertTrue(mgr._playlist_filter_timer.isActive())   # typing starts the debounce timer


if __name__ == "__main__":
    unittest.main()
