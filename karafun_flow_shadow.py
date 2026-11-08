"""KaraFun flow shadow (Stage E): three decisions of the KaraFun automation are also computed by the Rust helper (``singws-karafun``) and a DIFFERENCE is logged.

SHADOW ONLY. The app always acts on its own Python decision. Setting ``karafun_flow_shadow`` (default True). Every failure is swallowed; a missing helper is skipped silently.
Decisions: ``watchdog`` (the duration-watchdog timer: extend or warn the operator), ``readback`` (the key/tempo value the app believes after two display reads),
``startflow`` (press Play / skip / defer to the monitor, the assumed start state, whether playback counts as verified).
The log names the decision and the differing FIELDS only, never song text:  ``[KARAFUN-FLOW-SHADOW] MISMATCH <decision> fields=<a,b>``; ``enabled available=0/1`` once per launch;
``<decision> first check clean`` once per decision.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading

import karafun_script_shadow as _scripts

TAG = "KARAFUN-FLOW-SHADOW"
TIMEOUT_S = 10.0
_clean_noted: set[str] = set()
_mismatch_seen: set[str] = set()


def enabled(settings) -> bool:
    return bool((settings or {}).get("karafun_flow_shadow", True))


def announce(settings, log) -> str:
    try:
        text = f"enabled available={int(_scripts.find_helper() is not None)}" if enabled(settings) else "off (karafun_flow_shadow=false)"
        log(f"[{TAG}] {text}")
        return text
    except Exception:  # noqa: BLE001
        return ""


def _run_helper(helper, request: dict) -> dict:
    proc = subprocess.run([str(helper)], input=json.dumps(request).encode("utf-8"), capture_output=True, timeout=TIMEOUT_S, preexec_fn=lambda: os.nice(10))
    if proc.returncode != 0:
        raise RuntimeError(f"helper exit status {proc.returncode}")
    return json.loads(proc.stdout)


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return False
    return a == b


def compare(python: dict, rust: dict) -> list[str]:
    """Names of the fields on which the two answers differ (only the keys the app decided)."""
    return sorted(k for k, v in python.items() if not _same(v, rust.get(k)))


def run_shadow(decision: str, request: dict, python: dict, log, helper=None) -> str:
    """One comparison. Returns ``clean``, ``mismatch`` or ``skipped`` (for tests)."""
    def say(text):
        try:
            log(f"[{TAG}] {text}")
        except Exception:
            pass
    try:
        helper = helper or _scripts.find_helper()
        if helper is None:
            return "skipped"
        rust = _run_helper(helper, dict(request, op=decision_op(decision)))
        diffs = compare(python, rust)
        if not diffs:
            if decision not in _clean_noted:
                _clean_noted.add(decision)
                say(f"{decision} first check clean")
            return "clean"
        key = f"{decision}:{','.join(diffs)}"
        if key not in _mismatch_seen:
            _mismatch_seen.add(key)
            say(f"MISMATCH {decision} fields={','.join(diffs)}")
        return "mismatch"
    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app
        say(f"skipped: {type(exc).__name__}: {str(exc)[:120]}")
        return "skipped"


def decision_op(decision: str) -> str:
    return {"watchdog": "watchdog_check", "readback": "readback", "startflow": "startflow"}[decision]


def _start(settings, log, decision: str, request: dict, python: dict):
    try:
        if not enabled(settings) or _scripts.find_helper() is None:
            return None
        thread = threading.Thread(target=run_shadow, args=(decision, request, python, log), name="karafun-flow-shadow", daemon=True)
        thread.start()
        return thread
    except Exception:  # noqa: BLE001
        return None


def watchdog(settings, log, *, now, last_playing_ts, confirmed_at, duration, seek_offset, python_extend):
    """``python_extend``: the seconds the app will wait before looking again, or None when it warns."""
    req = {"now": float(now), "last_playing_ts": last_playing_ts, "confirmed_at": confirmed_at, "duration": int(duration), "seek_offset": float(seek_offset or 0.0)}
    return _start_watchdog(settings, log, req, python_extend)


def _start_watchdog(settings, log, req, python_extend):
    # the Rust answer is {"extend": e} or {"warn": line}: compare the KIND and the extension
    def run():
        helper = _scripts.find_helper()
        def say(t):
            try:
                log(f"[{TAG}] {t}")
            except Exception:
                pass
        try:
            if helper is None:
                return
            rust = _run_helper(helper, dict(req, op="watchdog_check"))
            ok = (("extend" in rust) and python_extend is not None and _same(rust["extend"], python_extend)) or (("warn" in rust) and python_extend is None)
            if ok:
                if "watchdog" not in _clean_noted:
                    _clean_noted.add("watchdog")
                    say("watchdog first check clean")
            else:
                key = f"watchdog:{'extend' if python_extend is not None else 'warn'}"
                if key not in _mismatch_seen:
                    _mismatch_seen.add(key)
                    say(f"MISMATCH watchdog fields={'extend' if python_extend is not None else 'warn'}")
        except Exception as exc:  # noqa: BLE001
            say(f"skipped: {type(exc).__name__}: {str(exc)[:120]}")
    try:
        if not enabled(settings) or _scripts.find_helper() is None:
            return None
        t = threading.Thread(target=run, name="karafun-flow-shadow", daemon=True)
        t.start()
        return t
    except Exception:  # noqa: BLE001
        return None


def readback(settings, log, *, known, first, second, python_values):
    def pair(v):
        return list(v) if v else None
    req = {"known": pair(known), "first": pair(first), "second": pair(second)}
    return _start(settings, log, "readback", req, {"values": pair(python_values)})


def startflow(settings, log, *, fast_start, managed_handoff, managed_start_state, explicit_play_sent, probe_ok, probe_state, was_running, python_action, python_initial, python_verified):
    req = {"fast_start": bool(fast_start), "managed_handoff": bool(managed_handoff), "managed_start_state": str(managed_start_state or ""), "explicit_play_sent": bool(explicit_play_sent),
           "probe_ok": bool(probe_ok), "probe_state": str(probe_state or ""), "was_running": bool(was_running), "text": "", "key_steps": 0, "tempo_steps": 0, "key": 0, "tempo": 100}
    return _start(settings, log, "startflow", req, {"action": python_action, "initial": python_initial, "verified": bool(python_verified)})
