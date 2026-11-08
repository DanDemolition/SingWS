"""KaraFun script-builder shadow (Stage E): the app's static AppleScript builders (preflight, press play, probes, seek, key/tempo menu, ...) are wrapped ONCE at launch so that
each distinct call is also built by the Rust helper (``singws-karafun``, op ``script``) on a low-priority thread, and a DIFFERENCE is logged.

SHADOW ONLY. The wrapper always returns exactly what the Python builder returned; nothing here runs a script or touches KaraFun. Setting ``karafun_builder_shadow`` (default True).
Every failure is swallowed. With the setting off or no helper binary, NOTHING is wrapped (no overhead). Each builder is checked for at most MAX_PER_BUILDER distinct argument
sets per launch, so the monitor probe that runs on every poll costs one check, not hundreds.

The log never contains script text or arguments, only the builder name and a line number or length:
  ``MISMATCH <builder> line N`` / ``MISMATCH <builder> length: app=A rust=B``
  ``skipped: <reason>``   the helper failed or answered nonsense
Log lines carry the tag KARAFUN-BUILDER-SHADOW. One ``enabled available=0/1`` line per launch and one ``first check clean`` once.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import subprocess
import threading

import karafun_script_shadow as base

TAG = "KARAFUN-BUILDER-SHADOW"
MAX_PER_BUILDER = 3
TIMEOUT_S = 20.0

# Python static method -> (Rust script name, how its arguments become request fields). None = no arguments.
BUILDERS = {
    "_karafun_preflight_script": ("preflight", None),
    "_karafun_main_window_state_script": ("main_window_state", None),
    "_karafun_press_play_script": ("press_play", None),
    "_karafun_playback_menu_state_script": ("playback_menu_state", None),
    "_karafun_raise_results_window_script": ("raise_results_window", None),
    "_karafun_read_key_tempo_script": ("read_key_tempo", None),
    "_karafun_toggle_playback_script": ("toggle_playback", None),
    "_karafun_restore_script": ("restore", None),
    "_karafun_play_now_queue_script": ("play_now_queue", None),
    "_karafun_renderer_ready_script": ("renderer_ready", None),
    "_karafun_start_probe_script": ("start_probe", None),
    "_karafun_monitor_probe_script": ("monitor_probe", None),
    "_karafun_handoff_script": ("handoff", ("x", "y", "width", "height")),
    "_karafun_click_point_script": ("click_point", ("point",)),
    "_karafun_adjust_menu_script": ("adjust_menu", ("key_item", "key_steps", "tempo_item", "tempo_steps")),
    "_karafun_set_key_tempo_script": ("set_key_tempo", ("requested_key", "requested_tempo")),
    "_karafun_seek_script": ("seek", ("count", "item")),
    "_karafun_double_click_script": ("double_click", ("x", "y")),
    "_karafun_apply_key_tempo_script": ("apply_key_tempo", ("key_delta", "tempo_delta", "key_item", "tempo_item")),
}

_state_lock = threading.Lock()
_seen: dict[str, set[str]] = {}
_reported: set[str] = set()
_clean_noted = False


def enabled(settings) -> bool:
    return bool((settings or {}).get("karafun_builder_shadow", True))


def announce(settings, log) -> str:
    try:
        text = f"enabled available={int(base.find_helper() is not None)}" if enabled(settings) else "off (karafun_builder_shadow=false)"
        log(f"[{TAG}] {text}")
        return text
    except Exception:  # noqa: BLE001
        return ""


def _request(name: str, fields: tuple | None, bound: dict) -> dict | None:
    """The helper request for one call, or None when the arguments are not plain integers/strings the Rust side models."""
    req: dict = {"op": "script", "name": name}
    for field in fields or ():
        value = bound.get(field)
        if field == "point":
            if not (isinstance(value, (tuple, list)) and len(value) == 2 and all(type(v) is int for v in value)):
                return None
            req["x"], req["y"] = value
            continue
        if type(value) is int or isinstance(value, str):
            req[field] = value
        else:
            return None
    return req


def _run(helper, request: dict) -> dict:
    proc = subprocess.run([str(helper)], input=json.dumps(request).encode("utf-8"), capture_output=True, timeout=TIMEOUT_S, preexec_fn=lambda: os.nice(10))
    if proc.returncode != 0:
        raise RuntimeError(f"helper exit status {proc.returncode}")
    return json.loads(proc.stdout)


def check(builder: str, request: dict, py_lines, log, helper=None) -> str:
    """One comparison. Returns ``clean``, ``mismatch`` or ``skipped`` (for tests)."""
    global _clean_noted

    def say(text):
        try:
            log(f"[{TAG}] {text}")
        except Exception:
            pass

    try:
        helper = helper or base.find_helper()
        if helper is None:
            return "skipped"
        rs_lines = _run(helper, request).get("lines")
        if not isinstance(rs_lines, list):
            say(f"skipped: the helper answered no script for {builder}")
            return "skipped"
        diffs = base.compare(py_lines, rs_lines)
        if not diffs:
            if not _clean_noted:
                _clean_noted = True
                say("first check clean (the Rust builder scripts equal the app's)")
            return "clean"
        for d in diffs:
            key = f"{builder} {d}"
            with _state_lock:
                fresh = key not in _reported
                _reported.add(key)
            if fresh:
                say(f"MISMATCH {builder} {d.replace(': the scripts differ', '')}")
        return "mismatch"
    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app
        say(f"skipped: {type(exc).__name__}: {str(exc)[:160]}")
        return "skipped"


def _wrap(method_name: str, original, name: str, fields, log):
    try:
        params = list(inspect.signature(original).parameters)
    except (TypeError, ValueError):
        return None

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            bound = dict(zip(params, args))
            bound.update(kwargs)
            request = _request(name, fields, bound)
            if request is not None and isinstance(result, list):
                key = json.dumps({k: v for k, v in request.items()}, sort_keys=True)
                with _state_lock:
                    seen = _seen.setdefault(name, set())
                    go = key not in seen and len(seen) < MAX_PER_BUILDER
                    if go:
                        seen.add(key)
                if go:
                    threading.Thread(target=check, args=(name, request, list(result), log), name="karafun-builder-shadow", daemon=True).start()
        except Exception:  # noqa: BLE001
            pass
        return result

    return wrapper


def install(cls, settings, log) -> int:
    """Wrap the builders of ``cls`` that exist as static methods. Returns how many were wrapped (0 when off, no helper, or on any failure)."""
    try:
        if not enabled(settings) or base.find_helper() is None:
            return 0
        wrapped = 0
        for method_name, (name, fields) in BUILDERS.items():
            attr = next((k.__dict__[method_name] for k in cls.__mro__ if method_name in k.__dict__), None)
            if not isinstance(attr, staticmethod):
                continue
            wrapper = _wrap(method_name, attr.__func__, name, fields, log)
            if wrapper is None:
                continue
            setattr(cls, method_name, staticmethod(wrapper))
            wrapped += 1
        return wrapped
    except Exception:  # noqa: BLE001
        return 0
