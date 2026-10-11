"""Key and tempo run through mpv's `signalsmith` audio filter (Signalsmith Stretch). The operator's hard rule: a tempo change must never move
the key. These tests drive the real libmpv runtime with pure tones (skipped when the runtime has no such filter)."""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
MPV_DIR = Path(os.environ.get("SINGWS_MPV_FRAMEWORKS") or ROOT / "native/mpv_runtime/artifacts/arm64/Frameworks")
LIB = MPV_DIR / "singws_libmpv.2.dylib"


def _has_filter() -> bool:
    try:
        return LIB.exists() and b"Signalsmith Stretch" in LIB.read_bytes()
    except OSError:
        return False


class KeyEngineSettingTests(unittest.TestCase):
    def test_source_wiring(self):
        main = (ROOT / "0.2.18.1.py").read_text()
        self.assertIn('"key_engine": "signalsmith"', main)
        self.assertIn("SINGWS_KEY_ENGINE", main)
        core = (ROOT / "rust/crates/singws-video/src/core.rs").read_text()
        self.assertIn("fn stretch_engine()", core)
        # a libmpv without the filter must not lose the whole DSP chain
        self.assertIn("using rubberband", core)


@unittest.skipUnless(_has_filter(), "needs a libmpv runtime built with the signalsmith filter")
class IndependenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["SINGWS_MPV_FRAMEWORKS"] = str(MPV_DIR)
        os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-keyengine-"))
        import numpy as np
        import stretch_pitch_check as T
        cls.np, cls.T = np, T
        t = np.arange(int(T.RATE * 6)) / T.RATE
        cls.src = os.path.join(tempfile.mkdtemp(prefix="singws-keysrc-"), "tone.wav")
        T.write_wav(cls.src, 0.3 * np.sin(2 * np.pi * T.F1 * t) + 0.2 * np.sin(2 * np.pi * T.F2 * t))

    def _err(self, semitones, speed):
        T, np = self.T, self.np
        y = T.run("signalsmith", self.src, semitones, speed)
        mid = y[len(y) // 4: 3 * len(y) // 4]
        pitch = 2 ** (semitones / 12.0)
        e = T.cents(T.peak_hz(mid, T.F1 * pitch * 0.7, T.F1 * pitch * 1.4), T.F1 * pitch)
        ratio = (len(y) / T.RATE) / (6.0 / speed)
        return e, ratio

    def test_tempo_alone_never_moves_the_key(self):
        for speed in (0.5, 0.75, 0.9, 1.1, 1.5, 2.0):
            e, ratio = self._err(0, speed)
            self.assertLess(abs(e), 1.0, (speed, e))
            self.assertAlmostEqual(ratio, 1.0, delta=0.03)

    def test_key_alone_is_the_requested_shift_and_keeps_the_length(self):
        for semis in (-12, -5, 2, 7, 12):
            e, ratio = self._err(semis, 1.0)
            self.assertLess(abs(e), 6.0, (semis, e))
            self.assertAlmostEqual(ratio, 1.0, delta=0.03)

    def test_key_and_tempo_together_are_both_exact(self):
        for semis, speed in ((2, 0.9), (-3, 1.1), (5, 0.75)):
            e, ratio = self._err(semis, speed)
            self.assertLess(abs(e), 6.0, (semis, speed, e))
            self.assertAlmostEqual(ratio, 1.0, delta=0.03)

    def test_live_pitch_command_changes_the_key_without_rebuilding_the_chain(self):
        """The bridge sends `af-command stretch set-pitch` to the RUNNING filter (a chain rebuild would restart the stretcher = a click)."""
        import time
        from libmpv_media_jobs import OfflineMpvJob
        T, np = self.T, self.np
        out = os.path.join(tempfile.mkdtemp(prefix="singws-live-"), "o.wav")
        job = OfflineMpvJob()
        try:
            for k, v in (("config", "no"), ("vid", "no"), ("ao", "pcm"), ("ao-pcm-file", out), ("ao-pcm-waveheader", "yes"),
                         ("audio-format", "s16"), ("audio-samplerate", str(T.RATE)), ("audio-channels", "stereo"),
                         ("pause", "yes"), ("af", "@stretch:signalsmith=pitch-scale=1.000000")):
                job.option(k, v)
            job.initialize()
            job.command("loadfile", self.src, "replace")
            time.sleep(1.0)
            job.command("af-command", "stretch", "set-pitch", f"{2 ** (3 / 12.0):.6f}")
            job.command("set", "pause", "no")
            job.wait_for_end(60.0)
        finally:
            job.close()
        y = T.read_wav_mono(out)
        mid = y[len(y) // 4: 3 * len(y) // 4]
        got = T.peak_hz(mid, 300, 900)
        self.assertAlmostEqual(T.cents(got, T.F1 * 2 ** (3 / 12.0)), 0.0, delta=8, msg=got)


if __name__ == "__main__":
    unittest.main()
