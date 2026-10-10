#!/usr/bin/env python3
"""Summarise the Stage 3 shadow log lines and check the plan's acceptance rule.

    python3 tools/transport_shadow_report.py FILE [FILE ...]        # e.g. the "Send Last Show's Logs" exports
    python3 tools/transport_shadow_report.py                        # no arguments: ~/SingWS/logs/*.log*

Each FILE is counted as ONE show (pass one last-show export per night; a plain daily log is one batch). Per file it prints how
many songs the Rust lifecycle machine watched, how many times it DISAGREED with the app (``MISMATCH`` lines, listed with
their session) and which "ignore" rules fired. The acceptance rule from RUST_MIGRATION_PLAN.md Stage 3: at least 3 shows
with zero unexplained disagreements before the machine may drive anything. Read-only; prints no settings or secrets.
"""
from __future__ import annotations

import collections
import glob
import os
import re
import sys

SESSION = re.compile(r"\[TRANSPORT-SHADOW\] session (\d+) summary events=(\d+) total_mismatches=(\d+)")
CMD = re.compile(r"\[TRANSPORT-SHADOW\] cmd session=(\d+) (\{.*\})")
IGNORE = re.compile(r'"Ignore": "([^"]+)"')
MISMATCH = re.compile(r"\[TRANSPORT-SHADOW\] MISMATCH (.*)")
REQUIRED_SHOWS = 3


def summarise(path: str) -> dict:
    sessions = set()
    mismatches = []
    ignores = collections.Counter()
    commands = collections.Counter()
    enabled = False
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if "[TRANSPORT-SHADOW]" not in line:
                    continue
                if "enabled available=" in line:
                    enabled = "available=1" in line
                m = SESSION.search(line)
                if m:
                    sessions.add(int(m.group(1)))
                m = MISMATCH.search(line)
                if m:
                    mismatches.append(m.group(1).strip())
                m = CMD.search(line)
                if m:
                    body = m.group(2)
                    ig = IGNORE.search(body)
                    if ig:
                        ignores[ig.group(1)] += 1
                    else:
                        name = re.match(r'\{"(\w+)"', body)
                        if name:
                            commands[name.group(1)] += 1
    except OSError as exc:
        return {"path": path, "error": str(exc)}
    return {"path": path, "enabled": enabled, "sessions": len(sessions), "mismatches": mismatches,
            "ignores": ignores, "commands": commands}


def verdict(results: list[dict]) -> tuple[int, int, bool]:
    watched = [r for r in results if r.get("sessions", 0) > 0]
    clean = [r for r in watched if not r["mismatches"]]
    return len(watched), len(clean), len(clean) >= REQUIRED_SHOWS and len(clean) == len(watched)


def main(argv: list[str]) -> int:
    files = argv or sorted(glob.glob(os.path.expanduser("~/SingWS/logs/*.log*")))
    results = [summarise(f) for f in files]
    for r in results:
        name = os.path.basename(r["path"])
        if "error" in r:
            print(f"{name}: cannot read ({r['error']})")
            continue
        if r["sessions"] == 0:
            print(f"{name}: no shadow sessions" + ("" if r["enabled"] else " (shadow off or not available)"))
            continue
        print(f"{name}: {r['sessions']} songs watched, {len(r['mismatches'])} disagreements")
        for text in r["mismatches"][:10]:
            print(f"    MISMATCH {text}")
        if r["ignores"]:
            print("    rules that fired: " + ", ".join(f"{k} x{v}" for k, v in r["ignores"].most_common(6)))
    watched, clean, accepted = verdict(results)
    print(f"\nshows watched: {watched}, shows with zero disagreements: {clean}")
    if accepted:
        print(f"ACCEPTANCE MET: {clean} clean shows (needs {REQUIRED_SHOWS}) and none with a disagreement.")
    else:
        print(f"Acceptance NOT met yet: needs at least {REQUIRED_SHOWS} watched shows, all with zero unexplained disagreements.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
