import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import contextlib
import ctypes
import io
import json
import os
import random
import tempfile
import unittest
from pathlib import Path

import plan_shadow as ps
import transition_analysis as ta
from tools import plan_parity as pp

_DYLIB = Path(os.environ.get("SINGWS_DSP_LIB") or os.path.expanduser("~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))


def _lib():
    return pp.load_lib(str(_DYLIB))


def make_record(**kw):
    base = dict(path="/x.mp3", mtime=1, size=1, media_kind="bgm", duration=200.0, audio_start=0.0, audio_end=198.0)
    base.update(kw)
    return ta.TransitionAnalysis(**base)


class Fake:
    """Answers every plan request with a fixed JSON object."""
    def __init__(self, answer):
        self.answer = json.dumps(answer).encode()
        self.calls = 0
        self.singws_plan_json = self._call

    def _call(self, request, buf, cap):
        self.calls += 1
        ctypes.memmove(buf, self.answer, len(self.answer))
        return len(self.answer)


class ShadowBehaviourTests(unittest.TestCase):
    def test_unavailable_machine_is_a_silent_no_op(self):
        lines = []
        sh = ps.PlanShadow(lines.append, lib=None)
        sh.available = False
        self.assertIsNone(sh.check_window(make_record(), 200.0, (0.0, 198.1)))
        self.assertIsNone(sh.check_crossfade(make_record(), 5.0, (5.0, "verified_dead_tail")))
        self.assertEqual(lines, [])

    def test_a_disagreement_is_logged_and_counted(self):
        lines = []
        sh = ps.PlanShadow(lines.append, lib=Fake({"seconds": 3.0, "reason": "hard_ending"}))
        self.assertFalse(sh.check_crossfade(make_record(), 5.0, (5.0, "verified_dead_tail")))
        self.assertEqual(sh.mismatches, 1)
        self.assertIn("[PLAN-SHADOW] MISMATCH op=crossfade", lines[0])

    def test_identical_inputs_are_checked_once_so_the_tick_loop_stays_cheap(self):
        fake = Fake({"start": 0.0, "end": 198.1})
        sh = ps.PlanShadow(lambda _t: None, lib=fake)
        rec = make_record()
        self.assertTrue(sh.check_window(rec, 200.0, (0.0, 198.1)))
        for _ in range(50):
            self.assertIsNone(sh.check_window(rec, 200.0, (0.0, 198.1)))
        self.assertEqual(fake.calls, 1)
        sh.check_window(rec, 201.0, (0.0, 198.1))  # a different duration is a new input
        self.assertEqual(fake.calls, 2)
        sh.check_window(make_record(audio_end=190.0), 200.0, (0.0, 190.1))  # so is a different record
        self.assertEqual(fake.calls, 3)

    def test_agreement_logs_each_kind_once(self):
        lines = []
        sh = ps.PlanShadow(lines.append, lib=Fake({"seconds": 5.0, "reason": "verified_dead_tail"}))
        for default in (5.0, 5.5, 6.0):
            self.assertTrue(sh.check_crossfade(make_record(), default, (5.0, "verified_dead_tail")))
        self.assertEqual(len([l for l in lines if "decision" in l]), 1)

    def test_a_record_with_nan_is_skipped_not_raised(self):
        sh = ps.PlanShadow(lambda _t: None, lib=Fake({"start": 0.0, "end": 1.0}))
        self.assertIsNone(sh.check_window(make_record(audio_end=float("nan")), 200.0, (0.0, 200.0)))

    def test_a_failing_log_callback_cannot_break_the_caller(self):
        def boom(_t):
            raise RuntimeError("log failed")
        sh = ps.PlanShadow(boom, lib=Fake({"seconds": 3.0, "reason": "hard_ending"}))
        self.assertFalse(sh.check_crossfade(make_record(), 5.0, (5.0, "verified_dead_tail")))


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the plan export")
class RealLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = _lib()

    def test_real_rust_agrees_with_the_app_on_each_crossfade_kind(self):
        sh = ps.PlanShadow(lambda _t: None, lib=self.lib)
        cases = [
            None,
            make_record(audio_end=None),
            make_record(audio_end=190.0),                                   # dead tail
            make_record(audio_end=199.5, fade_start=193.0, fade_confidence=0.9),  # natural fade
            make_record(audio_end=199.5, fade_confidence=0.1),              # hard ending
            make_record(audio_end=199.5, fade_confidence=0.5),              # typical
        ]
        for rec in cases:
            py = ta.select_bgm_crossfade_seconds(rec, default_seconds=5.0)
            self.assertTrue(sh.check_crossfade(rec, 5.0, py), (rec, py))
            sh._last.clear()
        self.assertEqual(sh.mismatches, 0)

    def test_real_rust_agrees_with_the_app_on_windows(self):
        sh = ps.PlanShadow(lambda _t: None, lib=self.lib)
        rec = make_record(audio_start=3.0, audio_end=190.5)
        for duration in (200.0, 200.9, 201.5, 0.0):
            self.assertTrue(sh.check_window(rec, duration, ta.bgm_audio_window(rec, duration)), duration)
            sh._last.clear()
        self.assertTrue(sh.check_window(None, 120.0, ta.bgm_audio_window(None, 120.0)))


def _rand_opt(rng, lo, hi, p_none=0.25):
    return None if rng.random() < p_none else round(rng.uniform(lo, hi), rng.choice([1, 2, 6]))


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the plan export")
class DifferentialFuzzTests(unittest.TestCase):
    """The app's own Python functions are the oracle; Rust must match exactly on random inputs, valid and invalid."""

    @classmethod
    def setUpClass(cls):
        cls.lib = _lib()

    def random_record(self, rng):
        duration = rng.choice([rng.uniform(0.0, 400.0), 200.0, 0.0, -5.0, rng.uniform(1.0, 30.0)])
        conf = [0.0, 0.1, 0.2, 0.21, 0.5, 0.79, 0.8, 0.84, 0.85, 0.9, 1.0, 1.2, -0.1]
        return ta.TransitionAnalysis(
            path="/f", mtime=1, size=1, media_kind=rng.choice(["bgm", "karaoke", "cdg", "mp4"]), duration=duration,
            analysis_version=rng.choice([1, 1, 1, 1, 2, 0]), hop_seconds=rng.choice([0.1, 0.1, 0.05, 0.5, 0.0, -1.0]),
            audio_start=_rand_opt(rng, 0, max(duration, 1) + 1), audio_end=_rand_opt(rng, 0, max(duration, 1) + 1),
            fade_start=_rand_opt(rng, 0, max(duration, 1) + 1), fade_confidence=rng.choice(conf),
            visual_start=_rand_opt(rng, 0, max(duration, 1) + 1), visual_end=_rand_opt(rng, 0, max(duration, 1) + 1),
            visual_confidence=rng.choice(conf), effective_karaoke_end=_rand_opt(rng, 0, max(duration, 1) + 1, 0.6),
            safe_for_early_completion=rng.random() < 0.2,
        )

    def test_records_validity_window_crossfade_and_effective_end(self):
        rng = random.Random(20261023)
        bad, kinds = [], set()
        for _ in range(3000):
            rec = self.random_record(rng)
            payload = {k: v for k, v in pp.record_payload(rec).items()}
            valid_py = rec.is_valid()
            valid_rs = pp.call(self.lib, {"op": "valid", "record": payload})["valid"]
            if valid_py != valid_rs:
                bad.append(("valid", payload, valid_py, valid_rs))
            duration = rng.choice([rec.duration, rec.duration + rng.uniform(-3, 3), 0.0, rng.uniform(0, 400)])
            py_w = ta.bgm_audio_window(rec, duration)
            rs_w = pp.call(self.lib, {"op": "window", "record": payload, "duration": duration})
            if py_w != (rs_w["start"], rs_w["end"]):
                bad.append(("window", payload, py_w, rs_w))
            default = rng.choice([5.0, 2.0, 8.0, 0.1, 99.0, rng.uniform(0, 12)])
            py_c = ta.select_bgm_crossfade_seconds(rec, default_seconds=default)
            rs_c = pp.call(self.lib, {"op": "crossfade", "record": payload, "default_seconds": default})
            kinds.add(py_c[1])
            if py_c != (rs_c["seconds"], rs_c["reason"]):
                bad.append(("crossfade", payload, py_c, rs_c))
            fresh = ta.TransitionAnalysis(**{**{k: v for k, v in pp.record_payload(rec).items()}})
            py_e = ta.calculate_effective_karaoke_end(fresh)
            rs_e = pp.call(self.lib, {"op": "effective_end", "record": payload})
            kinds.add("end:" + fresh.safety_reason)
            if (py_e, fresh.safe_for_early_completion, fresh.safety_reason) != (rs_e["effective"], rs_e["safe"], rs_e["reason"]):
                bad.append(("effective_end", payload, (py_e, fresh.safe_for_early_completion, fresh.safety_reason), rs_e))
        self.assertEqual(bad[:3], [], f"{len(bad)} disagreements")
        # the fuzz must actually reach every branch, including the verified-early-end one the real cache never hits
        for expected in ("end:verified_audio_and_visual", "end:normal_eos", "end:audio_end_unverified", "end:visual_end_unverified",
                         "natural_fade", "hard_ending", "typical_ending", "verified_dead_tail", "audio_boundary_unavailable",
                         "metadata_unavailable"):
            self.assertIn(expected, kinds)

    def test_boundaries_and_fade_on_random_envelopes(self):
        rng = random.Random(7)
        bad, found_fade = [], 0
        for _ in range(1500):
            n = rng.randint(0, 400)
            hop = rng.choice([0.1, 0.1, 0.05, 0.25, 0.0])
            shape = rng.choice(["noise", "fade", "gaps", "silence", "loud"])
            env = []
            for i in range(n):
                if shape == "noise":
                    env.append(rng.uniform(-96, 6))
                elif shape == "fade":
                    env.append(-10.0 - max(0, i - n * 0.6) * rng.uniform(0.2, 1.5) if rng.random() > 0.05 else -96.0)
                elif shape == "gaps":
                    env.append(-96.0 if (i // rng.choice([3, 5, 9])) % 3 == 0 else rng.uniform(-40, -5))
                elif shape == "silence":
                    env.append(-96.0)
                else:
                    env.append(-12.0)
            env = ta.quantize_envelope_db(env)
            py_b = ta.audio_boundaries_from_envelope(env, hop_seconds=hop)
            rs_b = pp.call(self.lib, {"op": "boundaries", "envelope": env, "hop": hop})
            if py_b != (rs_b["start"], rs_b["end"]):
                bad.append(("boundaries", hop, py_b, rs_b))
            py_f = ta.estimate_fade_out_from_envelope(env, hop_seconds=hop)
            rs_f = pp.call(self.lib, {"op": "fade", "envelope": env, "hop": hop})
            found_fade += py_f[0] is not None
            if py_f != (rs_f["fade_start"], rs_f["confidence"]):
                bad.append(("fade", hop, py_f, rs_f))
        self.assertEqual(bad[:3], [], f"{len(bad)} disagreements")
        self.assertGreater(found_fade, 50, "the fuzz never produced a fade, so it proves nothing about fade detection")


class FadeHeuristicPinTests(unittest.TestCase):
    """Pins a known quirk of the app's fade estimate so that changing it is a deliberate, reviewed decision.

    estimate_fade_out_from_envelope searches only the last 12 s before the audio end and counts flat loud audio as "not rising",
    so the EARLIEST qualifying window wins. For a track that holds -10 dB for 10 s and then fades for 5 s it reports a fade
    starting 1.3 s into the file (the start of the 12 s search window), not at the audible 10 s. In practice 50 of the 67 real
    tracks with a fade report exactly 12.0 s, but only the 3 that also lack a dead tail reach the natural-fade crossfade path
    (always the 8 s cap), so the audible effect today is small. The Rust port (singws-plan) reproduces this exactly. To change
    the heuristic: change Python first, then the Rust port and this pin together, and re-run tools/plan_parity.py.
    """

    def test_flat_loud_audio_before_a_fade_pulls_the_reported_start_to_the_search_edge(self):
        env = [-10.0] * 100 + [-10.0 - 1.4 * i for i in range(50)] + [-96.0] * 50
        self.assertEqual(ta.estimate_fade_out_from_envelope(env, hop_seconds=0.1), (1.3, 1.0))
        self.assertEqual(ta.audio_boundaries_from_envelope(env, hop_seconds=0.1), (0.0, 13.3))

    @unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the plan export")
    def test_the_rust_port_reproduces_the_same_answer(self):
        env = [-10.0] * 100 + [-10.0 - 1.4 * i for i in range(50)] + [-96.0] * 50
        out = pp.call(_lib(), {"op": "fade", "envelope": env, "hop": 0.1})
        self.assertEqual((out["fade_start"], out["confidence"]), (1.3, 1.0))


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the plan export")
class ParityToolTests(unittest.TestCase):
    def test_tool_passes_on_a_small_cache_and_skips_temp_records(self):
        rec = make_record(audio_end=199.5, fade_start=193.0, fade_confidence=0.9)
        env = [-10] * 1990
        payload = {**rec.to_dict(), "envelope_db": env}
        temp = {**payload, "path": "/var/folders/xx/T/tmp1/song.zip"}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cache.json"
            path.write_text(json.dumps({"analysis_version": 1, "records": {rec.path: payload, temp["path"]: temp}}))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                status = pp.main([str(path), "--lib", str(_DYLIB)])
        out = buf.getvalue()
        self.assertEqual(status, 0, out)
        self.assertIn('"records_used": 1', out)
        self.assertIn('"records_total": 2', out)


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")

    def test_off_by_default_and_log_only(self):
        self.assertIn('"plan_shadow": False', self.src)
        start = self.src.index("def _plan_shadow_for")
        block = self.src[start:start + 700]
        self.assertIn("except Exception:", block)

    def test_both_bgm_sites_return_the_apps_own_answer(self):
        win = self.src[self.src.index("def _bgm_content_window"):][:900]
        self.assertIn("shadow.check_window(record, window_duration, result)", win)
        self.assertIn("return result", win)
        xf = self.src[self.src.index("def _adaptive_crossfade_duration_ms"):][:1500]
        self.assertIn("shadow.check_crossfade(record, configured / 1000.0, (seconds, reason))", xf)
        self.assertIn("return int(round(seconds * 1000.0)), reason", xf)

    def test_specs_bundle_the_module(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = Path(__file__).with_name(spec).read_text()
            self.assertIn('"plan_shadow.py"', text)
            self.assertIn("'plan_shadow'", text)


if __name__ == "__main__":
    unittest.main()
