"""Regression: the native BASS DX8 BGM EQ must use audible octave-wide bands.

DX8 PARAMEQ bandwidth is measured in SEMITONES (1-36). The bands sit an
octave apart, so each needs ~12 semitones; with fBandwidth=1.0 the "graphic
EQ" was ten surgical notches that did nothing audible on real music
(2026-07-20: "BGM EQ not working"). This test plays a tone BETWEEN band
centers (700 Hz) through the real BASS mixer and requires the low-cut curve
to attenuate it clearly — narrow notches fail this, octave bands pass.
"""

import ctypes
import math
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

from singws_eq import GraphicEQ


def _bass_available():
    try:
        from bass_background_engine import BassBackgroundEngine
        engine = BassBackgroundEngine()
        engine.close()
        return True
    except Exception:
        return False


@unittest.skipUnless(_bass_available(), "BASS runtime/output unavailable")
class NativeEqBandwidthTests(unittest.TestCase):
    SR = 48000

    def _tone(self, tmp, freq):
        path = Path(tmp) / f"tone{freq}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(self.SR)
            chunk = bytearray()
            for i in range(self.SR * 3):
                v = int(0.5 * 32767.0 * math.sin(2.0 * math.pi * freq * i / self.SR))
                chunk += struct.pack("<hh", v, v)
            wav.writeframes(bytes(chunk))
        return path

    def _mixer_rms(self, engine):
        n = 8192
        buf = (ctypes.c_float * n)()
        got = engine.bass.BASS_ChannelGetData(engine.mixer, buf, (n * 4) | 0x40000000)
        if got in (-1, 0):
            return -1.0
        m = min(got // 4, n)
        return math.sqrt(sum(buf[i] * buf[i] for i in range(m)) / max(1, m))

    def test_off_center_tone_is_attenuated_by_low_cut(self):
        from bass_background_engine import BassBackgroundEngine

        engine = BassBackgroundEngine()
        self.addCleanup(engine.close)
        engine.set_master_volume(0.002)  # keep the test effectively silent

        cut = GraphicEQ(sample_rate=self.SR, channels=2)
        cut.set_all_gains_db([-12.0] * 6 + [0.0] * 4)
        cut.set_enabled(True)

        with tempfile.TemporaryDirectory() as tmp:
            tone = self._tone(tmp, 700)  # between the 500 and 1000 Hz centers
            results = {}
            for label, eq in (("off", None), ("cut", cut)):
                engine.set_eq(eq)
                engine.load(str(tone), paused=False)
                time.sleep(0.5)
                vals = [v for v in (self._mixer_rms(engine) for _ in range(5)) if v >= 0]
                engine.stop()
                self.assertTrue(vals, "mixer produced no measurable data")
                results[label] = sum(vals) / len(vals)

        delta_db = 20 * math.log10(results["cut"] / results["off"])
        self.assertLessEqual(
            delta_db, -6.0,
            f"native EQ must audibly cut between band centers; measured {delta_db:+.1f} dB "
            "(narrow notch bandwidth regression?)",
        )


if __name__ == "__main__":
    unittest.main()
