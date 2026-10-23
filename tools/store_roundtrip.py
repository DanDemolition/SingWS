#!/usr/bin/env python3
"""Stage D compatibility check: can Rust rewrite each persisted JSON store exactly as Python wrote it?

READ-ONLY. For every `*.json` in the profile (default ~/SingWS) the tool finds the Python `json.dumps` style that reproduces the file
byte for byte, hands the file's text and that style to the Rust writer (`singws_store_roundtrip`), and checks the bytes are identical.
A file that no known style reproduces (hand-edited, written by another tool, or a style the app never uses) is reported, not forced.
File contents are never printed; only names, sizes and verdicts.

Usage:  python3 tools/store_roundtrip.py [PROFILE_DIR] [--lib PATH]
Exit status 1 if any file Python can reproduce is NOT reproduced by Rust.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from pathlib import Path

# the two layouts the app itself writes (_save_json_atomic), then the other layouts seen in the profile
CANDIDATE_STYLES = [
    {"indent": 2, "trailing_newline": True},
    {"compact": True, "trailing_newline": True},
    {"indent": 2},
    {"compact": True},
    {},
    {"indent": 2, "ensure_ascii": False, "trailing_newline": True},
    {"compact": True, "ensure_ascii": False},
    {"compact": True, "ensure_ascii": False, "trailing_newline": True},
    {"ensure_ascii": False},
]


def python_dumps(data, style):
    kw = {"ensure_ascii": style.get("ensure_ascii", True), "sort_keys": style.get("sort_keys", False)}
    if style.get("indent") is not None:
        kw["indent"] = style["indent"]
    elif style.get("compact"):
        kw["separators"] = (",", ":")
    return json.dumps(data, **kw) + ("\n" if style.get("trailing_newline") else "")


def detect_style(text):
    """The first candidate style under which Python's own dumps reproduces `text` exactly, else None."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    for style in CANDIDATE_STYLES:
        try:
            if python_dumps(data, style) == text:
                return style
        except (ValueError, OverflowError):
            continue
    return None


def load_lib(path=None):
    for candidate in (path or "", os.environ.get("SINGWS_DSP_LIB") or "",
                      os.path.expanduser("~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib")):
        if candidate and Path(candidate).exists():
            lib = ctypes.CDLL(str(candidate))
            fn = lib.singws_store_roundtrip  # AttributeError if the dylib predates Stage D
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            return lib
    raise SystemExit("no libsingws_dsp_ffi.dylib with singws_store_roundtrip found (build rust/ or pass --lib)")


def rust_roundtrip(lib, text, style):
    """Rust's rewrite of `text` in `style`, or an int error code (negative) from the FFI."""
    raw = text.encode("utf-8")
    cap = len(raw) * 6 + 4096  # ensure_ascii can expand non-ASCII text up to 6x
    buf = ctypes.create_string_buffer(cap)
    n = lib.singws_store_roundtrip(raw, json.dumps(style).encode(), buf, cap)
    return n if n < 0 else buf.raw[:n].decode("utf-8")


def check_profile(lib, profile):
    results = []
    for path in sorted(Path(profile).glob("*.json")):
        text = path.read_text(encoding="utf-8")
        style = detect_style(text)
        if style is None:
            results.append((path.name, len(text), None, "no_python_style"))
            continue
        out = rust_roundtrip(lib, text, style)
        if isinstance(out, int):
            results.append((path.name, len(text), style, f"rust_error_{out}"))
        elif out == text:
            results.append((path.name, len(text), style, "identical"))
        else:
            results.append((path.name, len(text), style, "DIFFERENT"))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("profile", nargs="?", default=os.path.expanduser("~/SingWS"))
    parser.add_argument("--lib")
    args = parser.parse_args(argv)
    lib = load_lib(args.lib)
    results = check_profile(lib, args.profile)
    bad = 0
    for name, size, style, verdict in results:
        shown = "-" if style is None else json.dumps(style, sort_keys=True)
        print(f"{verdict:16s} {name:36s} {size:9d} bytes  style={shown}")
        bad += verdict not in ("identical", "no_python_style")
    print(f"{sum(v == 'identical' for *_, v in results)} of {len(results)} files rewritten byte-identically by Rust")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
