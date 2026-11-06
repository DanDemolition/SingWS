"""KaraFun search-script shadow (Stage E): every time the app builds the AppleScript that searches KaraFun, a separate low-priority process (Rust
``singws-karafun``) builds the SAME script from the same inputs and a DIFFERENCE from the app's script is logged.

SHADOW ONLY. The script the app runs is always the Python one; nothing here changes it, runs it, or touches KaraFun. Setting ``karafun_script_shadow``
(default True). Every failure is swallowed; a missing helper binary is skipped without a log line.

The log never contains the query, title or artist (only line numbers and counts):
  ``MISMATCH line N: the scripts differ``      the first differing line (counted from 0)
  ``MISMATCH length: app=A rust=B``            the scripts have a different number of lines
  ``skipped: <reason>``                        the helper failed or answered nonsense
Log lines carry the tag KARAFUN-SCRIPT-SHADOW. One ``enabled available=0/1`` line per launch and one ``first check clean`` once.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

HELPER_NAME = "singws-karafun"
TIMEOUT_S = 20.0
TAG = "KARAFUN-SCRIPT-SHADOW"
_seen: set[str] = set()
_clean_noted = False


def enabled(settings) -> bool:
    return bool((settings or {}).get("karafun_script_shadow", True))


def find_helper() -> Path | None:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    candidates: list[Path] = []
    override = os.environ.get("SINGWS_KARAFUN_BIN")
    if override:
        candidates.append(Path(override))
    for root in (getattr(sys, "_MEIPASS", None),
                 Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
                 Path(__file__).resolve().parent):
        if root:
            root = Path(root)
            candidates += [root / HELPER_NAME, root / "Frameworks" / HELPER_NAME, root / "native" / "singws_karafun" / arch / HELPER_NAME]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def run_helper(helper: Path, request: dict) -> dict:
    proc = subprocess.run([str(helper)], input=json.dumps(dict(request, op="search_script")).encode("utf-8"), capture_output=True, timeout=TIMEOUT_S,
                          preexec_fn=lambda: os.nice(10))
    if proc.returncode != 0:
        raise RuntimeError(f"helper exit status {proc.returncode}")
    return json.loads(proc.stdout)


def compare(py_lines, rs_lines) -> list[str]:
    """Differences between the app's script and the helper's, as text with no script content."""
    py_lines, rs_lines = list(py_lines), list(rs_lines)
    out: list[str] = []
    for i, (a, b) in enumerate(zip(py_lines, rs_lines)):
        if a != b:
            out.append(f"line {i}: the scripts differ")
            break
    if len(py_lines) != len(rs_lines):
        out.append(f"length: app={len(py_lines)} rust={len(rs_lines)}")
    return out


def announce(settings, log) -> str:
    try:
        text = f"enabled available={int(find_helper() is not None)}" if enabled(settings) else "off (karafun_script_shadow=false)"
        log(f"[{TAG}] {text}")
        return text
    except Exception:  # noqa: BLE001
        return ""


def run_shadow(py_lines, request: dict, log, helper: Path | None = None) -> str:
    """One comparison. Returns ``clean``, ``mismatch`` or ``skipped`` (for tests)."""
    global _clean_noted

    def say(text):
        try:
            log(f"[{TAG}] {text}")
        except Exception:
            pass

    try:
        helper = helper or find_helper()
        if helper is None:
            return "skipped"
        rs = run_helper(helper, request)
        rs_lines = rs.get("lines")
        if not isinstance(rs_lines, list):
            say("skipped: the helper answered no script")
            return "skipped"
        diffs = compare(py_lines, rs_lines)
        if not diffs:
            if not _clean_noted:
                _clean_noted = True
                say("first check clean (the Rust search script equals the app's)")
            return "clean"
        fresh = [d for d in diffs if d not in _seen]
        _seen.update(diffs)
        for d in fresh:
            say(f"MISMATCH {d}")
        return "mismatch"
    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app
        say(f"skipped: {type(exc).__name__}: {str(exc)[:160]}")
        return "skipped"


def start(settings, log, *, py_lines, query, safe_title, safe_artist, require_exact_title, resolve_only) -> threading.Thread | None:
    """Called by the app right after it built the search script. ``py_lines``: the app's script. Returns the daemon thread (None when off or on any failure)."""
    try:
        if not enabled(settings) or find_helper() is None:
            return None
        request = {"query": str(query or ""), "safe_title": str(safe_title or ""), "safe_artist": str(safe_artist or ""),
                   "require_exact_title": bool(require_exact_title), "resolve_only": bool(resolve_only)}
        thread = threading.Thread(target=run_shadow, args=(list(py_lines), request, log), name="karafun-script-shadow", daemon=True)
        thread.start()
        return thread
    except Exception:  # noqa: BLE001
        return None
