"""Timing counters for the Python master-processor DSP callback that runs on BASS's audio thread."""
import ctypes
import re
import unittest
from pathlib import Path

import numpy as np

import bass_background_engine as bbe
from bass_background_engine import AudioCallbackStats, BassBackgroundEngine, format_audio_diag

ROOT = Path(__file__).resolve().parent


class AudioCallbackStatsTests(unittest.TestCase):
    def test_counts_mean_max_and_budget(self):
        s = AudioCallbackStats()
        block = 0.0256  # budget fraction 0.5 -> 12.8 ms
        s.record(0.000, 0.001, block)   # 1 ms
        s.record(0.026, 0.028, block)   # 2 ms
        s.record(0.052, 0.070, block)   # 18 ms: over half its own duration
        d = s.summary()
        self.assertEqual(d["blocks"], 3)
        self.assertEqual(d["over_budget"], 1)
        self.assertAlmostEqual(d["max_us"], 18000, delta=1)
        self.assertAlmostEqual(d["mean_us"], 7000, delta=1)

    def test_p99_uses_histogram_upper_edge(self):
        s = AudioCallbackStats()
        for i in range(99):
            s.record(i * 0.03, i * 0.03 + 0.0004, 0.03)   # 400 us -> bucket <=500
        s.record(10.0, 10.02, 0.03)                         # 20 ms -> bucket <=25000
        self.assertEqual(s.p99_upper_us(), 500)
        s.record(11.0, 11.1, 0.03)                           # 100 ms: beyond the last edge
        s.record(12.0, 12.1, 0.03)
        self.assertIsNone(AudioCallbackStats.p99_upper_us(self._all_slow()))

    @staticmethod
    def _all_slow():
        s = AudioCallbackStats()
        for i in range(10):
            s.record(i * 0.2, i * 0.2 + 0.1, 0.03)
        return s

    def test_gaps_are_counted_and_long_pauses_are_restarts_not_starvation(self):
        s = AudioCallbackStats()
        s.record(0.00, 0.001, 0.03)
        s.record(0.03, 0.031, 0.03)    # 30 ms gap: normal
        s.record(0.20, 0.201, 0.03)    # 170 ms gap
        s.record(0.90, 0.901, 0.03)    # 700 ms gap
        s.record(5.00, 5.001, 0.03)    # 4.1 s: mixer was paused, a restart
        d = s.summary()
        self.assertEqual((d["gaps_over_100ms"], d["gaps_over_500ms"], d["restarts"]), (2, 1, 1))
        self.assertAlmostEqual(d["gap_max_ms"], 700, delta=1)

    def test_snapshot_carries_last_start_so_a_reset_is_not_a_gap(self):
        old = AudioCallbackStats()
        old.record(1.0, 1.001, 0.03)
        fresh = AudioCallbackStats(last_start=old.last_start)
        fresh.record(1.03, 1.031, 0.03)
        self.assertEqual(fresh.summary()["gaps_over_100ms"], 0)


class FormatTests(unittest.TestCase):
    def test_empty_when_nothing_to_report(self):
        self.assertEqual(format_audio_diag(None), "")
        self.assertEqual(format_audio_diag({"master": {"blocks": 0}, "mixer_stalled": 0}), "")

    def test_line_has_the_fields_and_the_tag(self):
        s = AudioCallbackStats()
        s.record(0.0, 0.0004, 0.03)
        line = format_audio_diag({"master": s.summary(), "bass_cpu": 3.14159, "mixer_stalled": 0})
        self.assertTrue(line.startswith("[AUDIO-DIAG] master-dsp"))
        for field in ("blocks=1", "p99<=500us", "over_budget=0", "gap_max=0ms", "bass_cpu=3.1%", "mixer_stalled_polls=0"):
            self.assertIn(field, line)


class _FakeProc:
    def configure_stream(self, rate, channels):
        pass

    def process_f32_array(self, frames):
        return frames * 0.5


class _FakeBass:
    def __init__(self):
        self.callback = None

    def BASS_ChannelSetDSP(self, mixer, callback, user, priority):
        self.callback = callback
        return 7

    def BASS_GetCPU(self):
        return 2.5

    def BASS_ChannelIsActive(self, handle):
        return bbe.BASS_ACTIVE_STALLED


def _engine():
    e = BassBackgroundEngine.__new__(BassBackgroundEngine)
    e.sample_rate = 48000
    e.mixer = 1
    e._master_proc = _FakeProc()
    e._master_proc_ref = {"proc": None}
    e._master_dsp_callback = None
    e._master_dsp_handle = 0
    e._diag_ref = {"master": None, "sample_rate": 48000.0}
    e._diag_stalled_polls = 0
    e._closed = True
    e.close = lambda: None   # a stub: nothing to tear down in __del__
    e.bass = _FakeBass()
    return e


def _call(engine, frames=1200):
    buf = (ctypes.c_float * (frames * 2))(*([0.5] * (frames * 2)))
    cb = ctypes.cast(engine._master_dsp_callback, ctypes.c_void_p)
    engine._master_dsp_callback(1, 2, ctypes.addressof(buf), frames * 2 * 4, None)
    return buf


class MasterCallbackInstrumentationTests(unittest.TestCase):
    def test_records_when_enabled_and_still_processes_audio(self):
        e = _engine()
        e._attach_master_dsp()
        e.set_audio_diagnostics(True)
        buf = _call(e)
        self.assertAlmostEqual(buf[0], 0.25, places=5)     # the processor still ran (0.5 * 0.5)
        snap = e.audio_diagnostics_snapshot()
        self.assertEqual(snap["master"]["blocks"], 1)
        self.assertEqual(snap["bass_cpu"], 2.5)
        self.assertEqual(snap["mixer_stalled"], 1)
        # a snapshot resets the counters
        self.assertEqual(e.audio_diagnostics_snapshot()["master"]["blocks"], 0)

    def test_nothing_recorded_when_disabled(self):
        e = _engine()
        e._attach_master_dsp()
        e.set_audio_diagnostics(False)
        buf = _call(e)
        self.assertAlmostEqual(buf[0], 0.25, places=5)
        self.assertIsNone(e.audio_diagnostics_snapshot())

    def test_a_failing_processor_never_raises_into_bass(self):
        e = _engine()
        e._master_proc.process_f32_array = lambda frames: (_ for _ in ()).throw(RuntimeError("boom"))
        e._attach_master_dsp()
        e.set_audio_diagnostics(True)
        _call(e)   # must swallow the error like before
        self.assertEqual(e.audio_diagnostics_snapshot()["master"]["blocks"], 0)

    def test_recording_overhead_is_tiny(self):
        s = AudioCallbackStats()
        import time
        n = 20000
        t0 = time.perf_counter()
        for i in range(n):
            s.record(i * 0.025, i * 0.025 + 0.0004, 0.025)
        per_call_us = (time.perf_counter() - t0) / n * 1e6
        self.assertLess(per_call_us, 20.0, f"record() costs {per_call_us:.2f} us")


class HostWiringTests(unittest.TestCase):
    SRC = (ROOT / "0.2.18.1.py").read_text()

    def test_setting_exists_and_defaults_on(self):
        self.assertRegex(self.SRC, r'"audio_diag_counters":\s*True')

    def test_host_logs_once_a_minute_and_can_be_switched_off(self):
        self.assertIn("def _start_audio_diag_counters", self.SRC)
        self.assertIn("timer.setInterval(60000)", self.SRC)
        self.assertIn('parent.settings.get("audio_diag_counters", True)', self.SRC)
        self.assertIn("engine.set_audio_diagnostics(enabled)", self.SRC)
        self.assertIn("format_audio_diag", self.SRC)


if __name__ == "__main__":
    unittest.main()
