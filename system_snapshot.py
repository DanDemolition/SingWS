"""One-line snapshot of what the Mac is doing, for the stall watchdog (log only, never acts).

Called from the main-thread watchdog when the GUI has been blocked for over a second, so the log says what else was
using the machine (the 2026-10-09 21:55 freeze coincided with opening Chrome, and nothing in the log could say so).
Read-only: psutil reads, one short ``sysctl``. Every step is guarded; a failure just leaves that field out.
"""

from __future__ import annotations

import os
import subprocess
import time

CPU_SAMPLE_S = 0.25
_PRESSURE = {"1": "normal", "2": "warn", "4": "critical"}


def _memory_pressure() -> str:
    try:
        out = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
            capture_output=True, text=True, timeout=1.0,
        ).stdout.strip()
        return _PRESSURE.get(out, out or "?")
    except Exception:
        return "?"


def snapshot(psutil_module=None, sleep=time.sleep, pressure=_memory_pressure, top_n: int = 4) -> str:
    """Return e.g. ``load=2.1/1.8/1.5 mem_pressure=normal ram_free=24.1GB swap=0MB cpu: Google Chrome 85%, ... | mem: ...``."""
    parts: list[str] = []
    try:
        load = os.getloadavg()
        parts.append("load=%.1f/%.1f/%.1f" % load)
    except Exception:
        pass
    parts.append(f"mem_pressure={pressure()}")
    ps = psutil_module
    if ps is None:
        try:
            import psutil as ps  # type: ignore
        except Exception:
            return " ".join(parts)
    try:
        vm = ps.virtual_memory()
        parts.append(f"ram_free={vm.available / 1e9:.1f}GB")
    except Exception:
        pass
    try:
        parts.append(f"swap={ps.swap_memory().used / 1e6:.0f}MB")
    except Exception:
        pass
    try:
        me = os.getpid()
        procs = []
        for p in ps.process_iter(["name"]):
            try:
                p.cpu_percent(None)  # first call only arms the counter; the second, after a pause, is real
                procs.append(p)
            except Exception:
                continue
        sleep(CPU_SAMPLE_S)
        rows = []
        for p in procs:
            try:
                rows.append((p.cpu_percent(None), p.memory_info().rss / 1e6, str(p.info.get("name") or p.pid), p.pid == me))
            except Exception:
                continue
        top_cpu = sorted(rows, key=lambda r: r[0], reverse=True)[:top_n]
        top_mem = sorted(rows, key=lambda r: r[1], reverse=True)[:top_n]
        mine = next((r for r in rows if r[3]), None)
        if mine is not None:
            parts.append(f"singws_cpu={mine[0]:.0f}%")
        parts.append("cpu: " + ", ".join(f"{n} {c:.0f}%" for c, _m, n, _me in top_cpu))
        parts.append("| mem: " + ", ".join(f"{n} {m:.0f}MB" for _c, m, n, _me in top_mem))
    except Exception:
        pass
    return " ".join(parts)
