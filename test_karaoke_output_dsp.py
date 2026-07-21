"""Karaoke EQ/master DSP runs at OUTPUT time for instant live response.

Before this, the transport applied EQ/master when queueing decoded audio, but
it buffers several seconds ahead for gapless seeks, so a live EQ/master change
was not audible until that buffer drained (~4s) — while BGM's BASS DSP runs at
the output and responds instantly. The user read this as "advanced audio DSP
works for BGM, not karaoke" (2026-07-20). The DSP now runs in the feeder as
each block reaches the device. These tests pin: byte-exact passthrough when no
DSP is attached, correct processing through the feeder, and instant response
to a mid-stream gain change.
"""

import os
import sys
import unittest

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QIODevice

from python_karaoke_transport import PythonKaraokeTransport, _PcmFeeder
from singws_eq import GraphicEQ

_APP = QApplication.instance() or QApplication(["test"])
SR = 48000


def _tone(seconds=2.0, freq=440.0, amp=0.3):
    n = int(SR * seconds)
    t = np.arange(n) / SR
    mono = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.repeat(mono[:, None], 2, axis=1).ravel().astype(np.float32).tobytes()


class KaraokeOutputDspTests(unittest.TestCase):
    def _transport_with(self, raw, eq=None, master=None):
        t = PythonKaraokeTransport("/dev/null", mode="audio", probe_duration_on_init=False)
        t.eq = eq
        t.master = master
        feeder = _PcmFeeder(t)
        feeder.open(QIODevice.OpenModeFlag.ReadOnly)
        with t._pcm_lock:
            step = 9600 * 4
            for i in range(0, len(raw), step):
                t._pcm_chunks.append(raw[i:i + step])
                t._pcm_bytes += len(raw[i:i + step])
        return t, feeder

    def _drain(self, feeder, total_bytes, read_size=9600 * 4):
        out = bytearray()
        while len(out) < total_bytes:
            d = feeder.readData(read_size)
            if not d:
                break
            out += d
        return np.frombuffer(bytes(out[:total_bytes]), dtype=np.float32)

    def test_passthrough_is_byte_exact_without_dsp(self):
        raw = _tone()
        t, feeder = self._transport_with(raw)
        out = self._drain(feeder, len(raw))
        self.assertTrue(np.array_equal(out, np.frombuffer(raw, dtype=np.float32)))

    def test_passthrough_exact_with_non_frame_aligned_reads(self):
        raw = _tone()
        t, feeder = self._transport_with(raw)
        # +13 bytes = deliberately not a whole-frame multiple; the carry logic
        # must still reproduce the input exactly.
        out = self._drain(feeder, len(raw), read_size=9600 * 4 + 13)
        self.assertTrue(np.allclose(out, np.frombuffer(raw, dtype=np.float32), atol=1e-7))

    def test_eq_is_applied_through_the_feeder(self):
        raw = _tone(freq=60.0)  # low tone; +12 dB low bands boost it
        eq = GraphicEQ(sample_rate=SR, channels=2)
        eq.set_all_gains_db([12.0] * 4 + [0.0] * 6)
        eq.set_enabled(True)
        t, feeder = self._transport_with(raw, eq=eq)
        out = self._drain(feeder, len(raw))
        base = np.frombuffer(raw, dtype=np.float32)
        self.assertGreater(_rms(out), _rms(base) * 1.5)

    def test_live_gain_change_affects_the_next_block(self):
        raw = _tone(seconds=4.0)
        eq = GraphicEQ(sample_rate=SR, channels=2)
        eq.set_all_gains_db([0.0] * 10)
        eq.set_enabled(True)
        t, feeder = self._transport_with(raw, eq=eq)
        first = np.frombuffer(feeder.readData(9600 * 4), dtype=np.float32)
        eq.set_all_gains_db([12.0] * 10)  # boost NOW
        nxt = np.frombuffer(feeder.readData(9600 * 4), dtype=np.float32)
        # Instant: the very next block is already much louder (no multi-second
        # buffer lag). Flat block ~ unchanged, boosted block clearly up.
        self.assertGreater(_rms(nxt), _rms(first) * 1.5)

    def test_queue_pcm_no_longer_processes(self):
        # Queued audio must be the raw stream now (DSP moved to the feeder), so
        # a strong EQ leaves _pcm_chunks untouched.
        raw = _tone(freq=60.0)
        eq = GraphicEQ(sample_rate=SR, channels=2)
        eq.set_all_gains_db([12.0] * 10)
        eq.set_enabled(True)
        t = PythonKaraokeTransport("/dev/null", mode="audio", probe_duration_on_init=False)
        t.eq = eq
        t._queue_pcm(raw)
        with t._pcm_lock:
            queued = b"".join(t._pcm_chunks)
        self.assertEqual(queued, raw)


def _rms(a):
    return float(np.sqrt(np.mean(a.astype(np.float64) ** 2))) if a.size else 0.0


if __name__ == "__main__":
    unittest.main()
