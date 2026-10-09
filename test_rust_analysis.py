"""The Rust analysis helper front end (rust_analysis.py) and its hooks in the app (analysis_engine setting).

Most tests use a fake helper script, so they need no Rust build; the last class runs the real binary when it exists
(SINGWS_ANALYZE_BIN or the dev build in ~/.cache/singws-rust-target/release)."""
import importlib.util
import json
import math
import os
import stat
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

import rust_analysis as ra

FAKE_TEMPLATE = '''#!{python}
import json, sys, time
args = sys.argv[1:]
path = args[-1]
mode = {mode!r}
if mode == "sleep":
    time.sleep(60)
if mode == "silent":
    sys.exit(3)
if mode == "garbage":
    print("not json at all"); sys.exit(0)
if mode == "echo_args":
    print(json.dumps({{"path": path, "ok": True, "i": -14.0, "peak_db": -1.0, "duration": 10.0, "start": 0.0, "end": 10.0,
                      "argv": args}})); sys.exit(0)
if mode == "file_error":
    print(json.dumps({{"path": path, "ok": False, "error": "corrupt", "detail": "bad header"}})); sys.exit(0)
if mode == "internal":
    print(json.dumps({{"path": path, "ok": False, "error": "internal", "detail": "panic"}})); sys.exit(0)
rec = {{"path": path, "ok": True, "i": {lufs}, "peak_db": {peak}, "duration": {duration}, "start": {start}, "end": {end}}}
if "--envelope" in args:
    rec["envelope"] = [-60.0, -20.0, -10.0, -10.0, -30.0]
print(json.dumps(rec))
'''


def make_helper(tmp, mode="ok", lufs=-14.0, peak=-1.0, duration=200.0, start=1.0, end=199.0):
    path = Path(tmp) / f"fake-helper-{mode}"
    path.write_text(FAKE_TEMPLATE.format(python=sys.executable, mode=mode, lufs=lufs, peak=peak,
                                         duration=duration, start=start, end=end))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class EngineNameTests(unittest.TestCase):
    def test_unknown_values_mean_libmpv(self):
        for raw in (None, "", "garbage", 5, "LIBMPV "):
            self.assertEqual(ra.normalize_engine(raw), "libmpv")
        self.assertEqual(ra.normalize_engine(" Shadow "), "shadow")
        self.assertEqual(ra.normalize_engine("RUST"), "rust")


class HelperDiscoveryTests(unittest.TestCase):
    def test_env_override_wins_and_must_be_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = make_helper(tmp)
            with mock.patch.dict(os.environ, {"SINGWS_ANALYZE_BIN": str(good)}):
                self.assertEqual(ra.find_helper(), good)
            plain = Path(tmp) / "plain.txt"
            plain.write_text("x")
            with mock.patch.dict(os.environ, {"SINGWS_ANALYZE_BIN": str(plain)}):
                self.assertNotEqual(ra.find_helper(), plain)


class RunHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_success_record_is_returned(self):
        rec = ra.run_helper("/music/a.mp3", helper=make_helper(self.tmp.name))
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["i"], -14.0)

    def test_options_are_passed_and_dash_paths_are_protected(self):
        rec = ra.run_helper("-weird.mp3", helper=make_helper(self.tmp.name, "echo_args"), want_envelope=True, timeout=30)
        argv = rec["argv"]
        self.assertIn("--envelope", argv)
        self.assertEqual(argv[-1], "./-weird.mp3")
        self.assertEqual(argv[argv.index("--jobs") + 1], "1")
        self.assertEqual(argv[argv.index("--timeout") + 1], "30.0")

    def test_a_file_error_is_not_a_helper_fault(self):
        with self.assertRaises(ra.RustAnalysisError) as cm:
            ra.run_helper("x.mp3", helper=make_helper(self.tmp.name, "file_error"))
        self.assertFalse(cm.exception.helper_fault)
        self.assertEqual(cm.exception.code, "corrupt")

    def test_internal_error_counts_as_a_helper_fault(self):
        with self.assertRaises(ra.RustAnalysisError) as cm:
            ra.run_helper("x.mp3", helper=make_helper(self.tmp.name, "internal"))
        self.assertTrue(cm.exception.helper_fault)

    def test_no_usable_output_is_a_helper_fault(self):
        for mode in ("silent", "garbage"):
            with self.assertRaises(ra.RustAnalysisError) as cm:
                ra.run_helper("x.mp3", helper=make_helper(self.tmp.name, mode))
            self.assertTrue(cm.exception.helper_fault, mode)

    def test_missing_helper_is_a_helper_fault(self):
        with mock.patch.object(ra, "find_helper", return_value=None):
            with self.assertRaises(ra.RustAnalysisError) as cm:
                ra.run_helper("x.mp3")
        self.assertTrue(cm.exception.helper_fault)

    def test_timeout_kills_the_process(self):
        with mock.patch.object(ra, "TIMEOUT_GRACE_S", 0.0):
            t0 = time.monotonic()
            with self.assertRaises(TimeoutError):
                ra.run_helper("x.mp3", helper=make_helper(self.tmp.name, "sleep"), timeout=1.0)
        self.assertLess(time.monotonic() - t0, 5.0)

    def test_cancel_kills_the_process_quickly(self):
        start = time.monotonic()
        with self.assertRaises(InterruptedError):
            ra.run_helper("x.mp3", helper=make_helper(self.tmp.name, "sleep"), timeout=100,
                          cancel_check=lambda: time.monotonic() - start > 0.4)
        self.assertLess(time.monotonic() - start, 5.0)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_shapes_match_the_libmpv_session(self):
        s = ra.RustAnalysisSession(make_helper(self.tmp.name))
        self.assertEqual(s.measure("a.mp3"), (-14.0, -1.0))
        self.assertEqual(s.measure_karaoke_transition("a.mp3"), (-14.0, -1.0, 200.0, 1.0, 199.0))
        lufs, peak, env = s.measure_transition("a.mp3")
        self.assertEqual((lufs, peak, env), (-14.0, -1.0, [-60.0, -20.0, -10.0, -10.0, -30.0]))
        self.assertTrue(s.isolated and s.usable)

    def test_silent_file_edges_come_through_as_none(self):
        s = ra.RustAnalysisSession(make_helper(self.tmp.name, start=None, end=None))
        self.assertEqual(s.measure_karaoke_transition("a.mp3")[3:], (None, None))

    def test_three_helper_faults_disable_it_but_file_errors_never_do(self):
        bad = ra.RustAnalysisSession(make_helper(self.tmp.name, "silent"))
        for _ in range(3):
            with self.assertRaises(ra.RustAnalysisError):
                bad.measure("a.mp3")
        self.assertFalse(bad.usable)
        picky = ra.RustAnalysisSession(make_helper(self.tmp.name, "file_error"))
        for _ in range(10):
            with self.assertRaises(ra.RustAnalysisError):
                picky.measure("a.mp3")
        self.assertTrue(picky.usable)

    def test_a_good_answer_resets_the_fault_count(self):
        s = ra.RustAnalysisSession(make_helper(self.tmp.name, "silent"))
        for _ in range(2):
            with self.assertRaises(ra.RustAnalysisError):
                s.measure("a.mp3")
        s._helper = make_helper(self.tmp.name, "ok")
        s.measure("a.mp3")
        s._helper = make_helper(self.tmp.name, "silent")
        for _ in range(2):
            with self.assertRaises(ra.RustAnalysisError):
                s.measure("a.mp3")
        self.assertTrue(s.usable)


class CompareTests(unittest.TestCase):
    def test_within_tolerance_is_quiet(self):
        self.assertEqual(ra.compare_results("loudness", (-14.0, -1.0), (-14.1, -1.1)), [])

    def test_loudness_and_peak_differences_are_named(self):
        text = " ".join(ra.compare_results("loudness", (-14.0, -1.0), (-13.5, 0.4)))
        self.assertIn("lufs", text)
        self.assertIn("peak", text)

    def test_boundaries_and_duration(self):
        a = (-14.0, -1.0, 200.0, 1.0, 199.0)
        self.assertEqual(ra.compare_results("karaoke", a, (-14.0, -1.0, 200.01, 1.05, 199.05)), [])
        text = " ".join(ra.compare_results("karaoke", a, (-14.0, -1.0, 200.0, 2.0, 190.0)))
        self.assertIn("audio_start", text)
        self.assertIn("audio_end", text)
        self.assertTrue(ra.compare_results("karaoke", a, (-14.0, -1.0, 200.0, None, 199.0)))

    def test_envelope_length_and_level(self):
        a = (-14.0, -1.0, [-20.0] * 100)
        self.assertEqual(ra.compare_results("bgm", a, (-14.0, -1.0, [-20.2] * 101)), [])
        self.assertTrue(ra.compare_results("bgm", a, (-14.0, -1.0, [-20.0] * 108)))     # the 44.1 kHz 8% case
        self.assertTrue(ra.compare_results("bgm", a, (-14.0, -1.0, [-30.0] * 100)))


class ShadowTests(unittest.TestCase):
    def test_returns_the_primary_result_untouched_even_if_rust_disagrees_or_fails(self):
        logs = []
        stats = ra.ShadowStats(every=1000)
        got = ra.shadow_run("loudness", "a.mp3", lambda: (-14.0, -1.0), lambda: (-9.0, 0.0), log=logs.append, stats=stats)
        self.assertEqual(got, (-14.0, -1.0))
        self.assertTrue(any("MISMATCH" in m for m in logs))

        def boom():
            raise RuntimeError("rust exploded")
        logs.clear()
        got = ra.shadow_run("loudness", "a.mp3", lambda: (-14.0, -1.0), boom, log=logs.append, stats=stats)
        self.assertEqual(got, (-14.0, -1.0))
        self.assertTrue(any("rust failed" in m for m in logs))
        self.assertEqual((stats.compared, stats.mismatched, stats.rust_errors), (2, 1, 1))

    def test_a_primary_error_propagates_like_it_would_without_shadowing(self):
        def bad():
            raise ValueError("libmpv failed")
        with self.assertRaises(ValueError):
            ra.shadow_run("loudness", "a.mp3", bad, lambda: (-14.0, -1.0), log=lambda m: None, stats=ra.ShadowStats())

    def test_rust_runs_alongside_not_after(self):
        def slow(v):
            def call():
                time.sleep(0.4)
                return v
            return call
        t0 = time.monotonic()
        ra.shadow_run("loudness", "a.mp3", slow((-14.0, -1.0)), slow((-14.0, -1.0)), log=lambda m: None, stats=ra.ShadowStats())
        self.assertLess(time.monotonic() - t0, 0.75)

    def test_summary_line_every_n(self):
        logs = []
        stats = ra.ShadowStats(every=3)
        for _ in range(6):
            ra.shadow_run("loudness", "a.mp3", lambda: (-14.0, -1.0), lambda: (-14.0, -1.0), log=logs.append, stats=stats)
        self.assertEqual(len([m for m in logs if "summary" in m]), 2)


class FakePrimary:
    isolated = True
    usable = True

    def __init__(self):
        self.calls = []

    def measure(self, s, *, timeout=120.0):
        self.calls.append("measure")
        return (-20.0, -3.0)

    def measure_fast(self, s, *, timeout=120.0):
        self.calls.append("fast")
        return (-21.0, -3.0)

    def measure_transition(self, s, *, timeout=120.0):
        self.calls.append("bgm")
        return (-20.0, -3.0, [-20.0] * 10)

    def measure_karaoke_transition(self, s, *, timeout=120.0):
        self.calls.append("karaoke")
        return (-20.0, -3.0, 100.0, 0.5, 99.0)

    def measure_video_tail(self, *a, **k):
        self.calls.append("video")
        return [1]

    def close(self):
        self.calls.append("close")


class HybridTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs = []

    def session(self, engine, mode="ok", **kw):
        self.primary = FakePrimary()
        rust = ra.RustAnalysisSession(make_helper(self.tmp.name, mode, **kw))
        return ra.HybridAnalysisSession(self.primary, rust, engine, log=self.logs.append)

    def test_shadow_always_answers_with_libmpv(self):
        h = self.session("shadow")
        self.assertEqual(h.measure("a.mp3"), (-20.0, -3.0))
        self.assertEqual(h.measure_karaoke_transition("a.mp3"), (-20.0, -3.0, 100.0, 0.5, 99.0))
        self.assertEqual(h.measure_transition("a.mp3")[0], -20.0)
        self.assertEqual(self.primary.calls, ["measure", "karaoke", "bgm"])

    def test_shadow_survives_a_broken_helper(self):
        h = self.session("shadow", "silent")
        self.assertEqual(h.measure("a.mp3"), (-20.0, -3.0))
        self.assertTrue(any("rust failed" in m for m in self.logs))

    def test_rust_mode_uses_rust_for_verified_kinds_only(self):
        h = self.session("rust", lufs=-14.0)
        self.assertEqual(h.measure("a.mp3"), (-14.0, -1.0))                       # loudness verified -> Rust
        self.assertEqual(h.measure_karaoke_transition("a.mp3"), (-14.0, -1.0, 200.0, 1.0, 199.0))   # boundaries verified -> Rust
        self.assertEqual(h.measure_transition("a.mp3")[2], [-20.0] * 10)          # envelope NOT equivalent -> libmpv
        self.assertEqual(self.primary.calls, ["bgm"])

    def test_boundaries_go_back_to_libmpv_if_they_are_ever_unverified(self):
        with mock.patch.object(ra, "RUST_BOUNDARIES_VERIFIED", False):
            h = self.session("rust")
            self.assertEqual(h.measure_karaoke_transition("a.mp3")[2], 100.0)
            self.assertEqual(self.primary.calls, ["karaoke"])

    def test_rust_mode_falls_back_to_libmpv_on_any_rust_trouble(self):
        for mode in ("silent", "garbage", "file_error", "internal"):
            h = self.session("rust", mode)
            self.assertEqual(h.measure("a.mp3"), (-20.0, -3.0), mode)

    def test_rust_with_no_loudness_falls_back(self):
        h = self.session("rust", lufs="None")
        self.assertEqual(h.measure("a.mp3"), (-20.0, -3.0))

    def test_fast_and_video_always_stay_on_libmpv_and_close_closes_both(self):
        h = self.session("rust")
        h.measure_fast("a.mp3")
        h.measure_video_tail("v.mp4", duration_seconds=5)
        h.close()
        self.assertEqual(self.primary.calls, ["fast", "video", "close"])
        self.assertTrue(h.usable and h.isolated)

    def test_make_session_is_a_no_op_for_libmpv_and_without_a_helper(self):
        primary = FakePrimary()
        self.assertIs(ra.make_session("libmpv", lambda: primary), primary)
        with mock.patch.object(ra, "find_helper", return_value=None):
            self.assertIs(ra.make_session("shadow", lambda: primary, log=self.logs.append), primary)
        self.assertTrue(any("not found" in m for m in self.logs))
        with mock.patch.dict(os.environ, {"SINGWS_ANALYZE_BIN": str(make_helper(self.tmp.name))}):
            self.assertIsInstance(ra.make_session("shadow", lambda: primary), ra.HybridAnalysisSession)


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_rust_analysis", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AppHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: self.singws._set_analysis_engine("libmpv"))
        self.logs = []
        patcher = mock.patch.object(self.singws, "_diag", side_effect=self.logs.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.libmpv = mock.patch("libmpv_media_jobs.measure_loudness_lufs", return_value=(-20.0, -3.0))
        self.libmpv_mock = self.libmpv.start()
        self.addCleanup(self.libmpv.stop)
        self.singws._rust_live_sessions.clear()

    def helper_env(self, mode="ok", **kw):
        return mock.patch.dict(os.environ, {"SINGWS_ANALYZE_BIN": str(make_helper(self.tmp.name, mode, **kw))})

    def test_the_default_is_libmpv_and_nothing_else_runs(self):
        self.assertEqual(self.singws.DEFAULTS["analysis_engine"], "libmpv")
        with mock.patch.object(ra, "RustAnalysisSession", side_effect=AssertionError("rust must not be touched")):
            self.assertEqual(self.singws._measure_loudness_lufs("/m/a.mp3"), (-20.0, -3.0))
            primary = object()
            self.assertIs(self.singws._make_analysis_session(lambda: primary), primary)

    def test_bad_setting_values_mean_libmpv(self):
        for raw in (None, "", "turbo", 7):
            self.singws._set_analysis_engine(raw)
            self.assertEqual(self.singws._analysis_engine_setting, "libmpv")

    def test_shadow_returns_libmpv_and_logs_a_mismatch(self):
        self.singws._set_analysis_engine("shadow")
        with self.helper_env(lufs=-9.0, peak=0.0):
            self.assertEqual(self.singws._measure_loudness_lufs("/m/a.mp3"), (-20.0, -3.0))
        self.assertTrue(any("MISMATCH" in m for m in self.logs), self.logs)

    def test_shadow_with_an_agreeing_helper_is_silent(self):
        self.singws._set_analysis_engine("shadow")
        with self.helper_env(lufs=-20.0, peak=-3.0):
            self.assertEqual(self.singws._measure_loudness_lufs("/m/a.mp3"), (-20.0, -3.0))
        self.assertFalse(any("MISMATCH" in m for m in self.logs), self.logs)

    def test_rust_returns_the_rust_answer_without_calling_libmpv(self):
        self.singws._set_analysis_engine("rust")
        with self.helper_env(lufs=-14.0, peak=-1.0):
            self.assertEqual(self.singws._measure_loudness_lufs("/m/a.mp3"), (-14.0, -1.0))
        self.libmpv_mock.assert_not_called()

    def test_rust_falls_back_to_libmpv_when_the_helper_misbehaves_or_is_missing(self):
        self.singws._set_analysis_engine("rust")
        for mode in ("silent", "garbage", "file_error", "internal"):
            self.singws._rust_live_sessions.clear()
            with self.helper_env(mode):
                self.assertEqual(self.singws._measure_loudness_lufs("/m/a.mp3"), (-20.0, -3.0), mode)
        self.singws._rust_live_sessions.clear()
        with mock.patch.object(ra, "find_helper", return_value=None):
            self.assertEqual(self.singws._measure_loudness_lufs("/m/a.mp3"), (-20.0, -3.0))

    def test_partial_and_session_calls_never_use_rust(self):
        self.singws._set_analysis_engine("rust")
        session = mock.Mock(usable=True, isolated=False)
        session.measure.return_value = (-18.0, -2.0)
        with self.helper_env(lufs=-14.0):
            self.assertEqual(self.singws._measure_loudness_lufs("/m/a.mp3", session=session), (-18.0, -2.0))
            self.singws._measure_loudness_lufs("/m/a.mp3", mode="fast")
        session.measure.assert_called_once()

    def test_a_cancelled_rust_request_returns_nothing(self):
        self.singws._set_analysis_engine("rust")
        with self.helper_env("sleep"):
            t0 = time.monotonic()
            result = self.singws._measure_loudness_lufs("/m/a.mp3", cancel_check=lambda: time.monotonic() - t0 > 0.4)
        self.assertEqual(result, (None, None))
        self.assertLess(time.monotonic() - t0, 6.0)

    def test_scan_session_is_wrapped_only_when_asked(self):
        primary = FakePrimary()
        self.assertIs(self.singws._make_analysis_session(lambda: primary), primary)
        self.singws._set_analysis_engine("shadow")
        with self.helper_env():
            wrapped = self.singws._make_analysis_session(lambda: primary)
        self.assertIsInstance(wrapped, ra.HybridAnalysisSession)
        self.assertTrue(wrapped.isolated and wrapped.usable)

    def test_the_scan_asks_for_its_session_through_the_hook(self):
        src = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn("session = _make_analysis_session(IsolatedLoudnessSession)", src)
        self.assertIn('_set_analysis_engine(self.settings.get("analysis_engine", "libmpv"))', src)


def _wav(path, seconds=6, rate=44100, amp=0.0708):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for n in range(int(seconds * rate)):
            v = int(amp * 32767 * math.sin(2 * math.pi * 997 * n / rate))
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))


class RealBinaryTests(unittest.TestCase):
    """The real helper, when it has been built (rust/build_analyze.sh or the dev build)."""

    @classmethod
    def setUpClass(cls):
        dev = Path.home() / ".cache/singws-rust-target/release/singws-analyze"
        if not os.environ.get("SINGWS_ANALYZE_BIN") and dev.exists():
            os.environ["SINGWS_ANALYZE_BIN"] = str(dev)
        cls.helper = ra.find_helper()

    def test_a_reference_tone_measures_minus_23_lufs_through_the_real_helper(self):
        if self.helper is None:
            self.skipTest("singws-analyze is not built")
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "tone.wav"
            _wav(wav)
            s = ra.RustAnalysisSession()
            lufs, peak = s.measure(str(wav))
            self.assertAlmostEqual(lufs, -23.0, delta=0.15)
            self.assertAlmostEqual(peak, -23.0, delta=0.2)
            k = s.measure_karaoke_transition(str(wav))
            self.assertAlmostEqual(k[2], 6.0, delta=0.05)
            self.assertAlmostEqual(k[3], 0.0, delta=0.05)
            env = s.measure_transition(str(wav))[2]
            self.assertEqual(len(env), 60)

    def test_a_missing_file_is_a_file_error_not_a_helper_fault(self):
        if self.helper is None:
            self.skipTest("singws-analyze is not built")
        s = ra.RustAnalysisSession()
        with self.assertRaises(ra.RustAnalysisError) as cm:
            s.measure("/nonexistent/none.mp3")
        self.assertFalse(cm.exception.helper_fault)
        self.assertTrue(s.usable)


if __name__ == "__main__":
    unittest.main()
