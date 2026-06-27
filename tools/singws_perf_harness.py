#!/usr/bin/env python3
"""Collect lightweight SingWS performance snapshots during live testing.

Typical Intel Mac run:
  python3 tools/singws_perf_harness.py --duration 180 --sample-every 30

The script does not drive the UI. Start playback, run this script, then stress
the app manually: server adds, Singer History, Waitlist, Sound Clip Board, etc.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        return int(proc.returncode), proc.stdout or ""
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}\n"


def _find_pid(name: str) -> int | None:
    code, out = _run(["ps", "-axo", "pid=,comm="])
    if code != 0:
        return None
    needle = (name or "SingWS").lower()
    matches: list[tuple[int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_text, command = line.split(None, 1)
            pid = int(pid_text)
        except Exception:
            continue
        if needle in command.lower():
            matches.append((pid, command))
    if not matches:
        return None
    matches.sort(key=lambda item: ("/Contents/MacOS/" not in item[1], item[0]))
    return matches[0][0]


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)
        if text and not text.endswith("\n"):
            fh.write("\n")


def _snapshot_ps(pid: int) -> str:
    code, out = _run(["ps", "-p", str(pid), "-o", "pid,ppid,%cpu,%mem,rss,vsz,nlwp,etime,command"], timeout=5.0)
    stamp = _dt.datetime.now().isoformat(timespec="seconds")
    return f"\n### {stamp} ps snapshot\nexit={code}\n{out}"


def _snapshot_threads(pid: int) -> str:
    code, out = _run(["ps", "-M", str(pid)], timeout=5.0)
    stamp = _dt.datetime.now().isoformat(timespec="seconds")
    return f"\n### {stamp} thread snapshot\nexit={code}\n{out}"


def _run_sample(pid: int, seconds: int, dest: Path) -> str:
    if shutil.which("sample") is None:
        return "sample unavailable on this system\n"
    code, out = _run(["sample", str(pid), str(max(1, int(seconds))), "-file", str(dest)], timeout=max(10.0, seconds + 10.0))
    if code != 0:
        return f"sample failed exit={code}\n{out}\n"
    return f"sample wrote {dest}\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Collect SingWS live performance snapshots.")
    parser.add_argument("--pid", type=int, default=0, help="SingWS process id. Auto-detected when omitted.")
    parser.add_argument("--name", default="SingWS", help="Process name fragment for auto-detect.")
    parser.add_argument("--duration", type=int, default=180, help="Total capture duration in seconds.")
    parser.add_argument("--interval", type=int, default=5, help="ps/thread snapshot interval in seconds.")
    parser.add_argument("--sample-every", type=int, default=30, help="Run macOS sample every N seconds. 0 disables sample.")
    parser.add_argument("--sample-seconds", type=int, default=5, help="Duration for each macOS sample capture.")
    parser.add_argument("--out", default="", help="Output directory. Defaults to /tmp/singws_perf_<timestamp>.")
    args = parser.parse_args(argv)

    pid = int(args.pid or 0) or _find_pid(args.name)
    if not pid:
        print("Could not find SingWS process. Pass --pid after launching the app.", file=sys.stderr)
        return 2

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out or f"/tmp/singws_perf_{stamp}").expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    ps_log = out_dir / "ps_snapshots.txt"
    thread_log = out_dir / "thread_snapshots.txt"
    notes = out_dir / "README.txt"
    notes.write_text(
        "SingWS performance capture\n"
        f"pid={pid}\n"
        f"duration={args.duration}s interval={args.interval}s sample_every={args.sample_every}s\n"
        "During capture, exercise playback, server adds, Singer History, Waitlist, and Sound Clip Board.\n",
        encoding="utf-8",
    )

    print(f"Capturing SingWS pid={pid} into {out_dir}")
    started = time.monotonic()
    next_ps = 0.0
    next_sample = 0.0
    sample_index = 1
    while True:
        elapsed = time.monotonic() - started
        if elapsed >= max(1, int(args.duration)):
            break
        if elapsed >= next_ps:
            _append(ps_log, _snapshot_ps(pid))
            _append(thread_log, _snapshot_threads(pid))
            next_ps = elapsed + max(1, int(args.interval))
        if int(args.sample_every) > 0 and elapsed >= next_sample:
            sample_path = out_dir / f"sample_{sample_index:03d}.txt"
            _append(out_dir / "sample_log.txt", _run_sample(pid, int(args.sample_seconds), sample_path))
            sample_index += 1
            next_sample = elapsed + max(1, int(args.sample_every))
        time.sleep(0.25)

    _append(ps_log, _snapshot_ps(pid))
    _append(thread_log, _snapshot_threads(pid))
    print(f"Done. Capture bundle: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
