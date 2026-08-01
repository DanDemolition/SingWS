"""Karaoke EQ/master DSP stays close to output without blocking Qt's callback.

Before this, the transport applied EQ/master when queueing decoded audio, but
it buffers several seconds ahead for gapless seeks, so a live EQ/master change
was not audible until that buffer drained (~4s). Applying it in QIODevice's
read callback fixed latency but stalled the Intel macOS GUI because Qt invokes
that callback on the main thread there. A bounded worker now processes only a
short distance ahead. These tests pin byte-exact passthrough, correct DSP, and
bounded response to live changes.
"""

import os
import sys
import time
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
        self.addCleanup(t.stop)
        step = 9600 * 4
        for i in range(0, len(raw), step):
            t._queue_pcm(raw[i:i + step])
        return t, feeder

    @staticmethod
    def _wait_for_output(t, timeout=2.0):
        deadline = time.monotonic() + timeout
        with t._pcm_lock:
            while (
                t._processed_bytes <= 0
                and not t._pending_output
                and time.monotonic() < deadline
            ):
                t._pcm_lock.wait(timeout=0.02)
            return t._processed_bytes > 0 or bool(t._pending_output)

    def _drain(self, feeder, total_bytes, read_size=9600 * 4):
        out = bytearray()
        while len(out) < total_bytes:
            self.assertTrue(self._wait_for_output(feeder._t))
            with feeder._t._pcm_lock:
                available = feeder._t._processed_bytes + len(feeder._t._pending_output)
            # The real audio device pulls at wall-clock pace. Tests drain much
            # faster, so request only bytes the worker has prepared; otherwise
            # the feeder correctly interprets the synthetic burst as an
            # underrun and pads it with silence.
            request_bytes = min(read_size, available, total_bytes - len(out))
            d = feeder.readData(request_bytes)
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
        self.assertTrue(self._wait_for_output(t))
        first = np.frombuffer(feeder.readData(9600 * 4), dtype=np.float32)
        eq.set_all_gains_db([12.0] * 10)  # boost NOW
        later = []
        for _ in range(5):
            self.assertTrue(self._wait_for_output(t))
            later.append(np.frombuffer(feeder.readData(9600 * 4), dtype=np.float32))
        # The worker is capped near 170ms, so the change reaches one of the next
        # few 100ms blocks rather than sitting behind the 4s decoder queue.
        self.assertGreater(max(_rms(block) for block in later), _rms(first) * 1.5)

    def test_dsp_is_prepared_before_the_feeder_reads(self):
        raw = _tone(freq=60.0)
        eq = GraphicEQ(sample_rate=SR, channels=2)
        eq.set_all_gains_db([12.0] * 10)
        eq.set_enabled(True)
        t = PythonKaraokeTransport("/dev/null", mode="audio", probe_duration_on_init=False)
        self.addCleanup(t.stop)
        t.eq = eq
        t._queue_pcm(raw)
        self.assertTrue(self._wait_for_output(t))
        with t._pcm_lock:
            queued = b"".join(t._processed_chunks)
        self.assertNotEqual(queued, raw[:len(queued)])


def _rms(a):
    return float(np.sqrt(np.mean(a.astype(np.float64) ** 2))) if a.size else 0.0


if __name__ == "__main__":
    unittest.main()
