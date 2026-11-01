"""Network slices shadow (Stage E step 4): the app builds a few network decisions itself (the sync probe's result, ...); a separate low-priority
process (Rust ``singws-netslice``) rebuilds each from a snapshot of the same inputs and a DIFFERENCE is logged.

SHADOW ONLY. The value the app computed is always the one used; nothing here sends anything, opens a connection or changes what the app asks of the server.
The sync probe is observed through a thin stand-in for the ``requests`` module that forwards every call UNCHANGED (an exception propagates exactly as
before) and only remembers what was asked and what came back. Setting ``net_slice_shadow`` (default True). Every failure is swallowed; a missing helper
binary is skipped without a log line (the launch line says ``available=0``).

Log lines (grep ``NET-SLICE-SHADOW``), written ONLY on a difference or a helper failure, and NEVER containing keys, tenants, URLs or response text (a
difference carries the slice, the key path of the first difference and a length plus a short hash of the two values):
  ``enabled available=N`` / ``off (...)``   once per launch
  ``first check clean (...)``               once per launch, the first comparison that agreed
  ``MISMATCH <slice> at <path>: app=<summary> rust=<summary>``   each distinct difference once per session, at most ``MAX_LOGGED``
  ``skipped: <reason>``                      the helper failed or answered nonsense
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

TAG = "[NET-SLICE-SHADOW]"
HELPER_NAME = "singws-netslice"
TIMEOUT_S = 30.0
MAX_LOGGED = 20
_seen: set[str] = set()
_helper_cache: list = []
_clean_noted = False


def enabled(settings) -> bool:
    return bool((settings or {}).get("net_slice_shadow", True))


def find_helper() -> Path | None:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    candidates: list[Path] = []
    override = os.environ.get("SINGWS_NETSLICE_BIN")
    if override:
        candidates.append(Path(override))
    for root in (getattr(sys, "_MEIPASS", None),
                 Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
                 Path(__file__).resolve().parent):
        if root:
            root = Path(root)
            candidates += [root / HELPER_NAME, root / "Frameworks" / HELPER_NAME, root / "native" / "singws_netslice" / arch / HELPER_NAME]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def _helper() -> Path | None:
    if not _helper_cache:
        _helper_cache.append(find_helper())
    return _helper_cache[0]


def announce(settings, log) -> str:
    """One line per launch: is this shadow on, and is its helper there."""
    try:
        text = f"enabled available={int(_helper() is not None)}" if enabled(settings) else "off (net_slice_shadow=false)"
        log(f"{TAG} {text}")
        return text
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- comparison

def summary(v) -> str:
    text = json.dumps(v, ensure_ascii=False, sort_keys=True)
    return f"len={len(text)} sha={hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:10]}"


def first_diff(a, b, path: str = "$"):
    if type(a) is not type(b):
        return path, a, b
    if isinstance(a, dict):
        if list(a) != list(b):
            return path + ".<keys>", list(a), list(b)
        for k in a:
            d = first_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        if len(a) != len(b):
            return f"{path}.<len>", len(a), len(b)
        return None
    return None if a == b else (path, a, b)


def compare(slice_name: str, expected, rust) -> str | None:
    """None when identical (or the helper declines the case), else a one-line description with no secrets in it."""
    if not isinstance(rust, dict):
        return "rust answer is not an object"
    if "unsupported" in rust:
        return None
    d = first_diff(expected, rust)
    if d is None:
        return None
    path, a, b = d
    return f"{slice_name} at {path}: app={summary(a)} rust={summary(b)}"


def run_helper(helper: Path, requests: list[dict]) -> list:
    proc = subprocess.run([str(helper)], input=json.dumps(requests, ensure_ascii=False).encode("utf-8"), capture_output=True, timeout=TIMEOUT_S,
                          preexec_fn=lambda: os.nice(10))
    if proc.returncode != 0:
        raise RuntimeError(f"helper exit status {proc.returncode}")
    out = json.loads(proc.stdout)
    if not isinstance(out, list) or len(out) != len(requests):
        raise RuntimeError("helper answered the wrong number of requests")
    return out


def _note_clean(say, what: str) -> None:
    global _clean_noted
    if not _clean_noted:
        _clean_noted = True
        say(f"first check clean ({what})")


def run_shadow(batch: list[tuple[str, dict, object]], log, helper: Path | None = None) -> str:
    """One comparison batch of (slice name, helper request, what the app computed). Returns ``clean``, ``mismatch`` or ``skipped`` (for tests)."""
    def say(text):
        try:
            log(f"{TAG} {text}")
        except Exception:
            pass

    try:
        helper = helper or _helper()
        if helper is None or not batch:
            return "skipped"
        answers = run_helper(helper, [req for _n, req, _e in batch])
        diffs = [d for (name, _r, expected), rust in zip(batch, answers) if (d := compare(name, expected, rust))]
        if not diffs:
            _note_clean(say, "the Rust network slices equal the app's")
            return "clean"
        for d in diffs:
            if d in _seen:
                continue
            if len(_seen) >= MAX_LOGGED:
                if len(_seen) == MAX_LOGGED:
                    _seen.add("suppressed")
                    say("further mismatches suppressed for this session")
                break
            _seen.add(d)
            say(f"MISMATCH {d}")
        return "mismatch"
    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app
        say(f"skipped: {type(exc).__name__}: {str(exc)[:160]}")
        return "skipped"


def submit(batch, log) -> threading.Thread | None:
    """Compare on a daemon thread; never blocks the caller."""
    try:
        if not batch:
            return None
        thread = threading.Thread(target=run_shadow, args=(batch, log), name="net-slice-shadow", daemon=True)
        thread.start()
        return thread
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- slice 1: probe_network_sync_status

def _classify(exc, requests_module) -> dict:
    """Timeout first (a ConnectTimeout is both), then ConnectionError, as the probe's own except clauses are ordered."""
    ex = getattr(requests_module, "exceptions", None)
    kind = "other"
    try:
        if ex is not None and isinstance(exc, ex.Timeout):
            kind = "timeout"
        elif ex is not None and isinstance(exc, ex.ConnectionError):
            kind = "connection"
    except Exception:  # noqa: BLE001
        kind = "other"
    return {"kind": kind, "type": type(exc).__name__, "text": str(exc)}


class RecordingRequests:
    """Stands in for the ``requests`` module inside ``probe_network_sync_status``: ``get`` goes to the real module with the very same arguments and the
    answer (or the exception) comes back untouched; the call and its outcome are remembered for the shadow. Every other attribute is the real module's."""

    def __init__(self, real):
        self._real = real
        self.calls: list[dict] = []

    def get(self, url, *args, **kwargs):
        rec = {"url": url, "params": kwargs.get("params"), "headers": kwargs.get("headers"), "timeout": kwargs.get("timeout")}
        if args:
            rec["positional"] = True       # not a shape the probe uses: the shadow will skip this one
        self.calls.append(rec)
        try:
            resp = self._real.get(url, *args, **kwargs)
        except BaseException as exc:
            try:
                rec["outcome"] = _classify(exc, self._real)
            except Exception:  # noqa: BLE001
                pass
            raise
        try:
            status = getattr(resp, "status_code", None)
            try:
                body = str(getattr(resp, "text", "") or "")[:300]
            except Exception:  # noqa: BLE001
                body = ""
            out = {"kind": "response", "status": status if isinstance(status, int) and not isinstance(status, bool) else None, "body": body, "json_ok": False, "json_error": ""}
            if status == 200:
                try:
                    resp.json()
                    out["json_ok"] = True
                except Exception as exc:  # noqa: BLE001
                    out["json_error"] = str(exc)
            rec["outcome"] = out
        except Exception:  # noqa: BLE001
            pass
        return resp

    def __getattr__(self, name):
        return getattr(self._real, name)


def probe_batch(base_url, tenant, api_key, timeout_sec, calls, result):
    """The batch entry for one finished probe, or None when it cannot be compared (odd argument types, a call that was not recorded in full)."""
    try:
        if not all(isinstance(v, str) for v in (base_url, tenant, api_key)) or isinstance(timeout_sec, bool) or not isinstance(timeout_sec, (int, float)):
            return None
        now = None
        for c in calls:
            p = c.get("params")
            if isinstance(p, dict) and "_" in p:
                now = p["_"]
            if "outcome" not in c or c.get("positional"):
                return None
        req = {"op": "probe", "base_url": base_url, "tenant": tenant, "api_key": api_key, "timeout_sec": timeout_sec, "now": now,
               "calls": [{k: v for k, v in c.items()} for c in calls]}
        plan = [{"url": c["url"], "params": c["params"], "headers": c["headers"], "timeout": c["timeout"]} for c in calls]
        json.dumps(req), json.dumps(result)
        return ("probe", req, {"result": result, "calls": plan})
    except Exception:  # noqa: BLE001
        return None
