"""Stage 3 shadow hook: feeds real playback events to the Rust song-lifecycle machine and logs disagreements.

SHADOW ONLY. The machine never drives anything; every method swallows its own errors, so a missing dylib, a bad
event or a crash in here cannot affect a show. Off by default (setting ``transport_shadow``); read once at launch.

Log lines (grep ``TRANSPORT-SHADOW``):
  * ``cmd``      what the machine would do for an event (Ignore reasons are logged once per reason per session)
  * ``MISMATCH`` the app finished a song the machine did not complete, or the machine completed one the app never did
  * ``session`` a one-line summary when a session ends
"""

from __future__ import annotations

import ctypes
import json
import time

COMPLETE_GRACE_S = 20.0
_OUT_CAP = 8192


class TransportShadow:
    def __init__(self, log, lib=None):
        self._log = log
        self._lib = lib if lib is not None else _load_lib()
        self.available = self._lib is not None
        self._state = '"Idle"'
        self._session = 0
        self._next_session = 0
        self._shadow_completed_at = None  # monotonic time the machine commanded CompleteSong
        self._app_finished = False
        self._ignored = set()
        self._events = 0
        self.mismatches = 0
        self._auto_flag = False

    # ---- helpers -------------------------------------------------------------------------------------------
    def _say(self, text):
        try:
            self._log(f"[TRANSPORT-SHADOW] {text}")
        except Exception:
            pass

    def _step(self, event, stop_in_progress=False):
        if not self.available:
            return []
        buf = ctypes.create_string_buffer(_OUT_CAP)
        n = self._lib.singws_transport_step_json(
            self._state.encode(), json.dumps(event).encode(), int(bool(stop_in_progress)), buf, _OUT_CAP
        )
        if n < 0:
            self._say(f"step failed code={n} event={event}")
            return []
        out = json.loads(buf.raw[:n].decode())
        self._state = json.dumps(out["state"])
        self._events += 1
        return out["commands"]

    def _check_overdue(self, now):
        t = self._shadow_completed_at
        if t is not None and not self._app_finished and now - t > COMPLETE_GRACE_S:
            self._mismatch(f"machine completed session {self._session} {now - t:.0f}s ago; app has not")
            self._shadow_completed_at = None

    def _mismatch(self, text):
        self.mismatches += 1
        self._say(f"MISMATCH {text}")

    def _record(self, commands, now):
        for c in commands:
            if isinstance(c, dict) and "Ignore" in c:
                reason = c["Ignore"]
                if reason in self._ignored:
                    continue
                self._ignored.add(reason)
            if (isinstance(c, dict) and "CompleteSong" in c) or c == "CompleteSong":
                self._shadow_completed_at = now
            self._say(f"cmd session={self._session} {json.dumps(c)}")

    # ---- events from the app (each returns quietly on any error) -------------------------------------------
    def start(self, external, duration=None):
        """Begin a new session; returns its id (store it with the app's own session object)."""
        try:
            self._flush_session()
            self._next_session += 1
            self._session = self._next_session
            self._state = '"Idle"'
            self._shadow_completed_at = None
            self._app_finished = False
            self._ignored = set()
            self._events = 0
            ev = {"StartRequested": {"session": self._session, "external": bool(external),
                                     "duration": float(duration) if duration else None}}
            self._record(self._step(ev), time.monotonic())
            return self._session
        except Exception as exc:
            self._say(f"start error: {exc!r}")
            return 0

    def observe(self, session, kind, now=None):
        """kind: 'playing' | 'idle' | 'end_clock' | 'watchdog'."""
        try:
            if not self.available or not session:
                return
            now = time.monotonic() if now is None else now
            self._check_overdue(now)
            name = {"playing": "PlayingObserved", "idle": "IdleObserved", "end_clock": "EndClockObserved",
                    "watchdog": "WatchdogExpired"}[kind]
            body = {"session": int(session)} if kind == "watchdog" else {"session": int(session), "now": float(now)}
            self._record(self._step({name: body}), now)
        except Exception as exc:
            self._say(f"observe error: {exc!r}")

    def app_auto_complete(self, session):
        """The app's monitor decided the song ended on its own (called just before it finishes the song)."""
        self._auto_flag = True
        try:
            if self.available and session and not self._completed_in_shadow():
                self._mismatch(f"app auto-completed session {session}; machine had not completed it (state={self._state[:60]})")
        except Exception:
            pass

    def app_finished(self, session, action):
        """The app finished the song (any route). A finish with no auto-complete flag is a manual Complete."""
        try:
            if not self.available or not session:
                return
            now = time.monotonic()
            self._app_finished = True
            if not self._auto_flag:
                self._record(self._step({"ManualComplete": {"session": int(session)}}), now)
            self._auto_flag = False
            self._step({"CleanupDone": {"session": int(session)}})
            self._flush_session()
        except Exception as exc:
            self._say(f"finish error: {exc!r}")

    def _completed_in_shadow(self):
        return '"Ending"' in self._state or '"Ending":' in self._state or self._state.startswith('{"Ending"')

    def _flush_session(self):
        if self._session:
            self._check_overdue(time.monotonic() + COMPLETE_GRACE_S + 1.0)
            self._say(f"session {self._session} summary events={self._events} total_mismatches={self.mismatches}")
            self._session = 0


def _load_lib():
    try:
        from rust_master_dsp import _candidate_paths
        for path in _candidate_paths():
            if not path.exists():
                continue
            lib = ctypes.CDLL(str(path))
            fn = lib.singws_transport_step_json  # AttributeError if this dylib predates Stage 3
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int32, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            return lib
    except Exception:
        pass
    return None
