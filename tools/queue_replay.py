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
            return {**base, "kind": "tombstone_created", "id": rid, "reason": kv.get("reason", "")}
        if body.startswith("poll old request ignored") or body.startswith("stale non-terminal row"):
            return {**base, "kind": "tombstone_poll_ignored", "id": rid}
        if body.startswith("reused request id accepted") or body.startswith("id reused with different song signature"):
            return {**base, "kind": "tombstone_reuse", "id": rid}
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
                "source": kv.get("source", "")}
    if tag == "QUEUE-INSERT":
        return {**base, "kind": "queue_insert", "id": rid, "source": kv.get("source", ""),
                "reason": kv.get("reason", ""), "queue_revision": _int(kv.get("queue_revision"))}
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


def _is_real(event):
    ids = [event.get("id"), event.get("kept_id")]
    ids = [i for i in ids if i is not None]
    return bool(ids) and all(i >= REAL_ID_MIN for i in ids)


def filter_real(events):
    kept, dropped = [], 0
    for event in events:
        if event["kind"] == "queue_insert" and event.get("id") is None:
            kept.append(event)  # host-local inserts carry no request id; harmless to keep
        elif _is_real(event):
            kept.append(event)
        else:
            dropped += 1
    return kept, dropped


def check_invariants(events):
    """events must be in log order (file, then line). Returns a list of violation dicts."""
    violations = []
    tombstoned = {}  # id -> event that created the tombstone
    key_ids = defaultdict(set)
    for event in events:
        kind, rid = event["kind"], event.get("id")
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
    }


def unexercised(cov):
    """Names of evidence counters that are zero for the headline invariants."""
    checks = {"RESURRECTION": "tombstones", "DEDUP_OLDEST": "dedup_events", "ID_STABLE": "host_sync_events",
              "ID_REUSE_PATH": "tombstone_reuse_events"}
    return [name for name, field in checks.items() if cov[name][field] == 0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+")
    parser.add_argument("--corpus", help="write the normalized events as JSON lines")
    parser.add_argument("--include-test-ids", action="store_true", help="keep unit-test ids (request_id < 1e12)")
    parser.add_argument("--show", type=int, default=10, help="violations to print")
    args = parser.parse_args(argv)

    events = load_events(sorted(args.logs))
    dropped = 0
    if not args.include_test_ids:
        events, dropped = filter_real(events)
    violations = check_invariants(events)
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
        print(f"VIOLATION {v['invariant']} id={v['id']} {v['file']} {v['at']}: {v['detail']}")
    print("QUEUE REPLAY CLEAN" if not violations else f"QUEUE REPLAY: {len(violations)} violation(s)")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
