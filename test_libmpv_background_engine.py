"""Tests for the libmpv/Qt background-music recovery engine.

The engine is constructed with ``create_sink=False`` and the tests pump
``mix_block`` directly, so everything here runs without audio hardware and
without GStreamer.
"""

import importlib.util
import inspect
import math
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

import numpy as np

from libmpv_background_engine import (
    CHANNELS,
    LibmpvBackgroundEngine,
    SAMPLE_RATE,
)


def _write_tone_wav(path: Path, freq: float, seconds: float = 2.0, amp: float = 0.5):
    frames = int(SAMPLE_RATE * seconds)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        chunk = bytearray()
        for i in range(frames):
            value = int(amp * 32767.0 * math.sin(2.0 * math.pi * freq * i / SAMPLE_RATE))
            chunk += struct.pack("<hh", value, value)
        wav.writeframes(bytes(chunk))


def _wait_for_buffer(deck, min_frames: int = SAMPLE_RATE // 4, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with deck.cond:
            if deck.buffered_frames >= min_frames or deck.eof:
                return True
        time.sleep(0.01)
    return False


class _DspStub:
    """Minimal EQ/master-processor double matching the BASS DSP contract."""

    def __init__(self, scale: float):
        self.scale = float(scale)
        self.configured = None
        self.blocks = 0

    def configure_stream(self, sample_rate, channels):
        self.configured = (int(sample_rate), int(channels))

    def process_f32_array(self, frames):
        self.blocks += 1
        return frames * self.scale


class LibmpvBackgroundEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tone_a = Path(cls.tmp.name) / "tone_a.wav"
        cls.tone_b = Path(cls.tmp.name) / "tone_b.wav"
        _write_tone_wav(cls.tone_a, 440.0)
        _write_tone_wav(cls.tone_b, 880.0)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _engine(self):
        engine = LibmpvBackgroundEngine(create_sink=False)
        self.addCleanup(engine.close)
        return engine

    def _mix_seconds(self, engine, seconds, chunk_frames=4800):
        total = int(SAMPLE_RATE * seconds)
        out = []
        while total > 0:
            take = min(chunk_frames, total)
            out.append(engine.mix_block(take))
            total -= take
        return np.concatenate(out) if out else np.zeros((0, CHANNELS), np.float32)

    def test_module_has_no_gstreamer_imports(self):
        import libmpv_background_engine as module

        source = inspect.getsource(module)
        self.assertNotIn("import gi", source)
        self.assertNotIn("Gst", source)

    def test_load_play_advances_position_and_meter(self):
        engine = self._engine()
        engine.load(str(self.tone_a), paused=True, volume=0.8)
        self.assertTrue(_wait_for_buffer(engine.primary))
        self.assertFalse(engine.is_playing())
        self.assertTrue(engine.is_paused())
        self.assertTrue(engine.play())
        audio = self._mix_seconds(engine, 0.5)
        self.assertGreater(float(np.max(np.abs(audio))), 0.1)
        pos, dur = engine.get_times()
        self.assertAlmostEqual(pos, 0.5, delta=0.05)
        self.assertAlmostEqual(dur, 2.0, delta=0.1)
        self.assertGreater(engine.meter_level(), 0.1)
        self.assertFalse(engine.source_ended())

    def test_paused_engine_mixes_silence_without_consuming(self):
        engine = self._engine()
        engine.load(str(self.tone_a), paused=True)
        self.assertTrue(_wait_for_buffer(engine.primary))
        audio = self._mix_seconds(engine, 0.2)
        self.assertEqual(float(np.max(np.abs(audio))), 0.0)
        pos, _dur = engine.get_times()
        self.assertEqual(pos, 0.0)

    def test_seek_repositions_without_stale_audio(self):
        engine = self._engine()
        engine.load(str(self.tone_a), paused=False)
        self.assertTrue(_wait_for_buffer(engine.primary))
        self._mix_seconds(engine, 0.25)
        self.assertTrue(engine.seek(1.0))
        pos, _dur = engine.get_times()
        self.assertAlmostEqual(pos, 1.0, delta=0.01)
        self.assertTrue(_wait_for_buffer(engine.primary))
        self._mix_seconds(engine, 0.25)
        pos, _dur = engine.get_times()
        self.assertAlmostEqual(pos, 1.25, delta=0.05)

    def test_source_ended_after_draining_track(self):
        engine = self._engine()
        engine.load(str(self.tone_a), paused=False)
        self.assertTrue(engine.seek(1.9))
        deadline = time.monotonic() + 5.0
        while not engine.source_ended() and time.monotonic() < deadline:
            _wait_for_buffer(engine.primary, min_frames=1, timeout=0.5)
            engine.mix_block(4800)
        self.assertTrue(engine.source_ended())

    def test_crossfade_slides_decks_and_completes(self):
        engine = self._engine()
        engine.load(str(self.tone_a), paused=False)
        self.assertTrue(_wait_for_buffer(engine.primary))
        self.assertTrue(engine.start_crossfade(str(self.tone_b), 100, norm_gain=1.5))
        self.assertIsNotNone(engine.secondary)
        self.assertTrue(_wait_for_buffer(engine.secondary))
        self._mix_seconds(engine, 0.2)
        self.assertAlmostEqual(engine.primary.gain_current, 0.0, delta=0.01)
        self.assertAlmostEqual(engine.secondary.gain_current, 1.0, delta=0.01)
        promoted = engine.secondary
        self.assertTrue(engine.complete_crossfade())
        self.assertIs(engine.primary, promoted)
        self.assertIsNone(engine.secondary)
        self.assertEqual(promoted.path, str(self.tone_b))
        self.assertAlmostEqual(promoted.norm_gain, 1.5, delta=0.001)

    def test_cancel_crossfade_restores_primary(self):
        engine = self._engine()
        engine.load(str(self.tone_a), paused=False)
        self.assertTrue(_wait_for_buffer(engine.primary))
        self.assertTrue(engine.start_crossfade(str(self.tone_b), 3000))
        engine.cancel_crossfade()
        self.assertIsNone(engine.secondary)
        self.assertEqual(engine.primary.gain_current, 1.0)

    def test_normalize_gains_apply_per_deck(self):
        engine = self._engine()
        engine.load(str(self.tone_a), paused=False)
        engine.set_primary_normalize_gain(2.0)
        self.assertAlmostEqual(engine.primary.norm_gain, 2.0)
        # Norm factor clamps like the BASS engine.
        engine.set_primary_normalize_gain(99.0)
        self.assertAlmostEqual(engine.primary.norm_gain, 4.0)
        engine.set_primary_normalize_gain(0.0)
        self.assertAlmostEqual(engine.primary.norm_gain, 0.05)

    def test_master_volume_slide_ramps(self):
        engine = self._engine()
        engine.load(str(self.tone_a), paused=False)
        self.assertTrue(_wait_for_buffer(engine.primary))
        engine.set_master_volume(1.0)
        engine.slide_master_volume(0.0, 100)
        self._mix_seconds(engine, 0.05)
        self.assertAlmostEqual(engine._master_current, 0.5, delta=0.05)
        self._mix_seconds(engine, 0.1)
        self.assertEqual(engine._master_current, 0.0)
        self.assertEqual(engine._effective_master(), 0.0)
        audio = self._mix_seconds(engine, 0.05)
        self.assertEqual(float(np.max(np.abs(audio))), 0.0)

    def test_master_fade_uses_smooth_curve(self):
        engine = self._engine()
        engine.load(str(self.tone_a), paused=False)
        self.assertTrue(_wait_for_buffer(engine.primary))
        engine.set_master_volume(1.0)
        engine.slide_master_volume(0.0, 1000)
        self._mix_seconds(engine, 0.25)
        self.assertGreater(engine._master_current, 0.80)
        self._mix_seconds(engine, 0.25)
        self.assertAlmostEqual(engine._master_current, 0.5, delta=0.03)
        self._mix_seconds(engine, 0.25)
        self.assertLess(engine._master_current, 0.20)

    def test_master_fade_waits_for_decoded_audio(self):
        engine = self._engine()
        engine.load(str(self.tone_a), paused=False)
        engine.set_master_volume(0.0)
        engine.slide_master_volume(1.0, 1000)
        engine.primary.reader.stop()
        with engine.primary.cond:
            engine.primary.blocks.clear()
            engine.primary.buffered_frames = 0
        engine.mix_block(SAMPLE_RATE // 2)
        self.assertEqual(engine._master_current, 0.0)
        self.assertEqual(engine._master_ramp_frames, SAMPLE_RATE)

    def test_fade_settle_delay_covers_qt_audio_buffer(self):
        engine = self._engine()

        class _Sink:
            def bufferSize(self):
                return int(SAMPLE_RATE * CHANNELS * 4 * 0.2)

        engine.audio_sink = _Sink()
        self.assertGreaterEqual(engine.fade_settle_delay_ms(), 220)

    def test_eq_and_master_processor_run_in_chain(self):
        engine = self._engine()
        eq = _DspStub(0.5)
        master = _DspStub(0.5)
        engine.set_eq(eq)
        engine.set_master_processor(master)
        engine.load(str(self.tone_a), paused=False, volume=1.0)
        self.assertTrue(_wait_for_buffer(engine.primary))
        audio = self._mix_seconds(engine, 0.25)
        self.assertEqual(eq.configured, (SAMPLE_RATE, CHANNELS))
        self.assertEqual(master.configured, (SAMPLE_RATE, CHANNELS))
        self.assertGreater(eq.blocks, 0)
        self.assertGreater(master.blocks, 0)
        peak = float(np.max(np.abs(audio)))
        # 0.5 source amplitude scaled by both 0.5 stages.
        self.assertAlmostEqual(peak, 0.125, delta=0.02)


class HostRecoverySelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "singws_main_bgm_recovery", "0.2.18.1.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.singws = module

    def test_bass_failure_selects_libmpv_engine(self):
        module = self.singws

        class _StubLibmpvEngine:
            backend_name = "libmpv-Qt"

            def __init__(self, output_name=None):
                self.output_name = output_name

            def set_eq(self, eq):
                pass

            def set_master_processor(self, proc):
                pass

            def close(self):
                pass

        def _bass_raises(output_name=None):
            raise module.BassBackgroundError("simulated BASS init failure")

        player = module.BackgroundMusicPlayer.__new__(module.BackgroundMusicPlayer)
        player._bass_engine = None
        original_bass = module.BassBackgroundEngine
        original_libmpv = module.LibmpvBackgroundEngine
        module.BassBackgroundEngine = _bass_raises
        module.LibmpvBackgroundEngine = _StubLibmpvEngine
        try:
            ok = module.BackgroundMusicPlayer._init_bass_engine(player)
        finally:
            module.BassBackgroundEngine = original_bass
            module.LibmpvBackgroundEngine = original_libmpv
        self.assertTrue(ok)
        self.assertIsInstance(player._bass_engine, _StubLibmpvEngine)
        self.assertTrue(player._bass_ready())

    def test_both_engines_failing_reports_false(self):
        module = self.singws

        def _bass_raises(output_name=None):
            raise module.BassBackgroundError("simulated BASS init failure")

        def _libmpv_raises(output_name=None):
            raise RuntimeError("simulated libmpv engine failure")

        player = module.BackgroundMusicPlayer.__new__(module.BackgroundMusicPlayer)
        player._bass_engine = None
        original_bass = module.BassBackgroundEngine
        original_libmpv = module.LibmpvBackgroundEngine
        module.BassBackgroundEngine = _bass_raises
        module.LibmpvBackgroundEngine = _libmpv_raises
        try:
            ok = module.BackgroundMusicPlayer._init_bass_engine(player)
        finally:
            module.BassBackgroundEngine = original_bass
            module.LibmpvBackgroundEngine = original_libmpv
        self.assertFalse(ok)
        self.assertIsNone(player._bass_engine)


if __name__ == "__main__":
    unittest.main()
