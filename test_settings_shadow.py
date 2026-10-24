import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import ast
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import random
import stat
import tempfile
import time
import unittest
from pathlib import Path

import settings_shadow as ss
from test_store_roundtrip import random_value, random_string

_DYLIB = Path(os.environ.get("SINGWS_DSP_LIB") or os.path.expanduser("~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))
_SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


def _lib():
    lib = ctypes.CDLL(str(_DYLIB))
    fn = lib.singws_settings_json
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
    fn.restype = ctypes.c_int64
    return lib


_FUNCTIONS = None


def app_function(name, namespace):
    """Compile the app's own module-level function `name` (not a re-implementation) into `namespace`.

    The 62,000-line source is parsed ONCE per process (about 1.4 s); every later call only compiles the one function."""
    global _FUNCTIONS
    if _FUNCTIONS is None:
        _FUNCTIONS = {n.name: n for n in ast.parse(_SRC).body if isinstance(n, ast.FunctionDef)}
    node = _FUNCTIONS.get(name)
    if node is None:
        raise AssertionError(f"{name} not found in the app")
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "0.2.18.1.py", "exec"), namespace)
    return namespace[name]


_METHODS = None


def app_method(name, namespace):
    """Compile the app's own METHOD `name` (unique across all classes) as a plain function taking `self` first."""
    global _METHODS
    if _METHODS is None:
        _METHODS = {}
        for cls in (n for n in ast.parse(_SRC).body if isinstance(n, ast.ClassDef)):
            for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
                _METHODS.setdefault(fn.name, []).append(fn)
    nodes = _METHODS.get(name, [])
    if len(nodes) != 1:
        raise AssertionError(f"{name} must exist exactly once as a method, found {len(nodes)}")
    node = ast.FunctionDef(name=name, args=nodes[0].args, body=nodes[0].body, decorator_list=[], returns=None,
                           type_comment=None, type_params=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "0.2.18.1.py", "exec"), namespace)
    return namespace[name]


def load_oracle():
    return app_function("_load_json_file", {"json": json, "Path": Path})


def random_settings(rng):
    keys = [random_string(rng) or "k" for _ in range(rng.randint(0, 25))]
    return {k: random_value(rng, 2) for k in keys}


class Fake:
    def __init__(self, answer):
        self.answer = json.dumps(answer).encode()
        self.singws_settings_json = self._call

    def _call(self, request, buf, cap):
        ctypes.memmove(buf, self.answer, len(self.answer))
        return len(self.answer)


class ShadowBehaviourTests(unittest.TestCase):
    def test_unavailable_machine_is_a_silent_no_op(self):
        lines = []
        sh = ss.SettingsShadow(lines.append, lib=None)
        sh.available = False
        self.assertIsNone(sh.check_save({"a": 1}, "{}\n"))
        self.assertIsNone(sh.check_defaults({}, {"a": 1}, {"a": 1}))
        self.assertIsNone(sh.check_load("/nonexistent", {}))
        self.assertEqual(lines, [])

    def test_a_disagreement_is_logged_and_counted(self):
        lines = []
        sh = ss.SettingsShadow(lines.append, lib=Fake({"payload": "different"}))
        self.assertFalse(sh.check_save({"a": 1}, "{\n  \"a\": 1\n}\n"))
        self.assertEqual(sh.mismatches, 1)
        self.assertIn("[SETTINGS-SHADOW] MISMATCH check=save", lines[0])

    def test_values_json_cannot_carry_are_skipped_not_raised(self):
        sh = ss.SettingsShadow(lambda _t: None, lib=Fake({"payload": "x"}))
        self.assertIsNone(sh.check_save({"a": float("nan")}, "x"))
        self.assertIsNone(sh.check_save({"a": object()}, "x"))

    def test_a_missing_file_is_not_a_comparison(self):
        sh = ss.SettingsShadow(lambda _t: None, lib=Fake({"ok": True, "settings": {}}))
        self.assertIsNone(sh.check_load("/definitely/not/here/settings.json", {}))

    def test_a_failing_log_callback_cannot_break_the_caller(self):
        def boom(_t):
            raise RuntimeError("log failed")
        sh = ss.SettingsShadow(boom, lib=Fake({"payload": "different"}))
        self.assertFalse(sh.check_save({"a": 1}, "x"))


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the settings export")
class LoadDifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = _lib()
        cls.oracle = staticmethod(load_oracle())

    def agree(self, text):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(text, encoding="utf-8")
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):  # the app prints "Settings ignored: ..."
                result = self.oracle(path, {}, expected_type=dict, label="Settings")
            return ss.SettingsShadow(lambda _t: None, lib=self.lib).check_load(path, result)

    def test_everything_the_app_accepts_or_rejects_is_handled_the_same(self):
        rng = random.Random(31)
        texts = [json.dumps(random_settings(rng), indent=2) + "\n" for _ in range(300)]
        texts += ["{}", "{}\n", '{"a": 1, "a": 2}', '{"b": 1, "a": {"y": 2, "x": [1.0, -0.0, 1e-05]}}',
                  "[1, 2]", '"text"', "null", "42", "true", "{", "", "   ", "﻿{}", '{"a": }', "{'a': 1}",
                  '{"a": "\\u00e9\\ud83d\\ude00"}', '{"a": 1}  \n\n', '{"a": 1} trailing']
        bad = [(t[:60], self.agree(t)) for t in texts if self.agree(t) is not True]
        self.assertEqual(bad, [])

    def test_known_stricter_cases_are_reported_as_mismatches_not_hidden(self):
        # Python loads these; Rust deliberately refuses (or cannot represent) them, and the shadow must surface that.
        for text in ('{"a": 18446744073709551616}', '{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}',
                     "[" * 200 + "]" * 200 if False else '{"d":' + "[" * 200 + "]" * 200 + "}"):
            self.assertFalse(self.agree(text), text[:50])


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the settings export")
class DefaultsDifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = _lib()

    def test_the_oracle_is_the_apps_actual_fill_loop(self):
        self.assertIn("for k, v in DEFAULTS.items():\n            if k not in self.settings:\n                self.settings[k] = v\n                changed = True", _SRC)

    def test_random_settings_and_defaults_fill_identically_including_key_order(self):
        rng = random.Random(77)
        sh = ss.SettingsShadow(lambda _t: None, lib=self.lib)
        for n in range(500):
            defaults = random_settings(rng)
            saved = {k: v for k, v in random_settings(rng).items()}
            for k in list(defaults)[: rng.randint(0, len(defaults))]:
                saved[k] = random_value(rng, 3)  # some saved values override defaults, including falsy ones
            after = dict(saved)
            for k, v in defaults.items():  # the app's loop, verbatim
                if k not in after:
                    after[k] = v
            self.assertTrue(sh.check_defaults(saved, defaults, after), n)
        self.assertEqual(sh.mismatches, 0)

    def test_the_apps_real_defaults_against_the_live_style_settings(self):
        class Lenient(dict):  # DEFAULTS refers to a few named constants; any unknown name resolves to its own text
            def __missing__(self, name):
                return f"<{name}>"

        defaults = None
        for node in ast.parse(_SRC).body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DEFAULTS" for t in node.targets):
                defaults = eval(compile(ast.Expression(node.value), "DEFAULTS", "eval"), {"__builtins__": {}}, Lenient())
        self.assertIsInstance(defaults, dict)
        self.assertGreater(len(defaults), 100)  # the real table (173 keys when written)
        saved = dict(list(defaults.items())[::2])  # every other key already saved
        saved["legacy_unknown_key"] = {"keep": [1, 2.5]}
        after = dict(saved)
        for k, v in defaults.items():
            if k not in after:
                after[k] = v
        sh = ss.SettingsShadow(lambda _t: None, lib=self.lib)
        self.assertTrue(sh.check_defaults(saved, defaults, after))


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the settings export")
class SaveDifferentialTests(unittest.TestCase):
    """Runs the app's real `_save_json_atomic` against a private temp directory and compares it with Rust."""

    @classmethod
    def setUpClass(cls):
        cls.lib = _lib()

    def namespace(self, td, shadow):
        calls = []
        ns = {
            "json": json, "os": os, "tempfile": tempfile, "hashlib": hashlib, "time": time, "Path": Path,
            "SETTINGS_PATH": Path(td) / "settings.json", "TRACKS_PATH": Path(td) / "tracks.json",
            "_JSON_WRITE_DIGESTS": {}, "_perf_record": lambda *a, **k: None,
            "_settings_shadow_for": lambda value: shadow, "calls": calls,
            "fcntl": fcntl, "_fcntl": fcntl, "_errno": errno, "_DURABLE_JSON_WRITES": True,
        }
        app_function("_fsync_durable", ns)
        app_function("_fsync_directory", ns)
        app_function("_atomic_write_text", ns)
        return ns, app_function("_save_json_atomic", ns)

    def test_the_file_the_app_writes_is_what_rust_serializes_and_the_shadow_hook_agrees(self):
        rng = random.Random(5)
        with tempfile.TemporaryDirectory() as td:
            shadow = ss.SettingsShadow(lambda _t: None, lib=self.lib)
            ns, save = self.namespace(td, shadow)
            for n in range(120):
                settings = random_settings(rng)
                save(ns["SETTINGS_PATH"], settings)
                written = ns["SETTINGS_PATH"].read_text(encoding="utf-8")
                rust = shadow._call({"op": "serialize", "settings": settings})["payload"]
                self.assertEqual(written, rust, n)
            self.assertEqual(shadow.mismatches, 0)
            self.assertGreaterEqual(shadow.checks, 100, "the hook inside the real function fired for the writes that changed content")
            self.assertEqual(stat.S_IMODE(ns["SETTINGS_PATH"].stat().st_mode), 0o600)
            self.assertEqual([p.name for p in Path(td).iterdir()], ["settings.json"])

    def test_the_hook_does_not_fire_when_the_app_skips_an_unchanged_write(self):
        with tempfile.TemporaryDirectory() as td:
            shadow = ss.SettingsShadow(lambda _t: None, lib=self.lib)
            ns, save = self.namespace(td, shadow)
            save(ns["SETTINGS_PATH"], {"a": 1})
            before = shadow.checks
            mtime = ns["SETTINGS_PATH"].stat().st_mtime_ns
            save(ns["SETTINGS_PATH"], {"a": 1})
            self.assertEqual(shadow.checks, before)
            self.assertEqual(ns["SETTINGS_PATH"].stat().st_mtime_ns, mtime)

    def test_other_files_are_never_sent_to_the_settings_shadow(self):
        with tempfile.TemporaryDirectory() as td:
            shadow = ss.SettingsShadow(lambda _t: None, lib=self.lib)
            ns, save = self.namespace(td, shadow)
            save(Path(td) / "queue.json", [{"a": 1}])
            save(ns["TRACKS_PATH"], [{"t": "é"}])
            self.assertEqual(shadow.checks, 0)


class WiringTests(unittest.TestCase):
    def test_off_by_default_and_log_only(self):
        self.assertIn('"settings_shadow": False', _SRC)
        block = _SRC[_SRC.index("def _settings_shadow_for"):][:700]
        self.assertIn("except Exception:", block)
        self.assertNotIn("singws_settings", _SRC.replace("singws_settings_json", ""), "no other settings export is used by the app")

    def test_rust_can_never_write_through_the_exported_library(self):
        ffi = Path(__file__).parent / "rust" / "crates" / "singws-dsp-ffi" / "src" / "lib.rs"
        text = ffi.read_text(encoding="utf-8")
        block = text[text.index("pub unsafe extern \"C\" fn singws_settings_json"):][:900]
        self.assertIn("settings_bridge", block)
        for forbidden in ("write_atomic", "save(", "fs::write", "OpenOptions"):
            self.assertNotIn(forbidden, block)
        bridge = (Path(__file__).parent / "rust/crates/singws-store/src/settings.rs").read_text(encoding="utf-8")
        body = bridge[bridge.index("pub fn settings_bridge"):bridge.index("#[cfg(test)]")]
        for forbidden in ("write_atomic", "save(", "fs::"):
            self.assertNotIn(forbidden, body, "the bridge must stay pure")

    def test_three_hooks_in_pipeline_order(self):
        load = _SRC.index("shadow.check_load(SETTINGS_PATH, loaded)")
        fill_before = _SRC.index("_fill_before = dict(self.settings)")
        fill_after = _SRC.index(".check_defaults(_fill_before, DEFAULTS, self.settings)")
        save = _SRC.index("shadow.check_save(value, payload)")
        self.assertLess(fill_before, _SRC.index("for k, v in DEFAULTS.items():", fill_before))
        self.assertLess(_SRC.index("for k, v in DEFAULTS.items():", fill_before), fill_after)
        self.assertLess(_SRC.index("if path == SETTINGS_PATH:", 0), save)
        # the save hook sits after the unchanged-payload skip and before the file is written
        skip = _SRC.index("json_write_skipped", 0)
        self.assertLess(skip, save)
        self.assertLess(save, _SRC.index("_atomic_write_text(path, payload)", skip))
        self.assertGreater(load, 0)

    def test_specs_bundle_the_module(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = Path(__file__).with_name(spec).read_text()
            self.assertIn('"settings_shadow.py"', text)
            self.assertIn("'settings_shadow'", text)


if __name__ == "__main__":
    unittest.main()
