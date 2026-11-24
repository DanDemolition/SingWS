"""Song-end drive (Stage E): Rust answers one live song-end question, Python keeps the last word.

OPT-IN (setting ``songend_drive_prestart``, default OFF). The question: at this 500 ms tick, should the background music start under the song now (the "pre-start"
of ``update_time_left``)? The app computes its own answer exactly as before; with the setting on, ``singws-songend``'s rule (``prestart_due``, called through
``singws_songend_json`` in libsingws_dsp_ffi) is asked the same question and the two are compared. They agree: the Rust answer is used. They differ, or Rust cannot
answer: the Python answer is used and the difference is logged (``[SONGEND-DRIVE] ... disagreement=1``). So a wrong or missing Rust can never change what a show does.

Only ticks inside the last 8.5 s of a song are asked (the window is at most 8 s, so both answers are False earlier: nothing is lost by not asking).
"""
from __future__ import annotations

import ctypes
import json

TAG = "SONGEND-DRIVE"
_OUT_CAP = 256
ASK_WITHIN_NS = 8_500_000_000
_lib = None
_loaded = False


def _library():
    global _lib, _loaded
    if _loaded:
        return _lib
    _loaded = True
    try:
        from rust_master_dsp import _candidate_paths
        for path in _candidate_paths():
            if not path.exists():
                continue
            lib = ctypes.CDLL(str(path))
            fn = lib.singws_songend_json          # AttributeError when this dylib predates the song-end drive
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            _lib = lib
            return _lib
    except Exception:
        pass
    return None


def rust_prestart_due(remaining_ns: int, lead_s: float, crossfade: bool, prefired: bool, has_playlist: bool, bg_playing: bool, bg_enabled: bool, bg_autoplay: bool):
    """Rust's answer (True/False), or None when it could not answer."""
    lib = _library()
    if lib is None:
        return None
    request = {"op": "prestart_due", "crossfade": bool(crossfade), "remaining_ns": int(remaining_ns), "lead_s": float(lead_s), "prefired": bool(prefired),
               "has_playlist": bool(has_playlist), "bg_playing": bool(bg_playing), "bg_enabled": bool(bg_enabled), "bg_autoplay": bool(bg_autoplay)}
    out = ctypes.create_string_buffer(_OUT_CAP)
    n = lib.singws_songend_json(json.dumps(request).encode("utf-8"), out, _OUT_CAP)
    if n < 0:
        return None
    answer = json.loads(out.raw[:n].decode("utf-8"))
    value = answer.get("v")
    return bool(value) if isinstance(value, bool) else None


def decide_prestart(python_due: bool, remaining_ns: int, lead_s: float, crossfade: bool, prefired: bool, has_playlist: bool, bg_playing: bool, bg_enabled: bool,
                    bg_autoplay: bool, log=None):
    """(due, disagreed, owner): Rust's answer when it agrees with Python's, Python's otherwise. Never raises."""
    import transport_shadow
    rust = None
    try:
        if remaining_ns <= ASK_WITHIN_NS:
            rust = rust_prestart_due(remaining_ns, lead_s, crossfade, prefired, has_playlist, bg_playing, bg_enabled, bg_autoplay)
    except Exception as exc:
        if log:
            log(f"[{TAG}] decision=prestart fallback=python error={exc!r}")
        rust = None
    if remaining_ns > ASK_WITHIN_NS:
        return bool(python_due), False, "python"
    due, disagreed, owner = transport_shadow.select_drive_decision(python_due, rust, True)
    if log and (disagreed or due):
        log(f"[{TAG}] decision=prestart python={int(bool(python_due))} rust={'-' if rust is None else int(rust)} selected={int(due)} owner={owner} disagreement={int(disagreed)}")
    return due, disagreed, owner
