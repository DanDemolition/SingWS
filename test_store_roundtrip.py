import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import contextlib
import io
import json
import math
import os
import random
import struct
import tempfile
import unittest
from pathlib import Path

from tools import store_roundtrip as sr

_DYLIB = Path(os.environ.get("SINGWS_DSP_LIB") or os.path.expanduser("~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))

STYLES = [
    {"indent": 2, "trailing_newline": True},
    {"indent": 2, "ensure_ascii": False},
    {"indent": 4, "sort_keys": True},
    {"compact": True, "trailing_newline": True},
    {"compact": True, "ensure_ascii": False, "sort_keys": True},
    {},
    {"ensure_ascii": False, "sort_keys": True},
]

TRICKY_FLOATS = [0.0, -0.0, 1.0, 0.1, 0.5, 100.0, 1e-4, 9.999e-5, 1e-5, 1.5e-7, 1e15, 999999999999999.9, 1e16, 1.5e16, 1e22, 1e23,
                 123456789012345680.0, 5e-324, 2.2250738585072014e-308, 1.7976931348623157e308, 0.30000000000000004,
                 9007199254740993.0, 4.35, 2.675, 1e-10, 3.14159, -2.5e-8, 12345678901234567.0]

TRICKY_STRINGS = ["", "plain", "quote\"back\\slash", "ctl\x00\x01\x1f\x7f", "tab\tnl\ncr\rbs\bff\f", "é ü ñ", "日本語", "emoji 😀🎤",
                  "mixed    sep", "­​ zero-width", "a" * 300, "'single' and /slash/"]


def random_float(rng):
    if rng.random() < 0.4:
        f = rng.choice(TRICKY_FLOATS) * rng.choice([1, -1, 1, 10, 0.1])
        if math.isfinite(f):  # 1.79e308 * 10 overflows to inf, which Python writes as the non-JSON token Infinity
            return f
    while True:  # a uniformly random 64-bit pattern: exercises every exponent and mantissa shape
        f = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if math.isfinite(f):
            return f


def random_string(rng):
    if rng.random() < 0.5:
        return rng.choice(TRICKY_STRINGS)
    pool = list("abcXYZ 019-_.\"\\/") + ["é", "日", "😀", "\x00", "\x1f", "\x7f", " ", "퟿", "", "￿"]
    return "".join(rng.choice(pool) for _ in range(rng.randint(0, 12)))


def random_value(rng, depth=0):
    kinds = ["int", "float", "str", "bool", "null"] + (["list", "dict"] * 2 if depth < 4 else [])
    k = rng.choice(kinds)
    if k == "int":
        return rng.choice([0, 1, -1, 42, 1790974569756, -(2**31), 2**53 + 1, 2**63 - 1, -(2**63), 2**64 - 1, rng.randint(-10**6, 10**6)])
    if k == "float":
        return random_float(rng)
    if k == "str":
        return random_string(rng)
    if k == "bool":
        return rng.random() < 0.5
    if k == "null":
        return None
    if k == "list":
        return [random_value(rng, depth + 1) for _ in range(rng.randint(0, 5))]
    keys = [random_string(rng) for _ in range(rng.randint(0, 5))]
    return {key: random_value(rng, depth + 1) for key in keys}


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the store export")
class DifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = sr.load_lib(str(_DYLIB))

    def test_random_documents_in_every_layout_are_rewritten_byte_identically(self):
        rng = random.Random(20261023)
        bad = []
        for n in range(1500):
            doc = random_value(rng)
            if not isinstance(doc, (dict, list)):
                doc = [doc]
            style = STYLES[n % len(STYLES)]
            text = sr.python_dumps(doc, style)
            out = sr.rust_roundtrip(self.lib, text, style)
            if out != text:
                at = next((i for i, (a, b) in enumerate(zip(text, out if isinstance(out, str) else "")) if a != b), -1)
                bad.append((style, at, text[max(0, at - 30):at + 30], out if isinstance(out, int) else out[max(0, at - 30):at + 30]))
        self.assertEqual(bad[:3], [], f"{len(bad)} of 1500 documents differ")

    def test_float_repr_agrees_with_cpython_for_random_bit_patterns(self):
        rng = random.Random(99)
        floats = [random_float(rng) for _ in range(6000)]
        text = json.dumps(floats)
        out = sr.rust_roundtrip(self.lib, text, {})
        self.assertEqual(out, text)
        # and as the app writes them (one per line)
        self.assertEqual(sr.rust_roundtrip(self.lib, sr.python_dumps(floats, STYLES[0]), STYLES[0]), sr.python_dumps(floats, STYLES[0]))

    def test_values_python_can_write_but_rust_cannot_reproduce_are_errors_not_silent_changes(self):
        for text in ("[18446744073709551616]", "[-9223372036854775809]", "[NaN]", "[Infinity]", '["\\ud800"]', "{"):
            out = sr.rust_roundtrip(self.lib, text, {})
            self.assertIsInstance(out, int, f"{text!r} must be rejected, got {out!r}")
            self.assertLess(out, 0)

    def test_big_integer_digits_inside_strings_are_fine(self):
        text = json.dumps(["99999999999999999999999999", {"k": "-123456789012345678901234567890"}])
        self.assertEqual(sr.rust_roundtrip(self.lib, text, {}), text)


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the store export")
class ProfileToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = sr.load_lib(str(_DYLIB))

    def test_style_detection_matches_what_the_app_writes(self):
        data = {"b": [1, 2.5, "é"], "a": None}
        self.assertEqual(sr.detect_style(json.dumps(data, indent=2) + "\n"), {"indent": 2, "trailing_newline": True})
        self.assertEqual(sr.detect_style(json.dumps(data, separators=(",", ":")) + "\n"), {"compact": True, "trailing_newline": True})
        self.assertEqual(sr.detect_style(json.dumps(data, indent=2)), {"indent": 2})
        self.assertIsNone(sr.detect_style('{"a":   1}'), "hand-spaced JSON matches no Python layout")
        self.assertIsNone(sr.detect_style("not json"))

    def test_profile_check_reports_each_file_and_never_prints_contents(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "settings.json").write_text(json.dumps({"singer": "Secret Name", "k": [1.5]}, indent=2) + "\n")
            (Path(td) / "tracks.json").write_text(json.dumps([{"p": "é"}], separators=(",", ":")) + "\n")
            (Path(td) / "hand_edited.json").write_text('{"a":   1}')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                status = sr.main([td, "--lib", str(_DYLIB)])
            out = buf.getvalue()
        self.assertEqual(status, 0, out)
        self.assertIn("2 of 3 files rewritten byte-identically", out)
        self.assertIn("no_python_style", out)
        self.assertNotIn("Secret Name", out)


class WiringTests(unittest.TestCase):
    def test_nothing_in_the_app_uses_the_rust_store_yet(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertNotIn("singws_store", src, "Stage D is compatibility proof only; the app must not call the Rust writer yet")


if __name__ == "__main__":
    unittest.main()
