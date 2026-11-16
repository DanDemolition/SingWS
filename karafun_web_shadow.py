"""Shadow for the KaraFun web player: the Rust port (`singws-karafun-svc`, called through `singws_kfweb_json` in libsingws_dsp_ffi) is fed the SAME events as
`karafun_web.KaraFunWebService` (sign-in, saved login, reload, profile reset) and `karafun_web_host.KaraFunWebHost` (a song held while the player signs in, play sent,
started, the never-started watchdog, recovery, the end of the song) and its answers are compared with what the Python really did.

SHADOW ONLY: the Python always acts; every function here swallows its own errors, so a missing dylib or a crash in here cannot affect a song or a show. Setting
``karafun_web_engine`` (read once at launch): "shadow" (default) compares and logs, "python" switches this off, "rust" is reserved for the day Rust drives the player
(not built: it behaves like "shadow" and says so in the log).

How it works: the Python code calls `tap.event(...)` around each thing the Rust machine models and `tap.fx(...)` at the few places where it acts ("set" a state, "login",
"reload", "sign_in", "reset", "play", "fail", "started", "complete", "recover", "returns"). When the bracket ends, the event is sent to the Rust session, its actions are
projected onto the same small vocabulary, and the two ordered lists are compared. Nothing private is logged: no titles, no page text, no password (the Rust machine only
hears whether a saved login was found).

Log lines (grep ``KFWEB-SHADOW``):
  enabled available=0|1 engine=shadow                                one per launch
  decision kind=<svc_message|host_start|host_service|host_status|host_error|host_stall|host_finish> agree=0|1 [outcome=...]
                                                                      one per compared decision that DID something (and the first "still waiting" of each song for host_stall)
  MISMATCH <kind> python=[...] rust=[...]                            every disagreement (effects only, no text)
  declined <kind> <reason>                                           Rust does not model this case (not compared)

Known, accepted noise: the watchdog compares `now - sent_at` with a limit; Python takes `sent_at` a few milliseconds after the event time Rust uses, so a check that
lands within a few milliseconds of the limit can disagree once.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import threading
import time

_lib = None
_loaded = False
_mode = "python"
_log = lambda text: None  # noqa: E731
_OUT_CAP = 1 << 16
MODES = ("shadow", "python", "rust")


def configure(mode, log) -> None:
    """Called once at launch with the setting's value and the app's diag function."""
    global _mode, _log
    mode = str(mode or "shadow").strip().lower()
    _mode = mode if mode in MODES else "shadow"
    _log = log if callable(log) else (lambda text: None)
    if _mode != "python":
        _say(f"enabled available={int(_library() is not None)} engine={_mode}" + ("  (rust driving is not built yet: shadow only)" if _mode == "rust" else ""))
    else:
        _say("off (karafun_web_engine=python)")


def enabled() -> bool:
    return _mode != "python" and _library() is not None


def _say(text: str) -> None:
    try:
        _log(f"[KFWEB-SHADOW] {text}")
    except Exception:
        pass


def _library():
    global _lib, _loaded
    if _loaded:
        return _lib
    _loaded = True
    try:
        from rust_master_dsp import _candidate_paths
        for path in _candidate_paths():
            if not path.exists():
                continue
            lib = ctypes.CDLL(str(path))
            fn = lib.singws_kfweb_json            # AttributeError when this dylib predates the web-player shadow
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            _lib = lib
            return _lib
    except Exception:
        pass
    return None


def _call(request: dict):
    """One request to the Rust sessions; the decoded answer, or None when it could not answer."""
    lib = _library()
    if lib is None:
        return None
    out = ctypes.create_string_buffer(_OUT_CAP)
    n = lib.singws_kfweb_json(json.dumps(request, default=str).encode("utf-8"), out, _OUT_CAP)
    if n < 0:
        return None
    return json.loads(out.raw[:n].decode("utf-8"))


# ---- what the Python did / what the Rust answered, in one small vocabulary
def fail_kind(why) -> str:
    """The reason of a failed song as a class (never the text, which can carry page output)."""
    text = str(why or "")
    if text.startswith("KaraFun's web player is not signed in"):
        return "sign_in"
    if text == "KaraFun's web player never started the song":
        return "never_started"
    if text.startswith("KaraFun's player stopped and could not be restarted"):
        return "recover_gave_up"
    if text.startswith("KaraFun's player could not be restarted"):
        return "restart_failed"
    return "other"


def project_service(actions) -> list:
    out = []
    for a in actions or []:
        name = a[0] if a else ""
        if name == "set":
            out.append(["set", a[1]])
        elif name in ("reload", "login", "sign_in"):
            out.append([name])
        elif name == "restart":
            out.append(["reset"])
    return out


_GIVING_UP = "giving up on the song: "


def project_host(actions, kind: str) -> list:
    out = []
    for a in actions or []:
        name = a[0] if a else ""
        if name == "web_play":
            out.append(["play"])
        elif name == "log" and len(a) > 1 and str(a[1]).startswith(_GIVING_UP):
            out.append(["fail", fail_kind(str(a[1])[len(_GIVING_UP):])])
        elif name == "start_frames":
            out.append(["started"])
        elif name == "finish_complete":
            out.append(["complete"])
        elif name == "service_stop":
            out.append(["recover"])
        elif name == "returns" and kind in ("host_start", "host_finish"):
            out.append(["returns", bool(a[1])])
    return out


class _Bracket:
    def __init__(self, tap, kind, event, compare):
        self.tap, self.kind, self.event, self.compare = tap, kind, event, compare
        self.effects: list = []
        self.saved = None            # the saved-login read's answer, when the Python read it during this event
        self.always = False          # log the decision even when neither side did anything (the first "still waiting" check of a song)
        self.inner: list = []        # events that began and ended INSIDE this one (a song's finish runs inside the status that ended it): sent to Rust after this one

    def set(self, **fields):
        self.event.update(fields)


class Tap:
    """One live session of a Rust machine (the service or the host) and the effects the Python reports around each event."""

    def __init__(self, machine: str, session_id: int, project):
        self.machine, self.id, self.project = machine, session_id, project
        self._lock = threading.Lock()
        self._local = threading.local()
        self.dead = False

    # -- the Python reports what it did
    def fx(self, *effect) -> None:
        bracket = getattr(self._local, "bracket", None)
        if bracket is not None:
            bracket.effects.append(list(effect))

    def saved(self, found: bool) -> None:
        bracket = getattr(self._local, "bracket", None)
        if bracket is not None:
            bracket.saved = bool(found)

    # -- one event
    @contextlib.contextmanager
    def event(self, kind: str, event: dict, compare: bool = True):
        if self.dead:
            yield _Bracket(self, kind, event, False)
            return
        bracket = _Bracket(self, kind, dict(event), compare)
        previous = getattr(self._local, "bracket", None)
        self._local.bracket = bracket
        failed = False
        try:
            yield bracket
        except BaseException:
            failed = True
            raise
        finally:
            self._local.bracket = previous
            if previous is not None:
                previous.inner.append((bracket, failed))     # Rust must hear the OUTER event first: it caused the inner one
            else:
                try:
                    self._finish(bracket, failed)
                except Exception:
                    pass

    def feed(self, event: dict) -> None:
        """An event the Rust machine must hear to stay in step, with nothing to compare."""
        if self.dead:
            return
        try:
            self._send(event)
        except Exception:
            pass

    def _send(self, event: dict):
        op = "svc_step" if self.machine == "svc" else "host_step"
        with self._lock:
            return _call({"op": op, "id": self.id, "event": event})

    def _finish(self, b: _Bracket, failed: bool) -> None:
        try:
            self._finish_one(b, failed)
        finally:
            for inner, inner_failed in b.inner:
                try:
                    self._finish(inner, inner_failed)
                except Exception:
                    pass

    def _finish_one(self, b: _Bracket, failed: bool) -> None:
        event = dict(b.event)
        if self.machine == "svc" and b.saved is not None:
            event["saved"] = {"result": "found" if b.saved else "none"}
        answer = self._send(event)
        if answer is None:
            self.dead = True                       # the library went away: stop pretending
            _say(f"rust could not answer {b.kind}; this shadow stops for the rest of the run")
            return
        if "declined" in answer:
            _say(f"declined {b.kind} {str(answer['declined'])[:80]}")
            return
        if failed or not b.compare:
            return
        rust = self.project(answer.get("actions"), b.kind) if self.machine == "host" else self.project(answer.get("actions"))
        python = b.effects
        agree = python == rust
        if not agree:
            _say(f"MISMATCH {b.kind} python={json.dumps(python)} rust={json.dumps(rust)}")
        if python or rust or b.always:
            _say(f"decision kind={b.kind} agree={int(agree)}" + ("" if (python or rust) else " outcome=waiting"))

    def close(self) -> None:
        try:
            with self._lock:
                _call({"op": "drop", "id": self.id})
        except Exception:
            pass
        self.dead = True


def service_tap(has_credentials: bool = True):
    """A tap for one KaraFunWebService, or None when the shadow is off or the library is missing."""
    try:
        if not enabled():
            return None
        answer = _call({"op": "svc_new", "has_credentials": bool(has_credentials)})
        if not answer or "id" not in answer:
            return None
        return Tap("svc", int(answer["id"]), project_service)
    except Exception:
        return None


def host_tap():
    """A tap for one KaraFunWebHost, or None when the shadow is off or the library is missing."""
    try:
        if not enabled():
            return None
        answer = _call({"op": "host_new"})
        if not answer or "id" not in answer:
            return None
        return Tap("host", int(answer["id"]), project_host)
    except Exception:
        return None


def now() -> float:
    return time.monotonic()
