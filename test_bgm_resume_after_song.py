"""After a song, background music resumes even if Play was never pressed since launch (but never on a fresh launch)."""
import os
import unittest

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"), encoding="utf-8").read()


class BgmResumeAfterSongTests(unittest.TestCase):
    def test_gate_allows_resume_once_a_song_has_played_but_still_blocks_a_fresh_launch(self):
        body = SRC[SRC.index("    def _start_bg_with_fade(self):"):SRC.index("    def _start_bg_with_fade_safe")]
        self.assertIn('not getattr(self, "_bgm_session_started", False) and not self.__dict__.get("_karaoke_played_this_session", False)', body)
        self.assertIn("[BG-HANDOFF] resume blocked", body)        # the silent gate now says so in the log

    def test_both_song_start_paths_mark_the_session(self):
        self.assertEqual(SRC.count("self._karaoke_played_this_session = True"), 2)    # native and KaraFun starts


if __name__ == "__main__":
    unittest.main()
