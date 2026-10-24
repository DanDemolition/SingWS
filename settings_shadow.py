"""Stage D1 shadow hook: compares the app's settings-store steps with the Rust ``singws-store`` implementation and LOGS disagreements.

SHADOW ONLY. Rust never writes anything and never changes what the app loads or saves (the library does not even export a write
function); every method swallows its own errors, so a missing dylib, an odd value or a crash in here cannot affect a show.
Setting ``settings_shadow`` (default off until the package gate passes), read from the settings dict being processed.

Three checks, one per step of the settings pipeline:
  * ``load``      the file's text parsed by Rust equals what ``_load_json_file(..., expected_type=dict)`` returned (including key order)
  * ``defaults``  Rust's ``DEFAULTS`` fill of the pre-fill dict equals what the app's fill loop produced (values, order, added keys)
  * ``save``      Rust's serialization of the settings equals the exact payload ``_save_json_atomic`` is about to write

Log lines (grep ``SETTINGS-SHADOW``): ``decision`` (first agreement per check), ``MISMATCH``, ``summary`` every SUMMARY_EVERY checks.
"""

from __future__ import annotations

import ctypes
import json

_CAP_FLOOR = 4096
SUMMARY_EVERY = 50


class SettingsShadow:
    def __init__(self, log, lib=None):
        self._log = log
        self._lib = lib if lib is not None else _load_lib()
        self.available = self._lib is not None
        self.checks = 0
        self.mismatches = 0
        self._seen = set()

    def _say(self, text):
        try:
            self._log(f"[SETTINGS-SHADOW] {text}")
        except Exception:
            pass

    def _call(self, request):
        """Rust's JSON answer, or None when it cannot answer (library missing, a value JSON cannot carry, buffer error)."""
        if not self.available:
            return None
        try:
            payload = json.dumps(request, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            return None
        cap = len(payload) * 8 + _CAP_FLOOR  # ensure_ascii can expand text ~6x, plus JSON-in-JSON escaping
        buf = ctypes.create_string_buffer(cap)
        n = self._lib.singws_settings_json(payload, buf, cap)
        if n < 0:
            self._say(f"call failed code={n} op={request.get('op')}")
            return None
        return json.loads(buf.raw[:n].decode("utf-8"))

    def _finish(self, check, agree, detail):
        self.checks += 1
        if not agree:
            self.mismatches += 1
            self._say(f"MISMATCH check={check} {detail}")
        elif check not in self._seen:
            self._seen.add(check)
            self._say(f"decision check={check} agree=1")
        if self.checks % SUMMARY_EVERY == 0:
            self._say(f"summary checks={self.checks} mismatches={self.mismatches}")
        return agree

    def check_load(self, path, python_result):
        """``python_result`` is what ``_load_json_file(path, {}, expected_type=dict)`` returned. Never raises."""
        try:
            if not self.available:
                return None
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    text = handle.read()
            except OSError:
                return None  # no file: the app used its default and there is nothing to compare
            out = self._call({"op": "parse", "text": text})
            if out is None:
                return None
            if out.get("ok"):
                same = json.dumps(out["settings"]) == _dumps_or_none(python_result)
                return self._finish("load", same, f"rust_keys={len(out['settings'])} app_keys={_len(python_result)}")
            # Rust could not use the text: the app must then also have fallen back to an empty dict
            same = isinstance(python_result, dict) and not python_result
            return self._finish("load", same, f"rust_issue={out.get('issue')} detail={str(out.get('detail', ''))[:80]!r} "
                                              f"app_keys={_len(python_result)}")
        except Exception as exc:
            self._say(f"check_load error: {exc!r}")
            return None

    def check_defaults(self, before, defaults, after):
        """``after`` is the dict the app's fill loop produced from ``before``. Never raises."""
        try:
            if not self.available:
                return None
            out = self._call({"op": "fill_defaults", "settings": before, "defaults": defaults})
            if out is None:
                return None
            app_added = [k for k in defaults if k not in before]
            same = json.dumps(out["settings"]) == _dumps_or_none(after) and out["added"] == app_added
            return self._finish("defaults", same, f"rust_added={out['added'][:5]} app_added={app_added[:5]}")
        except Exception as exc:
            self._say(f"check_defaults error: {exc!r}")
            return None

    def check_save(self, settings, payload):
        """``payload`` is the exact text ``_save_json_atomic`` will write for ``settings``. Never raises."""
        try:
            if not self.available:
                return None
            out = self._call({"op": "serialize", "settings": settings})
            if out is None:
                return None
            same = out["payload"] == payload
            at = next((i for i, (a, b) in enumerate(zip(out["payload"], payload)) if a != b), min(len(out["payload"]), len(payload)))
            return self._finish("save", same, f"first_difference_at={at} rust_len={len(out['payload'])} app_len={len(payload)}")
        except Exception as exc:
            self._say(f"check_save error: {exc!r}")
            return None


def _dumps_or_none(value):
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return None


def _len(value):
    try:
        return len(value)
    except TypeError:
        return -1


def _load_lib():
    try:
        from rust_master_dsp import _candidate_paths
        for path in _candidate_paths():
            if not path.exists():
                continue
            lib = ctypes.CDLL(str(path))
            fn = lib.singws_settings_json  # AttributeError if this dylib predates Stage D1
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            return lib
    except Exception:
        pass
    return None
