#!/usr/bin/env python3
"""Stage B replay harness: turn real SingWS show logs into a normalized queue/request event corpus and check the
AGENTS.md queue invariants against what the *current Python app* actually did.

This is the data side of Stage B. It does not model the queue; the pure ``singws-queue`` Rust state machine comes
later and will be replayed against the corpus written here (``--corpus``). Until then the invariant checks report
whether real shows ever violated the rules (a violation is either an app bug to fix first, or a rule to refine).

Invariants checked on observed traces:
  RESURRECTION   a request the host removed or completed (tombstone created) is never inserted again from the server
                 unless the id was legitimately reused for a different song (tombstone-reuse event in between).
  DEDUP_OLDEST   when a duplicate is dropped, the request that is kept is the oldest (ids are epoch-millisecond
                 timestamps, so the kept id must be <= the dropped id).
  ID_STABLE      one host request key never maps to two different request ids.

Host-action invariants (parsed from QUEUE_SYNC_AUTHORITY, ROTATION-SLOT, SERVER-STATE, QUEUE-AUDIT, QUEUE-STATE):
  AUTH_PERMUTATION  a remote reorder never adds or drops request ids (the host/server cannot recreate a removed song).
  AUTH_HOST_ORDER   when the incoming host order is applied, its ids appear in that relative order afterwards.
  AUTH_ECHO_NOOP    a host echo or a pending host order leaves the local order untouched.
  SLOT_FILL         a retained/replacement slot fill only follows a preserve (or an earlier fill), never a host removal
                    and never a slot with no history at all (state carries across log files: a restart restores slots).
  ACCEPTING_HOST    an accepting-requests mismatch is always repaired server-to-local (the host is the source of truth).
Every violation carries an ``origin`` (real, test, ambiguous, unverifiable). "unverifiable" is a slot fill with no recorded
history (the log that preserved the slot is missing). Test and unverifiable violations are printed but do not fail the run:
unit tests write into the live logs on purpose with odd states. Singer names overlap between tests and real shows, so name-only
events are classified from the names seen next to real vs test request ids.

Real request ids are epoch milliseconds. Smaller ids come from unit tests that wrote into a live log; they are dropped
and counted so they cannot create false violations.

Usage:
  python3 tools/queue_replay.py ~/SingWS/logs/*.log [--corpus out.jsonl] [--include-test-ids]
Exit status is 1 when any invariant is violated.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

NON_FAILING_ORIGINS = ("test", "unverifiable")  # printed and counted, but they do not fail the run
REAL_ID_MIN = 10**12  # epoch milliseconds since 2001; unit-test ids are tiny

_LINE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] \[[A-Z]+\] \[([A-Za-z0-9_-]+)\] (.*)$")
_KV = re.compile(r"(\w+)=('(?:[^']*)'|\"(?:[^\"]*)\"|\[[^\]]*\]|\S*)")
_FILE_DATE = re.compile(r"(\d{4}-\d\d-\d\d)")
_TERMINAL_REASONS = {"host_remove_song", "song_completed"}


def _kv(text):
    out = {}
    for key, value in _KV.findall(text):
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


def _int(value):
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _id_list(text):
    """'[1, None, 3]' -> [1, None, 3]; anything unparsable -> None (the event is then skipped by the checks)."""
    if text is None or not text.startswith("["):
        return None
    out = []
    for part in text.strip("[]").split(","):
        part = part.strip()
        if not part:
            continue
        if part == "None":
            out.append(None)
            continue
        value = _int(part)
        if value is None:
            return None
        out.append(value)
    return out


def parse_line(line, date="", seq=0):
    """Return a normalized event dict for one log line, or None if the line is not a queue event."""
    m = _LINE.match(line.rstrip("\n"))
    if not m:
        return None
    hh, mm, ss, tag, body = m.groups()
    kv = _kv(body)
    base = {"t": f"{date}T{hh}:{mm}:{ss}" if date else f"{hh}:{mm}:{ss}", "seq": seq}
    rid = _int(kv.get("request_id"))

    if tag == "REMOTE-TOMBSTONE":
        if body.startswith("created "):
            return {**base, "kind": "tombstone_created", "id": rid, "reason": kv.get("reason", ""),
                    "singer": kv.get("singer", ""), "title": kv.get("title", "")}
        if body.startswith("poll old request ignored"):
            return {**base, "kind": "tombstone_poll_ignored", "id": rid, "branch": "tombstone", "title": kv.get("title", "")}
        if body.startswith("stale non-terminal row"):
            return {**base, "kind": "tombstone_poll_ignored", "id": rid, "branch": "terminal_stale", "title": kv.get("title", "")}
        if body.startswith("reused request id accepted") or body.startswith("id reused with different song signature"):
            return {**base, "kind": "tombstone_reuse", "id": rid, "branch": "tombstone",
                    "old_title": kv.get("tombstone_title", ""), "new_title": kv.get("request_title", "")}
        if body.startswith("terminal id reused by new request"):
            return {**base, "kind": "tombstone_reuse", "id": rid, "branch": "terminal",
                    "old_title": kv.get("old_title", ""), "new_title": kv.get("new_title", "")}
        return None
    if tag == "REQUEST-LIFECYCLE":
        if body.startswith("server confirmed terminal state"):
            return {**base, "kind": "terminal_confirmed", "id": rid, "state": kv.get("state", "")}
        if body.startswith("terminal acknowledgement rejected"):
            return {**base, "kind": "terminal_rejected", "id": rid, "state": kv.get("state", "")}
        if body.startswith("completion queued"):
            return {**base, "kind": "completion_queued", "id": rid, "reason": kv.get("reason", "")}
        return None
    if tag == "QUEUE-DEDUP":
        return {**base, "kind": "dedup", "id": rid, "kept_id": _int(kv.get("kept_request_id")),
                "source": kv.get("source", ""), "singer": kv.get("singer", "")}
    if tag == "QUEUE-INSERT":
        return {**base, "kind": "queue_insert", "id": rid, "source": kv.get("source", ""),
                "reason": kv.get("reason", ""), "queue_revision": _int(kv.get("queue_revision")),
                "singer": kv.get("singer", "")}
    if tag == "QUEUE_SYNC_AUTHORITY" and body.startswith("partial remote reorder"):
        return {**base, "kind": "authority", "singer": kv.get("singer", ""), "ranked": _int(kv.get("ranked")),
                "incoming": _id_list(kv.get("incoming_order")), "before": _id_list(kv.get("before")),
                "after": _id_list(kv.get("after")), "decision": kv.get("decision", "")}
    if tag == "ROTATION-SLOT":
        for prefix, action in (("preserved empty singer slot", "preserved"), ("removed empty singer row", "removed"),
                               ("filled preserved singer slot", "filled")):
            if body.startswith(prefix):
                return {**base, "kind": "slot", "action": action, "singer": kv.get("singer", ""),
                        "reason": kv.get("reason", "")}
        return None
    if tag == "SERVER-STATE":
        if body.startswith("accepting watchdog mismatch"):
            return {**base, "kind": "accepting_mismatch", "server": _int(kv.get("server")),
                    "local": _int(kv.get("local")), "action": kv.get("action", "")}
        if body.startswith("accepting watchdog repair"):
            return {**base, "kind": "accepting_repair", "ok": _int(kv.get("ok"))}
        return None
    if tag == "QUEUE-AUDIT":
        action = kv.get("action", "")
        if action == "dedup_same_request_id":
            return {**base, "kind": "dedup", "id": rid, "kept_id": _int(kv.get("kept_request_id")),
                    "source": "audit", "singer": kv.get("singer", "")}
        if action == "play_start_rollback":
            return {**base, "kind": "play_rollback", "reason": kv.get("reason", ""), "singers": _int(kv.get("singers"))}
        return None
    if tag == "QUEUE-STATE":
        return {**base, "kind": "queue_state", "same_item": kv.get("same_item") == "True",
                "has_current": body.find("current=('', '')") < 0}
    if tag == "HOST-REQUEST-SYNC" and "request_key" in kv and "request_id" in kv and kv.get("result") == "synced":
        return {**base, "kind": "host_sync", "id": rid, "key": kv["request_key"]}
    return None


def load_events(paths):
    events = []
    for path in paths:
        path = Path(path)
        match = _FILE_DATE.search(path.name)
        date = match.group(1) if match else ""
        with open(path, encoding="utf-8", errors="replace") as handle:
            for seq, line in enumerate(handle):
                event = parse_line(line, date=date, seq=seq)
                if event is not None:
                    event["file"] = path.name
                    events.append(event)
    return events


_ID_KINDS = {"tombstone_created", "tombstone_poll_ignored", "tombstone_reuse", "terminal_confirmed", "terminal_rejected",
             "completion_queued", "dedup", "queue_insert", "host_sync"}


def _event_ids(event):
    if event["kind"] == "authority":
        return [i for i in (event.get("incoming") or []) + (event.get("before") or []) + (event.get("after") or [])
                if i is not None]
    return [i for i in (event.get("id"), event.get("kept_id")) if i is not None]


def _is_real(event):
    ids = _event_ids(event)
    return bool(ids) and all(i >= REAL_ID_MIN for i in ids)


def filter_real(events):
    """Drop id-bearing events whose ids are unit-test ids. Name-only events (slots, queue state, accepting) have no
    id to judge, so they are kept and classified by singer name instead (see ``classify``)."""
    kept, dropped = [], 0
    for event in events:
        if event["kind"] == "queue_insert" and event.get("id") is None:
            kept.append(event)  # host-local inserts carry no request id; harmless to keep
        elif event["kind"] in _ID_KINDS or event["kind"] == "authority":
            if _is_real(event):
                kept.append(event)
            else:
                dropped += 1
        else:
            kept.append(event)
    return kept, dropped


def singer_origins(all_events):
    """Map singer name -> "real" / "test" / "ambiguous" from the ids that appear next to the name in ALL events."""
    seen = defaultdict(set)
    for event in all_events:
        name = event.get("singer")
        if not name or event["kind"] not in _ID_KINDS and event["kind"] != "authority":
            continue
        ids = _event_ids(event)
        if ids:
            seen[name].add("real" if all(i >= REAL_ID_MIN for i in ids) else "test")
    return {name: (next(iter(kinds)) if len(kinds) == 1 else "ambiguous") for name, kinds in seen.items()}


def origin_of(event, origins):
    ids = _event_ids(event)
    if ids:
        return "real" if all(i >= REAL_ID_MIN for i in ids) else "test"
    return origins.get(event.get("singer", ""), "ambiguous")


def _order_without_none(ids):
    return [i for i in (ids or []) if i is not None]


def _check_authority(event):
    """Return (invariant, detail) problems for one QUEUE_SYNC_AUTHORITY event."""
    problems = []
    before, after, incoming = event.get("before"), event.get("after"), event.get("incoming")
    if before is None or after is None:
        return problems
    b, a = _order_without_none(before), _order_without_none(after)
    if sorted(b) != sorted(a):
        problems.append(("AUTH_PERMUTATION", f"ids changed: before={b} after={a}"))
    decision = event.get("decision", "")
    if decision in ("host_echo", "pending_host_order") and before != after:
        problems.append(("AUTH_ECHO_NOOP", f"decision={decision} but order changed {before} -> {after}"))
    if decision in ("applied", "newer_singer_order") and incoming:
        wanted = [i for i in incoming if i in a]
        got = [i for i in a if i in set(wanted)]
        if got != wanted:
            problems.append(("AUTH_HOST_ORDER", f"incoming {wanted} not honoured, got {got}"))
    return problems


def check_invariants(events, origins=None):
    """events must be in log order (file, then line). Returns a list of violation dicts, each tagged with an origin."""
    origins = origins or {}
    violations = []
    tombstoned = {}  # id -> event that created the tombstone
    key_ids = defaultdict(set)
    slots = {}  # (file, singer) -> "preserved" | "removed" | "filled"
    for event in events:
        kind, rid = event["kind"], event.get("id")
        if kind == "authority":
            for name, detail in _check_authority(event):
                violations.append({"invariant": name, "id": None, "at": event["t"], "file": event.get("file"),
                                   "detail": f"singer={event.get('singer')!r}: {detail}", "origin": origin_of(event, origins)})
            continue
        if kind == "slot":
            slot_key = event.get("singer")  # not per file: preserved slots survive an app restart (startup restore)
            if event["action"] == "filled" and event.get("reason") in ("empty_slot_retained", "replacement_added") \
                    and slots.get(slot_key) not in ("preserved", "filled"):
                prior = slots.get(slot_key)
                violations.append({"invariant": "SLOT_FILL", "id": None, "at": event["t"], "file": event.get("file"),
                                   "detail": f"singer={event.get('singer')!r} slot filled (reason={event.get('reason')}) "
                                             f"but state was {prior!r}",
                                   # no history at all usually means the preserving log is missing (e.g. a skipped day)
                                   "origin": "unverifiable" if prior is None else origin_of(event, origins)})
            slots[slot_key] = event["action"]
            continue
        if kind == "accepting_mismatch":
            if event.get("action") != "repair_server_to_local":
                violations.append({"invariant": "ACCEPTING_HOST", "id": None, "at": event["t"], "file": event.get("file"),
                                   "detail": f"mismatch server={event.get('server')} local={event.get('local')} "
                                             f"repaired with {event.get('action')!r}", "origin": "real"})
            continue
        n_before = len(violations)
        if kind == "tombstone_created" and event.get("reason") in _TERMINAL_REASONS and rid is not None:
            tombstoned[rid] = event
        elif kind == "tombstone_reuse" and rid is not None:
            tombstoned.pop(rid, None)
        elif kind == "queue_insert" and event.get("source") == "server" and rid in tombstoned:
            violations.append({"invariant": "RESURRECTION", "id": rid, "at": event["t"], "file": event.get("file"),
                               "detail": f"inserted from server after tombstone at {tombstoned[rid]['t']}"})
        elif kind == "dedup" and rid is not None and event.get("kept_id") is not None:
            if event["kept_id"] > rid:
                violations.append({"invariant": "DEDUP_OLDEST", "id": rid, "at": event["t"], "file": event.get("file"),
                                   "detail": f"kept newer id {event['kept_id']} over older {rid}"})
        elif kind == "host_sync":
            key_ids[event["key"]].add(rid)
            if len(key_ids[event["key"]]) > 1:
                violations.append({"invariant": "ID_STABLE", "id": rid, "at": event["t"], "file": event.get("file"),
                                   "detail": f"key {event['key']} maps to ids {sorted(key_ids[event['key']])}"})
        for v in violations[n_before:]:
            v["origin"] = origin_of(event, origins)
    return violations


def summarize(events, dropped, violations):
    kinds = Counter(e["kind"] for e in events)
    ids = {e["id"] for e in events if e.get("id") is not None}
    guards = kinds.get("tombstone_poll_ignored", 0)
    return {
        "events": len(events), "dropped_test_events": dropped, "distinct_request_ids": len(ids),
        "kinds": dict(sorted(kinds.items())), "resurrection_guard_hits": guards,
        "violations": len(violations),
        "violations_by_invariant": dict(Counter(v["invariant"] for v in violations)),
        "violations_by_origin": dict(Counter(v.get("origin", "real") for v in violations)),
    }


def coverage(events):
    """How much real evidence backs each invariant. A clean result on an unexercised invariant proves nothing."""
    kinds = Counter(e["kind"] for e in events)
    keys = {e["key"] for e in events if e["kind"] == "host_sync"}
    return {
        "RESURRECTION": {"tombstones": kinds.get("tombstone_created", 0), "guard_hits": kinds.get("tombstone_poll_ignored", 0),
                         "server_inserts": sum(1 for e in events if e["kind"] == "queue_insert" and e.get("source") == "server")},
        "DEDUP_OLDEST": {"dedup_events": kinds.get("dedup", 0)},
        "ID_STABLE": {"host_sync_events": kinds.get("host_sync", 0), "distinct_keys": len(keys)},
        "ID_REUSE_PATH": {"tombstone_reuse_events": kinds.get("tombstone_reuse", 0)},
        "AUTH": {"authority_events": kinds.get("authority", 0),
                 "reorders_applied": sum(1 for e in events if e["kind"] == "authority"
                                         and e.get("decision") in ("applied", "newer_singer_order"))},
        "SLOT_FILL": {"slot_events": kinds.get("slot", 0),
                      "fills": sum(1 for e in events if e["kind"] == "slot" and e.get("action") == "filled")},
        "ACCEPTING_HOST": {"mismatches": kinds.get("accepting_mismatch", 0)},
        "QUEUE_STATE": {"snapshots": kinds.get("queue_state", 0),
                        "with_current_song": sum(1 for e in events if e["kind"] == "queue_state" and e.get("has_current")),
                        "same_item_true": sum(1 for e in events if e["kind"] == "queue_state" and e.get("same_item"))},
        "PLAY_ROLLBACK": {"rollbacks": kinds.get("play_rollback", 0)},
    }


def unexercised(cov):
    """Names of evidence counters that are zero for the headline invariants."""
    checks = {"RESURRECTION": "tombstones", "DEDUP_OLDEST": "dedup_events", "ID_STABLE": "host_sync_events",
              "ID_REUSE_PATH": "tombstone_reuse_events", "AUTH": "authority_events", "SLOT_FILL": "fills",
              "ACCEPTING_HOST": "mismatches"}
    return [name for name, field in checks.items() if cov[name][field] == 0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+")
    parser.add_argument("--corpus", help="write the normalized events as JSON lines")
    parser.add_argument("--include-test-ids", action="store_true", help="keep unit-test ids (request_id < 1e12)")
    parser.add_argument("--show", type=int, default=10, help="violations to print")
    args = parser.parse_args(argv)

    events = load_events(sorted(args.logs))
    origins = singer_origins(events)
    dropped = 0
    if not args.include_test_ids:
        events, dropped = filter_real(events)
    violations = check_invariants(events, origins)
    failing = [v for v in violations if v.get("origin", "real") not in NON_FAILING_ORIGINS]
    if args.corpus:
        with open(args.corpus, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
    summary = summarize(events, dropped, violations)
    summary["coverage"] = coverage(events)
    print(json.dumps(summary, indent=2))
    gaps = unexercised(summary["coverage"])
    if gaps:
        print("NOT EXERCISED by this data (a clean result proves nothing for): " + ", ".join(gaps))
    for v in violations[: args.show]:
        print(f"VIOLATION[{v.get('origin', 'real')}] {v['invariant']} id={v['id']} {v['file']} {v['at']}: {v['detail']}")
    if failing:
        print(f"QUEUE REPLAY: {len(failing)} violation(s) from real or ambiguous traces "
              f"({len(violations) - len(failing)} test/unverifiable ignored)")
    else:
        note = f" ({len(violations)} test/unverifiable violation(s) ignored)" if violations else ""
        print("QUEUE REPLAY CLEAN" + note)
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
