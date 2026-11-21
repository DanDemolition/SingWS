"""1.0.4.0 (2026-11-19 show: the transition "starts too long after the end of the song"): a CDG song is handed over a few seconds after the LAST visible change of its
graphics (the last lyric wipe) instead of waiting for the instrumental tail or the static final card. Checked on real CDG files too when the library is on this Mac."""
import _test_profile  # noqa: F401
import glob
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from test_recent_regressions import load_main_module, make_app

NS = 1_000_000_000


class LyricCueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_main_module()

    def make(self, cue=272.2, duration=304.0, **settings):
        app = make_app(self.m)
        app.settings.update(settings)
        app._end_silence_mode = "cdg"
        app._karaoke_visual_end_path = "/lib/song.cdg"
        app._karaoke_lyric_cue_s = None
        if cue is not None:
            app._set_lyric_cue("/lib/song.cdg", cue, duration)
        return app

    def test_the_cue_arms_only_when_it_is_trustworthy(self):
        self.assertEqual(self.make(272.2, 304.0)._karaoke_lyric_cue_s, 272.2)
        self.assertIsNone(self.make(12.0, 250.0)._karaoke_lyric_cue_s, "a title card only: the song would end at 15 s")
        self.assertIsNone(self.make(25.0, 28.0)._karaoke_lyric_cue_s, "under 30 s")
        self.assertIsNone(self.make(100.0, 304.0)._karaoke_lyric_cue_s, "less than 60% of the graphics")
        self.assertIsNone(self.make(None, 304.0)._karaoke_lyric_cue_s)
        app = self.make()
        app._set_lyric_cue("/other.cdg", 280.0, 304.0)          # another song's late scan result is ignored
        self.assertEqual(app._karaoke_lyric_cue_s, 272.2)
        app._set_lyric_cue("/lib/song.cdg", "x", 304.0)
        self.assertIsNone(app._karaoke_lyric_cue_s, "unreadable value disarms it")

    def test_ready_three_seconds_after_the_last_wipe_with_the_song_still_running(self):
        app = self.make(272.2, 304.0)
        self.assertFalse(app._lyric_end_cue_ready(275.1, 28.9), "2.9 s after the wipe is too early")
        self.assertTrue(app._lyric_end_cue_ready(275.2, 28.8))
        self.assertTrue(app._lyric_end_cue_ready(290.0, 14.0))
        self.assertFalse(app._lyric_end_cue_ready(300.5, 3.5), "under 4 s left: the normal end is right behind it")
        self.assertFalse(app._lyric_end_cue_ready(100.0, 204.0), "mid-song")

    def test_settings_switch_it_off_and_move_the_delay(self):
        self.assertFalse(self.make(karaoke_end_on_lyric_cue=False)._lyric_end_cue_ready(290.0, 14.0))
        app = self.make(karaoke_lyric_cue_delay_sec=6.0)
        self.assertFalse(app._lyric_end_cue_ready(278.0, 26.0))
        self.assertTrue(app._lyric_end_cue_ready(278.3, 25.7))
        self.assertTrue(self.make(karaoke_lyric_cue_delay_sec="x")._lyric_end_cue_ready(275.3, 28.7), "a bad value falls back to 3 s")
        self.assertEqual((self.m.DEFAULTS["karaoke_end_on_lyric_cue"], self.m.DEFAULTS["karaoke_lyric_cue_delay_sec"],
                          self.m.DEFAULTS["karaoke_lyric_cue_min_remaining_sec"]), (True, 3.0, 4.0))

    def test_only_cdg_songs_use_it(self):
        app = self.make()
        app._end_silence_mode = "mp4"
        self.assertFalse(app._lyric_end_cue_ready(290.0, 14.0))
        app._end_silence_mode = ""
        self.assertFalse(app._lyric_end_cue_ready(290.0, 14.0))

    def stubbed(self):
        app = self.make(272.2, 304.0)
        object.__getattribute__(app, "__dict__")["_song_outro_shown_early"] = False
        app._song_outro_payload_from_current = lambda: {"singer": "Dan", "title": "T", "artist": "A", "on_deck": ""}
        self.outros = []
        app._mark_next_up_overlay_pending_after_completion = lambda reason="media_end": (self.outros.append(reason), True)[1]
        app._handle_media_end_safe = lambda trigger="eos": self.fail("the cue must never end the song")
        app._prefire_bgm_at_verified_audio_end = lambda: self.fail("the cue must never start the music")
        return app

    def test_at_the_cue_only_the_animation_starts_and_the_audio_is_left_alone(self):
        app = self.stubbed()
        self.assertFalse(app._maybe_show_lyric_cue_outro(int(304 * NS), int(200 * NS)), "mid-song")
        self.assertEqual(self.outros, [])
        self.assertTrue(app._maybe_show_lyric_cue_outro(int(304 * NS), int(276 * NS)))
        self.assertEqual(self.outros, ["lyric_cue"])
        self.assertFalse(app._maybe_show_lyric_cue_outro(int(304 * NS), int(280 * NS)), "once per song")
        self.assertEqual(self.outros, ["lyric_cue"])
        self.assertFalse(object.__getattribute__(app, "__dict__").get("_end_silence_tail_handoff_started"), "no handoff was started")

    def test_the_tick_shows_it_even_with_the_silence_trim_setting_off_and_never_ends_the_song(self):
        app = self.stubbed()
        app.settings["karaoke_trim_verified_tail"] = False
        app.settings["karaoke_allow_early_silence_trim"] = False
        self.assertFalse(app._karaoke_early_silence_trim_enabled())
        self.assertFalse(app._maybe_trim_end_silence(int(304 * NS), int(280 * NS)), "the tick itself ends nothing")
        self.assertEqual(self.outros, ["lyric_cue"])

    def test_no_speaker_name_no_animation_but_the_flag_is_set_so_the_real_end_still_works(self):
        app = self.stubbed()
        app._song_outro_payload_from_current = lambda: {}
        self.assertFalse(app._maybe_show_lyric_cue_outro(int(304 * NS), int(280 * NS)))
        self.assertEqual(self.outros, [])

    def test_the_real_media_end_does_not_show_the_outro_a_second_time_and_each_song_resets_the_flag(self):
        import inspect
        end = inspect.getsource(self.m.KaraokeApp._finish_media_end_cleanup)
        self.assertIn('get("_song_outro_shown_early")', end)
        self.assertLess(end.index('get("_song_outro_shown_early")'), end.index('reason="media_end"'))
        setup = inspect.getsource(self.m.KaraokeApp._setup_end_silence_state)
        self.assertIn('"_song_outro_shown_early"] = False', setup)

    def test_on_real_cdg_files_the_cue_is_inside_the_song_and_the_old_end_is_later(self):
        lib = "/Users/daniel/Music/Karaoke/Karaoke Library"
        files = sorted(glob.glob(os.path.join(lib, "*.cdg")))[:12]
        if not files:
            self.skipTest("no CDG library on this Mac")
        import transition_analysis as ta
        gaps = []
        for f in files:
            r = ta.analyze_cdg_visual(f)
            if r.last_change is None:
                continue
            app = self.make(None)
            app._karaoke_visual_end_path = f
            app._set_lyric_cue(f, r.last_change, r.duration)
            if app._karaoke_lyric_cue_s is not None:
                gaps.append(r.duration - r.last_change)
                self.assertTrue(app._lyric_end_cue_ready(r.last_change + 3.1, r.duration - r.last_change - 3.1 + 5.0) or r.duration - r.last_change < 8.0, f)
        self.assertTrue(gaps, "at least one real song armed the cue")


if __name__ == "__main__":
    unittest.main()
