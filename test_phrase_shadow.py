import os
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import phrase_detect
import phrase_shadow

_DYLIB = Path(os.environ.get("SINGWS_DSP_LIB") or os.path.expanduser("~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))


def _song(seconds=14.0, sr=16000, bpm=120.0):
    rng = np.random.default_rng(7)
    n = int(sr * seconds)
    x = np.zeros(n, dtype=np.float64)
    beat = 60.0 / bpm
    k = 0
    while k * beat < seconds - 0.2:
        i = int(k * beat * sr)
        ln = min(int(0.12 * sr), n - i)
        x[i:i + ln] += 0.8 * np.sin(2 * np.pi * 60 * np.arange(ln) / sr) * np.exp(-np.arange(ln) / (0.03 * sr))
        j = i + int(beat / 2 * sr)
        ln = min(int(0.04 * sr), n - j)
        if ln > 0:
            x[j:j + ln] += 0.25 * rng.standard_normal(ln) * np.exp(-np.arange(ln) / (0.01 * sr))
        k += 1
    x += 0.2 * np.sin(2 * np.pi * 110.0 * np.arange(n) / sr)
    x[n // 2:] *= 2.0                                   # a louder second half
    return x.astype(np.float32), sr


class Capture:
    def __init__(self):
        self.lines = []

    def __call__(self, text):
        self.lines.append(text)

    def mismatches(self):
        return [l for l in self.lines if "MISMATCH" in l]

    def decisions(self):
        return [l for l in self.lines if "decision=" in l]


class ShadowBase(unittest.TestCase):
    def setUp(self):
        self.log = Capture()
        phrase_shadow._lib = None
        phrase_shadow._loaded = False
        phrase_shadow._enabled = False

    def tearDown(self):
        phrase_shadow._enabled = False


@unittest.skipUnless(_DYLIB.exists(), "needs the built Rust library with singws_phrase_json")
class RealLibraryTests(ShadowBase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"SINGWS_DSP_LIB": str(_DYLIB)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_editors_analysis_of_a_song_agrees_with_the_port_and_logs_one_row_per_decision(self):
        pcm, sr = _song()
        phrase_shadow.configure(True, self.log)
        self.assertIn("enabled available=1", self.log.lines[0])
        res = phrase_detect.estimate_tempo_and_beat(pcm, sr)
        phrase_shadow.compare_tempo(pcm, sr, res, 5.0)
        peaks = phrase_detect.peaks(pcm, 600)
        bpm = phrase_detect.estimate_bpm(pcm, sr)
        suggested = phrase_detect.suggest_start(pcm, sr, bpm=bpm)
        sections = phrase_detect.detect_sections(pcm, sr, bpm=bpm)
        phrase_shadow.compare_waveform(pcm, sr, 600, None, peaks, bpm, -1.0 if suggested is None else suggested, sections, 20.0)
        self.assertEqual(self.log.mismatches(), [], self.log.lines)
        names = [l.split("decision=")[1].split()[0] for l in self.log.decisions()]
        self.assertEqual(sorted(set(names)), ["peaks", "sections", "suggest", "tempo"])
        self.assertTrue(all("agree=1" in l for l in self.log.decisions()), self.log.lines)

    def test_a_wrong_answer_from_python_is_logged_as_a_mismatch_with_the_numbers_but_no_song_text(self):
        pcm, sr = _song()
        phrase_shadow.configure(True, self.log)
        res = phrase_detect.estimate_tempo_and_beat(pcm, sr)
        wrong = dict(res, bpm=res["bpm"] + 7.0)
        phrase_shadow.compare_tempo(pcm, sr, wrong)
        self.assertEqual(len(self.log.mismatches()), 1)
        self.assertIn("bpm py=", self.log.mismatches()[0])

    def test_nothing_is_run_when_the_setting_is_off(self):
        pcm, sr = _song(4.0)
        phrase_shadow.configure(False, self.log)
        with mock.patch.object(phrase_shadow, "_rust", side_effect=AssertionError("must not run")):
            phrase_shadow.compare_tempo(pcm, sr, None)
            phrase_shadow.compare_waveform(pcm, sr, 100, None, None, 0.0, -1.0, [])
        self.assertEqual(self.log.lines, [])


class NoLibraryTests(ShadowBase):
    def test_a_missing_library_is_a_silent_no_op(self):
        with mock.patch.dict(os.environ, {"SINGWS_DSP_LIB": "/nonexistent/lib.dylib"}), mock.patch("rust_master_dsp._candidate_paths", return_value=[Path("/nonexistent/lib.dylib")]):
            phrase_shadow.configure(True, self.log)
            phrase_shadow.compare_tempo(np.zeros(100, dtype=np.float32), 16000, None)
        self.assertEqual([l for l in self.log.lines if "decision=" in l or "MISMATCH" in l], [])

    def test_an_error_inside_the_comparison_never_escapes(self):
        phrase_shadow._enabled = True
        with mock.patch.object(phrase_shadow, "enabled", return_value=True), mock.patch.object(phrase_shadow, "_rust", side_effect=RuntimeError("boom")):
            phrase_shadow._log = self.log
            phrase_shadow.compare_tempo(np.zeros(100, dtype=np.float32), 16000, None)
            phrase_shadow.compare_waveform(np.zeros(100, dtype=np.float32), 16000, 10, None, np.zeros((10, 2)), 100.0, 1.0, [])
        self.assertTrue(any("error" in l for l in self.log.lines))

    def test_the_documented_zero_versus_none_suggestion_is_an_agreement_marked_known(self):
        phrase_shadow._enabled = True
        phrase_shadow._log = self.log
        with mock.patch.object(phrase_shadow, "enabled", return_value=True), mock.patch.object(phrase_shadow, "_rust", return_value=(None, 1.0)):
            phrase_shadow.compare_waveform(np.zeros(100, dtype=np.float32), 16000, 10, 100.0, None, 100.0, 0.0, [])
        suggest = [l for l in self.log.lines if "decision=suggest" in l]
        self.assertEqual(len(suggest), 1)
        self.assertIn("agree=1", suggest[0])
        self.assertIn("known=none_vs_zero", suggest[0])


if __name__ == "__main__":
    unittest.main()
