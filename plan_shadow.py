"""Stage C shadow hook: compares the app's BGM transition-planning answers with the Rust ``singws-plan`` rules and LOGS
disagreements.

SHADOW ONLY. The Rust answer never drives anything; every method swallows its own errors, so a missing dylib, a bad record or
a crash in here cannot affect a show. Setting ``plan_shadow`` (default off until the package gate passes).

The two hooked decisions run inside the per-tick background-music monitor, so each check is de-duplicated: the same inputs as
the previous check of that kind are skipped, and a track costs one comparison, not one per timer tick.

Log lines (grep ``PLAN-SHADOW``):
  * ``decision``  first time each (op, answer-kind) is seen, e.g. crossfade reason natural_fade
  * ``MISMATCH``  Rust computed something different than the app (the app's answer was used)
  * ``summary``   running totals every SUMMARY_EVERY checks
"""

from __future__ import annotations

import ctypes
import json

_OUT_CAP = 4096
SUMMARY_EVERY = 100


class PlanShadow:
    def __init__(self, log, lib=None):
        self._log = log
        self._lib = lib if lib is not None else _load_lib()
        self.available = self._lib is not None
        self.checks = 0
        self.mismatches = 0
        self._last = {}
        self._seen = set()

    def _say(self, text):
        try:
            self._log(f"[PLAN-SHADOW] {text}")
        except Exception:
            pass

    def _call(self, request):
        """Rust's answer for a request dict, or None when it cannot answer (library missing, NaN in the record, ...)."""
        if not self.available:
            return None
        try:
            payload = json.dumps(request, allow_nan=False)
        except (TypeError, ValueError):
            return None
        buf = ctypes.create_string_buffer(_OUT_CAP)
        n = self._lib.singws_plan_json(payload.encode(), buf, _OUT_CAP)
        if n < 0:
            self._say(f"plan call failed code={n} op={request.get('op')}")
            return None
        return json.loads(buf.raw[:n].decode())

    # The only fields the window / crossfade rules read. Copying just these (never asdict) keeps the per-tick cost tiny: the
    # record also carries a ~1,900-sample envelope that the rules do not use.
    _FIELDS = ("analysis_version", "duration", "hop_seconds", "audio_start", "audio_end", "fade_start", "fade_confidence",
               "visual_start", "visual_end", "visual_confidence", "effective_karaoke_end", "safe_for_early_completion")

    @classmethod
    def _record_dict(cls, record):
        if record is None:
            return None
        return {name: getattr(record, name, None) for name in cls._FIELDS if getattr(record, name, None) is not None}

    def _once(self, op, key):
        """True when this exact input was already checked last time for ``op``."""
        if self._last.get(op) == key:
            return True
        self._last[op] = key
        return False

    def _finish(self, op, kind, agree, detail):
        self.checks += 1
        if not agree:
            self.mismatches += 1
            self._say(f"MISMATCH op={op} {detail}")
        elif (op, kind) not in self._seen:
            self._seen.add((op, kind))
            self._say(f"decision op={op} kind={kind} agree=1")
        if self.checks % SUMMARY_EVERY == 0:
            self._say(f"summary checks={self.checks} mismatches={self.mismatches}")
        return agree

    def check_window(self, record, duration, py_result):
        """Compare ``bgm_audio_window(record, duration)`` with Rust. Never raises; returns agree / None when skipped."""
        try:
            if not self.available:
                return None
            rec = self._record_dict(record)
            key = (tuple(sorted(rec.items())) if rec is not None else None, duration)
            if self._once("window", key):
                return None
            out = self._call({"op": "window", "record": rec, "duration": duration})
            if out is None:
                return None
            got = (out["start"], out["end"])
            return self._finish("window", "bounds", got == tuple(py_result),
                                f"duration={duration!r} app={tuple(py_result)!r} rust={got!r}")
        except Exception as exc:
            self._say(f"check_window error: {exc!r}")
            return None

    def check_crossfade(self, record, default_seconds, py_result):
        """Compare ``select_bgm_crossfade_seconds(record, default_seconds)`` with Rust. Never raises."""
        try:
            if not self.available:
                return None
            rec = self._record_dict(record)
            key = (tuple(sorted(rec.items())) if rec is not None else None, default_seconds)
            if self._once("crossfade", key):
                return None
            out = self._call({"op": "crossfade", "record": rec, "default_seconds": default_seconds})
            if out is None:
                return None
            got = (out["seconds"], out["reason"])
            return self._finish("crossfade", got[1], got == tuple(py_result),
                                f"default={default_seconds!r} app={tuple(py_result)!r} rust={got!r}")
        except Exception as exc:
            self._say(f"check_crossfade error: {exc!r}")
            return None


def _load_lib():
    try:
        from rust_master_dsp import _candidate_paths
        for path in _candidate_paths():
            if not path.exists():
                continue
            lib = ctypes.CDLL(str(path))
            fn = lib.singws_plan_json  # AttributeError if this dylib predates Stage C
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            return lib
    except Exception:
        pass
    return None
