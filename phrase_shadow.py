"""Shadow for the waveform editor's analysis: the Rust port (`singws-phrase`, called through `singws_phrase_json` in libsingws_dsp_ffi) is run on the SAME samples as
`phrase_detect.py` (numpy/scipy) and the two answers are compared. SHADOW ONLY: the editor always uses Python's answer; every function here swallows its own errors, so a
missing dylib or a crash in here cannot affect the editor or a show. On by default (setting ``phrase_shadow``, read once at launch). It runs on the editor's own worker
threads, after Python has finished, so it never delays the first answer.

Why a tolerance and not equality: numpy/scipy do their FFTs in float32, the port in f64, so the answers cannot be identical. The same rules as `tools/phrase_parity.py`:
tempo within 0.05 BPM, first beat within 0.02 s, confidence within 0.02; waveform peaks equal (float32); suggested start within 0.06 s (and both "none"); sections equal in
number, each within 0.26 s with the same label.

Log lines (grep ``PHRASE-SHADOW``), no song names, nothing private:
  decision=<tempo|peaks|suggest|sections> agree=0|1 py_ms=<n> rust_ms=<n> [known=none_vs_zero] [detail]     one per compared decision
  MISMATCH <decision> ...                                                                                   the numbers that differ
"""

from __future__ import annotations

import ctypes
import json
import time

_lib = None
_loaded = False
_enabled = False
_log = lambda text: None  # noqa: E731
_OUT_CAP = 1 << 20

BPM_TOL = 0.05
FIRST_BEAT_TOL = 0.02
CONFIDENCE_TOL = 0.02
SUGGEST_TOL = 0.06
SECTION_TOL = 0.26


def configure(enabled: bool, log) -> None:
    """Called once at launch with the setting and the app's diag function."""
    global _enabled, _log
    _enabled = bool(enabled)
    _log = log if callable(log) else (lambda text: None)
    if _enabled:
        available = _library() is not None
        _say(f"enabled available={int(available)}")


def enabled() -> bool:
    return _enabled and _library() is not None


def _say(text: str) -> None:
    try:
        _log(f"[PHRASE-SHADOW] {text}")
    except Exception:
        pass


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
            fn = lib.singws_phrase_json          # AttributeError when this dylib predates the phrase shadow
            fn.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            _lib = lib
            return _lib
    except Exception:
        pass
    return None


def _rust(request: dict, pcm=None):
    """Run one op in Rust: returns (answer_value, milliseconds), or (None, 0) when it could not answer."""
    lib = _library()
    if lib is None:
        return None, 0.0
    import numpy as np
    samples = np.ascontiguousarray(pcm if pcm is not None else np.zeros(0, dtype=np.float32), dtype=np.float32)
    out = ctypes.create_string_buffer(_OUT_CAP)
    started = time.monotonic()
    n = lib.singws_phrase_json(json.dumps(request).encode("utf-8"), samples.ctypes.data if samples.size else None, int(samples.size), out, _OUT_CAP)
    ms = (time.monotonic() - started) * 1000.0
    if n < 0:
        _say(f"rust could not answer op={request.get('op')} code={n}")
        return None, ms
    answer = json.loads(out.raw[:n].decode("utf-8"))
    if "err" in answer:
        _say(f"rust error op={request.get('op')}")
        return None, ms
    return answer.get("v"), ms


def _row(decision: str, agree: bool, py_ms: float, rust_ms: float, extra: str = "") -> None:
    _say(f"decision={decision} agree={int(bool(agree))} py_ms={py_ms:.0f} rust_ms={rust_ms:.0f}" + (f" {extra}" if extra else ""))


def _mismatch(decision: str, detail: str) -> None:
    _say(f"MISMATCH {decision} {detail}")


def tempo_agrees(py, rs):
    """py: dict(bpm, first_beat, confidence) or None; rs: the same from Rust or None. Returns (agree, detail)."""
    if py is None or rs is None:
        return (py is None) == (rs is None), f"py_none={int(py is None)} rust_none={int(rs is None)}"
    fb_py, fb_rs = py.get("first_beat"), rs.get("first_beat")
    fb_ok = (fb_py is None) == (fb_rs is None) and (fb_py is None or abs(float(fb_py) - float(fb_rs)) <= FIRST_BEAT_TOL)
    bpm_ok = abs(float(py["bpm"]) - float(rs["bpm"])) <= BPM_TOL
    conf_ok = abs(float(py.get("confidence") or 0.0) - float(rs.get("confidence") or 0.0)) <= CONFIDENCE_TOL
    return bpm_ok and fb_ok and conf_ok, f"bpm py={float(py['bpm']):.2f} rust={float(rs['bpm']):.2f} first_beat_ok={int(fb_ok)} confidence_ok={int(conf_ok)}"


def compare_tempo(pcm, sr: int, py_result, py_ms: float = 0.0) -> None:
    """The quick BPM analysis (`estimate_tempo_and_beat`)."""
    if not enabled():
        return
    try:
        rs, rust_ms = _rust({"op": "tempo", "sr": int(sr), "min": 70.0, "max": 180.0}, pcm)
        if rs is None and py_result is not None and not _library():
            return
        agree, detail = tempo_agrees(py_result, rs)
        _row("tempo", agree, py_ms, rust_ms)
        if not agree:
            _mismatch("tempo", detail)
    except Exception as exc:
        _say(f"tempo error: {type(exc).__name__}")


def compare_waveform(pcm, sr: int, n_cols: int, bpm_given, py_peaks, py_bpm, py_suggest, py_sections, py_ms: float = 0.0) -> None:
    """The editor's full analysis: peaks, the estimated BPM (when none was given), the suggested start and the sections, each compared with Rust's."""
    if not enabled():
        return
    try:
        import numpy as np
        # peaks (exact)
        if py_peaks is not None:
            rs, rust_ms = _rust({"op": "peaks", "sr": int(sr), "n_cols": int(n_cols)}, pcm)
            ok = rs is not None and np.allclose(np.array(rs, dtype=np.float32), np.asarray(py_peaks, dtype=np.float32), atol=1e-7)
            _row("peaks", ok, py_ms, rust_ms)
            if not ok:
                _mismatch("peaks", f"columns py={len(py_peaks)} rust={0 if rs is None else len(rs)}")
        # BPM estimated from the audio (only when the editor had none)
        if not bpm_given and py_bpm:
            rs, rust_ms = _rust({"op": "tempo", "sr": int(sr), "min": 70.0, "max": 180.0}, pcm)
            rs_bpm = None if rs is None else rs.get("bpm")
            ok = rs_bpm is not None and abs(float(py_bpm) - float(rs_bpm)) <= BPM_TOL
            _row("tempo", ok, py_ms, rust_ms, "source=editor")
            if not ok:
                _mismatch("tempo", f"bpm py={float(py_bpm):.2f} rust={'none' if rs_bpm is None else f'{float(rs_bpm):.2f}'}")
        bpm = float(py_bpm) if py_bpm else None
        # suggested start
        rs, rust_ms = _rust({"op": "suggest_start", "sr": int(sr), "bpm": bpm, "fraction": 0.35}, pcm)
        py_s = None if py_suggest is None or float(py_suggest) < 0 else float(py_suggest)
        known = ""
        if py_s == 0.0 and rs is None:
            ok, known = True, "known=none_vs_zero"                 # the one boundary case documented in tools/phrase_parity.py
        else:
            ok = (py_s is None) == (rs is None) and (py_s is None or abs(py_s - float(rs)) <= SUGGEST_TOL)
        _row("suggest", ok, py_ms, rust_ms, known)
        if not ok:
            _mismatch("suggest", f"py={py_s} rust={rs}")
        # sections
        rs, rust_ms = _rust({"op": "sections", "sr": int(sr), "bpm": bpm}, pcm)
        py_sec = list(py_sections or [])
        ok = rs is not None and len(rs) == len(py_sec) and all(abs(float(a["seconds"]) - float(b["seconds"])) <= SECTION_TOL and a["label"] == b["label"] for a, b in zip(py_sec, rs))
        _row("sections", ok, py_ms, rust_ms, f"count_py={len(py_sec)} count_rust={0 if rs is None else len(rs)}")
        if not ok:
            _mismatch("sections", f"count py={len(py_sec)} rust={0 if rs is None else len(rs)}")
    except Exception as exc:
        _say(f"waveform error: {type(exc).__name__}")
