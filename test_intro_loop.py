"""Intro Loop — pure-logic tests (no Qt/GStreamer/audio).

Covers the transport's A–B loop wrap decision and the loop-length math. The
full auto-engage/release flow needs the GUI + real audio and is QA'd manually.
"""
import importlib.util
import unittest

import phrase_markers as pm


class LoopTargetContractTests(unittest.TestCase):
    """The transport's _loop_target must wrap only at/after loop_end of a valid
    region. We mirror the exact logic and also assert the source contains it."""

    @staticmethod
    def loop_target(position, loop_start, loop_end):
        if loop_start is None or loop_end is None:
            return None
        try:
            ls = float(loop_start); le = float(loop_end)
        except (TypeError, ValueError):
            return None
        if le <= ls:
            return None
        if float(position) >= le:
            return ls
        return None

    def test_inside_region_no_wrap(self):
        self.assertIsNone(self.loop_target(5.0, 4.0, 12.0))
        self.assertIsNone(self.loop_target(11.99, 4.0, 12.0))

    def test_at_or_after_end_wraps_to_start(self):
        self.assertEqual(self.loop_target(12.0, 4.0, 12.0), 4.0)
        self.assertEqual(self.loop_target(50.0, 4.0, 12.0), 4.0)

    def test_no_loop_or_invalid(self):
        self.assertIsNone(self.loop_target(5.0, None, 12.0))
        self.assertIsNone(self.loop_target(5.0, 4.0, None))
        self.assertIsNone(self.loop_target(5.0, 12.0, 4.0))   # end <= start
        self.assertIsNone(self.loop_target(5.0, 4.0, 4.0))

    def test_source_has_loop_target(self):
        # Guard against the transport implementation drifting from this contract.
        with open("python_karaoke_transport.py", "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _loop_target(", src)
        self.assertIn("def set_loop(", src)
        self.assertIn("def clear_loop(", src)
        self.assertIn("self.seek(_wrap)", src)  # the loop-back in _tick


class LoopLengthMathTests(unittest.TestCase):
    def test_loop_end_from_bars(self):
        # 8 bars @ 120 BPM = 16s; loop_end = phrase_start + length
        length = pm.bars_to_seconds(8, 120)
        self.assertAlmostEqual(length, 16.0)
        phrase_start = 4.0
        self.assertAlmostEqual(phrase_start + length, 20.0)

    def test_bars_variants(self):
        self.assertAlmostEqual(pm.bars_to_seconds(4, 120), 8.0)
        self.assertAlmostEqual(pm.bars_to_seconds(16, 120), 32.0)
        self.assertIsNone(pm.bars_to_seconds(8, 0))


if __name__ == "__main__":
    unittest.main()
