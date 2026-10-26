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


class HostStateBackgroundMusicTests(HostStateKarafunTests):
    """`bg_index` / `bg_track`: which background-music track the player is on, for the shell's Music tab (same clamp as the app's own playlist list)."""

    def with_bg(self, playlist, index, playing=False):
        import types
        s = self.stub()
        s.bg_music = types.SimpleNamespace(playlist=playlist, current_index=index, is_playing=playing, volume=0.5)
        return s

    def test_the_track_and_its_row_are_published(self):
        st = self.with_bg(["/music/a/One.mp3", "/music/a/Two.flac", "/music/b/Three.m4a"], 1, playing=True)._host_control_state()
        self.assertEqual((st["bg_index"], st["bg_track"], st["bg_playing"], st["bg_volume"]), (1, "Two.flac", True, 0.5))

    def test_the_index_is_clamped_like_the_apps_own_list_and_no_playlist_means_minus_one(self):
        for raw, want in ((99, 2), (-5, 0), (2, 2), (0, 0)):
            st = self.with_bg(["/m/a.mp3", "/m/b.mp3", "/m/c.mp3"], raw)._host_control_state()
            self.assertEqual(st["bg_index"], want, raw)
        st = self.with_bg([], 3)._host_control_state()
        self.assertEqual((st["bg_index"], st["bg_track"]), (-1, ""))
        s = self.stub()
        self.assertEqual((s._host_control_state()["bg_index"], s._host_control_state()["bg_track"]), (-1, ""), "no background player at all")

    def test_dict_entries_and_garbage_never_break_the_state(self):
        st = self.with_bg([{"path": "/m/Dict Song.mp3", "filename": "Dict Song.mp3"}], 0)._host_control_state()
        self.assertEqual(st["bg_track"], "Dict Song.mp3")
        st = self.with_bg([None, 5], 0)._host_control_state()
        self.assertEqual((st["bg_index"], st["bg_track"]), (0, ""))
        st = self.with_bg(["/m/a.mp3"], "not a number")._host_control_state()
        self.assertEqual((st["bg_index"], st["bg_track"]), (-1, ""), "an unreadable index falls back, it does not raise")


if __name__ == "__main__":
    unittest.main()
