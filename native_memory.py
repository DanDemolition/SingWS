"""Native-memory probe: logs WHERE the process's memory is (macOS `footprint` categories) so a show log can say which native owner grows.

Why: the shows of 2026-10-23/25 grew from ~0.9 GB to ~3 GB while Python's own allocations stayed flat, and RSS alone cannot say whether the growth is
audio buffers (malloc), GPU/IOSurface frames, image caches or the decoder. `footprint` splits the process's physical footprint by VM category
(Malloc Large, IOSurface, IOAccelerator, ...) in ~0.2 s. This module only LOGS; it never changes behaviour.

Safety: the probe runs on its own daemon thread, never on the GUI thread. `request()` only sets an event and returns. At most one `footprint` runs at a
time, with a hard timeout; runs are coalesced (>= MIN_GAP_S apart); three failures in a row turn the probe off for the session (logged once).

Log line (grep MEMORY-NATIVE): `label=<why> footprint_mb=<total> top=<Category mb (dMB since the last probe)> | ...`. Category names are macOS VM
categories only; no paths, hosts or values.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time

FOOTPRINT = "/usr/bin/footprint"
INTERVAL_S = 300.0
MIN_GAP_S = 20.0
TIMEOUT_S = 8.0
TOP_N = 6
MAX_FAILURES = 3


def parse_footprint(text: str, pid: int | None = None):
    """-> (total_bytes, {category: (bytes, regions)}) from `footprint -j` output, or None when it is not what we expect.
    A category's size is dirty + swapped (what the process owns and the system cannot just drop); clean file-backed pages are not footprint."""
    try:
        doc = json.loads(text)
        procs = doc.get("processes") or []
        proc = None
        for p in procs:
            if pid is None or int(p.get("pid", -1)) == int(pid):
                proc = p
                break
        if proc is None:
            return None
        cats = {}
        for name, c in (proc.get("categories") or {}).items():
            size = int(c.get("dirty", 0) or 0) + int(c.get("swapped", 0) or 0)
            if size > 0:
                cats[str(name)] = (size, int(c.get("regions", 0) or 0))
        total = int(proc.get("footprint", 0) or sum(v[0] for v in cats.values()))
        return total, cats
    except Exception:
        return None


def format_line(label: str, total: int, cats: dict, prev: dict | None, top_n: int = TOP_N) -> str:
    mb = 1024.0 * 1024.0
    ranked = sorted(cats.items(), key=lambda kv: kv[1][0], reverse=True)[:top_n]
    parts = []
    for name, (size, regions) in ranked:
        before = (prev or {}).get(name, (0, 0))[0] if prev is not None else None
        delta = "" if before is None else f" ({(size - before) / mb:+.0f})"
        parts.append(f"{name} {size / mb:.0f}{delta}")
    return f"[MEMORY-NATIVE] label={label} footprint_mb={total / mb:.0f} top=" + " | ".join(parts)


class NativeMemoryProbe:
    def __init__(self, log, pid: int | None = None, run=subprocess.run, interval: float = INTERVAL_S, min_gap: float = MIN_GAP_S,
                 footprint: str = FOOTPRINT, clock=time.monotonic):
        self._log = log
        self._pid = int(pid or os.getpid())
        self._run = run
        self._interval = float(interval)
        self._min_gap = float(min_gap)
        self._footprint = footprint
        self._clock = clock
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pending = None
        self._prev = None
        self._last_run = None
        self._failures = 0
        self.disabled = False
        self.runs = 0
        self._thread = None

    def start(self):
        if self._thread is not None or self.disabled:
            return False
        self._thread = threading.Thread(target=self._loop, name="native-memory-probe", daemon=True)
        self._thread.start()
        self.request("launch")
        return True

    def stop(self):
        self._stop.set()
        self._wake.set()

    def request(self, label: str) -> None:
        """Ask for a sample soon. Never blocks and never runs anything on the caller's thread."""
        try:
            with self._lock:
                self._pending = str(label)[:40]
            self._wake.set()
        except Exception:
            pass

    def _loop(self):
        while not self._stop.is_set():
            self._wake.wait(self._interval)
            if self._stop.is_set():
                return
            self._wake.clear()
            with self._lock:
                label, self._pending = self._pending, None
            now = self._clock()
            if label is not None and self._last_run is not None and now - self._last_run < self._min_gap:
                time.sleep(max(0.0, min(self._min_gap, self._min_gap - (now - self._last_run))))
            self.sample(label or "timer")

    def _footprint_text(self) -> str:
        """Run `footprint <pid> -j <file>` (it refuses stdout and needs a real file) in a private temp dir and return the JSON text."""
        with tempfile.TemporaryDirectory(prefix="singws-fp-") as d:
            out = os.path.join(d, "fp.json")
            result = self._run([self._footprint, str(self._pid), "-j", out], capture_output=True, text=True, timeout=TIMEOUT_S,
                               stdin=subprocess.DEVNULL, check=False)
            if getattr(result, "returncode", 1) != 0:
                raise RuntimeError(f"footprint rc={getattr(result, 'returncode', '?')}")
            with open(out, "r", encoding="utf-8") as fh:
                return fh.read()

    def sample(self, label: str) -> bool:
        """One `footprint` run and one log line. Returns True when a line was logged."""
        if self.disabled:
            return False
        try:
            self._last_run = self._clock()
            text = self._footprint_text()
            parsed = parse_footprint(text, self._pid) if text else None
            if parsed is None:
                raise RuntimeError("unparsed output")
            total, cats = parsed
            self._failures = 0
            self.runs += 1
            self._log(format_line(label, total, cats, self._prev))
            self._prev = cats
            return True
        except Exception as exc:
            self._failures += 1
            if self._failures == 1 or self._failures >= MAX_FAILURES:
                self._log(f"[MEMORY-NATIVE] unavailable ({type(exc).__name__}: {str(exc)[:80]})" + (" - probe off for this session" if self._failures >= MAX_FAILURES else ""))
            if self._failures >= MAX_FAILURES:
                self.disabled = True
                self._stop.set()
            return False
