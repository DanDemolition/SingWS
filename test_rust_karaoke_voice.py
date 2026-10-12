"""The karaoke song as a voice of the Rust engine (roadmap item 3, step 2): the REAL engine, headless (no sound card, nothing audible).

Skipped when the Rust library is missing (`cd rust && cargo build --release -p singws-bgm-ffi`). The operator's hard rule is tested here
end to end through the C interface: a tempo change must never move the key."""
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

import rust_bgm_engine as rbe
from test_rust_bgm_engine import RATE, freq_of, rms, tone_file

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
LIB_OK = rbe.available()

if LIB_OK:
    from rust_karaoke_voice import RustKaraokeVoice


class FakeEq:
    def __init__(self, gains, on=True):
        self._g, self._on = gains, on

    def gains_db(self):
        return self._g

    def enabled(self):
        return self._on

    def is_flat(self):
        return not any(self._g)


@unittest.skipUnless(LIB_OK, "libsingws_bgm_ffi.dylib not built")
class KaraokeVoiceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="singws-kv-")
        self.engine = rbe.RustBackgroundEngine(headless=True, sample_rate=RATE)
        self.k = RustKaraokeVoice(self.engine)
        self.song = tone_file(self.dir, "song.wav", 440.0, 0.3, 40.0)

    def tearDown(self):
        self.engine.close()

    def test_ready_flag_follows_loading_and_seeking(self):
        self.assertFalse(self.k.is_ready())
        self.k.load(self.song, paused=True)
        for _ in range(200):
            self.engine.render(480)
            if self.k.is_ready():
                break
        self.assertTrue(self.k.is_ready(), "a loaded song's source must become ready (even while paused)")
        self.k.seek_seconds(30.0)
        self.engine.render(480)
        for _ in range(300):
            self.engine.render(480)
            if self.k.is_ready():
                break
        self.assertTrue(self.k.is_ready(), "ready again after the seek, still paused")
        self.assertTrue(self.k.is_paused())

    def test_plays_and_the_clock_follows_the_song(self):
        self.k.load(self.song, paused=False)
        out = self.engine.render(RATE * 2)
        self.assertGreater(rms(out), 0.1)
        pos, dur = self.k.times()
        self.assertAlmostEqual(dur, 40.0, delta=0.1)
        self.assertAlmostEqual(pos, 2.0, delta=0.3)
        self.assertTrue(self.k.is_playing() and self.k.is_loaded())

    def test_tempo_never_moves_the_key(self):
        for pct in (50, 75, 90, 110, 150, 200):
            self.k.set_tempo_percent(pct)
            self.k.set_key_semitones(0)
            self.k.load(self.song, paused=False)
            self.engine.render(RATE)                     # settle
            out = self.engine.render(RATE * 3)
            self.assertAlmostEqual(freq_of(out), 440.0, delta=3.0, msg=f"tempo {pct}% moved the key")
            self.assertAlmostEqual(self.k.position_seconds(), 4.0 * pct / 100.0, delta=0.6 + 0.02 * pct / 10, msg=f"clock at {pct}%")

    def test_key_changes_pitch_and_not_the_clock(self):
        self.k.set_key_semitones(2)
        self.k.load(self.song, paused=False)
        self.engine.render(RATE)
        out = self.engine.render(RATE * 3)
        self.assertAlmostEqual(freq_of(out), 440.0 * 2 ** (2 / 12.0), delta=5.0)
        self.assertAlmostEqual(self.k.position_seconds(), 4.0, delta=0.4)

    def test_live_key_and_tempo_changes_do_not_click(self):
        self.k.load(self.song, paused=False)
        self.engine.render(RATE)
        chunks = []
        for i, (key, tempo) in enumerate([(0, 100), (2, 100), (2, 150), (-5, 60), (0, 100), (3, 80)]):
            self.k.set_key_semitones(key)
            self.k.set_tempo_percent(tempo)
            chunks.append(self.engine.render(RATE // 2))
        out = np.concatenate(chunks)[RATE // 4:]
        worst = float(np.max(np.abs(np.diff(out[:, 0]))))
        self.assertLess(worst, 0.06, f"click: largest step {worst} (a clean tone this loud steps ~0.02-0.03)")

    def test_seek_pause_and_end(self):
        self.k.load(self.song, paused=False)
        self.engine.render(RATE)
        self.assertTrue(self.k.seek_seconds(30.0))
        self.engine.render(RATE // 2)
        self.assertAlmostEqual(self.k.position_seconds(), 30.5, delta=0.4)
        self.assertTrue(self.k.pause())
        p = self.k.position_seconds()
        silent = self.engine.render(RATE // 2)
        self.assertEqual(float(np.abs(silent).max()), 0.0)
        self.assertAlmostEqual(self.k.position_seconds(), p, delta=0.01)
        self.assertTrue(self.k.is_paused())
        self.assertTrue(self.k.play())
        self.engine.render(RATE * 12)                    # past the 40 s end
        self.assertTrue(self.k.is_finished())

    def test_fade_out_and_stop(self):
        self.k.load(self.song, paused=False, volume=1.0)
        self.engine.render(RATE // 2)
        self.k.slide_volume(0.0, 300)
        self.engine.render(RATE)
        tail = self.engine.render(RATE // 4)
        self.assertLess(float(np.abs(tail).max()), 0.002)
        self.k.stop()
        self.assertFalse(self.k.is_loaded())

    def test_the_song_joins_the_music_and_has_its_own_chain(self):
        music = tone_file(self.dir, "music.wav", 220.0, 0.3, 30.0)
        self.engine.load(music, paused=False, volume=1.0)
        self.k.load(self.song, paused=False)
        both = self.engine.render(RATE * 2)
        self.assertGreater(rms(both), 0.25)              # two tones summed
        # the karaoke EQ (the 500 Hz band, +12 dB, touches the 440 Hz tone) changes the song only
        eq = FakeEq([0, 0, 0, 0, 12.0, 0, 0, 0, 0, 0])
        self.k.set_eq(eq)
        self.engine.render(RATE // 2)
        boosted = self.engine.render(RATE)
        self.assertGreater(rms(boosted), rms(both) * 1.2)

    def test_a_new_song_keeps_the_key_and_tempo_that_were_set(self):
        self.k.set_key_semitones(2)
        self.k.set_tempo_percent(90)
        self.k.load(self.song, paused=False)
        self.engine.render(RATE)
        out = self.engine.render(RATE * 3)
        self.assertAlmostEqual(freq_of(out), 440.0 * 2 ** (2 / 12.0), delta=5.0)
        self.assertAlmostEqual(self.k.position_seconds(), 4.0 * 0.9, delta=0.5)


if __name__ == "__main__":
    unittest.main()
