import unittest

import numpy as np

import phrase_detect as pd


class PeaksTests(unittest.TestCase):
    def test_peaks_shape_and_range(self):
        pcm = np.linspace(-1.0, 1.0, 8000, dtype=np.float32)
        p = pd.peaks(pcm, 100)
        self.assertEqual(p.shape, (100, 2))
        # min column <= max column everywhere
        self.assertTrue(np.all(p[:, 0] <= p[:, 1]))
        # overall extremes preserved
        self.assertAlmostEqual(float(p[:, 0].min()), -1.0, places=2)
        self.assertAlmostEqual(float(p[:, 1].max()), 1.0, places=2)

    def test_peaks_empty(self):
        p = pd.peaks(np.zeros(0, dtype=np.float32), 50)
        self.assertEqual(p.shape, (50, 2))


class SuggestTests(unittest.TestCase):
    def _silence_then_tone(self, sr=8000, silence_s=10.0, tone_s=10.0):
        n_sil = int(sr * silence_s)
        n_tone = int(sr * tone_s)
        t = np.arange(n_tone) / sr
        tone = 0.6 * np.sin(2 * np.pi * 220 * t).astype(np.float32)
        return np.concatenate([np.zeros(n_sil, dtype=np.float32), tone]), sr

    def test_suggest_finds_boundary(self):
        pcm, sr = self._silence_then_tone(silence_s=10.0, tone_s=15.0)
        s = pd.suggest_start(pcm, sr)  # search_fraction 0.35 of 25s ~= 8.75s -> won't see 10s boundary
        # widen search to include the 10s boundary
        s = pd.suggest_start(pcm, sr, search_fraction=0.6)
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s, 10.0, delta=0.6)

    def test_suggest_snaps_to_bar(self):
        pcm, sr = self._silence_then_tone(silence_s=10.0, tone_s=15.0)
        # 120 BPM -> 2.0 s/bar; nearest bar to ~10.0s is 10.0s exactly.
        s = pd.suggest_start(pcm, sr, bpm=120, search_fraction=0.6)
        self.assertIsNotNone(s)
        spb = 4 * 60.0 / 120.0
        self.assertAlmostEqual(s / spb, round(s / spb), places=3)

    def test_suggest_none_on_silence(self):
        self.assertIsNone(pd.suggest_start(np.zeros(8000 * 5, dtype=np.float32), 8000))

    def test_suggest_none_on_short(self):
        self.assertIsNone(pd.suggest_start(np.zeros(100, dtype=np.float32), 8000))


if __name__ == "__main__":
    unittest.main()
