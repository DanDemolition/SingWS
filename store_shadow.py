"""Stage D2 shadow hook: proves the Rust JSON store writer can reproduce, byte for byte, every store file the app writes.

SHADOW ONLY. Rust never writes anything. After the app has durably written a store (queue, singer history, singer preferences, tracks,
tombstones, playlist, settings, ...) the exact bytes it wrote are handed to a BACKGROUND worker, which asks the Rust library to parse them and
write them back in a known Python layout; the result must equal the original bytes. A layout Rust cannot reproduce, a value it refuses
(integers beyond 64 bits, NaN/Infinity, lone surrogates) or any crash is logged and never affects the show.

Why a worker: singer_history.json reaches ~1 MB and the check costs tens of milliseconds, which must never land on the GUI thread. The
worker takes one item at a time from a small bounded queue; when it is busy the newest check is DROPPED and counted, not queued without limit.
Setting ``store_shadow`` (default off until the package gate passes).

Log lines (grep ``STORE-SHADOW``): ``enabled``, ``decision store=<file> layout=<name> agree=1`` (first agreement per store),
``MISMATCH store=<file> ...``, ``dropped``, ``summary`` every SUMMARY_EVERY checks.
"""

from __future__ import annotations

import ctypes
import json
import queue
import threading

SUMMARY_EVERY = 100
MAX_PENDING = 4

# The layouts the app writes (see _save_json_atomic and the tombstone / playlist writers), most common first. A store passes if Rust
# reproduces the payload under ANY of them; `layout` in the log says which one.
LAYOUTS = (
    ("indent2+newline", {"indent": 2, "trailing_newline": True}),
    ("indent2", {"indent": 2}),
    ("compact+newline", {"compact": True, "trailing_newline": True}),
    ("compact", {"compact": True}),
    ("default", {}),
)


class StoreShadow:
    def __init__(self, log, lib=None, start_worker=True):
        self._log = log
        self._lib = lib if lib is not None else _load_lib()
        self.available = self._lib is not None
        self.checks = 0
        self.mismatches = 0
        self.dropped = 0
        self._seen = set()
        self._pending = queue.Queue(maxsize=MAX_PENDING)
        self._idle = threading.Event()
        self._idle.set()
        self._worker = None
        if self.available and start_worker:
            self._worker = threading.Thread(target=self._run, name="store-shadow", daemon=True)
            self._worker.start()

    def _say(self, text):
        try:
            self._log(f"[STORE-SHADOW] {text}")
        except Exception:
            pass

    def submit(self, store, payload):
        """Queue a check of `payload` (the exact text just written for `store`). Never blocks, never raises."""
        try:
            if not self.available:
                return False
            self._idle.clear()
            self._pending.put_nowait((str(store), payload))
            return True
        except queue.Full:
            self.dropped += 1
            if self.dropped in (1, 10, 100):
                self._say(f"dropped checks={self.dropped} (worker busy; the app was not slowed)")
            return False
        except Exception:
            return False

    def wait_idle(self, timeout=10.0):
        """Block until every submitted check has finished (tests and shutdown only)."""
        return self._idle.wait(timeout)

    def _run(self):
        while True:
            store, payload = self._pending.get()
            try:
                self.check(store, payload)
            except Exception as exc:  # the worker must outlive any single bad item
                self._say(f"worker error store={store}: {exc!r}")
            finally:
                if self._pending.empty():
                    self._idle.set()

    def _roundtrip(self, text, style):
        raw = text.encode("utf-8")
        cap = len(raw) * 6 + 4096  # ensure_ascii can expand non-ASCII text up to 6x
        buf = ctypes.create_string_buffer(cap)
        n = self._lib.singws_store_roundtrip(raw, json.dumps(style).encode(), buf, cap)
        return n if n < 0 else buf.raw[:n].decode("utf-8")

    def check(self, store, payload):
        """Synchronous check (called by the worker). Returns True/False for agree/disagree, None when no comparison was possible."""
        try:
            if not self.available:
                return None
            tried, errors = [], []
            for name, style in LAYOUTS:
                out = self._roundtrip(payload, style)
                if isinstance(out, int):
                    errors.append(out)
                    continue
                if out == payload:
                    return self._finish(store, True, name, "")
                tried.append(name)
            if errors and len(errors) == len(LAYOUTS):
                # Rust refused the document under every layout: a value it cannot reproduce (big integer, NaN, lone surrogate, ...)
                return self._finish(store, False, "-", f"rust_refused code={errors[0]} bytes={len(payload.encode('utf-8'))}")
            return self._finish(store, False, "-", f"no_layout_reproduces_the_bytes tried={','.join(tried)} bytes={len(payload.encode('utf-8'))}")
        except Exception as exc:
            self._say(f"check error store={store}: {exc!r}")
            return None

    def _finish(self, store, agree, layout, detail):
        self.checks += 1
        if not agree:
            self.mismatches += 1
            self._say(f"MISMATCH store={store} {detail}")
        elif store not in self._seen:
            self._seen.add(store)
            self._say(f"decision store={store} layout={layout} agree=1")
        if self.checks % SUMMARY_EVERY == 0:
            self._say(f"summary checks={self.checks} mismatches={self.mismatches} dropped={self.dropped}")
        return agree


def _load_lib():
    try:
        from rust_master_dsp import _candidate_paths
        for path in _candidate_paths():
            if not path.exists():
                continue
            lib = ctypes.CDLL(str(path))
            fn = lib.singws_store_roundtrip  # AttributeError if this dylib predates Stage D
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            return lib
    except Exception:
        pass
    return None
