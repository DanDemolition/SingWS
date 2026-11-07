#!/usr/bin/env python3
"""Parity: the duration WATCHDOG of the real `_start_karafun_completion_monitor` (0.2.18.1.py) vs the Rust `singws-karafun` watchdog functions.

The real monitor is replayed (karafun_monitor_parity.Replay: scripted probe answers, fake clock). The Timer objects it creates are recorded; after the run the
tool puts the clock at a chosen time, re-activates the session and fires the real timer callback, then calls the real re-arm hook. Compared: the delay of the
first timer, what the callback does (schedule another look after N seconds, or log and warn the operator), and the delay of the re-armed timer.

  python3 tools/karafun_watchdog_parity.py [--seeds N] [--binary PATH]
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import karafun_monitor_parity as mp  # noqa: E402


def python_watchdog(loaded, c, offset, seek):
    r = mp.Replay(loaded, c)
    r.run()
    out = {"initial_delay": r.timers[0][0] if r.timers else None, "check": None, "rearm_delay": None}
    final_now = r.now
    if not r.timers:
        return out, final_now
    fire_now = final_now + offset
    r.host._active_external_karafun = r.active
    r.now = fire_now
    r.entry["karafun_seek_offset_s"] = seek
    timers_before, messages_before, status_before = len(r.timers), len(r.messages), len(r.status_calls)
    r.timers[0][1]()
    new_timers = r.timers[timers_before:]
    if new_timers:
        out["check"] = {"extend": new_timers[0][0]}
    elif len(r.status_calls) > status_before:
        out["check"] = {"warn": r.messages[messages_before], "status": r.status_calls[status_before]}
    rearm = getattr(r.host, "_karafun_duration_rearm", None)
    if rearm:
        n = len(r.timers)
        rearm[1]()
        out["rearm_delay"] = r.timers[n][0]
    return out, final_now


def scenarios(seeds):
    out = []
    for s in range(seeds):
        r = random.Random(10_000 + s)
        c = mp.scenario(s % 3000)
        c = dict(c, duration=r.choice([c["duration"], 60, 100, 263]), duration_estimated=False if r.random() < 0.8 else c["duration_estimated"])
        out.append((c, r.choice([0.0, 1.0, 5.0, 19.5, 20.0, 20.5, 60.0, 200.0, 400.0]), r.choice([0.0, 0.0, 30.0, -60.0, 150.0, 12.5])))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3000)
    ap.add_argument("--binary", default=os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun"))
    args = ap.parse_args(argv)
    loaded = mp.load()
    cases = scenarios(args.seeds)
    wants, requests = [], []
    for c, offset, seek in cases:
        want, final_now = python_watchdog(loaded, c, offset, seek)
        wants.append(want)
        req = mp.rust_request(c)
        req.update(op="watchdog", fire_now=final_now + offset, fire_seek_offset=seek)
        requests.append(req)
    got = mp.run_rust(args.binary, requests)
    diffs, counts = [], {"extend confirmed": 0, "extend playing": 0, "warn": 0, "no watchdog": 0}
    for (c, offset, seek), want, g in zip(cases, wants, got):
        if "decline" in g:
            continue
        mine = {"initial_delay": g["initial_delay"], "check": g["check"], "rearm_delay": g["rearm_delay"]}
        if want["initial_delay"] is None:
            counts["no watchdog"] += 1
            mine = {"initial_delay": mine["initial_delay"], "check": None, "rearm_delay": None}
        else:
            chk = want["check"] or {}
            counts["warn" if "warn" in chk else "extend confirmed" if chk.get("extend", 0) not in (15.0,) else "extend playing"] += 1
        if mine != want:
            diffs.append((c, offset, seek, mine, want))
    print(f"scenarios={len(cases)}; " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for c, offset, seek, mine, want in diffs[:4]:
        print(f" - fire +{offset}s seek {seek}: {json.dumps(c['events'])[:160]}\n   rust={mine}\n   py  ={want}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
