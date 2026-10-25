import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import errno
import hashlib
import json
import os
import random
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

import store_shadow as st
from test_settings_shadow import app_function
from test_store_roundtrip import STYLES, random_value, random_string
from tools import store_roundtrip as sr

_DYLIB = Path(os.environ.get("SINGWS_DSP_LIB") or os.path.expanduser("~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))
_SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


class Fake:
    """A library stand-in whose roundtrip returns a fixed answer (or an error code)."""
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0
        self.singws_store_roundtrip = self._call

    def _call(self, raw, style, buf, cap):
        self.calls += 1
        if isinstance(self.answer, int):
            return self.answer
        data = self.answer.encode()
        import ctypes
        ctypes.memmove(buf, data, len(data))
        return len(data)


class BehaviourTests(unittest.TestCase):
    def test_unavailable_library_is_a_silent_no_op(self):
        lines = []
        # lib=None means "load the packaged library", which exists on this Mac, so force the unavailable case explicitly
        with unittest.mock.patch.object(st, "_load_lib", return_value=None):
            sh = st.StoreShadow(lines.append, lib=None, start_worker=False)
        self.assertFalse(sh.submit("queue.json", "[]\n"))
        self.assertIsNone(sh.check("queue.json", "[]\n"))
        self.assertEqual(lines, [])

    def test_a_payload_rust_cannot_reproduce_is_a_mismatch_with_the_layouts_tried(self):
        lines = []
        sh = st.StoreShadow(lines.append, lib=Fake("different"), start_worker=False)
        self.assertFalse(sh.check("queue.json", "[1]\n"))
        self.assertEqual(sh.mismatches, 1)
        self.assertIn("[STORE-SHADOW] MISMATCH store=queue.json no_layout_reproduces_the_bytes tried=indent2+newline", lines[0])

    def test_a_document_rust_refuses_under_every_layout_is_reported_as_refused(self):
        lines = []
        sh = st.StoreShadow(lines.append, lib=Fake(-1), start_worker=False)
        self.assertFalse(sh.check("queue.json", "[NaN]"))
        self.assertIn("rust_refused code=-1", lines[0])

    def test_first_agreement_per_store_is_logged_once_with_its_layout(self):
        lines = []
        sh = st.StoreShadow(lines.append, lib=Fake("[]\n"), start_worker=False)
        for _ in range(5):
            self.assertTrue(sh.check("queue.json", "[]\n"))
        self.assertTrue(sh.check("other.json", "[]\n"))
        self.assertEqual([l for l in lines if "decision" in l],
                         ["[STORE-SHADOW] decision store=queue.json layout=indent2+newline agree=1",
                          "[STORE-SHADOW] decision store=other.json layout=indent2+newline agree=1"])

    def test_a_full_queue_drops_the_newest_check_instead_of_blocking_the_caller(self):
        lines = []
        sh = st.StoreShadow(lines.append, lib=Fake("[]"), start_worker=False)  # nobody drains the queue
        results = [sh.submit("queue.json", "[]") for _ in range(st.MAX_PENDING + 3)]
        self.assertEqual(results.count(True), st.MAX_PENDING)
        self.assertEqual(sh.dropped, 3)
        self.assertTrue(any("dropped checks=1" in l for l in lines))

    def test_the_worker_checks_in_the_background_and_survives_a_bad_item(self):
        lines = []
        sh = st.StoreShadow(lines.append, lib=Fake("[]"))
        sh.submit("a.json", "[]")
        sh.submit("b.json", None)  # not a string: the check must fail without killing the worker
        sh.submit("c.json", "[]")
        self.assertTrue(sh.wait_idle(10))
        self.assertGreaterEqual(sh.checks, 2)
        sh.submit("d.json", "[]")
        self.assertTrue(sh.wait_idle(10))
        self.assertTrue(sh._worker.is_alive())

    def test_the_worker_survives_even_if_check_itself_raises(self):
        lines = []
        sh = st.StoreShadow(lines.append, lib=Fake("[]"))
        real_check, calls = sh.check, []

        def flaky(store, payload):
            calls.append(store)
            if store == "bad.json":
                raise RuntimeError("check blew up")
            return real_check(store, payload)

        sh.check = flaky
        sh.submit("bad.json", "[]")
        sh.submit("good.json", "[]")
        self.assertTrue(sh.wait_idle(10))
        self.assertEqual(calls, ["bad.json", "good.json"], "the item after the failure was still processed")
        self.assertTrue(any("worker error store=bad.json" in l for l in lines))

    def test_a_failing_log_callback_cannot_break_the_worker(self):
        def boom(_t):
            raise RuntimeError("log failed")
        sh = st.StoreShadow(boom, lib=Fake("different"))
        sh.submit("queue.json", "[1]\n")
        self.assertTrue(sh.wait_idle(10))
        self.assertTrue(sh._worker.is_alive())


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the store export")
class RealLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = sr.load_lib(str(_DYLIB))

    def shadow(self):
        self.lines = []
        return st.StoreShadow(self.lines.append, lib=self.lib, start_worker=False)

    def test_every_layout_the_app_writes_is_reproduced(self):
        rng = random.Random(11)
        sh = self.shadow()
        for n in range(300):
            doc = random_value(rng)
            doc = doc if isinstance(doc, (dict, list)) else [doc]
            style = STYLES[n % len(STYLES)]
            if style.get("sort_keys") or style.get("indent") == 4 or (style.get("ensure_ascii") is False):
                continue  # layouts the app never writes
            payload = sr.python_dumps(doc, style)
            self.assertTrue(sh.check("x.json", payload), (style, payload[:80]))
        self.assertEqual(sh.mismatches, 0)

    def test_the_real_store_layouts_by_name(self):
        sh = self.shadow()
        data = {"requests": {"2": {"title": "\u00e9"}, "1": {"t": 1.5}}, "version": 1}
        cases = {
            "settings.json": json.dumps({"k": [1, 2.5, "\u00e9"]}, indent=2) + "\n",
            "queue.json": json.dumps([{"a": 1}], indent=2) + "\n",
            "tracks.json": json.dumps([{"p": "\u00e9"}], separators=(",", ":")) + "\n",
            "remote_request_tombstones.json": json.dumps(data, indent=2, sort_keys=True),
            "bg_playlist.json": json.dumps(["/m/a.mp3"], indent=2),
            "loudness.json": json.dumps({"/a.mp3": {"i": -8.3}}),
        }
        for name, payload in cases.items():
            self.assertTrue(sh.check(name, payload), name)
        layouts = {l.split("store=")[1].split(" ")[0]: l.split("layout=")[1].split(" ")[0] for l in self.lines if "decision" in l}
        self.assertEqual(layouts["tracks.json"], "compact+newline")
        self.assertEqual(layouts["bg_playlist.json"], "indent2")
        self.assertEqual(layouts["loudness.json"], "default")

    def test_a_hand_edited_file_and_unreproducible_values_are_flagged(self):
        sh = self.shadow()
        self.assertFalse(sh.check("settings.json", '{"a":   1}'))
        self.assertFalse(sh.check("queue.json", "[18446744073709551616]\n"))
        self.assertFalse(sh.check("queue.json", "[NaN]\n"))
        self.assertEqual(sh.mismatches, 3)
        self.assertTrue(any("rust_refused" in l for l in self.lines))

    def test_the_actual_singer_history_size_is_cheap_enough_for_a_background_check(self):
        sh = self.shadow()
        rows = [{"singer": f"Singer {i}", "songs": [{"title": f"Song {j}", "artist": "A", "ts": 1790000000000 + j, "key": 0,
                                                        "avg": 0.5 + j / 1000} for j in range(40)]} for i in range(400)]
        payload = json.dumps(rows, indent=2) + "\n"
        self.assertGreater(len(payload), 900_000)
        t = time.perf_counter()
        self.assertTrue(sh.check("singer_history.json", payload))
        elapsed_ms = (time.perf_counter() - t) * 1000
        self.assertLess(elapsed_ms, 2000, f"{elapsed_ms:.0f} ms")


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the store export")
class RealWriterTests(unittest.TestCase):
    """The app's REAL `_atomic_write_text` (and `_save_json_atomic`), with the shadow attached the way the app attaches it."""

    @classmethod
    def setUpClass(cls):
        cls.lib = sr.load_lib(str(_DYLIB))

    def namespace(self, td, submit):
        import fcntl
        ns = {"json": json, "os": os, "tempfile": tempfile, "hashlib": hashlib, "time": time, "Path": Path, "_fcntl": fcntl,
              "_errno": errno, "_DURABLE_JSON_WRITES": True, "SETTINGS_PATH": Path(td) / "settings.json",
              "TRACKS_PATH": Path(td) / "tracks.json", "_JSON_WRITE_DIGESTS": {}, "_perf_record": lambda *a, **k: None,
              "_settings_shadow_for": lambda v: None, "_store_shadow_submit": submit}
        for fn in ("_fsync_durable", "_fsync_directory", "_atomic_write_text", "_save_json_atomic"):
            app_function(fn, ns)
        return ns

    def test_every_store_the_app_writes_agrees_when_written_through_the_real_writer(self):
        with tempfile.TemporaryDirectory() as td:
            lines = []
            sh = st.StoreShadow(lines.append, lib=self.lib)
            ns = self.namespace(td, sh.submit)
            rng = random.Random(3)
            for name in ("queue.json", "singer_history.json", "singer_preferences.json", "settings.json", "tracks.json"):
                for _ in range(8):
                    doc = {random_string(rng) or "k": random_value(rng, 2) for _ in range(rng.randint(1, 6))}
                    ns["_save_json_atomic"](Path(td) / name, doc)
            ns["_atomic_write_text"](Path(td) / "remote_request_tombstones.json", json.dumps({"requests": {}, "version": 1}, indent=2, sort_keys=True))
            ns["_atomic_write_text"](Path(td) / "bg_playlist.json", json.dumps(["/a.mp3"], indent=2))
            self.assertTrue(sh.wait_idle(30))
            self.assertEqual(sh.mismatches, 0, lines)
            seen = {l.split("store=")[1].split(" ")[0] for l in lines if "decision" in l}
            self.assertEqual(seen, {"queue.json", "singer_history.json", "singer_preferences.json", "settings.json", "tracks.json",
                                    "remote_request_tombstones.json", "bg_playlist.json"})

    def test_the_hook_runs_only_after_a_successful_durable_write(self):
        with tempfile.TemporaryDirectory() as td:
            submitted = []
            ns = self.namespace(td, lambda name, payload: submitted.append((name, payload)))
            ns["_atomic_write_text"](Path(td) / "queue.json", "[1]\n")
            self.assertEqual(submitted, [("queue.json", "[1]\n")])
            # a failed write (destination is a non-empty directory) submits nothing
            blocked = Path(td) / "blocked.json"
            (blocked / "child").mkdir(parents=True)
            with self.assertRaises(OSError):
                ns["_atomic_write_text"](blocked, "[2]\n")
            self.assertEqual(len(submitted), 1)

    def test_a_crash_inside_the_shadow_hook_cannot_fail_or_slow_a_save(self):
        with tempfile.TemporaryDirectory() as td:
            def explode(name, payload):
                raise RuntimeError("shadow bug")
            ns = self.namespace(td, explode)
            # the app's real helper swallows everything; the namespace stand-in must be wrapped the same way to mirror it
            real = app_function("_atomic_write_text", dict(ns, _store_shadow_submit=lambda n, p: None))
            real(Path(td) / "queue.json", "[1]\n")
            self.assertEqual((Path(td) / "queue.json").read_text(), "[1]\n")

    def test_submitting_a_one_megabyte_store_adds_almost_nothing_to_the_save(self):
        with tempfile.TemporaryDirectory() as td:
            sh = st.StoreShadow(lambda _t: None, lib=self.lib)
            payload = json.dumps([{"i": i, "s": "x" * 40} for i in range(14000)], indent=2) + "\n"
            self.assertGreater(len(payload), 900_000)
            plain = self.namespace(td, lambda n, p: None)
            shadowed = self.namespace(td, sh.submit)
            def median_ms(ns):
                times = []
                for i in range(7):
                    t = time.perf_counter()
                    ns["_atomic_write_text"](Path(td) / f"s{i}.json", payload)
                    times.append((time.perf_counter() - t) * 1000)
                return sorted(times)[len(times) // 2]
            base, with_shadow = median_ms(plain), median_ms(shadowed)
            self.assertTrue(sh.wait_idle(30))
            self.assertLess(with_shadow - base, 25.0, f"base {base:.1f} ms, with shadow {with_shadow:.1f} ms")
            self.assertEqual(sh.mismatches, 0)


class WiringTests(unittest.TestCase):
    def test_off_by_default_read_from_the_loaded_settings_and_never_synchronous(self):
        self.assertIn('"store_shadow": True', _SRC)
        self.assertIn('globals()["_STORE_SHADOW_ON"] = bool(loaded.get("store_shadow", False))', _SRC)
        helper = _SRC[_SRC.index("def _store_shadow_submit"):][:800]
        self.assertIn(".submit(name, payload)", helper)
        self.assertNotIn(".check(", helper, "the GUI-thread helper may only queue work for the background worker")
        self.assertIn("except Exception:", helper)

    def test_the_hook_is_in_the_else_branch_after_the_durable_write(self):
        body = _SRC[_SRC.index("def _atomic_write_text"):][:1700]
        self.assertLess(body.index("_fsync_directory(path.parent)"), body.index("    else:\n        _store_shadow_submit(path.name, payload)"))

    def test_specs_bundle_the_module(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = Path(__file__).with_name(spec).read_text()
            self.assertIn('"store_shadow.py"', text)
            self.assertIn("'store_shadow'", text)

    def test_the_log_formats_match_the_report_tool(self):
        text = Path(__file__).with_name("store_shadow.py").read_text()
        for needle in ("decision store={store} layout={layout} agree=1", "MISMATCH store={store}"):
            self.assertIn(needle, text)
        self.assertIn("[STORE-SHADOW] enabled available=", _SRC)


if __name__ == "__main__":
    unittest.main()
