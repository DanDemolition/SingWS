"""The live next-up loudness analysis is throttled with a pause/resume duty cycle (same decoded audio, about a quarter of the CPU),
because it decodes a whole song flat out inside the show (freezes at ~27x the normal rate during it, 2026-10-04 show).
Library scans (sessions, isolated helper) stay at full speed."""
import ctypes
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import libmpv_media_jobs as jobs

APP = Path("0.2.18.1.py").read_text(encoding="utf-8")


class FakeLib:
    """Scripted mpv_wait_event: `quiet` empty waits, then END_FILE. Records every command sent."""
    def __init__(self, quiet):
        self.quiet = quiet
        self.commands = []

    def mpv_wait_event(self, handle, timeout):
        time.sleep(min(float(timeout), 0.005))
        if self.quiet > 0:
            self.quiet -= 1
            return None
        ev = jobs._MpvEvent()
        ev.event_id = jobs.MPV_EVENT_END_FILE
        ev.error = 0
        return ctypes.pointer(ev)

    def mpv_command(self, handle, argv):
        self.commands.append(tuple(argv[i].decode() for i in range(3) if argv[i]))
        return 0


def job(quiet):
    j = object.__new__(jobs.OfflineMpvJob)
    j.lib = FakeLib(quiet)
    j.handle = 1
    return j


class PacedDecodeTests(unittest.TestCase):
    def test_without_a_duty_cycle_the_decoder_is_never_paused(self):
        j = job(quiet=6)
        j.wait_for_end(5.0, None)
        self.assertEqual(j.lib.commands, [])

    def test_with_a_duty_cycle_the_decoder_is_paused_and_always_resumed(self):
        j = job(quiet=40)
        j.wait_for_end(10.0, None, duty=(0.01, 0.01))
        pauses = [c for c in j.lib.commands if c == ("set", "pause", "yes")]
        resumes = [c for c in j.lib.commands if c == ("set", "pause", "no")]
        self.assertGreaterEqual(len(pauses), 2)
        self.assertEqual(len(pauses), len(resumes))
        self.assertEqual(j.lib.commands[-1], ("set", "pause", "no"))   # never left paused

    def test_cancelling_during_a_pause_resumes_and_raises(self):
        j = job(quiet=1000)
        state = {"n": 0}

        def cancel():
            state["n"] += 1
            return state["n"] > 6
        with self.assertRaises(InterruptedError):
            j.wait_for_end(10.0, None, cancel_check=cancel, duty=(0.01, 0.2))
        self.assertEqual(j.lib.commands[-1], ("set", "pause", "no"))

    def test_the_live_analysis_is_paced_and_library_scans_are_not(self):
        self.assertEqual(jobs.LIVE_ANALYSIS_DUTY, (0.05, 0.15))
        self.assertIn("duty=LIVE_ANALYSIS_DUTY if paced else None", Path("libmpv_media_jobs.py").read_text(encoding="utf-8"))
        # the background analysis worker asks for paced; the session/helper paths in _measure_loudness_lufs never do
        self.assertIn("cancel_check=lambda: not _loudness_workers_allowed(), paced=True)", APP)
        self.assertIn("measure_loudness_lufs(audio_path, timeout=300.0, paced=True) if paced", APP)
        self.assertNotIn("session.measure(audio_path, timeout=120.0, paced", APP)


if __name__ == "__main__":
    unittest.main()
