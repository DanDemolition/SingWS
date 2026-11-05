"""Queue-state shadow (Stage B M2): around each server reconcile, a separate low-priority process (Rust ``singws-queue-state``) is given the queue as it
was BEFORE the reconcile and the rows the server sent, and its resulting queue is compared with the one the app actually ended up with. A difference is logged.

SHADOW ONLY. Python's queue is always the one in use; nothing here changes it, sends anything or blocks the reconcile beyond two cheap snapshots. Setting
``queue_state_shadow`` (default True). Every failure is swallowed; a missing helper is skipped without a log line (apart from the one launch line).

Why it cannot cry wolf about operations the Rust model does not know: it resynchronises from the app's real state before EVERY checked reconcile (the helper
is told to ``load`` the pre-state), so edits made by drag/undo/rename/other code between two reconciles never count. A reconcile is not checked at all when
it touches something the model does not cover: the waitlist (setting on, a ``waiting``/failed row, or the waitlist/attention/unmatched sets changing while it
ran), deferred or async intake, rotation lock, duets, server-terminal rows, ``sort_order``, KaraFun rows, or a clear window. Library-match failures show up as a
change of the waitlist/attention sets and are skipped the same way.

At most one check per ``INTERVAL_S``. Log lines (grep ``QUEUE-STATE-SHADOW``):
  ``enabled available=0/1``              once per launch
  ``first check clean (...)``            once per launch, the first comparison that ran and agreed
  ``MISMATCH <path>: app=<summary> rust=<summary>``   each distinct difference once per session, at most ``MAX_LOGGED``; values are lengths/hashes, never names or titles
  ``skipped: <reason>``                  the helper failed or answered nonsense
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

HELPER_NAME = "singws-queue-state"
TIMEOUT_S = 20.0
INTERVAL_S = 30.0
MAX_LOGGED = 20
MAX_SINGERS = 400
MAX_SONGS = 3000
_seen: set[str] = set()
_last_started = 0.0
_helper_cache: list = []
_clean_noted = False
_lock = threading.Lock()

_UNSUPPORTED_ROW_KEYS = ("sort_order", "removed_at", "completed_at", "host_order_updated_at", "singer_order_updated_at", "order_revision")
_UNSUPPORTED_STATES = {"waiting", "failed", "failed_needs_review", "removed", "completed", "sung", "skipped", "active", "delivered"}


def enabled(settings) -> bool:
    return bool((settings or {}).get("queue_state_shadow", True))


def find_helper() -> Path | None:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    candidates: list[Path] = []
    override = os.environ.get("SINGWS_QUEUE_STATE_BIN")
    if override:
        candidates.append(Path(override))
    for root in (getattr(sys, "_MEIPASS", None),
                 Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
                 Path(__file__).resolve().parent):
        if root:
            root = Path(root)
            candidates += [root / HELPER_NAME, root / "Frameworks" / HELPER_NAME, root / "native" / "singws_queue_state" / arch / HELPER_NAME]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def _helper() -> Path | None:
    if not _helper_cache:
        _helper_cache.append(find_helper())
    return _helper_cache[0]


def announce(settings, log) -> str:
    """One line per launch: on or off, and whether the helper is there (a quiet shadow otherwise says nothing when everything agrees)."""
    try:
        text = f"enabled available={int(find_helper() is not None)}" if enabled(settings) else "off (queue_state_shadow=false)"
        log(f"[QUEUE-STATE-SHADOW] {text}")
        return text
    except Exception:  # noqa: BLE001
        return ""


class Unsnapshottable(Exception):
    """Skip this reconcile quietly."""


def _is_duet_name(name: str) -> bool:
    low = f" {name.lower()} "
    return any(t in low for t in ("&", "+", "/", ",", " and ", " with ", " w/", "/w "))


def check_rows(rows) -> None:
    """Raises Unsnapshottable when a row uses something the Rust model does not cover."""
    if not isinstance(rows, list) or len(rows) > 500:
        raise Unsnapshottable("rows")
    for r in rows:
        if not isinstance(r, dict):
            continue
        state = str(r.get("state", "") or "").strip().lower()
        if state in _UNSUPPORTED_STATES:
            raise Unsnapshottable("row state")
        if any(r.get(k) not in (None, "", 0, "0") for k in _UNSUPPORTED_ROW_KEYS):
            raise Unsnapshottable("row field")
        if r.get("sent") or r.get("delivered") or r.get("added_at") or r.get("queued_at"):
            raise Unsnapshottable("row delivered")
        if _is_duet_name(str(r.get("singer", "") or "")):
            raise Unsnapshottable("duet")
        blob = " ".join(str(r.get(k) or "") for k in ("selected_source", "provider", "source_provider", "selected_version", "selected_brand", "brand", "version", "provider_track_id", "selected_disc_id")).lower()
        if "karafun" in blob or blob.strip().startswith("kf_"):
            raise Unsnapshottable("karafun row")


def _guard(app) -> tuple:
    """What changes if the reconcile did something the model does not know (waitlist, attention, unmatched, deferred)."""
    d = object.__getattribute__(app, "__dict__")
    waiting = d.get("_waiting_for_add_requests")
    attention = d.get("_remote_attention_requests")
    unmatched = d.get("_unmatched_remote_request_ids")
    deferred = d.get("_deferred_remote_adds")
    return (len(waiting) if isinstance(waiting, dict) else 0, len(attention) if isinstance(attention, dict) else 0,
            len(unmatched) if isinstance(unmatched, (set, dict, list)) else 0, len(deferred) if isinstance(deferred, list) else 0)


def snapshot_state(app) -> dict:
    """The app's queue and request memory as plain data (read only)."""
    cls = type(app)
    d = object.__getattribute__(app, "__dict__")
    settings = d.get("settings") if isinstance(d.get("settings"), dict) else {}
    queue = list(d.get("queue") or [])
    if len(queue) > MAX_SINGERS:
        raise Unsnapshottable("too many singers")
    total = 0
    singers = []
    for singer in queue:
        if not isinstance(singer, dict):
            raise Unsnapshottable("odd singer")
        songs = []
        for e in list(singer.get("songs") or []):
            if not isinstance(e, dict):
                raise Unsnapshottable("odd entry")
            total += 1
            rid = e.get("remote_request_id")
            try:
                rid = int(rid) if rid not in (None, "") else None
            except (TypeError, ValueError):
                raise Unsnapshottable("odd request id")
            tempo = e.get("tempo_percent")
            songs.append({"rid": rid if rid and rid > 0 else None, "artist": str(e.get("artist") or ""), "title": str(e.get("title") or ""),
                          "key": int(e.get("key") or 0), "tempo": int(tempo) if isinstance(tempo, (int, float)) and not isinstance(tempo, bool) else None,
                          "skipped": bool(e.get("skipped", False)),
                          "request_order": float(e["request_order"]) if isinstance(e.get("request_order"), (int, float)) and not isinstance(e.get("request_order"), bool) else None})
        def _i(v):
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0
        singers.append({"host_order_updated_at": _i(singer.get("host_order_updated_at")), "singer_order_updated_at": _i(singer.get("singer_order_updated_at")),
                        "order_revision": _i(singer.get("order_revision")), "last_order_source": str(singer.get("last_order_source") or ""),
                        "server_singer_id": str(singer.get("server_singer_id") or ""), "server_singer_session_id": _i(singer.get("server_singer_session_id")),
                        "name": str(singer.get("name", "") or ""), "skipped": bool(singer.get("skipped", False)), "has_sung": bool(singer.get("has_sung", False)),
                        "round_sung": bool(singer.get("round_sung", False)), "rotation_marker": bool(singer.get("rotation_marker", False)),
                        "temp_empty": bool(singer.get("temporary_empty_slot", False)), "until": float(singer.get("empty_slot_until") or 0.0), "songs": songs})
    if total > MAX_SONGS:
        raise Unsnapshottable("too many songs")
    tombs = {}
    data = app._ensure_remote_request_tombstones()
    for rid, t in ((data or {}).get("requests") or {}).items():
        if isinstance(t, dict):
            tombs[str(int(rid))] = {"status": str(t.get("status") or ""), "reason": str(t.get("removal_reason") or ""), "singer": str(t.get("singer_name") or ""),
                                    "artist": str(t.get("artist") or ""), "title": str(t.get("title") or "")}
    removed = sorted(int(x) for x in (d.get("_remote_removed_request_ids") or set()))
    handled = sorted(int(x) for x in (d.get("_waiting_for_add_handled_ids") or set()))
    pending = {}
    for k, v in ((d.get("_pending_remote_order_syncs") or {}).items() if isinstance(d.get("_pending_remote_order_syncs"), dict) else []):
        try:
            pending[str(k)] = {"request_ids": [int(x) for x in (v.get("request_ids") or [])], "host_order_updated_at": int(v.get("host_order_updated_at") or 0),
                               "order_revision": int(v.get("order_revision") or 0)}
        except Exception:  # noqa: BLE001
            raise Unsnapshottable("odd pending order")
    terminal_titles = {str(int(k)): str(v or "") for k, v in (d.get("_remote_terminal_titles") or {}).items()} if isinstance(d.get("_remote_terminal_titles"), dict) else {}
    return {"queue_revision": int(d.get("_queue_revision", 0) or 0), "pending_order_syncs": pending, "terminal_titles": terminal_titles,
            "settings": {"queue_mode": str(settings.get("queue_mode", "classic")), "limit_pending_max": int(settings.get("limit_pending_max", 2) or 2),
                         "requests_accepting": bool(settings.get("requests_accepting", True))},
            "queue": singers, "tombstones": tombs, "removed": removed, "handled": handled}


def project_app(state: dict) -> dict:
    """What is compared: structure only (no names of songs, paths or timestamps)."""
    return {"queue": [{"name": s["name"], "skipped": s["skipped"], "has_sung": s["has_sung"], "round_sung": s["round_sung"], "rotation_marker": s["rotation_marker"],
                       "temp_empty": s["temp_empty"], "songs": [[e["rid"], e["key"], e["tempo"], e["skipped"]] for e in s["songs"]]} for s in state["queue"]],
            "tombstones": {k: v["status"] for k, v in sorted(state["tombstones"].items(), key=lambda kv: int(kv[0]))}, "removed": state["removed"], "handled": state["handled"]}


def project_rust(snap: dict) -> dict:
    return {"queue": [{"name": s["name"], "skipped": s["skipped"], "has_sung": s["has_sung"], "round_sung": s["round_sung"], "rotation_marker": s["rotation_marker"],
                       "temp_empty": s["temporary_empty_slot"], "songs": [[e["remote_request_id"], e["key"], e["tempo_percent"], e["skipped"]] for e in s["songs"]]} for s in snap["queue"]],
            "tombstones": {k: v["status"] for k, v in sorted(snap["tombstones"].items(), key=lambda kv: int(kv[0]))}, "removed": snap["removed_ids"], "handled": snap["handled_waitlist_ids"]}


def first_difference(a, b, path="$"):
    """(path, app_summary, rust_summary) of the first difference, or None. Values are summarised, never shown."""
    if type(a) != type(b):
        return path, _summ(a), _summ(b)
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                return f"{path}.{k}", "missing" if k not in a else "present", "missing" if k not in b else "present"
            d = first_difference(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return path, f"len={len(a)}", f"len={len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_difference(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else (path, _summ(a), _summ(b))


def _summ(v) -> str:
    if isinstance(v, (bool, int)) or v is None:
        return repr(v)
    h = hashlib.sha1(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()[:6]
    return f"{type(v).__name__}#{h}"


class Recorder:
    def __init__(self, pre: dict, rows: list, guard: tuple, now: float):
        self.pre, self.rows, self.guard, self.now = pre, rows, guard, now


def begin(app, rows, now: float | None = None, force: bool = False):
    """Called at the start of a reconcile. ``None`` = do nothing this time (off, no helper, not due, or the reconcile is outside the model)."""
    global _last_started
    try:
        d = object.__getattribute__(app, "__dict__")
        settings = d.get("settings") if isinstance(d.get("settings"), dict) else {}
        if not force and (not enabled(settings) or _helper() is None):
            return None
        t = time.monotonic()
        if not force and t - _last_started < INTERVAL_S:
            return None
        if settings.get("use_waiting_for_add") or settings.get("rotation_locked") or d.get("_deferred_remote_adds"):
            return None
        if d.get("_waiting_for_add_requests") or d.get("_remote_attention_requests"):
            return None
        if float(d.get("_remote_clear_in_progress_until", 0.0) or 0.0) > time.time():
            return None
        try:
            if app._should_async_remote_request_intake():
                return None
        except Exception:
            return None
        check_rows(rows)
        pre = snapshot_state(app)
        if any(_is_duet_name(s["name"]) for s in pre["queue"]):
            return None
        _last_started = t
        return Recorder(pre, [dict(r) for r in rows if isinstance(r, dict)], _guard(app), time.time() if now is None else now)
    except Exception:  # noqa: BLE001
        return None


def finish(rec, app, log, wait: bool = False):
    """Called when the reconcile returned. Takes the post-state on the calling thread (it reads live app state), then compares on a daemon thread."""
    if rec is None:
        return None
    try:
        if _guard(app) != rec.guard:
            return None  # the reconcile touched the waitlist / attention / unmatched / deferred state: outside the model
        post = snapshot_state(app)
        thread = threading.Thread(target=run_check, args=(rec, post, log), name="queue-state-shadow", daemon=True)
        thread.start()
        if wait:
            thread.join(TIMEOUT_S + 5)
        return thread
    except Exception:  # noqa: BLE001
        return None


def _say(log, text):
    try:
        log(f"[QUEUE-STATE-SHADOW] {text}")
    except Exception:
        pass


def run_helper(helper: Path, pre: dict, rows: list, now: float) -> dict:
    load = {"op": "load", "settings": pre["settings"], "queue": pre["queue"], "tombstones": pre["tombstones"], "removed": pre["removed"], "handled": pre["handled"],
            "queue_revision": pre["queue_revision"], "pending_order_syncs": pre["pending_order_syncs"], "terminal_titles": pre["terminal_titles"]}
    arrive = {"op": "remote_arrive", "requests": rows, "now": now}
    payload = json.dumps(load) + "\n" + json.dumps(arrive) + "\n"
    proc = subprocess.run([str(helper)], input=payload, capture_output=True, text=True, timeout=TIMEOUT_S, preexec_fn=lambda: os.nice(10))
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if proc.returncode != 0 or len(lines) != 2:
        raise RuntimeError(f"helper exit {proc.returncode}, {len(lines)} lines")
    return json.loads(lines[1])


def run_check(rec, post: dict, log, helper: Path | None = None) -> str:
    """One comparison. Returns clean / mismatch / skipped (for tests)."""
    global _clean_noted
    try:
        helper = helper or _helper()
        if helper is None:
            return "skipped"
        out = run_helper(helper, rec.pre, rec.rows, rec.now)
        if not out.get("result", {}).get("ok", False):
            _say(log, "skipped: the helper refused the reconcile")
            return "skipped"
        diff = first_difference(project_app(post), project_rust(out["snapshot"]))
        if diff is None:
            with _lock:
                first = not _clean_noted
                _clean_noted = True
            if first:
                _say(log, "first check clean (the Rust queue rules reproduce the app's reconcile)")
            return "clean"
        path, a, r = diff
        key = f"{path}|{a}|{r}"
        with _lock:
            fresh = key not in _seen and len(_seen) < MAX_LOGGED
            _seen.add(key)
        if fresh:
            _say(log, f"MISMATCH {path}: app={a} rust={r}")
        return "mismatch"
    except Exception as exc:  # noqa: BLE001
        _say(log, f"skipped: {type(exc).__name__}")
        return "skipped"
