"""Log-package shadow (Stage E): after the app builds a bug-report log package, a separate low-priority process (Rust ``singws-logpack``)
rebuilds the same last-show log text from the same inputs, and a DIFFERENCE is logged.

SHADOW ONLY. The Python package is always the one used and uploaded; nothing here sends anything, touches the recipient, server or settings,
or changes what ``_build_last_show_package`` returns. Setting ``logpack_shadow`` (default True). Every failure is swallowed; a missing helper binary
is skipped without a log line.

Log lines (grep ``LOGPACK-SHADOW``), written ONLY on a difference or a helper failure, and NEVER containing log text (it may hold secrets):
  ``MISMATCH <what>: app=<summary> rust=<summary>``   a summary is a length and a short hash, plus where the texts first differ
  ``skipped: <reason>``                                 the helper failed or answered nonsense
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

HELPER_NAME = "singws-logpack"
TIMEOUT_S = 60.0
_seen: set[str] = set()


def enabled(settings) -> bool:
    return bool((settings or {}).get("logpack_shadow", True))


def find_helper() -> Path | None:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    candidates: list[Path] = []
    override = os.environ.get("SINGWS_LOGPACK_BIN")
    if override:
        candidates.append(Path(override))
    for root in (getattr(sys, "_MEIPASS", None),
                 Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
                 Path(__file__).resolve().parent):
        if root:
            root = Path(root)
            candidates += [root / HELPER_NAME, root / "Frameworks" / HELPER_NAME, root / "native" / "singws_logpack" / arch / HELPER_NAME]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def list_logs(logs_dir) -> list[tuple[str, float]]:
    """(name, mtime) of every regular file in the log folder, taken once on the calling thread (cheap)."""
    out = []
    with os.scandir(logs_dir) as entries:
        for entry in entries:
            try:
                if entry.is_file():
                    out.append((entry.name, entry.stat().st_mtime))
            except OSError:
                pass
    return out


def summary(text) -> str:
    if text is None:
        return "none"
    data = str(text).encode("utf-8", "replace")
    return f"len={len(text)} sha={hashlib.sha256(data).hexdigest()[:10]}"


def first_diff(a: str, b: str) -> int:
    return next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))


def compare(py: dict, rs: dict) -> list[str]:
    """Differences between the app's package facts and the helper's. ``py``: text, window, used (names), extras (redacted texts)."""
    out: list[str] = []
    if rs.get("error"):
        out.append(f"error: app=none rust={str(rs.get('error'))[:80]!r}")
        return out
    a, b = py.get("text"), rs.get("text")
    if a != b:
        at = f" first_diff_at={first_diff(a, b)}" if a is not None and b is not None else ""
        out.append(f"text: app={summary(a)} rust={summary(b)}{at}")
    if (py.get("window") or "") != (rs.get("window") or ""):
        out.append(f"window: app={py.get('window')!r} rust={rs.get('window')!r}")
    if list(py.get("used") or []) != list(rs.get("used") or []):
        out.append(f"files: app={list(py.get('used') or [])} rust={list(rs.get('used') or [])}")
    if list(py.get("extras") or []) != list(rs.get("extras") or []):
        out.append(f"extras: app={[summary(x) for x in py.get('extras') or []]} rust={[summary(x) for x in rs.get('extras') or []]}")
    return out


def run_helper(helper: Path, payload: dict) -> dict:
    proc = subprocess.run([str(helper), "build"], input=json.dumps(payload).encode("utf-8"), capture_output=True, timeout=TIMEOUT_S,
                          preexec_fn=lambda: os.nice(10))
    if proc.returncode != 0:
        raise RuntimeError(f"helper exit status {proc.returncode}")
    return json.loads(proc.stdout)


def run_shadow(payload: dict, py: dict, log, helper: Path | None = None) -> str:
    """One comparison. Returns ``clean``, ``mismatch`` or ``skipped`` (for tests)."""
    def say(text):
        try:
            log(f"[LOGPACK-SHADOW] {text}")
        except Exception:
            pass

    try:
        helper = helper or find_helper()
        if helper is None:
            return "skipped"
        rs = run_helper(helper, payload)
        diffs = compare(py, rs)
        if not diffs:
            return "clean"
        fresh = [d for d in diffs if d not in _seen]
        _seen.update(diffs)
        for d in fresh:
            say(f"MISMATCH {d}")
        return "mismatch"
    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app
        say(f"skipped: {type(exc).__name__}: {str(exc)[:160]}")
        return "skipped"


def start(settings, log, *, logs_dir, days, now, texts: dict, extra_texts: list, py: dict) -> threading.Thread | None:
    """Called by the app after it built a package. ``texts``: log file name -> text the app read. ``extra_texts``: raw text of each attached
    extra file. ``py``: what the app produced (text, window, used, extras). Returns the daemon thread (None when off or on any failure)."""
    try:
        if not enabled(settings) or find_helper() is None:
            return None
        files = [{"name": name, "mtime": mtime, "text": texts.get(name)} for name, mtime in list_logs(logs_dir)]
        payload = {"days": int(days), "now": float(now), "files": files, "extras": [{"name": "extra", "text": t} for t in extra_texts]}
        thread = threading.Thread(target=run_shadow, args=(payload, dict(py), log), name="logpack-shadow", daemon=True)
        thread.start()
        return thread
    except Exception:  # noqa: BLE001
        return None
