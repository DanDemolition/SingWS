"""Stage B shadow hook: compares the Python poll-row admission decision with the Rust queue rules and LOGS disagreements.

SHADOW ONLY. The Rust answer never drives anything; every method swallows its own errors, so a missing dylib, a bad
fact or a crash in here cannot affect a show. Setting ``queue_shadow`` (default off until the package gate passes).

Log lines (grep ``QUEUE-SHADOW``):
  * ``decision``  first time each (branch, answer) pair is seen in a session, with the agreed answer
  * ``MISMATCH``  the Rust queue rules gave a different answer than the app did (the app's answer was used)
  * ``summary``   running totals, emitted every SUMMARY_EVERY checks
"""

from __future__ import annotations

import ctypes
import json

_OUT_CAP = 512
SUMMARY_EVERY = 200

# What the app decided -> the Rust enum name. Keeping the app's branches named here makes the hook call sites explicit.
BRANCHES = {
    "id_reuse": "AcceptIdReuse",              # tombstone matched, clearly a different song
    "tombstone": "RejectTombstone",           # tombstone matched, same song: host removal stands
    "terminal_reuse": "AcceptTerminalReuse",  # known terminal id, clearly a different song
    "terminal_stale": "RejectTerminalStale",  # known terminal id, same/unknown song
}


class QueueShadow:
    def __init__(self, log, lib=None):
        self._log = log
        self._lib = lib if lib is not None else _load_lib()
        self.available = self._lib is not None
        self.checks = 0
        self.mismatches = 0
        self._seen = set()

    def _say(self, text):
        try:
            self._log(f"[QUEUE-SHADOW] {text}")
        except Exception:
            pass

    def admit(self, tombstone_title, id_removed, terminal_title, row_title):
        """Rust's admission answer for the facts, or None when the machine cannot answer."""
        if not self.available:
            return None
        facts = {
            "tombstone_title": None if tombstone_title is None else str(tombstone_title),
            "id_removed": bool(id_removed),
            "terminal_title": None if terminal_title is None else str(terminal_title),
            "row_title": str(row_title or ""),
        }
        buf = ctypes.create_string_buffer(_OUT_CAP)
        n = self._lib.singws_queue_admit_json(json.dumps(facts).encode(), buf, _OUT_CAP)
        if n < 0:
            self._say(f"admit failed code={n}")
            return None
        return json.loads(buf.raw[:n].decode())["admit"]

    def check(self, branch, request_id, tombstone_title=None, id_removed=False, terminal_title=None, row_title=""):
        """Record the app's decision (``branch`` is a key of BRANCHES) and compare it with Rust. Never raises.

        Returns True/False for agree/disagree, or None when no comparison was possible."""
        try:
            if not self.available:
                return None
            expected = BRANCHES[branch]
            got = self.admit(tombstone_title, id_removed, terminal_title, row_title)
            if got is None:
                return None
            self.checks += 1
            agree = got == expected
            if not agree:
                self.mismatches += 1
                self._say(
                    f"MISMATCH request_id={request_id} app={expected} rust={got} tombstone_title={tombstone_title!r} "
                    f"row_title={row_title!r} id_removed={int(bool(id_removed))} terminal_title={terminal_title!r}"
                )
            elif (branch, got) not in self._seen:
                self._seen.add((branch, got))
                self._say(f"decision branch={branch} answer={got} agree=1")
            if self.checks % SUMMARY_EVERY == 0:
                self._say(f"summary checks={self.checks} mismatches={self.mismatches}")
            return agree
        except Exception as exc:
            self._say(f"check error: {exc!r}")
            return None


def _load_lib():
    try:
        from rust_master_dsp import _candidate_paths
        for path in _candidate_paths():
            if not path.exists():
                continue
            lib = ctypes.CDLL(str(path))
            fn = lib.singws_queue_admit_json  # AttributeError if this dylib predates Stage B
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            return lib
    except Exception:
        pass
    return None
