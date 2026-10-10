import os
import unittest
from pathlib import Path

import transport_shadow as ts

_DYLIB = Path(os.environ.get("SINGWS_DSP_LIB") or os.path.expanduser("~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))


def _lib():
    import ctypes
    lib = ctypes.CDLL(str(_DYLIB))
    fn = lib.singws_transport_step_json
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int32, ctypes.c_char_p, ctypes.c_size_t]
    fn.restype = ctypes.c_int64
    return lib


class NoLibraryTests(unittest.TestCase):
    def test_unavailable_machine_is_a_silent_no_op(self):
        lines = []
        sh = ts.TransportShadow(lines.append, lib=None)
        sh.available = False
        sid = sh.start(True, 100)
        sh.observe(sid, "playing")
        sh.app_auto_complete(sid)
        sh.app_finished(sid, "complete")
        self.assertEqual(sh.mismatches, 0)

    def test_missing_symbol_means_unavailable(self):
        class Bare:
            pass
        sh = ts.TransportShadow(lambda s: None, lib=Bare())
        sh.available = True
        sh.start(True, 10)  # must not raise even though the library has no function


@unittest.skipUnless(_DYLIB.exists(), "Rust dylib not built (rust/build_dsp.sh or cargo build -p singws-dsp-ffi)")
class ShadowAgainstRealMachineTests(unittest.TestCase):
    def setUp(self):
        self.lines = []
        self.sh = ts.TransportShadow(self.lines.append, lib=_lib())
        self.assertTrue(self.sh.available)

    def text(self):
        return "\n".join(self.lines)

    def test_normal_karafun_song_has_no_mismatch(self):
        sid = self.sh.start(True, 100.0)
        for t in (10.0, 12.0, 14.0):
            self.sh.observe(sid, "playing", now=t)
        self.sh.observe(sid, "end_clock", now=111.0)
        self.sh.observe(sid, "idle", now=112.0)
        self.sh.app_auto_complete(sid)
        self.sh.app_finished(sid, "complete")
        self.assertEqual(self.sh.mismatches, 0, self.text())
        self.assertIn("CompleteSong", self.text())
        self.assertIn("summary", self.text())

    def test_app_completing_on_an_uncorroborated_idle_is_a_mismatch(self):
        sid = self.sh.start(True, 263.0)
        self.sh.observe(sid, "playing", now=10.0)
        self.sh.observe(sid, "playing", now=12.0)
        self.sh.observe(sid, "idle", now=24.0)  # machine refuses: 251 s remain
        self.sh.app_auto_complete(sid)
        self.assertEqual(self.sh.mismatches, 1, self.text())
        self.assertIn("MISMATCH", self.text())

    def test_manual_complete_is_not_a_mismatch(self):
        sid = self.sh.start(True, 200.0)
        self.sh.observe(sid, "playing", now=1.0)
        self.sh.observe(sid, "playing", now=3.0)
        self.sh.app_finished(sid, "complete")
        self.assertEqual(self.sh.mismatches, 0, self.text())

    def test_machine_completing_but_app_never_does_is_a_mismatch(self):
        sid = self.sh.start(True, 50.0)
        self.sh.observe(sid, "playing", now=0.0)
        self.sh.observe(sid, "playing", now=2.0)
        self.sh.observe(sid, "idle", now=60.0)  # elapsed >= duration-slack: machine completes
        self.sh.start(True, 50.0)  # next song starts; app never finished the first
        self.assertEqual(self.sh.mismatches, 1, self.text())

    def test_ignore_reasons_are_logged_once_per_session(self):
        sid = self.sh.start(True, 263.0)
        self.sh.observe(sid, "playing", now=1.0)
        self.sh.observe(sid, "playing", now=3.0)
        for t in (10.0, 13.0, 16.0):
            self.sh.observe(sid, "idle", now=t)
        self.assertEqual(self.text().count("uncorroborated"), 1)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(_DYLIB.exists(), "Rust dylib not built (rust/build_dsp.sh or cargo build -p singws-dsp-ffi)")
class NativeSongTests(unittest.TestCase):
    """Native (non-KaraFun) songs: start -> playing -> media end -> cleanup, plus stop and return-to-queue."""

    def setUp(self):
        self.lines = []
        self.sh = ts.TransportShadow(self.lines.append, lib=_lib())

    def text(self):
        return "\n".join(self.lines)

    def test_a_native_song_that_ends_normally_has_no_mismatch(self):
        sid = self.sh.start(False, 200.0)
        self.sh.observe(sid, "playing")
        self.sh.observe(sid, "media_end")
        self.sh.app_auto_complete(sid)
        self.sh.app_finished(sid, "native_end")
        self.assertEqual(self.sh.mismatches, 0, self.text())
        self.assertIn("CompleteSong", self.text())
        self.assertIn("AdvanceRotation", self.text())

    def test_the_app_ending_a_song_the_machine_never_saw_playing_is_a_mismatch(self):
        sid = self.sh.start(False, 200.0)  # no "playing" observation: the machine is still Starting
        self.sh.observe(sid, "media_end")
        self.sh.app_auto_complete(sid)
        self.assertEqual(self.sh.mismatches, 1, self.text())

    def test_a_manual_stop_is_not_a_completion_and_not_a_mismatch(self):
        sid = self.sh.start(False, 200.0)
        self.sh.observe(sid, "playing")
        self.sh.app_stopped(sid)
        self.sh.start(False, 100.0)  # next song: nothing overdue from the stopped one
        self.assertEqual(self.sh.mismatches, 0, self.text())

    def test_return_to_queue_counts_as_a_stop_not_a_manual_complete(self):
        sid = self.sh.start(True, 200.0)
        self.sh.observe(sid, "playing", now=1.0)
        self.sh.observe(sid, "playing", now=3.0)
        self.sh.app_finished(sid, "return_to_queue")
        self.assertNotIn("CompleteSong", self.text())
        self.assertEqual(self.sh.mismatches, 0, self.text())

    def test_a_duplicate_media_end_is_ignored_by_the_machine_and_is_not_a_mismatch(self):
        sid = self.sh.start(False, 200.0)
        self.sh.observe(sid, "playing")
        self.sh.observe(sid, "media_end")
        self.sh.observe(sid, "media_end")  # the app logs "duplicate media-end ignored" for the same thing
        self.assertEqual(self.sh.mismatches, 0, self.text())
        self.assertIn("duplicate", self.text())

    def test_a_media_end_while_a_stop_is_in_progress_is_ignored(self):
        sid = self.sh.start(False, 200.0)
        self.sh.observe(sid, "playing")
        self.sh.observe(sid, "media_end", stop_in_progress=True)
        self.assertNotIn("CompleteSong", self.text())

    def test_the_machine_completing_a_native_song_the_app_never_finishes_is_a_mismatch(self):
        sid = self.sh.start(False, 200.0)
        self.sh.observe(sid, "playing")
        self.sh.observe(sid, "media_end")
        self.sh.start(False, 100.0)  # next song starts, the first one was never finished by the app
        self.assertEqual(self.sh.mismatches, 1, self.text())

    def test_events_for_a_finished_session_are_dropped_silently(self):
        sid = self.sh.start(False, 200.0)
        self.sh.observe(sid, "playing")
        self.sh.observe(sid, "media_end")
        self.sh.app_auto_complete(sid)
        self.sh.app_finished(sid, "native_end")
        before = len(self.lines)
        self.sh.app_stopped(sid)  # the real app calls stop_playback right after the end cleanup
        self.sh.observe(sid, "media_end")
        self.assertEqual(len(self.lines), before, self.text())

    def test_the_karafun_watchdog_only_warns(self):
        sid = self.sh.start(True, 200.0)
        self.sh.observe(sid, "playing", now=1.0)
        self.sh.observe(sid, "playing", now=3.0)
        self.sh.observe(sid, "watchdog")
        self.assertIn("WarnOperator", self.text())
        self.assertNotIn("CompleteSong", self.text())


class AppWiringGuardTests(unittest.TestCase):
    """Source guards: the hooks exist, are wrapped, and the feature is off by default."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path(__file__).with_name("0.2.18.1.py").read_text()

    def test_on_by_default_because_it_only_logs(self):
        # Flipped to on 2026-10-10 at the operator's request: log-only, every call wrapped, a missing dylib means "unavailable".
        self.assertIn('"transport_shadow": True', self.src)
        self.assertIn('self.settings.get("transport_shadow", True)', self.src)

    def test_hooks_present(self):
        for needle in ('_sh.start(True, _dur or None)', '_sh.observe(_sid, "idle")', "_sh.app_auto_complete(", "_sh.app_finished(",
                       '_sh.start(False, duration_seconds or None)', '"media_end"', "_sh.app_stopped(", '"watchdog"'):
            self.assertIn(needle, self.src)

    def test_specs_bundle_the_module(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = Path(__file__).with_name(spec).read_text()
            self.assertIn('"transport_shadow.py"', text)
            self.assertIn("'transport_shadow'", text)
