"""Update-rules shadow (Stage E): after the app decides whether an update is available (``GitHubUpdateWorker._manifest_info`` / ``_release_info``), a separate
low-priority process (Rust ``singws-update``) is given the same manifest/release, the current version and the machine, and a DIFFERENT answer is logged.

SHADOW ONLY. Python's info dict is always the one used and emitted; the helper never fetches, downloads or replaces anything (it has no network code). Setting
``update_shadow`` (default True). Runs on a daemon thread, every failure is swallowed, a missing helper is skipped without a log line (apart from the launch line).

Log lines (grep ``UPDATE-SHADOW``):
  ``enabled available=0/1``           once per launch
  ``first check clean (...)``         once per launch, the first comparison that ran and agreed
  ``MISMATCH <kind> at <path>: app=<summary> rust=<summary>``   each distinct difference once per session, at most ``MAX_LOGGED``
  ``skipped: <reason>``               the helper failed or answered nonsense
"""

from __future__ import annotations

import copy
import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

HELPER_NAME = "singws-update"
TIMEOUT_S = 20.0
MAX_LOGGED = 10
_seen: set[str] = set()
_helper_cache: list = []
_clean_noted = False
_lock = threading.Lock()


def enabled(settings) -> bool:
    return bool((settings or {}).get("update_shadow", True))


def find_helper() -> Path | None:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    candidates: list[Path] = []
    override = os.environ.get("SINGWS_UPDATE_BIN")
    if override:
        candidates.append(Path(override))
    for root in (getattr(sys, "_MEIPASS", None),
                 Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
                 Path(__file__).resolve().parent):
        if root:
            root = Path(root)
            candidates += [root / HELPER_NAME, root / "Frameworks" / HELPER_NAME, root / "native" / "singws_update" / arch / HELPER_NAME]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def _helper() -> Path | None:
    if not _helper_cache:
        _helper_cache.append(find_helper())
    return _helper_cache[0]


def announce(settings, log) -> str:
    try:
        text = f"enabled available={int(find_helper() is not None)}" if enabled(settings) else "off (update_shadow=false)"
        log(f"[UPDATE-SHADOW] {text}")
        return text
    except Exception:  # noqa: BLE001
        return ""


def _say(log, text):
    try:
        log(f"[UPDATE-SHADOW] {text}")
    except Exception:
        pass


def _first_difference(a, b, path="$"):
    from queue_state_shadow import first_difference
    return first_difference(a, b, path)


def run_check(kind: str, payload: dict, info: dict, current: str, plat: str, machine: str, log, helper: Path | None = None) -> str:
    """One comparison. Returns clean / mismatch / skipped (for tests)."""
    global _clean_noted
    try:
        helper = helper or _helper()
        if helper is None:
            return "skipped"
        key = "manifest" if kind == "manifest_info" else "release"
        req = {"op": kind, key: payload, "current": current, "platform": plat, "machine": machine}
        proc = subprocess.run([str(helper)], input=json.dumps(req), capture_output=True, text=True, timeout=TIMEOUT_S, preexec_fn=lambda: os.nice(10))
        if proc.returncode != 0:
            raise RuntimeError(f"exit {proc.returncode}")
        rust = json.loads(proc.stdout)
        if "error" in rust:
            _say(log, "skipped: the helper refused the request")
            return "skipped"
        app = json.loads(json.dumps(info, sort_keys=True, default=str))
        diff = _first_difference(app, rust)
        if diff is None:
            with _lock:
                first = not _clean_noted
                _clean_noted = True
            if first:
                _say(log, "first check clean (the Rust update rules reproduce the app's decision)")
            return "clean"
        path, a, r = diff
        sig = f"{kind}|{path}|{a}|{r}"
        with _lock:
            fresh = sig not in _seen and len(_seen) < MAX_LOGGED
            _seen.add(sig)
        if fresh:
            _say(log, f"MISMATCH {kind} at {path}: app={a} rust={r}")
        return "mismatch"
    except Exception as exc:  # noqa: BLE001
        _say(log, f"skipped: {type(exc).__name__}")
        return "skipped"


def observe(kind: str, payload, info, current: str, log, wait: bool = False):
    """Called by the update worker right after it computed ``info``. Copies the inputs and the answer, then compares on a daemon thread."""
    try:
        if _helper() is None or not isinstance(payload, dict) or not isinstance(info, dict):
            return None
        thread = threading.Thread(target=run_check, args=(kind, copy.deepcopy(payload), copy.deepcopy(info), str(current or ""), sys.platform, platform.machine(), log),
                                  name="update-shadow", daemon=True)
        thread.start()
        if wait:
            thread.join(TIMEOUT_S + 5)
        return thread
    except Exception:  # noqa: BLE001
        return None
