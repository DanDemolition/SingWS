#!/usr/bin/env python3
"""Stage B parity: replay the real request-admission decisions from show logs through the Rust queue rules.

For every logged poll decision the Python app made (ignore a stale row, accept a reused id) the same facts are fed to
``singws_queue_admit_json`` in the Rust dylib and the answers are compared. Python's logged outcome is the oracle.

Facts are rebuilt from the log itself, so coverage is limited by what the log records:
  * "poll old request ignored" (tombstone branch): the tombstone title comes from the matching earlier
    ``[REMOTE-TOMBSTONE] created`` line. A tombstone created before the first available log is UNKNOWN and skipped.
  * "stale non-terminal row ... ignored" (terminal-id branch): the log does not record the remembered terminal title, so
    this only proves "unknown terminal title => rejected", a weak check that is reported separately.
  * id-reuse acceptances carry both titles in the log and are checked strongly.

Usage:  python3 tools/queue_parity.py ~/SingWS/logs/*.log [--include-test-ids] [--lib PATH]
Exit status 1 on any disagreement.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import queue_replay as qr  # noqa: E402

_OUT_CAP = 512
# The signature-conflict rule (a clearly different title lets a removed id come back) landed in commit 62e7452 at this time.
# Older logs came from the previous rule, which rejected ANY tombstone match, so comparing them against the current rule
# would report 381 "disagreements" that are really just history. They are counted, not compared.
RULE_CHANGE_AT = "2026-07-10T11:46:06"  # commit time of 62e7452 (local, same clock as the log timestamps)


def load_lib(path=None):
    candidates = [path] if path else []
    candidates.append(os.environ.get("SINGWS_DSP_LIB") or "")
    candidates.append(os.path.expanduser("~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            lib = ctypes.CDLL(str(candidate))
            fn = lib.singws_queue_admit_json  # AttributeError if the dylib predates Stage B
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            return lib
    raise SystemExit("no libsingws_dsp_ffi.dylib with singws_queue_admit_json found (build rust/ or pass --lib)")


def rust_admit(lib, tombstone_title=None, id_removed=False, terminal_title=None, row_title=""):
    facts = {"tombstone_title": tombstone_title, "id_removed": bool(id_removed),
             "terminal_title": terminal_title, "row_title": row_title}
    buf = ctypes.create_string_buffer(_OUT_CAP)
    n = lib.singws_queue_admit_json(json.dumps(facts).encode(), buf, _OUT_CAP)
    if n < 0:
        raise RuntimeError(f"rust admit failed code={n} facts={facts}")
    return json.loads(buf.raw[:n].decode())["admit"]


def replay(events, lib):
    """Return (stats Counter, disagreements list)."""
    stats, bad = Counter(), []
    tombstones = {}  # id -> title, as the app's tombstone store would hold it
    for e in events:
        kind = e["kind"]
        if kind == "tombstone_created" and e.get("id") is not None:
            # The app logs "created" only when no tombstone exists for the id, so a second "created" means the earlier one
            # was retired or the store was reset (unit tests reuse small ids this way): the newest title is the live one.
            tombstones[e["id"]] = e.get("title", "")
            continue
        if kind in ("tombstone_poll_ignored", "tombstone_reuse") and e["t"] < RULE_CHANGE_AT:
            stats["skipped_before_rule_change"] += 1
            continue
        if kind == "tombstone_poll_ignored":
            if e.get("branch") == "tombstone":
                if e["id"] not in tombstones:
                    stats["skipped_unknown_tombstone"] += 1
                    continue
                got = rust_admit(lib, tombstone_title=tombstones[e["id"]], id_removed=True, row_title=e.get("title", ""))
                stats["tombstone_ignored_checked"] += 1
                if got != "RejectTombstone":
                    bad.append((e, "RejectTombstone", got, tombstones[e["id"]]))
            else:
                got = rust_admit(lib, id_removed=True, terminal_title=None, row_title=e.get("title", ""))
                stats["terminal_stale_checked_weak"] += 1
                if got != "RejectTerminalStale":
                    bad.append((e, "RejectTerminalStale", got, None))
        elif kind == "tombstone_reuse" and e.get("old_title") and e.get("new_title"):
            if e.get("branch") == "terminal":
                got = rust_admit(lib, id_removed=True, terminal_title=e["old_title"], row_title=e["new_title"])
                want = "AcceptTerminalReuse"
            else:
                got = rust_admit(lib, tombstone_title=e["old_title"], row_title=e["new_title"])
                want = "AcceptIdReuse"
            stats["id_reuse_checked"] += 1
            if got != want:
                bad.append((e, want, got, e["old_title"]))
        elif kind == "tombstone_reuse":
            stats["reuse_without_titles_skipped"] += 1
    return stats, bad


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+")
    parser.add_argument("--include-test-ids", action="store_true")
    parser.add_argument("--lib")
    args = parser.parse_args(argv)
    lib = load_lib(args.lib)
    events = qr.load_events(sorted(args.logs))
    dropped = 0
    if not args.include_test_ids:
        events, dropped = qr.filter_real(events)
    stats, bad = replay(events, lib)
    print(json.dumps({"events": len(events), "dropped_test_events": dropped, **dict(stats), "disagreements": len(bad)}, indent=2))
    for e, want, got, tomb in bad[:10]:
        print(f"DISAGREEMENT id={e['id']} {e.get('file')} {e['t']}: python={want} rust={got} tombstone_title={tomb!r} row_title={e.get('title') or e.get('new_title')!r}")
    strong = stats["tombstone_ignored_checked"] + stats["id_reuse_checked"]
    if not strong:
        print("NO STRONG EVIDENCE: no tombstone/id-reuse decision could be reconstructed from these logs")
    print("QUEUE PARITY OK" if not bad else f"QUEUE PARITY: {len(bad)} disagreement(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
