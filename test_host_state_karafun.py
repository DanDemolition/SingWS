"""The DAW / Live Player state for a KaraFun song: real artist/title, KaraFun's clock, key and tempo (never the streaming id)."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import importlib.util
import os
import time
import unittest


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_hostkf", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HostStateKarafunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def stub(self):
        K = self.m.KaraokeApp
        names = ["_host_control_state", "_host_rotation_state", "_host_empty_rotation_slot", "_host_song_title_from_path",
                 "_karafun_display_times", "_karafun_active_entry", "_gst_query_times", "_is_karaoke_paused",
                 "_host_queue_entry_title", "_host_next_entry_for_singer", "_next_singer_entry", "_current_play_item_key",
                 "_host_seek_relative", "_karaoke_seek_seconds"]
        ns = {n: getattr(K, n) for n in names if hasattr(K, n)}
        ns["_next_singer_entry"] = lambda self: None
        ns["_current_play_item_key"] = lambda self: ("", "")
        ns["lookup_display_name"] = lambda self, path, artist_title_only=False: path
        ns["_karafun_seek_to"] = lambda self, t: self.__dict__.setdefault("seeks", []).append(t) or True
        S = type("Stub", (), ns)
        s = S()
        s.karaoke_playing = True
        s.settings = {}
        s._current_karaoke_song_path = "karafun_streaming:kf_123"
        s._current_karaoke_singer_display = "Dan"
        s._current_karaoke_artist = "Sugarcult"
        s._current_karaoke_title = "Memory"
        s._current_karaoke_semitones = 2
        s._karaoke_tempo_percent = 95
        s.karaoke_transport = None
        s._active_external_karafun = {"entry": {"duration": 240, "karafun_display_started_at": time.monotonic() - 30.0}}
        return s

    def test_karafun_song_shows_artist_title_clock_key_and_tempo(self):
        s = self.stub()
        st = s._host_control_state()
        pb = st["playback"]
        self.assertEqual((pb["artist"], pb["title"]), ("Sugarcult", "Memory"))
        self.assertEqual(st["rotation"]["current"]["title"], "Memory")
        self.assertEqual(pb["duration_seconds"], 240.0)
        self.assertAlmostEqual(pb["position_seconds"], 30.0, delta=1.5)
        self.assertAlmostEqual(pb["remaining_seconds"], 210.0, delta=1.5)
        self.assertEqual(pb["key"], 2)
        self.assertEqual(pb["tempo"], 0.95)
        self.assertNotIn("karafun_streaming", repr(st))

    def test_dragging_the_daw_seek_moves_karafun_from_its_own_clock(self):
        s = self.stub()
        ok, _msg = s._host_seek_relative(10)
        self.assertTrue(ok)
        self.assertAlmostEqual(s.seeks[0], 40.0, delta=1.5)


if __name__ == "__main__":
    unittest.main()
