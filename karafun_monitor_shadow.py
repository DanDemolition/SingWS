"""KaraFun completion-monitor shadow (Stage E): the app's completion monitor (`_start_karafun_completion_monitor`) records what it sees on every pass of its
loop (the probe's time and answer, a few entry flags) and what it does (a recovery Play press, an operator warning, a completion, a key/tempo read). When the
monitor ends, the recorded run is replayed through the Rust monitor (`singws-karafun`, op `monitor_replay`) on a low-priority thread and a DIFFERENCE in the
decisions is logged.

SHADOW ONLY. The monitor's own decisions are always the ones acted on; the recorder never raises into the monitor (every method swallows its errors), writes
nothing to the entry and calls nothing but read-only host methods. Setting `karafun_monitor_shadow` (default True; off for a host without a settings dict, so
test doubles stay silent). A missing helper binary is skipped without a log line.

The log never contains the song, the singer or the probe text (only pass numbers and action names):
  ``MISMATCH pass N: app=[press, ...] rust=[...]``     the actions taken on that pass differ
  ``skipped: <reason>``                                  the helper failed or answered nonsense
Log lines carry the tag KARAFUN-MONITOR-SHADOW. One ``enabled available=0/1`` line per launch and one ``first song clean`` once.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

HELPER_NAME = "singws-karafun"
TIMEOUT_S = 30.0
TAG = "KARAFUN-MONITOR-SHADOW"
KINDS = ("press", "alert", "kt", "complete")
MAX_PASSES = 20000          # a 5-hour song would still fit; a runaway recorder stops growing
MAX_MISMATCH_LINES = 5
_seen: set[str] = set()
_clean_noted = False
last_thread: threading.Thread | None = None   # for tests


def enabled(settings) -> bool:
    return bool((settings or {}).get("karafun_monitor_shadow", True))


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


def announce(settings, log) -> str:
    try:
        text = f"enabled available={int(find_helper() is not None)}" if enabled(settings) else "off (karafun_monitor_shadow=false)"
        log(f"[{TAG}] {text}")
        return text
    except Exception:  # noqa: BLE001
        return ""


class _Null:
    """What the monitor holds when the shadow is off or could not start: every call does nothing."""

    def note_pass(self, *args, **kwargs):
        pass

    event = finish = note_pass


NULL = _Null()


def _flag(value) -> bool:
    try:
        return bool(value)
    except Exception:  # noqa: BLE001
        return False


class Recorder:
    def __init__(self, app, entry, log, clock, cfg, now, started, seek_offset, helper):
        self.app, self.entry, self.log, self.clock = app, entry, log, clock
        self.cfg, self.now, self.started, self.seek_offset, self.helper = cfg, float(now), float(started), float(seek_offset), helper
        self.passes: list[dict] = []
        self.events: list[tuple[int, str]] = []
        self.finished = False

    def note_pass(self, app, entry, probe_started, ok, raw):
        try:
            if len(self.passes) >= MAX_PASSES:
                return
            try:
                times = app._karafun_display_times()
                display_times = [float(times[0]), float(times[1])] if times else None
            except Exception:  # noqa: BLE001
                display_times = None
            try:
                seek = float(entry.get("karafun_seek_offset_s") or 0.0)
            except Exception:  # noqa: BLE001
                seek = 0.0
            self.passes.append({
                "now_before": float(probe_started), "probe_elapsed": max(0.0, float(self.clock()) - float(probe_started)), "ok": _flag(ok), "raw": str(raw or ""),
                "status_is_manual": str(entry.get("karafun_status") or "") == "manual", "seek_offset": seek, "display_times": display_times,
                "kt_running": _flag(getattr(app, "_karafun_kt_running", False)), "kt_dirty": _flag(entry.get("karafun_kt_dirty")),
                "kt_busy": _flag(entry.get("karafun_kt_busy")), "press_ok": True, "press_error": ""})
        except Exception:  # noqa: BLE001
            pass

    def event(self, kind, pressed=True, error=""):
        try:
            if not self.passes or kind not in KINDS:
                return
            self.events.append((len(self.passes) - 1, kind))
            if kind == "press":
                self.passes[-1]["press_ok"] = _flag(pressed)
                self.passes[-1]["press_error"] = str(error or "")
        except Exception:  # noqa: BLE001
            pass

    def finish(self):
        global last_thread
        try:
            if self.finished or not self.passes:
                return
            self.finished = True
            last_thread = threading.Thread(target=replay_and_compare, args=(self.log, self.helper, self.request(), list(self.events)), name="karafun-monitor-shadow", daemon=True)
            last_thread.start()
        except Exception:  # noqa: BLE001
            pass

    def request(self) -> dict:
        return {"op": "monitor_replay", "cfg": self.cfg, "now": self.now, "started": self.started, "seek_offset": self.seek_offset, "passes": self.passes}


def run_helper(helper: Path, request: dict) -> dict:
    proc = subprocess.run([str(helper)], input=json.dumps(request).encode("utf-8"), capture_output=True, timeout=TIMEOUT_S, preexec_fn=lambda: os.nice(10))
    if proc.returncode != 0:
        raise RuntimeError(f"helper exit status {proc.returncode}")
    return json.loads(proc.stdout)


def compare(events, rust_passes) -> list[str]:
    """Differences per pass between what the monitor did and what the Rust machine would have done (action names only)."""
    app: dict[int, list[str]] = {}
    for index, kind in events:
        app.setdefault(index, []).append(kind)
    out = []
    for i, p in enumerate(rust_passes):
        rust = [k for k in KINDS if k in (p.get("events") or [])]
        mine = [k for k in KINDS if k in app.get(i, [])]
        if rust != mine:
            out.append(f"pass {i}: app={mine} rust={rust}")
    return out


def replay_and_compare(log, helper, request, events) -> str:
    """Returns ``clean``, ``mismatch`` or ``skipped`` (for tests)."""
    global _clean_noted

    def say(text):
        try:
            log(f"[{TAG}] {text}")
        except Exception:  # noqa: BLE001
            pass

    try:
        helper = helper or find_helper()
        if helper is None:
            return "skipped"
        rs = run_helper(helper, request)
        if "decline" in rs:
            say(f"skipped: {str(rs['decline'])[:120]}")
            return "skipped"
        passes = rs.get("passes")
        if not isinstance(passes, list):
            say("skipped: the helper answered no passes")
            return "skipped"
        diffs = compare(events, passes)
        if not diffs:
            if not _clean_noted:
                _clean_noted = True
                say(f"first song clean ({len(passes)} passes: the Rust monitor took the same actions)")
            return "clean"
        for d in [d for d in diffs if d not in _seen][:MAX_MISMATCH_LINES]:
            say(f"MISMATCH {d}")
        _seen.update(diffs)
        return "mismatch"
    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app
        say(f"skipped: {type(exc).__name__}: {str(exc)[:160]}")
        return "skipped"


def start(app, entry, log, *, clock=time.monotonic, started=0.0, now=0.0):
    """Called by the monitor once at its start; returns a recorder, or the do-nothing object when off or on any failure."""
    try:
        settings = getattr(app, "settings", None)
        if not isinstance(settings, dict) or not enabled(settings):
            return NULL
        helper = find_helper()
        if helper is None:
            return NULL
        try:
            duration = max(0, int(entry.get("duration") or 0))
        except Exception:  # noqa: BLE001
            duration = 0
        try:
            seek = float(entry.get("karafun_seek_offset_s") or 0.0)
        except Exception:  # noqa: BLE001
            seek = 0.0
        cfg = {"title": str(entry.get("title") or ""), "duration": duration, "duration_estimated": _flag(entry.get("duration_estimated", False)),
               "playback_assumed": _flag(entry.get("karafun_playback_assumed", False)),
               "handoff_timed_out_before_play": _flag(entry.get("karafun_handoff_timed_out_before_play", False)),
               "controlled_start": _flag(entry.get("karafun_playback_clock_started_at")), "capture_session": _flag(getattr(app, "_karafun_capture_session", False)),
               "recovery_delay": float(getattr(app, "KARAFUN_PLAYBACK_RECOVERY_DELAY_S", 12.0)),
               "handoff_recovery_delay": float(getattr(app, "KARAFUN_HANDOFF_TIMEOUT_RECOVERY_DELAY_S", 6.0)),
               "alert_delay": float(getattr(app, "KARAFUN_PLAYBACK_ALERT_DELAY_S", 40.0))}
        return Recorder(app, entry, log, clock, cfg, now, started, seek, helper)
    except Exception:  # noqa: BLE001
        return NULL
