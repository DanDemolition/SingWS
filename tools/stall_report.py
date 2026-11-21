#!/usr/bin/env python3
"""Post-show report of the UI stalls: how many, how long, and which slow operations were logged beside them.

READ-ONLY over the show logs. The app logs `[STALL] GUI thread blocked >120ms` / `recovered after ~Nms` and, for any operation slower than its threshold,
`[PERF-DIAG] <name> took Nms`. With the stack capture off (the default) a stall has no stack, so the best evidence is the operations logged within two seconds of it.

Prints: the stall count and total, percentiles, the biggest stalls (after the first minute of each launch) with the slow operations beside them, and the operations
by total time. Several logs are read in order (give the rotated file before the current one).

Usage:  python3 tools/stall_report.py LOG [LOG ...] [--top N]
Exit status: 0; 2 no usable log.
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import sys

_LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] \[[A-Z]+\] (.*)$")
_PERF = re.compile(r"^\[PERF-DIAG\] (\w+) took (\d+)ms")
_STALL = re.compile(r"^\[STALL\] GUI thread recovered after ~(\d+)ms")
_LAUNCH = re.compile(r"^\[LAUNCH\] \{")


def _seconds(clock: str) -> int:
    h, m, s = (int(x) for x in clock.split(":"))
    return h * 3600 + m * 60 + s


def read(paths):
    """Yield (absolute seconds, message); the clock is lifted by a day each time it goes backwards (a log that runs past midnight)."""
    day, last = 0, None
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                m = _LINE.match(line.rstrip("\n"))
                if not m:
                    continue
                t = _seconds(m.group(1))
                if last is not None and t < last - 3600:
                    day += 86400
                last = t
                yield t + day, m.group(2)


def analyse(rows, near_s=2, quiet_s=90):
    stalls, perf, launches = [], [], []
    for t, msg in rows:
        if _LAUNCH.match(msg):
            launches.append(t)
        p = _PERF.match(msg)
        if p:
            perf.append((t, p.group(1), int(p.group(2))))
            continue
        s = _STALL.match(msg)
        if s:
            stalls.append((t, int(s.group(1))))
    ms = sorted(x for _, x in stalls)

    def pct(q):
        return ms[min(len(ms) - 1, int(len(ms) * q))] if ms else 0

    by_op = collections.defaultdict(lambda: [0, 0, 0])      # count, total ms, max ms
    for _, name, took in perf:
        c = by_op[name]
        c[0] += 1
        c[1] += took
        c[2] = max(c[2], took)
    beside = collections.Counter()
    for t, _ in stalls:
        for name in {n for (pt, n, _m) in perf if abs(pt - t) <= near_s}:
            beside[name] += 1

    def after_launch(t):
        return all(not (0 <= t - l < quiet_s) for l in launches)

    biggest = []
    for t, x in sorted((s for s in stalls if after_launch(s[0])), key=lambda s: -s[1]):
        ops = sorted(((n, m) for (pt, n, m) in perf if abs(pt - t) <= near_s and m >= 60), key=lambda o: -o[1])
        biggest.append((t, x, ops[:3]))
    return {"stalls": len(stalls), "total_ms": sum(ms), "p50": pct(0.5), "p90": pct(0.9), "p99": pct(0.99), "max": ms[-1] if ms else 0,
            "over_500": sum(1 for x in ms if x > 500), "over_1000": sum(1 for x in ms if x > 1000), "by_op": dict(by_op), "beside": beside,
            "biggest": biggest, "launches": len(launches)}


def _hms(t: int) -> str:
    return "%02d:%02d:%02d" % ((t // 3600) % 24, (t // 60) % 60, t % 60)


def render(r, top=8):
    out = [f"{r['stalls']} stalls, {r['total_ms'] / 1000:.0f} s in all (p50 {r['p50']} ms, p90 {r['p90']} ms, p99 {r['p99']} ms, max {r['max']} ms; "
           f"{r['over_500']} over 500 ms, {r['over_1000']} over 1 s); {r['launches']} app launch(es)"]
    out.append(f"\nbiggest stalls outside the first minute after a launch:")
    for t, x, ops in r["biggest"][:top]:
        out.append(f"  {_hms(t)}  {x:5d} ms  " + (", ".join(f"{n} {m}ms" for n, m in ops) or "no slow operation logged beside it"))
    out.append("\nslow operations by total time (count, total ms, max ms):")
    for name, (c, tot, mx) in sorted(r["by_op"].items(), key=lambda kv: -kv[1][1])[:top + 4]:
        out.append(f"  {name:36s} {c:6d} {tot:9d} {mx:7d}")
    out.append("\noperations most often logged within 2 s of a stall: " + ", ".join(f"{n} ({c})" for n, c in r["beside"].most_common(top)))
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+")
    parser.add_argument("--top", type=int, default=8)
    args = parser.parse_args(argv)
    paths = [p for p in args.logs if os.path.isfile(p)]
    if not paths:
        print("no log file found", file=sys.stderr)
        return 2
    print(render(analyse(read(paths)), args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
