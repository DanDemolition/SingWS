"""Host relay message shadow (Stage E): after the app sends its host relay messages (playback_tick, state, queue, chat), a separate low-priority process
(Rust ``singws-relaymsg``) rebuilds the same messages from a snapshot of the same inputs, and a DIFFERENCE is logged.

SHADOW ONLY. The message the app built is always the one sent; nothing here sends anything or touches the relay worker, its retries, the connection
or the message schema. Setting ``relay_msg_shadow`` (default True). Every failure is swallowed; a missing helper binary is skipped without a log line.
At most one comparison batch per ``INTERVAL_S`` for the whole app (the publish runs every ~3 s; the snapshots are only taken when a batch is due).

Log lines (grep ``RELAY-MSG-SHADOW``), written ONLY on a difference or a helper failure, and NEVER containing chat text, names or ids (a difference
carries the message kind, the key path of the first difference, and a length plus a short hash of the two values):
  ``MISMATCH <kind> at <path>: app=<summary> rust=<summary>``   each distinct difference once per session, at most ``MAX_LOGGED`` per session
  ``skipped: <reason>``                                          the helper failed or answered nonsense
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

HELPER_NAME = "singws-relaymsg"
TIMEOUT_S = 30.0
INTERVAL_S = 30.0
MAX_LOGGED = 20
MAX_CHAT_ROWS = 3000
MAX_REMOVED = 500
_seen: set[str] = set()
_last_started = 0.0
_helper_cache: list = []

ROOM_FIELDS = ("id", "channel", "thread", "from_key", "to_key", "from", "kind", "media_id", "message", "status", "host", "created_at", "reports", "flag")
HOST_TEXT_FIELDS = ("id", "singer_key", "singer", "direction", "read", "message", "status", "created_at", "reports", "flag", "kind", "media_id", "host", "from", "from_key")
WAIT_FIELDS = ("request_id", "singer", "artist", "title", "status_label", "first_time_singer")


class Unsnapshottable(Exception):
    """The inputs are too big or not plain data: skip this batch quietly."""


def enabled(settings) -> bool:
    return bool((settings or {}).get("relay_msg_shadow", True))


def find_helper() -> Path | None:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    candidates: list[Path] = []
    override = os.environ.get("SINGWS_RELAYMSG_BIN")
    if override:
        candidates.append(Path(override))
    for root in (getattr(sys, "_MEIPASS", None),
                 Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
                 Path(__file__).resolve().parent):
        if root:
            root = Path(root)
            candidates += [root / HELPER_NAME, root / "Frameworks" / HELPER_NAME, root / "native" / "singws_relaymsg" / arch / HELPER_NAME]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def _helper() -> Path | None:
    """Looked up once per session (a stat or two), so a missing helper costs nothing afterwards."""
    if not _helper_cache:
        _helper_cache.append(find_helper())
    return _helper_cache[0]


# ---------------------------------------------------------------- snapshots (read-only; all take the app)

def _plain(v):
    """The value itself when it is plain JSON data, else the whole snapshot is skipped."""
    try:
        json.dumps(v)
    except (TypeError, ValueError):
        raise Unsnapshottable("not plain data")
    return v


def _pick(m, fields):
    if not isinstance(m, dict):
        return _plain(m)
    out = {k: _plain(m[k]) for k in fields if k in m}
    gif = m.get("gif")
    if "gif" in m:
        out["gif"] = ({k: _plain(gif[k]) for k in ("preview", "id") if k in gif} if isinstance(gif, dict) else _plain(gif))
    return out


def snapshot_queue(app) -> list:
    """Queue as `_shell_queue_summary` and `_queue_signature` read it. Ids and the artist/title pair are resolved by the app's own helpers (they mint
    ids and look songs up); only the first 200 singers / 25 songs are resolved, exactly what the summary emits."""
    cls = type(app)
    rows = []
    for si, singer in enumerate(list(app.queue or [])):
        if not isinstance(singer, dict):
            rows.append(None)
            continue
        songs = []
        for ei, entry in enumerate(list(singer.get("songs", []) or [])):
            is_dict = isinstance(entry, dict)
            artist = title = ""
            if si < 200 and ei < 25:
                try:
                    artist, title = app._queue_entry_artist_title(entry)
                except Exception:
                    artist, title = "", ""
            songs.append({"is_dict": is_dict, "entry_id": str(cls._ensure_queue_entry_id(entry)), "artist": artist, "title": title,
                          "skipped": bool(entry.get("skipped", False)) if is_dict else False})
        name = singer.get("name", "")
        rows.append({"singer_id": str(cls._ensure_singer_id(singer)), "name": name if isinstance(name, str) else str(name or ""),
                     "skipped": bool(singer.get("skipped", False)), "rotation_marker": bool(singer.get("rotation_marker", False)), "songs": songs})
    return rows


def _wait_row(row):
    if not isinstance(row, dict):
        return None
    return {k: _plain(row[k]) for k in WAIT_FIELDS if k in row}


def snapshot_waitlist(app) -> dict:
    rows = []
    try:
        for section in app._waiting_for_add_sections():
            if section.get("key") == "waitlist":
                rows = [_wait_row(r) for r in section.get("rows", [])]
    except Unsnapshottable:
        raise
    except Exception:
        rows = []
    removed = []
    try:
        records = [r for r in (getattr(app, "_waiting_for_add_removed_tonight", {}) or {}).values() if isinstance(r, dict)]
        if len(records) > MAX_REMOVED:
            raise Unsnapshottable("too many removed rows")
        for r in records:
            try:
                row = _wait_row(app._waiting_for_add_row(r, "removed", selectable=True))
            except Unsnapshottable:
                raise
            except Exception:
                row = None
            removed.append({"removed_at": _plain(r.get("removed_at")), "row": row})
    except Unsnapshottable:
        raise
    except Exception:
        removed = []
    return {"rows": rows, "removed": removed}


def snapshot_chat(app) -> dict:
    settings = dict(getattr(app, "_room_settings", {}) or {})
    new = getattr(app, "_room_new_by_channel", None) or {}
    rooms = getattr(app, "_room_messages", {}) or {}
    chat_messages = list(getattr(app, "_chat_messages", []) or [])
    if len(rooms) > MAX_CHAT_ROWS or len(chat_messages) > MAX_CHAT_ROWS:
        raise Unsnapshottable("too many chat rows")
    pairs = [[k, _pick(m, ROOM_FIELDS)] for k, m in rooms.items()]
    texts = [_pick(m, HOST_TEXT_FIELDS) for m in chat_messages]
    strings: set[str] = set()

    def note(v):
        if isinstance(v, str) and v:
            strings.add(v)
            if "|" in v:
                strings.update(p for p in v.split("|") if p)

    for _k, m in pairs:
        if isinstance(m, dict):
            for f in ("thread", "from_key", "to_key"):
                note(m.get(f))
    for m in texts:
        if isinstance(m, dict):
            for f in ("singer_key", "singer"):
                note(m.get(f))
    fold = {s: [s.casefold(), s.title()] for s in strings}
    return {"host_chat_name": _plain(app.settings.get("host_chat_name")), "room_settings": {"group_enabled": bool(settings.get("group_enabled")), "dm_enabled": bool(settings.get("dm_enabled"))},
            "reachable": _plain(getattr(app, "_room_reachable", None)), "new": {"group": _plain(new.get("group", 0)), "dm": _plain(new.get("dm", 0))},
            "attention_ids": [_plain(i) for i in (getattr(app, "_room_attention_ids", ()) or ())], "rooms": pairs, "chat_messages": texts,
            "media_read": [_plain(i) for i in (getattr(app, "_host_media_read", None) or ())], "fold": fold}


def _settings_user(app) -> dict:
    return {k: _plain(app.settings[k]) for k in ("user", "tenant") if k in app.settings}


_STATE_KEYS = ("rotation", "playback", "bg_playing", "bg_volume", "bg_index", "bg_track")


class Recorder:
    """Collects the messages one publish sends. ``None`` from :meth:`start` means: do nothing this time (off, no helper, or not due)."""

    def __init__(self, app):
        self.app = app
        self.sent: list[dict] = []

    @classmethod
    def start(cls, app, now: float | None = None, force: bool = False):
        global _last_started
        try:
            if not force:
                if not enabled(app.settings) or _helper() is None:
                    return None
                t = time.monotonic() if now is None else now
                if t - _last_started < INTERVAL_S:
                    return None
                _last_started = t
            return cls(app)
        except Exception:
            return None

    def wrap(self, worker):
        """A stand-in for the relay worker: ``send_json`` goes to the real worker first and unchanged (an exception there propagates exactly as
        before), and only then is the message remembered; every other attribute is the real worker's."""
        rec = self

        class _Recording:
            def send_json(self, msg, *args, **kwargs):
                result = worker.send_json(msg, *args, **kwargs)
                rec.add(msg)
                return result

            def __getattr__(self, name):
                return getattr(worker, name)

        return _Recording()

    def add(self, msg) -> None:
        try:
            self.sent.append(msg)
        except Exception:
            pass

    def requests(self, state, app_version) -> list[tuple[str, dict, str]]:
        """(kind, request for the helper, the exact JSON text the app sent) for every message of this publish."""
        out = []
        app = self.app
        user = _settings_user(app)
        for msg in self.sent:
            kind = msg.get("type")
            expected = json.dumps(msg, ensure_ascii=False)
            at = msg.get("updated_at")
            if kind == "playback_tick":
                req = {"op": "tick", "playback": _plain((state or {}).get("playback", {}) if isinstance(state, dict) else {}), "updated_at": at}
            elif kind == "state":
                req = {"op": "state", "settings": user, "app_version": app_version, "updated_at": at,
                       "state": {k: _plain(state[k]) for k in _STATE_KEYS if isinstance(state, dict) and k in state}, "queue_sig": msg.get("queue_sig")}
            elif kind == "queue":
                req = {"op": "queue", "settings": user, "queue": snapshot_queue(app), "waitlist": snapshot_waitlist(app), "updated_at": at}
            elif kind == "chat":
                req = {"op": "chat", "settings": user, "chat": snapshot_chat(app), "updated_at": at}
            else:
                continue
            out.append((kind, req, expected))
        return out


# ---------------------------------------------------------------- comparison

def summary(v) -> str:
    text = json.dumps(v, ensure_ascii=False, sort_keys=True)
    return f"len={len(text)} sha={hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:10]}"


def canonical(kind: str, msg):
    """Python orders the Host-chat threads from a set(): threads with the same `at` can legitimately arrive in either order, so that list is
    compared in (-at, key) order on both sides."""
    if kind == "chat" and isinstance(msg, dict) and isinstance(msg.get("chat"), dict) and isinstance(msg["chat"].get("host"), list):
        msg = dict(msg)
        chat = dict(msg["chat"])
        try:
            chat["host"] = sorted(chat["host"], key=lambda t: (-int(t.get("at", 0)), str(t.get("key", ""))))
        except Exception:
            pass
        msg["chat"] = chat
    return msg


def first_diff(a, b, path: str = "$") -> tuple[str, object, object] | None:
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


def compare(kind: str, expected_text: str, rust) -> str | None:
    """None when identical, else a one-line description (no chat text, names or ids)."""
    if not isinstance(rust, dict):
        return "rust answer is not an object"
    if "unsupported" in rust:
        return None
    app_msg = canonical(kind, json.loads(expected_text))
    d = first_diff(app_msg, canonical(kind, rust))
    if d is None:
        return None
    path, a, b = d
    return f"{kind} at {path}: app={summary(a)} rust={summary(b)}"


def run_helper(helper: Path, requests: list[dict]) -> list:
    proc = subprocess.run([str(helper)], input=json.dumps(requests, ensure_ascii=False).encode("utf-8"), capture_output=True, timeout=TIMEOUT_S,
                          preexec_fn=lambda: os.nice(10))
    if proc.returncode != 0:
        raise RuntimeError(f"helper exit status {proc.returncode}")
    out = json.loads(proc.stdout)
    if not isinstance(out, list) or len(out) != len(requests):
        raise RuntimeError("helper answered the wrong number of messages")
    return out


def run_shadow(batch: list[tuple[str, dict, str]], log, helper: Path | None = None) -> str:
    """One comparison batch. Returns ``clean``, ``mismatch`` or ``skipped`` (for tests)."""
    def say(text):
        try:
            log(f"[RELAY-MSG-SHADOW] {text}")
        except Exception:
            pass

    try:
        helper = helper or _helper()
        if helper is None or not batch:
            return "skipped"
        answers = run_helper(helper, [req for _k, req, _e in batch])
        diffs = [d for (kind, _r, expected), rust in zip(batch, answers) if (d := compare(kind, expected, rust))]
        if not diffs:
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


def finish(rec: Recorder | None, state, app_version, log) -> threading.Thread | None:
    """Called by the app after a publish. Builds the snapshots on the calling thread (they read live app state), then compares on a daemon thread."""
    if rec is None:
        return None
    try:
        batch = rec.requests(state, app_version)
        if not batch:
            return None
        thread = threading.Thread(target=run_shadow, args=(batch, log), name="relay-msg-shadow", daemon=True)
        thread.start()
        return thread
    except Unsnapshottable:
        return None
    except Exception:  # noqa: BLE001
        return None
