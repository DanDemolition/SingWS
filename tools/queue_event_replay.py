#!/usr/bin/env python3
"""Stage B state-replay harness: feed an event stream through the app's REAL Python queue code, snapshot after every event.

A future Rust queue runs the same stream through the same ``Implementation`` interface and ``compare`` reports the first
step where the two disagree. Nothing here changes the app: the real ``KaraokeApp`` methods run on a bare instance (the same
way test_remote_request_tombstones.py / test_shell_queue_commands.py build one), with only the UI, network and disk seams
stubbed. Schema, snapshot fields, exclusions and known nondeterminism: docs/design/stage-b-queue-replay.md.

(tools/queue_replay.py is the older LOG-invariant checker; its ``parse_line`` is reused by the log extractor below.)

  python3 tools/queue_event_replay.py corpus [--seeds N] [--length N]      replay the generated + adversarial corpus twice (determinism)
  python3 tools/queue_event_replay.py play FILE.jsonl                       print the snapshots for a stream
  python3 tools/queue_event_replay.py extract LOG... [--out FILE.jsonl]     build a stream from real logs (read-only) and say what it covers
Run with the app's venv (needs PyQt6); SINGWS_HOME is forced to a scratch directory.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import importlib.util
import json
import os
import random
import sys
import tempfile
import types
from pathlib import Path

SCHEMA_VERSION = 1
REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------------------------------------------- app
_MODULE = None
_SCRATCH = None


def load_app_module():
    """Import 0.2.18.1.py once, with SINGWS_HOME pointed at a scratch directory (never ~/SingWS)."""
    global _MODULE, _SCRATCH
    if _MODULE is not None:
        return _MODULE
    home = os.environ.get("SINGWS_HOME", "")
    if not home or Path(home).expanduser().resolve() == (Path.home() / "SingWS").resolve():
        _SCRATCH = tempfile.mkdtemp(prefix="singws-queue-replay-")
        os.environ["SINGWS_HOME"] = _SCRATCH
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")
    sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location("singws_main_queue_replay", str(REPO / "0.2.18.1.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _MODULE = module
    return module


def _quiet(*_a, **_k):
    return None


# ---------------------------------------------------------------------------------------------------------------- world
class World:
    """One bare app plus the alias tables that make snapshots comparable across runs and implementations."""

    def __init__(self, queue_mode="rotation", limit=20, accepting=True, waitlist=False, tombstone_dir=None):
        self.m = load_app_module()
        self.dir = Path(tombstone_dir or tempfile.mkdtemp(prefix="singws-replay-world-"))
        self.clock = 1_700_000_000.0
        self.settings = {"queue_mode": queue_mode, "limit_pending_max": limit, "requests_accepting": bool(accepting), "waitlist": bool(waitlist)}
        self.singer_alias: dict[str, str] = {}   # real singer_id -> S<n>
        self.entry_alias: dict[str, str] = {}    # real entry id (remote:N / local:uuid) -> R<N> / L<n>
        self.seen_singer_keys: set[str] = set()
        self.seen_entry_keys: set[str] = set()
        self.log: list[str] = []
        self._build()

    # -- construction -------------------------------------------------------------------------------
    def _build(self, queue=None, waitlist=None, handled=None, removed_tonight=None):
        m = self.m
        m.REMOTE_REQUEST_TOMBSTONES_PATH = self.dir / "tombstones.json"
        m.WAITLIST_REMOVED_TONIGHT_PATH = self.dir / "waitlist_removed_tonight.json"
        m.DEFERRED_REMOTE_ADDS_PATH = self.dir / "deferred_remote_adds.json"
        base = m.KaraokeApp

        class ReplayApp(base):
            # An uninitialised QObject raises RuntimeError (not AttributeError) for a missing attribute, which would
            # break the app's own hasattr/getattr(default) idioms. The real app has been __init__'d, so make it behave so.
            def __getattribute__(self, name):
                try:
                    return base.__getattribute__(self, name)
                except RuntimeError:
                    raise AttributeError(name)

            def __getattr__(self, name):  # reached only after __getattribute__ failed; keeps sip's own fallback from raising RuntimeError
                raise AttributeError(name)

        app = ReplayApp.__new__(ReplayApp)
        self.app = app
        app.settings = {
            "requests_accepting": True, "base_url": "", "user": "venue", "api_key": "", "karaoke_normalize_enabled": False,
            "empty_rotation_slot_timeout_sec": 180, "defer_remote_adds_until_between_singers": False,
            "shell_commands": "apply",
        }
        app.settings.update(self.settings)
        app.settings["use_waiting_for_add"] = bool(self.settings.get("waitlist", False))
        app.queue = queue if queue is not None else []
        app.singer_history = {"singers": {}, "deletions": {}}
        app._remote_request_tombstones = app._load_remote_request_tombstones()
        app._remote_removed_request_ids = set()
        app._unmatched_remote_request_ids = set()
        app._pending_remote_order_syncs = {}
        app._deferred_remote_adds = []
        app._remote_request_intake_inflight = set()
        app._queue_revision = int(app._remote_request_tombstones.get("revision", 0) or 0) if isinstance(app._remote_request_tombstones, dict) else 0
        app._remote_attention_requests = {}
        app._waiting_for_add_requests = waitlist if waitlist is not None else {}
        app._waiting_for_add_handled_ids = handled if handled is not None else set()
        app._waiting_for_add_removed_tonight = removed_tonight if removed_tonight is not None else {}
        app._singer_rename_aliases = {}
        app._queue_update_batch_depth = 0
        app._queue_display_batch_dirty = False
        app._undo_stack = []
        app._undo_limit = 20
        app._undo_restoring = False
        app._undo_action = None
        app._pending_track_data = None
        app.karaoke_playing = False
        app._current_karaoke_singer_id = ""
        app._current_karaoke_request_id = ""
        app._current_playback_detached_from_rotation = False
        app._disable_accepting_watchdog = True
        app._disable_waitlist_state_pull = True
        app.queue_display = types.SimpleNamespace(count=lambda: 0, item=lambda r: None, setCurrentRow=_quiet)
        app.queue_display_model = types.SimpleNamespace(_rows=[])
        app.singer_input = types.SimpleNamespace(clear=_quiet)
        app.key_selector = types.SimpleNamespace(findText=lambda text: 0, setCurrentIndex=_quiet)
        for name in ("update_queue_display", "_request_queue_display_refresh", "_schedule_save_data", "save_data", "save_settings",
                     "_update_deferred_remote_add_status", "_refresh_header_status", "_apply_idle_background", "_select_queue_singer_for_host",
                     "_save_deferred_remote_adds", "_schedule_waiting_for_add_view_refresh", "_show_queue_limit_rejected",
                     "_report_remote_attention_request_async", "_set_processing_text", "_report_remote_limit_blocked_async",
                     "_sync_remote_singer_order", "_refresh_queue_song_row_identities", "_update_queue_eta_label", "schedule_ticker_update",
                     "_update_undo_action", "_show_processing_notification", "_mark_waiting_for_add_delivered_async",
                     "_restore_waitlist_request_on_server_async", "_sync_remote_removal_tombstones_async", "_save_waitlist_removed_tonight",
                     "_notify_singer_waitlist_add_failed", "_cancel_pending_singer_start_for_queue_change", "_queue_item_kind"):
            setattr(app, name, _quiet)
        app.lookup_display_name = lambda song_path, artist_title_only=False: "Artist • Title"
        app._get_duration_secs = lambda song_path: 180
        app.is_network_configured = lambda: False
        app._prepare_remote_request_add_payload = self._prepare_payload
        app._delete_rotation_singer_row = lambda idx, reason="cleanup": app.queue.pop(idx)
        self.m.QTimer.singleShot = staticmethod(_quiet) if hasattr(self.m, "QTimer") else None

    def _prepare_payload(self, req):
        """Stand-in for the library match only (no filesystem): a deterministic fake .zip path. The REAL process_external_request
        (limit check, waitlist/attention recording, deferral, dedup, tombstones) and _add_song_to_queue run around it."""
        req = dict(req or {})
        singer = str(req.get("singer") or "").strip()
        artist, title = str(req.get("artist") or "").strip(), str(req.get("title") or "").strip()
        if not singer or not title:
            return {"_ok": False, "_failure_reason": "no_local_library_match", "request_id": req.get("request_id"), **req}
        try:
            key = int(req.get("key", 0) or 0)
        except Exception:
            key = 0
        tempo = 100 + max(-30, min(30, int(req.get("tempo", 0) or 0)))
        return {"_ok": True, "request_id": int(req.get("request_id", 0) or 0), "request_time": float(self.clock), "singer": singer, "artist": artist,
                "title": title, "song_data": (f"/lib/{artist}-{title}.zip", key, tempo), "track": _track(artist, title), "remote_meta": req, "brand_info": {}}

    # -- aliases ------------------------------------------------------------------------------------
    def refresh_aliases(self):
        """Assign S<n>/L<n> aliases to ids generated by the app (uuid4, nondeterministic) in queue order, first seen first."""
        E = self.m.KaraokeApp
        for singer in self.app.queue:
            if not isinstance(singer, dict):
                continue
            sid = E._ensure_singer_id(singer)
            if sid not in self.singer_alias:
                self.singer_alias[sid] = f"S{len(self.singer_alias) + 1}"
            for entry in singer.get("songs", []) or []:
                eid = E._ensure_queue_entry_id(entry)
                if eid not in self.entry_alias:
                    if eid.startswith("remote:"):
                        self.entry_alias[eid] = "R" + eid.split(":", 1)[1]
                    else:
                        self.entry_alias[eid] = f"L{sum(1 for v in self.entry_alias.values() if v.startswith('L')) + 1}"

    def real_singer_id(self, ref):
        ref = str(ref or "")
        for real, alias in self.singer_alias.items():
            if alias == ref:
                return real
        for singer in self.app.queue:  # also accept a display name
            if isinstance(singer, dict) and str(singer.get("name", "")) == ref:
                return self.m.KaraokeApp._ensure_singer_id(singer)
        return ""

    def real_entry_id(self, ref):
        ref = str(ref or "")
        for real, alias in self.entry_alias.items():
            if alias == ref:
                return real
        return ""

    # -- events -------------------------------------------------------------------------------------
    def apply(self, event: dict) -> dict:
        """Apply one event; returns {"ok": bool, "code": str}. Never raises for app-level refusals; a real exception is reported as code=exception:<Type>."""
        import time as _time
        op = event.get("op")
        handler = getattr(self, f"op_{op}", None)
        if handler is None:
            return {"ok": False, "code": "unknown_op"}
        self.clock += 1.0
        orig_time = _time.time
        _time.time = lambda: self.clock
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):  # the app prints its diag lines
                result = handler(event)
        except Exception as exc:  # a crash in the app is itself a result worth comparing
            result = {"ok": False, "code": f"exception:{type(exc).__name__}"}
        finally:
            _time.time = orig_time
        if not isinstance(result, dict):
            result = {"ok": bool(result), "code": ""}
        self.refresh_aliases()
        return result

    def op_config(self, e):
        for k in ("queue_mode", "limit_pending_max", "requests_accepting"):
            if k in e:
                self.app.settings[k] = e[k]
        if "waitlist" in e:
            self.app.settings["use_waiting_for_add"] = bool(e["waitlist"])
        return {"ok": True, "code": ""}

    def op_tick(self, e):
        self.clock += float(e.get("seconds", 1))
        return {"ok": True, "code": ""}

    def op_add_song(self, e):
        """Host-local add (no remote id): the operator's Add button path."""
        ok = self.app._add_song_to_queue(str(e["singer"]), (f"/lib/{e.get('artist', '')}-{e['title']}.zip", int(e.get("key", 0))),
                                        track=_track(e.get("artist", ""), e["title"]), remote_meta=None)
        return {"ok": bool(ok), "code": "added" if ok else "refused"}

    def op_remote_arrive(self, e):
        """A server poll / websocket delivery / reconnect resend of request rows, through the real reconcile."""
        reqs = [dict(r) for r in e.get("requests", [])]
        self.app._reconcile_remote_requests(reqs)
        return {"ok": True, "code": ""}

    def op_remove_song(self, e):
        eid = self.real_entry_id(e.get("entry"))
        return self._shell_result(self.app.shell_queue_remove_song(eid) if eid else "unknown_entry")

    def op_remove_singer(self, e):
        sid = self.real_singer_id(e.get("singer"))
        return self._shell_result(self.app.shell_queue_remove_singer(sid) if sid else "unknown_singer")

    def op_move_singer(self, e):
        sid = self.real_singer_id(e.get("singer"))
        return self._shell_result(self.app.shell_queue_move_singer(sid, e.get("where", "")) if sid else "unknown_singer")

    def op_move_song(self, e):
        eid = self.real_entry_id(e.get("entry"))
        return self._shell_result(self.app.shell_queue_move_song(eid, e.get("where", "")) if eid else "unknown_entry")

    def op_skip_singer(self, e):
        sid = self.real_singer_id(e.get("singer"))
        return self._shell_result(self.app.shell_queue_set_singer_skipped(sid, bool(e.get("skipped", True))) if sid else "unknown_singer")

    def op_rotation_advance(self, e):
        """The singer `singer` just started: the real next-singer selection (marker/has_sung bookkeeping). Does not start playback."""
        sid = self.real_singer_id(e.get("singer"))
        if not sid:
            return {"ok": False, "code": "unknown_singer"}
        idx = self.app._select_next_rotation_after_start(sid)
        return {"ok": True, "code": f"next_index={idx}"}

    def op_complete_song(self, e):
        """Playback finished: the real completion tombstone, then the entry leaves the queue. The removal itself lives inside
        play_next_file/the media-end handler (out of scope), so it is modelled here as 'delete the entry', and documented as such."""
        eid = self.real_entry_id(e.get("entry"))
        si, sj = self.app._queue_entry_position_by_id(eid) if eid else (-1, -1)
        if si < 0:
            return {"ok": False, "code": "unknown_entry"}
        singer = self.app.queue[si]
        entry = singer["songs"].pop(sj)
        rid = self.app._queue_entry_remote_request_id(entry)
        if rid is not None:
            self.app._complete_remote_request(rid, entry=entry, singer_name=str(singer.get("name", "")), reason="song_completed")
        singer["has_sung"] = True
        return {"ok": True, "code": "completed"}

    def op_waitlist_add(self, e):
        rid = int(e["request_id"])
        req = self.app._waiting_for_add_requests.get(rid)
        if req is None:
            return {"ok": False, "code": "unknown_waitlist_request"}
        ok, code, _detail = self.app._waitlist_add_request(req, rid)
        return {"ok": bool(ok), "code": code}

    def op_waitlist_remove(self, e):
        rid = int(e["request_id"])
        req = self.app._waiting_for_add_requests.get(rid)
        if req is None:
            return {"ok": False, "code": "unknown_waitlist_request"}
        self.app._waitlist_remove_request(req, rid)
        return {"ok": True, "code": "removed"}

    def op_waitlist_restore(self, e):
        rid = int(e["request_id"])
        req = (self.app._waiting_for_add_removed_tonight or {}).get(rid)
        if not isinstance(req, dict):
            return {"ok": False, "code": "unknown_removed_request"}
        self.app._waitlist_restore_request(req)
        return {"ok": True, "code": "restored"}

    def op_restart(self, e):
        """App restart: tombstones come back from disk, queue and waitlist from their persisted copies; every in-memory set is rebuilt."""
        app = self.app
        queue = copy.deepcopy(app.queue)
        waitlist = copy.deepcopy(app._waiting_for_add_requests)
        removed_tonight = copy.deepcopy(app._waiting_for_add_removed_tonight)
        handled = set(app._waiting_for_add_handled_ids)
        self._build(queue=queue, waitlist=waitlist, handled=handled, removed_tonight=removed_tonight)
        # startup restores the removed-id memory from the persisted tombstones
        try:
            for rid in (self.app._ensure_remote_request_tombstones().get("requests") or {}):
                self.app._remote_removed_request_ids.add(int(rid))
        except Exception:
            pass
        return {"ok": True, "code": "restarted"}

    @staticmethod
    def _shell_result(text):
        text = str(text)
        return {"ok": not text.startswith("unknown"), "code": text}

    # -- snapshot -----------------------------------------------------------------------------------
    def snapshot(self) -> dict:
        app = self.app
        self.refresh_aliases()
        sid_alias, eid_alias = self.singer_alias, self.entry_alias
        E = self.m.KaraokeApp
        singers = []
        in_queue = {}
        for singer in app.queue:
            sid = E._ensure_singer_id(singer)
            songs = []
            for entry in singer.get("songs", []) or []:
                eid = E._ensure_queue_entry_id(entry)
                rid = app._queue_entry_remote_request_id(entry)
                rec = {
                    "id": eid_alias[eid], "remote_request_id": rid, "artist": str(entry.get("artist", "")), "title": str(entry.get("title", "")),
                    "song_info": _jsonable(entry.get("song_info")), "key": entry.get("key", 0), "tempo_percent": entry.get("tempo_percent", 100),
                    "skipped": bool(entry.get("skipped", False)),
                }
                songs.append(rec)
                if rid is not None:
                    in_queue.setdefault(int(rid), []).append(rec["id"])
            singers.append({
                "id": sid_alias[sid], "name": str(singer.get("name", "")), "skipped": bool(singer.get("skipped", False)),
                "has_sung": bool(singer.get("has_sung", False)), "round_sung": bool(singer.get("round_sung", False)),
                "rotation_marker": bool(singer.get("rotation_marker", False)), "songs": songs,
                "temporary_empty_slot": bool(singer.get("temporary_empty_slot", False)),
            })
        tomb = {}
        data = app._ensure_remote_request_tombstones()
        for rid, t in sorted(((int(k), v) for k, v in (data.get("requests") or {}).items()), key=lambda kv: kv[0]):
            tomb[str(rid)] = {k: t.get(k) for k in ("status", "removal_reason", "removed_by", "singer_name", "artist", "title")}
        waitlist = {str(rid): _req_view(r) for rid, r in sorted(app._waiting_for_add_requests.items())}
        removed_tonight = {str(rid): _req_view(r) for rid, r in sorted((app._waiting_for_add_removed_tonight or {}).items())}
        lifecycle = {}
        for rid in sorted(set(in_queue) | {int(k) for k in tomb} | {int(k) for k in waitlist} | {int(k) for k in removed_tonight}):
            if rid in in_queue:
                state = "in_queue"
            elif str(rid) in tomb:
                state = {"completed": "completed", "sung": "completed"}.get(tomb[str(rid)]["status"], "removed")
            elif str(rid) in waitlist:
                state = "waitlisted"
            else:
                state = "removed_tonight"
            lifecycle[str(rid)] = {"state": state, "copies_in_queue": len(in_queue.get(rid, []))}
        return {
            "queue": singers,
            "rotation": {"mode": app.settings.get("queue_mode"), "locked": bool(app._is_rotation_locked()) if hasattr(app, "_is_rotation_locked") else False},
            "waitlist": waitlist, "removed_tonight": removed_tonight, "handled_waitlist_ids": sorted(app._waiting_for_add_handled_ids),
            "tombstones": tomb, "removed_ids": sorted(int(x) for x in app._remote_removed_request_ids),
            "deferred_remote_adds": len(app._deferred_remote_adds), "lifecycle": lifecycle,
        }


def _track(artist, title):
    return {"artist": artist, "title": title, "display": f"{artist} • {title}", "duration": 180, "path": f"/lib/{artist}-{title}.zip"}


def _req_view(r):
    r = r if isinstance(r, dict) else {}
    return {"singer": str(r.get("singer", "")), "artist": str(r.get("artist", "")), "title": str(r.get("title", "")), "state": str(r.get("state", ""))}


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


# ----------------------------------------------------------------------------------------------- implementations
class Implementation:
    """What the comparer needs from any queue implementation (Python today, Rust in Step 3)."""

    name = "implementation"

    def reset(self, config: dict | None = None) -> None:
        raise NotImplementedError

    def apply(self, event: dict) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError


class PythonImpl(Implementation):
    name = "python"

    def __init__(self):
        self.world = None

    def reset(self, config=None):
        cfg = dict(config or {})
        self.world = World(queue_mode=cfg.get("queue_mode", "rotation"), limit=cfg.get("limit_pending_max", 20), accepting=cfg.get("requests_accepting", True), waitlist=cfg.get("waitlist", False))

    def apply(self, event):
        return self.world.apply(event)

    def snapshot(self):
        return self.world.snapshot()


def canonical(snapshot) -> str:
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)


def run_stream(impl: Implementation, stream: list[dict]) -> list[dict]:
    """[{"result":..., "snapshot":...}, ...] one per event. The first event may be {"op":"config",...}; it also resets."""
    config = {}
    for ev in stream:
        if ev.get("op") == "config":
            config.update({k: v for k, v in ev.items() if k != "op"})
    impl.reset(config)
    out = []
    for ev in stream:
        result = impl.apply(ev)
        out.append({"result": result, "snapshot": impl.snapshot()})
    return out


def diff_paths(a, b, path="$", limit=8):
    """Up to ``limit`` JSON paths where a and b differ."""
    out = []

    def walk(x, y, p):
        if len(out) >= limit:
            return
        if type(x) != type(y):
            out.append(f"{p}: {x!r} != {y!r}")
        elif isinstance(x, dict):
            for k in sorted(set(x) | set(y), key=str):
                if k not in x or k not in y:
                    out.append(f"{p}.{k}: {'missing' if k not in x else 'present'} on a")
                else:
                    walk(x[k], y[k], f"{p}.{k}")
        elif isinstance(x, list):
            if len(x) != len(y):
                out.append(f"{p}: length {len(x)} != {len(y)}")
            for i, (xi, yi) in enumerate(zip(x, y)):
                walk(xi, yi, f"{p}[{i}]")
        elif x != y:
            out.append(f"{p}: {x!r} != {y!r}")

    walk(a, b, path)
    return out


def compare(stream: list[dict], impl_a: Implementation, impl_b: Implementation) -> dict:
    """Replay ``stream`` through both implementations. Returns {"equal": bool, "steps": n, "first_divergence": None | {...}}."""
    ra, rb = run_stream(impl_a, stream), run_stream(impl_b, stream)
    for i, (x, y) in enumerate(zip(ra, rb)):
        if canonical(x) != canonical(y):
            return {"equal": False, "steps": len(stream), "first_divergence": {
                "step": i, "event": stream[i], "a": impl_a.name, "b": impl_b.name, "diff": diff_paths(x, y)}}
    return {"equal": True, "steps": len(stream), "first_divergence": None}


# ----------------------------------------------------------------------------------------------- commuting orders
_COMMUTING_OPS = {"remove_song", "skip_singer", "waitlist_remove"}


def _touches(ev):
    return (ev.get("op"), ev.get("entry") or ev.get("singer") or ev.get("request_id"))


def equivalent_reordering(stream: list[dict], rng: random.Random) -> list[dict]:
    """A different ordering that must give the same FINAL state: adjacent commuting events on different targets are swapped,
    and the rows inside one ``remote_arrive`` batch are shuffled only where the singers differ (a singer's own rows keep order)."""
    out = [dict(e) for e in stream]
    for _ in range(len(out) * 2):
        if len(out) < 2:
            break
        i = rng.randrange(len(out) - 1)
        a, b = out[i], out[i + 1]
        if a.get("op") in _COMMUTING_OPS and b.get("op") in _COMMUTING_OPS and _touches(a) != _touches(b):
            out[i], out[i + 1] = b, a
    return out


# ------------------------------------------------------------------------------------------------------ corpus
SINGERS = ["Ada", "Bob", "Cy", "Dee", "Eve", "Flo"]
TITLES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliet"]


def _req(rid, singer, title, artist="Band"):
    return {"request_id": rid, "singer": singer, "artist": artist, "title": title, "key": 0, "tempo": 0}


def _waiting(rid, singer, title):
    return dict(_req(rid, singer, title), state="waiting", pending_reason="rotation_full")


def adversarial_cases() -> dict[str, list[dict]]:
    """Hand-written AGENTS.md cases. Each is a complete stream."""
    cfg = {"op": "config", "queue_mode": "rotation", "limit_pending_max": 20}
    a1, a2, b1 = _req(1001, "Ada", "Alpha"), _req(1002, "Ada", "Bravo"), _req(1003, "Bob", "Charlie")
    return {
        "host_deletes_then_server_relists": [cfg, {"op": "remote_arrive", "requests": [a1, a2, b1]}, {"op": "remove_song", "entry": "R1001"},
                                              {"op": "remote_arrive", "requests": [a1, a2, b1]}, {"op": "remote_arrive", "requests": [a1, a2, b1]}],
        "duplicate_arrivals_keep_oldest": [cfg, {"op": "remote_arrive", "requests": [a1]}, {"op": "remote_arrive", "requests": [a1, a1]},
                                           {"op": "remote_arrive", "requests": [dict(a1, request_id=1005)]}],
        "restart_does_not_recreate_removed_song": [cfg, {"op": "remote_arrive", "requests": [a1, b1]}, {"op": "remove_song", "entry": "R1001"},
                                                   {"op": "restart"}, {"op": "remote_arrive", "requests": [a1, b1]}],
        "restart_keeps_ids_and_order": [cfg, {"op": "remote_arrive", "requests": [a1, a2, b1]}, {"op": "move_singer", "singer": "S2", "where": "top"},
                                        {"op": "restart"}, {"op": "remote_arrive", "requests": [a2, b1, a1]}],
        "completed_is_not_resurrected": [cfg, {"op": "remote_arrive", "requests": [a1, b1]}, {"op": "complete_song", "entry": "R1001"},
                                         {"op": "remote_arrive", "requests": [a1, b1]}, {"op": "restart"}, {"op": "remote_arrive", "requests": [a1, b1]}],
        "remove_singer_removes_all_their_requests": [cfg, {"op": "remote_arrive", "requests": [a1, a2, b1]}, {"op": "remove_singer", "singer": "S1"},
                                                     {"op": "remote_arrive", "requests": [a1, a2, b1]}],
        "id_reused_for_a_different_song": [cfg, {"op": "remote_arrive", "requests": [a1]}, {"op": "remove_song", "entry": "R1001"},
                                           {"op": "remote_arrive", "requests": [_req(1001, "Zed", "Different", "Other")]}],
        "skip_and_reorder_keep_ids": [cfg, {"op": "remote_arrive", "requests": [a1, a2, b1]}, {"op": "skip_singer", "singer": "S1", "skipped": True},
                                      {"op": "move_song", "entry": "R1002", "where": "up"}, {"op": "move_singer", "singer": "S2", "where": "top"},
                                      {"op": "remote_arrive", "requests": [a1, a2, b1]}],
        "rotation_advance_then_remove_current": [cfg, {"op": "remote_arrive", "requests": [a1, b1]}, {"op": "rotation_advance", "singer": "S1"},
                                                 {"op": "remove_song", "entry": "R1001"}, {"op": "rotation_advance", "singer": "S2"}],
        "host_local_add_gets_stable_alias": [cfg, {"op": "add_song", "singer": "Cy", "title": "Local One"}, {"op": "add_song", "singer": "Cy", "title": "Local Two"},
                                             {"op": "move_song", "entry": "L2", "where": "top"}, {"op": "remove_song", "entry": "L1"}],
        "limit_blocked_never_waitlisted": [dict(cfg, limit_pending_max=1), {"op": "remote_arrive", "requests": [a1, a2]}, {"op": "waitlist_add", "request_id": 1002}],
        "waitlist_add_remove_restore": [dict(cfg, waitlist=True), {"op": "remote_arrive", "requests": [_waiting(1002, "Ada", "Bravo"), _waiting(1003, "Bob", "Charlie")]},
                                        {"op": "waitlist_add", "request_id": 1002}, {"op": "remote_arrive", "requests": [_waiting(1002, "Ada", "Bravo"), _waiting(1003, "Bob", "Charlie")]},
                                        {"op": "waitlist_remove", "request_id": 1003}, {"op": "remote_arrive", "requests": [_waiting(1003, "Bob", "Charlie")]},
                                        {"op": "waitlist_restore", "request_id": 1003}, {"op": "waitlist_add", "request_id": 1003}],
        "waitlist_removed_stays_removed_across_restart": [dict(cfg, waitlist=True), {"op": "remote_arrive", "requests": [_waiting(1001, "Ada", "Alpha")]},
                                                          {"op": "waitlist_remove", "request_id": 1001}, {"op": "restart"},
                                                          {"op": "remote_arrive", "requests": [_waiting(1001, "Ada", "Alpha")]}],
        "accepting_off_blocks_new_arrivals": [cfg, {"op": "config", "requests_accepting": False}, {"op": "remote_arrive", "requests": [a1]},
                                              {"op": "config", "requests_accepting": True}, {"op": "remote_arrive", "requests": [a1]}],
    }


def generate_stream(seed: int, length: int = 40) -> list[dict]:
    """A seeded random stream that respects the lifecycle rules: it drives a scratch world while choosing, so it only names
    entities that exist (plus a few deliberate stale references, which must be refused identically by every implementation)."""
    rng = random.Random(seed)
    world = World(queue_mode=rng.choice(["rotation", "classic"]), limit=rng.choice([2, 3, 20]))
    wl = rng.random() < 0.5
    stream = [{"op": "config", "queue_mode": world.settings["queue_mode"], "limit_pending_max": world.settings["limit_pending_max"], "waitlist": wl}]
    world.apply(stream[0])
    next_rid = 5000
    arrived: list[dict] = []

    def emit(ev):
        stream.append(ev)
        world.apply(ev)

    for _ in range(length):
        snap = world.snapshot()
        entries = [s["id"] for q in snap["queue"] for s in q["songs"]]
        singers = [q["id"] for q in snap["queue"]]
        choices = ["arrive"] * 5 + ["relist"] * 3 + ["add_local"] * 2 + ["tick"]
        if entries:
            choices += ["remove_song", "move_song", "complete"] * 2
        if singers:
            choices += ["skip", "move_singer", "advance"] * 2 + ["remove_singer"]
        if snap["waitlist"]:
            choices += ["wl_add", "wl_remove"]
        if snap["removed_tonight"]:
            choices += ["wl_restore"]
        choices += ["restart", "stale_ref"]
        pick = rng.choice(choices)
        if pick == "arrive":
            batch = []
            for _i in range(rng.randint(1, 3)):
                next_rid += 1
                r = _req(next_rid, rng.choice(SINGERS), rng.choice(TITLES))
                if wl and rng.random() < 0.4:
                    r = dict(r, state="waiting", pending_reason="rotation_full")
                arrived.append(r)
                batch.append(r)
            emit({"op": "remote_arrive", "requests": batch})
        elif pick == "relist" and arrived:
            emit({"op": "remote_arrive", "requests": [dict(r) for r in rng.sample(arrived, min(len(arrived), rng.randint(1, 5)))]})
        elif pick == "add_local":
            emit({"op": "add_song", "singer": rng.choice(SINGERS), "title": rng.choice(TITLES) + str(rng.randint(1, 3))})
        elif pick == "tick":
            emit({"op": "tick", "seconds": rng.choice([1, 30, 400])})
        elif pick == "remove_song" and entries:
            emit({"op": "remove_song", "entry": rng.choice(entries)})
        elif pick == "move_song" and entries:
            emit({"op": "move_song", "entry": rng.choice(entries), "where": rng.choice(["up", "down", "top", "bottom"])})
        elif pick == "complete" and entries:
            emit({"op": "complete_song", "entry": rng.choice(entries)})
        elif pick == "skip" and singers:
            emit({"op": "skip_singer", "singer": rng.choice(singers), "skipped": rng.random() < 0.6})
        elif pick == "move_singer" and singers:
            emit({"op": "move_singer", "singer": rng.choice(singers), "where": rng.choice(["up", "down", "top", "bottom"])})
        elif pick == "advance" and singers:
            emit({"op": "rotation_advance", "singer": rng.choice(singers)})
        elif pick == "remove_singer" and singers:
            emit({"op": "remove_singer", "singer": rng.choice(singers)})
        elif pick == "wl_add" and snap["waitlist"]:
            emit({"op": "waitlist_add", "request_id": int(rng.choice(sorted(snap["waitlist"])))})
        elif pick == "wl_remove" and snap["waitlist"]:
            emit({"op": "waitlist_remove", "request_id": int(rng.choice(sorted(snap["waitlist"])))})
        elif pick == "wl_restore" and snap["removed_tonight"]:
            emit({"op": "waitlist_restore", "request_id": int(rng.choice(sorted(snap["removed_tonight"])))})
        elif pick == "restart":
            emit({"op": "restart"})
        elif pick == "stale_ref":
            emit({"op": "remove_song", "entry": "R999999"})
    return stream


# ------------------------------------------------------------------------------------------------- log extraction
# Which operations a real log can and cannot reconstruct (also in the doc).
LOG_RECONSTRUCTS = {
    "remote_arrive": "REQUEST-DIAG status=received (request id, singer, artist, title, key): the arrival, not the server's whole row list",
    "remove_song": "REMOTE-TOMBSTONE created reason=host_remove_song/waiting_for_add_cleared etc. (which request, not which UI action)",
    "complete_song": "REMOTE-TOMBSTONE created status=completed / REQUEST-LIFECYCLE completion queued",
    "remove_singer": "tombstones with reason=host_remove_singer (one per song; the singer removal itself is not logged by id)",
}
LOG_CANNOT = ["move_singer / move_song (only a QUEUE_SYNC_AUTHORITY order, not the host gesture)", "skip_singer", "add_song (host-local adds carry no request id and no title)",
              "rotation_advance (QUEUE-STATE shows the result only)", "waitlist_* (WAITING-FOR-ADD lines carry ids but not the full request)",
              "restart (visible as a new [LAUNCH]; reconstructed here from file boundaries only)",
              "the server's full row list at each poll (only the rows that were acted on are logged)", "wall-clock gaps between events"]


def extract_stream(paths: list[str]) -> tuple[list[dict], dict]:
    """Build a replay stream from real logs, read-only. Returns (stream, coverage)."""
    import re
    sys.path.insert(0, str(REPO / "tools"))
    import queue_replay as qr  # the log parser from Stage B0

    line_re = re.compile(r"\[REQUEST-DIAG\] request_id=(\d+) singer='([^']*)' artist='([^']*)' title='([^']*)'.*?status=received")
    stream: list[dict] = [{"op": "config", "queue_mode": "rotation", "limit_pending_max": 20}]
    seen_arrival: set[int] = set()
    counts = {"lines": 0, "arrive": 0, "arrive_repeat_skipped": 0, "remove": 0, "complete": 0, "remove_singer": 0, "unsupported_kinds": {}}
    for path in paths:
        p = Path(path)
        if stream and len(stream) > 1:
            stream.append({"op": "restart"})
        with open(p, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                counts["lines"] += 1
                m = line_re.search(line)
                if m and int(m.group(1)) >= qr.REAL_ID_MIN:
                    rid = int(m.group(1))
                    if rid in seen_arrival:
                        counts["arrive_repeat_skipped"] += 1
                        continue
                    seen_arrival.add(rid)
                    stream.append({"op": "remote_arrive", "requests": [_req(rid, m.group(2), m.group(4), m.group(3))]})
                    counts["arrive"] += 1
                    continue
                ev = qr.parse_line(line, date="", seq=i)
                if ev is None:
                    continue
                kind = ev["kind"]
                if kind == "tombstone_created" and ev.get("id") and ev["id"] >= qr.REAL_ID_MIN:
                    ref = f"R{ev['id']}"
                    reason = ev.get("reason", "")
                    if ev["id"] not in seen_arrival:
                        continue
                    if reason == "song_completed" or "status=completed" in line:
                        stream.append({"op": "complete_song", "entry": ref}); counts["complete"] += 1
                    elif reason == "host_remove_singer":
                        stream.append({"op": "remove_song", "entry": ref}); counts["remove_singer"] += 1
                    else:
                        stream.append({"op": "remove_song", "entry": ref}); counts["remove"] += 1
                elif kind not in ("tombstone_poll_ignored", "tombstone_reuse", "dedup", "queue_insert"):
                    counts["unsupported_kinds"][kind] = counts["unsupported_kinds"].get(kind, 0) + 1
    removed_ids = {int(e["entry"][1:]) for e in stream if e["op"] in ("remove_song", "complete_song")}
    final = run_stream(PythonImpl(), stream)[-1]["snapshot"]["lifecycle"]
    arrived_ids = {r["request_id"] for e in stream if e["op"] == "remote_arrive" for r in e["requests"]}
    replay = {
        "arrived_requests": len(arrived_ids), "logged_removed_or_completed": len(removed_ids),
        "replay_final_removed_or_completed": sum(1 for r in removed_ids if final.get(str(r), {}).get("state") in ("removed", "completed")),
        "RESURRECTED_after_replay": sorted(r for r in removed_ids if final.get(str(r), {}).get("state") == "in_queue"),
        "arrived_not_in_final_state": sorted(r for r in arrived_ids if str(r) not in final)[:20],
    }
    coverage = {
        "replay": replay, "events": len(stream), **{k: v for k, v in counts.items() if k != "unsupported_kinds"}, "logged_but_not_replayable": counts["unsupported_kinds"],
        "reconstructs": LOG_RECONSTRUCTS, "cannot_reconstruct": LOG_CANNOT,
    }
    return stream, coverage


# ----------------------------------------------------------------------------------------------------- command line
def fingerprint(streams: list[list[dict]]) -> str:
    """sha256 over every snapshot of every stream: identical across processes (different PYTHONHASHSEED) iff nothing nondeterministic leaks in."""
    h = hashlib.sha256()
    for stream in streams:
        for step in run_stream(PythonImpl(), stream):
            h.update(canonical(step).encode())
    return h.hexdigest()


def determinism_check(stream: list[dict]) -> dict:
    return compare(stream, PythonImpl(), PythonImpl())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("corpus"); c.add_argument("--seeds", type=int, default=20); c.add_argument("--length", type=int, default=40)
    c.add_argument("--fingerprint", action="store_true", help="print only a hash of all snapshots (compare across PYTHONHASHSEED values)")
    p = sub.add_parser("play"); p.add_argument("file")
    x = sub.add_parser("extract"); x.add_argument("logs", nargs="+"); x.add_argument("--out")
    args = ap.parse_args(argv)
    load_app_module()
    if args.cmd == "corpus" and args.fingerprint:
        print("FINGERPRINT", fingerprint(list(adversarial_cases().values()) + [generate_stream(i, args.length) for i in range(args.seeds)]))
        return 0
    if args.cmd == "corpus":
        bad = 0
        for name, stream in adversarial_cases().items():
            r = determinism_check(stream)
            print(f"{'ok ' if r['equal'] else 'BAD'} adversarial {name} ({r['steps']} steps)")
            bad += not r["equal"]
        for seed in range(args.seeds):
            stream = generate_stream(seed, args.length)
            r = determinism_check(stream)
            print(f"{'ok ' if r['equal'] else 'BAD'} seed {seed} ({r['steps']} steps)")
            bad += not r["equal"]
        return 1 if bad else 0
    if args.cmd == "play":
        stream = [json.loads(l) for l in Path(args.file).read_text().splitlines() if l.strip()]
        for ev, step in zip(stream, run_stream(PythonImpl(), stream)):
            print(json.dumps({"event": ev, **step}, sort_keys=True, default=str))
        return 0
    stream, cov = extract_stream(args.logs)
    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(e, sort_keys=True) for e in stream) + "\n")
    print(json.dumps(cov, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
