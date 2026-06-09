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


class DetectSectionsTests(unittest.TestCase):
    SR = 16000

    def _section(self, freq, amp, noise, secs):
        n = int(self.SR * secs)
        t = np.arange(n) / self.SR
        rng = np.random.RandomState(int(freq))  # deterministic per section type
        sig = amp * np.sin(2 * np.pi * freq * t)
        if noise > 0:
            sig = sig + noise * rng.randn(n).astype(np.float32)
        return sig.astype(np.float32)

    def _song(self):
        # Intro (quiet, low instrumental) → Verse → Chorus → Verse → Chorus.
        intro = self._section(110, 0.15, 0.0, 12)
        verse = self._section(220, 0.40, 0.20, 14)
        chorus = self._section(330, 0.85, 0.35, 14)
        pcm = np.concatenate([intro, verse, chorus, verse, chorus])
        seams = [12, 26, 40, 54]
        return pcm, seams

    def test_boundaries_near_seams(self):
        pcm, seams = self._song()
        secs = pd.detect_sections(pcm, self.SR)
        self.assertGreaterEqual(len(secs), 2)
        # every detected boundary is time-ordered and well-formed
        times = [s["seconds"] for s in secs]
        self.assertEqual(times, sorted(times))
        for s in secs:
            self.assertIn(s["from_type"], pd.SECTION_TYPES)
            self.assertIn(s["to_type"], pd.SECTION_TYPES)
            self.assertTrue(0.0 <= s["confidence"] <= 1.0)
            self.assertEqual(s["label"], f"{s['from_type']}→{s['to_type']}")
        # at least half of the true seams have a detected boundary within 3.5s
        hits = sum(any(abs(t - seam) <= 3.5 for t in times) for seam in seams)
        self.assertGreaterEqual(hits, 2)

    def test_structural_labels(self):
        pcm, _ = self._song()
        secs = pd.detect_sections(pcm, self.SR)
        # the quiet first section should read as Intro or Instrumental
        self.assertIn(secs[0]["from_type"], ("Intro", "Instrumental"))
        # the loud, repeated section should surface as a Chorus somewhere
        all_types = {s["from_type"] for s in secs} | {s["to_type"] for s in secs}
        self.assertIn("Chorus", all_types)

    def test_bar_snap(self):
        pcm, _ = self._song()
        secs = pd.detect_sections(pcm, self.SR, bpm=120)  # 2.0 s/bar
        spb = 4 * 60.0 / 120.0
        for s in secs:
            self.assertAlmostEqual(s["seconds"] / spb, round(s["seconds"] / spb), places=3)

    def test_deterministic(self):
        pcm, _ = self._song()
        a = pd.detect_sections(pcm, self.SR)
        b = pd.detect_sections(pcm, self.SR)
        self.assertEqual([x["seconds"] for x in a], [x["seconds"] for x in b])
        self.assertEqual([x["label"] for x in a], [x["label"] for x in b])

    def test_short_and_silence(self):
        self.assertEqual(pd.detect_sections(np.zeros(self.SR * 3, dtype=np.float32), self.SR), [])
        self.assertEqual(pd.detect_sections(np.zeros(self.SR * 30, dtype=np.float32), self.SR), [])


class EstimateBpmTests(unittest.TestCase):
    SR = 16000

    def _click_track(self, bpm, secs=20.0):
        n = int(self.SR * secs)
        sig = np.zeros(n, dtype=np.float32)
        period = int(self.SR * 60.0 / bpm)
        click = np.exp(-np.linspace(0, 8, int(self.SR * 0.03))).astype(np.float32)
        for start in range(0, n - click.size, period):
            sig[start:start + click.size] += click
        return sig

    def test_estimates_known_tempo(self):
        for bpm in (90, 120, 140):
            est = pd.estimate_bpm(self._click_track(bpm), self.SR)
            self.assertIsNotNone(est)
            # accept the exact tempo or a clean octave (half/double), within 4%
            ratios = [est / bpm, est / (bpm / 2.0), est / (bpm * 2.0)]
            self.assertTrue(any(abs(r - 1.0) < 0.04 for r in ratios),
                            f"bpm={bpm} est={est}")

    def test_silence_and_short(self):
        self.assertIsNone(pd.estimate_bpm(np.zeros(self.SR * 10, dtype=np.float32), self.SR))
        self.assertIsNone(pd.estimate_bpm(np.zeros(self.SR * 2, dtype=np.float32), self.SR))

    def test_deterministic(self):
        ct = self._click_track(128)
        self.assertEqual(pd.estimate_bpm(ct, self.SR), pd.estimate_bpm(ct, self.SR))


class TempoAndBeatTests(unittest.TestCase):
    SR = 16000

    def _click_track(self, bpm, secs=20.0, phase=0.0):
        n = int(self.SR * secs)
        sig = np.zeros(n, dtype=np.float32)
        period = int(self.SR * 60.0 / bpm)
        click = np.exp(-np.linspace(0, 8, int(self.SR * 0.03))).astype(np.float32)
        start0 = int(self.SR * phase)
        for start in range(start0, n - click.size, period):
            sig[start:start + click.size] += click
        return sig

    def test_returns_bpm_and_beat(self):
        res = pd.estimate_tempo_and_beat(self._click_track(120), self.SR)
        self.assertIsNotNone(res)
        ratios = [res["bpm"] / 120.0, res["bpm"] / 60.0, res["bpm"] / 240.0]
        self.assertTrue(any(abs(r - 1.0) < 0.04 for r in ratios))
        self.assertIsNotNone(res["first_beat"])
        self.assertTrue(0.0 <= res["confidence"] <= 1.0)

    def test_bpm_is_precise(self):
        # Sub-BPM precision (parabolic interpolation) — critical so an N-bar loop
        # doesn't drift. Use tempos that don't land on integer autocorr lags.
        for bpm in (98.0, 117.3, 123.5, 140.7):
            est = pd.estimate_tempo_and_beat(self._click_track(bpm, secs=24), self.SR)["bpm"]
            # accept the exact tempo or a clean octave, within 0.6 BPM
            cands = [est, est / 2.0, est * 2.0]
            self.assertTrue(any(abs(c - bpm) < 0.6 for c in cands), f"bpm={bpm} est={est}")

    def test_first_beat_tracks_phase(self):
        # A click track offset by ~0.25s: first_beat (mod beat) should sit near
        # the clicks, not at 0. (Downbeat phase is within one bar.)
        res = pd.estimate_tempo_and_beat(self._click_track(120, phase=0.25), self.SR)
        self.assertIsNotNone(res)
        beat = 60.0 / res["bpm"]
        fb_mod = res["first_beat"] % beat
        # distance to the true 0.25s click phase (circular within a beat)
        d = min(abs(fb_mod - 0.25), beat - abs(fb_mod - 0.25))
        self.assertLess(d, 0.08)


class BeatAlignedLoopTests(unittest.TestCase):
    def test_exact_n_bars_and_downbeat(self):
        # 120 BPM → beat 0.5s, bar 2.0s. first_beat 0.1s.
        ls, le = pd.beat_aligned_loop(start_hint=4.4, bpm=120, first_beat=0.1, bars=8)
        bar = 2.0
        self.assertAlmostEqual(le - ls, 8 * bar)          # exactly 8 bars
        n = (ls - 0.1) / bar                              # whole number of bars from first_beat
        self.assertAlmostEqual(n, round(n), places=4)
        self.assertAlmostEqual(ls, 0.1 + round((4.4 - 0.1) / bar) * bar)

    def test_fallback_without_grid(self):
        ls, le = pd.beat_aligned_loop(start_hint=3.0, bpm=120, first_beat=None, bars=4)
        self.assertAlmostEqual(ls, 3.0)
        self.assertAlmostEqual(le - ls, 4 * 2.0)  # 4 bars @120 = 8s

    def test_never_before_first_downbeat(self):
        ls, _ = pd.beat_aligned_loop(start_hint=0.0, bpm=120, first_beat=1.3, bars=4)
        self.assertGreaterEqual(ls, 1.3)

    def test_invalid(self):
        self.assertIsNone(pd.beat_aligned_loop(0.0, 0, 0.0, 8))
        self.assertIsNone(pd.beat_aligned_loop(0.0, 120, 0.0, 0))


if __name__ == "__main__":
    unittest.main()
