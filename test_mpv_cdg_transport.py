"""Tests for the libmpv high-quality CDG transport.

Two layers:
  * Pure logic (no libmpv): pitch math and the audio-filter (af) chain built
    from the host's GraphicEQ, normalization gain, and key — these decide the
    exact mpv filter string, so they're worth pinning.
  * Integration (opt-in, non-macOS only for now): play a synthetic CDG + tone
    MP3 through the real transport headless (null audio), and assert it
    software-renders frames, advances the clock, reports times, and applies
    live modifier/EQ changes. Homebrew mpv 0.41 can abort inside AppKit/Touch
    Bar setup on macOS, so these tests stay skipped there until that path is
    made process-safe.

The transport is selected only when CDG quality is "high" and libmpv loads; the
standard path stays on GStreamer. That wiring lives in the main app; here we
cover the transport itself.
"""

import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")
os.environ.setdefault("SINGWS_MPV_SILENT", "1")  # integration: use null audio
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import mpv_cdg_transport as M


class FakeEq:
    def __init__(self, gains, enabled=True):
        self._gains = list(gains)
        self._enabled = enabled

    def enabled(self):
        return self._enabled

    def is_flat(self):
        return all(abs(g) < 1e-9 for g in self._gains)

    def gains_db(self):
        return list(self._gains)


def bare_transport():
    """A transport instance WITHOUT libmpv (via __new__) for pure-logic tests."""
    t = M.MpvCdgTransport.__new__(M.MpvCdgTransport)
    t.eq = None
    t._normalize_gain_db = 0.0
    t.semitones = 0.0
    t.tempo_ratio = 1.0
    return t


class PitchMathTests(unittest.TestCase):
    def test_zero_is_unity(self):
        self.assertAlmostEqual(M.pitch_scale_for_semitones(0), 1.0, places=9)

    def test_octave_up(self):
        self.assertAlmostEqual(M.pitch_scale_for_semitones(12), 2.0, places=6)

    def test_octave_down(self):
        self.assertAlmostEqual(M.pitch_scale_for_semitones(-12), 0.5, places=6)

    def test_monotonic(self):
        vals = [M.pitch_scale_for_semitones(s) for s in range(-12, 13)]
        self.assertEqual(vals, sorted(vals))


class AfChainTests(unittest.TestCase):
    def test_flat_eq_no_gain_no_key_is_empty(self):
        t = bare_transport()
        self.assertEqual(t._build_af(), "")

    def test_ten_band_eq_maps_each_nonzero_band(self):
        t = bare_transport()
        gains = [6, 0, -3, 0, 2, 0, 0, 4, 0, -1]
        t.eq = FakeEq(gains)
        af = t._build_af()
        parts = af.split(",")
        # one equalizer node per nonzero band
        eqs = [p for p in parts if p.startswith("equalizer=")]
        self.assertEqual(len(eqs), sum(1 for g in gains if g))
        # band centre frequencies + gains are present
        self.assertIn("equalizer=f=31.5:t=o:w=1:g=6.00", af)
        self.assertIn("equalizer=f=125.0:t=o:w=1:g=-3.00", af)
        self.assertIn("equalizer=f=4000.0:t=o:w=1:g=4.00", af)

    def test_disabled_eq_is_ignored(self):
        t = bare_transport()
        t.eq = FakeEq([6, 6, 6, 6, 6, 6, 6, 6, 6, 6], enabled=False)
        self.assertEqual(t._build_af(), "")

    def test_normalization_gain_adds_volume(self):
        t = bare_transport()
        t._normalize_gain_db = -4.2
        self.assertIn("volume=volume=-4.20dB", t._build_af())

    def test_tiny_gain_is_dropped(self):
        t = bare_transport()
        t._normalize_gain_db = 0.01
        self.assertNotIn("volume", t._build_af())

    def test_key_adds_rubberband_pitch(self):
        t = bare_transport()
        t.semitones = 2
        af = t._build_af()
        self.assertIn("rubberband=pitch-scale=", af)
        scale = float(af.split("pitch-scale=")[1])
        self.assertAlmostEqual(scale, M.pitch_scale_for_semitones(2), places=4)

    def test_full_chain_order_is_eq_then_volume_then_pitch(self):
        t = bare_transport()
        t.eq = FakeEq([3] + [0] * 9)
        t._normalize_gain_db = -2.0
        t.semitones = -1
        af = t._build_af()
        i_eq = af.index("equalizer=")
        i_vol = af.index("volume=")
        i_pitch = af.index("rubberband=")
        self.assertLess(i_eq, i_vol)
        self.assertLess(i_vol, i_pitch)


class MpvAvailabilityTests(unittest.TestCase):
    def test_returns_bool(self):
        self.assertIsInstance(M.mpv_available(), bool)

    def test_experimental_renderer_is_disabled_by_default(self):
        old = os.environ.pop("SINGWS_ENABLE_EXPERIMENTAL_MPV_CDG", None)
        old_run = os.environ.pop("SINGWS_RUN_MPV_INTEGRATION", None)
        try:
            self.assertFalse(M.mpv_available())
        finally:
            if old is not None:
                os.environ["SINGWS_ENABLE_EXPERIMENTAL_MPV_CDG"] = old
            if old_run is not None:
                os.environ["SINGWS_RUN_MPV_INTEGRATION"] = old_run


def _write_test_cdg(path: str, seconds: int = 8):
    """Minimal valid CDG: memory preset + palette, then a visible tile/sec."""
    def pkt(instr, data16):
        p = bytearray(24)
        p[0] = 0x09
        p[1] = instr
        p[4:4 + len(data16)] = data16
        return bytes(p)

    empty = bytes(24)
    stream = [empty] * 150
    stream.append(pkt(1, bytes([0, 0] + [0] * 14)))
    stream.append(pkt(30, struct.pack(">8H", *[0x0FFF] * 8)))
    for sec in range(1, seconds):
        stream += [empty] * (300 - 1)
        tile = bytes([0, 1, 5 + (sec % 10), 5 + (sec % 20)] + [0x3F] * 12)
        stream.append(pkt(6, tile))
    Path(path).write_bytes(b"".join(stream))


@unittest.skipUnless(
    sys.platform != "darwin"
    and os.environ.get("SINGWS_RUN_MPV_INTEGRATION") == "1"
    and M.mpv_available()
    and shutil.which("ffmpeg"),
    "set SINGWS_RUN_MPV_INTEGRATION=1 with libmpv + ffmpeg available on non-macOS",
)
class MpvCdgPlaybackTests(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cdg = os.path.join(cls.tmp.name, "test.cdg")
        cls.mp3 = os.path.join(cls.tmp.name, "test.mp3")
        _write_test_cdg(cls.cdg, seconds=8)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
             "-c:a", "libmp3lame", cls.mp3],
            check=True, timeout=120,
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _pump(self, transport, seconds, frames):
        t0 = time.time()
        while time.time() - t0 < seconds:
            transport._tick()
            self.app.processEvents()
            time.sleep(0.03)

    def test_plays_renders_frames_and_advances_clock(self):
        frames = []
        t = M.MpvCdgTransport(self.mp3, video_path=self.cdg, mode="cdg")
        t.max_video_height = 480
        t.frame_ready.connect(lambda img: frames.append(img))
        t.set_modifiers(1.0, 0)
        t.start(0.0)
        try:
            self._pump(t, 4.0, frames)
            self.assertGreater(len(frames), 0, "no frames rendered")
            img = frames[-1]
            self.assertGreater(img.width(), 300)   # upscaled beyond 300x216
            dur_ns, pos_ns = t.query_times_ns()
            self.assertIsNotNone(pos_ns)
            self.assertGreater(pos_ns, 0)
            d = t.diagnostics()
            self.assertEqual(d["engine"], "mpv")
            self.assertGreater(d["frames_delivered"], 0)
        finally:
            t.stop()

    def test_live_modifiers_and_eq_update_af(self):
        t = M.MpvCdgTransport(self.mp3, video_path=self.cdg, mode="cdg")
        t.start(0.0)
        try:
            self._pump(t, 1.0, [])
            t.eq = FakeEq([5, 0, 0, 0, 0, 0, 0, 0, 0, 3])
            t.normalize_gain_db = -3.0
            t.set_modifiers(1.1, 3)
            t._tick()
            af = t._af_applied or ""
            self.assertIn("equalizer=", af)
            self.assertIn("volume=volume=-3.00dB", af)
            self.assertIn("rubberband=pitch-scale=", af)
            self.assertAlmostEqual(float(t._get("speed") or 0), 1.1, places=2)
        finally:
            t.stop()

    def test_seek_bumps_generation(self):
        t = M.MpvCdgTransport(self.mp3, video_path=self.cdg, mode="cdg")
        t.start(0.0)
        try:
            self._pump(t, 0.6, [])
            g0 = t.cdg_generation()
            t.seek(3.0)
            self.assertEqual(t.cdg_generation(), g0 + 1)
        finally:
            t.stop()


if __name__ == "__main__":
    unittest.main()
