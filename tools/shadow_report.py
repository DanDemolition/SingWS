#!/usr/bin/env python3
"""Post-show report: did each Rust shadow actually see its decisions, and did any disagree with Python?

READ-ONLY over the show logs. Replaces grepping five log tags by hand, and guards against the easy mistake of reading "no
mismatches" as success when a shadow simply never fired. Each shadow gets one verdict:

  CLEAN          every expected decision type was seen and nothing disagreed
  PARTIAL        nothing disagreed, but some expected decision types never happened this session (listed)
  NOT EXERCISED  the shadow is switched on (per settings.json, or it logged "enabled") but saw none of its decisions
  NOT ENABLED    its setting is off in settings.json (nothing was checked), or the app build predates the shadow
  UNAVAILABLE    enabled, but the Rust library could not be loaded (it silently did nothing)
  DISAGREED      at least one MISMATCH / disagreement=1 line (the app's Python answer was used each time)
  ERROR          the shadow logged an internal error (it never affects the show, but it was not checking)

Shadows and the log lines they write:
  SETTINGS-SHADOW   decision check=load|defaults|save           (startup + every settings save)
  QUEUE-SHADOW      decision branch=tombstone|terminal_stale|id_reuse|terminal_reuse   (server poll rows for removed requests)
  PLAN-SHADOW       decision op=window|crossfade kind=...        (background-music track changes)
  TRANSPORT-SHADOW  session N summary ... total_mismatches=M    (KaraFun and native song lifecycle)
  TRANSPORT-DRIVE   decision=karafun_end|media_end|watchdog ... disagreement=0|1   (Rust-vs-Python drive decisions)
"Decision" lines are written once per decision type per app launch; MISMATCH lines every time.

By default only the LAST app launch in the newest log is read (the show you just ran); --all reads every launch in the given logs.

Usage:  python3 tools/shadow_report.py [LOG ...] [--all] [--require-all]
Exit status: 0 no disagreement or error; 1 a shadow DISAGREED or hit an ERROR; 2 no usable log; 3 with --require-all, something was
not CLEAN (PARTIAL / NOT EXERCISED / NOT ENABLED / UNAVAILABLE).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

_LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] \[[A-Z]+\] \[([A-Za-z0-9_-]+)\] (.*)$")
_LAUNCH = re.compile(r"^\[(\d\d:\d\d:\d\d)\] \[[A-Z]+\] \[LAUNCH\] \{")

# name -> (required decision types, optional decision types that are legitimately rare)
SHADOWS = {
    "SETTINGS-SHADOW": ({"load", "defaults", "save"}, set()),
    "QUEUE-SHADOW": ({"tombstone", "terminal_stale"}, {"id_reuse", "terminal_reuse"}),
    "PLAN-SHADOW": ({"window", "crossfade"}, set()),
}
# the settings.json key that switches each shadow on (transport_shadow defaults to ON; the others default to off)
SETTING_KEYS = {
    "SETTINGS-SHADOW": ("settings_shadow", False), "QUEUE-SHADOW": ("queue_shadow", False), "PLAN-SHADOW": ("plan_shadow", False),
    "TRANSPORT-SHADOW": ("transport_shadow", True), "TRANSPORT-DRIVE": ("transport_drive", True),
}
DRIVE_REQUIRED = {"karafun_end", "media_end"}
DRIVE_OPTIONAL = {"watchdog"}

_HINTS = {
    "load": "start the app (settings are read at launch)",
    "defaults": "start the app on a profile that is missing at least one default",
    "save": "change any setting so the app saves settings.json",
    "tombstone": "delete a request in the host while the server still lists it",
    "terminal_stale": "let the server re-report a request you already completed or removed",
    "id_reuse": "rare: needs the server to hand a removed id to a different song after a reset",
    "terminal_reuse": "rare: same, for an id the server reported terminal earlier",
    "window": "let background music play and change tracks",
    "crossfade": "let background music play and change tracks",
    "karafun_end": "finish a KaraFun song (automatic end)",
    "media_end": "let a native CDG or MP4 song finish by itself",
    "watchdog": "rare: a KaraFun song whose end cannot be verified until its estimated duration passes",
}


@dataclass
class Report:
    name: str
    verdict: str = "NOT ENABLED"
    enabled: bool = False
    available: bool = True
    seen: set = field(default_factory=set)
    missing: set = field(default_factory=set)
    optional_missing: set = field(default_factory=set)
    disagreements: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    detail: str = ""


def read_lines(paths, last_launch_only):
    """Yield (path, time, tag, message) for the selected part of each log."""
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
        start = 0
        if last_launch_only:
            for i, line in enumerate(lines):
                if _LAUNCH.match(line):
                    start = i
        for line in lines[start:]:
            m = _LINE.match(line)
            if m:
                yield path, m.group(1), m.group(2), m.group(3)


def _kv(text):
    return dict(re.findall(r"(\w+)=([^\s]+)", text))


def analyse(rows, settings=None):
    rows = list(rows)
    reports = _analyse(rows)
    if settings is not None:
        for r in reports:
            key, default = SETTING_KEYS[r.name]
            on = bool(settings.get(key, default))
            if r.name == "TRANSPORT-DRIVE":  # the drive has one flag per decision
                on = on or bool(settings.get("transport_drive_media_end", False)) or bool(settings.get("transport_drive_watchdog", False))
            if r.verdict in ("NOT ENABLED", "NOT EXERCISED") and not r.seen:
                r.verdict = "NOT EXERCISED" if on else "NOT ENABLED"
                r.detail = (r.detail + "; " if r.detail else "") + (
                    f"setting {key} is on, but no decision reached it" if on else f"setting {key} is off in settings.json")
    return reports


def _analyse(rows):
    by_tag = defaultdict(list)
    for path, t, tag, msg in rows:
        by_tag[tag].append((t, msg))
    reports = []

    for name, (required, optional) in SHADOWS.items():
        r = Report(name)
        key = {"SETTINGS-SHADOW": "check", "QUEUE-SHADOW": "branch", "PLAN-SHADOW": "op"}[name]
        for t, msg in by_tag.get(name, []):
            kv = _kv(msg)
            if msg.startswith("enabled"):
                r.enabled = True
                r.available = kv.get("available") != "0"
            elif msg.startswith("decision"):
                r.enabled = True
                r.seen.add(kv.get(key, "?"))
            elif msg.startswith("MISMATCH"):
                r.enabled = True
                r.disagreements.append(f"{t} {msg[:160]}")
            elif "error" in msg or "failed" in msg:
                r.errors.append(f"{t} {msg[:160]}")
        r.missing = required - r.seen
        r.optional_missing = optional - r.seen
        r.verdict = _verdict(r, required)
        reports.append(r)

    # transport lifecycle shadow
    r = Report("TRANSPORT-SHADOW")
    sessions, mismatch_lines, total = 0, [], 0
    sources = Counter()
    for t, msg in by_tag.get("TRANSPORT-SHADOW", []):
        if msg.startswith("enabled"):
            r.enabled = True
            r.available = _kv(msg).get("available") != "0"
        elif re.match(r"session \d+ started", msg):
            r.enabled = True
            sessions += 1
            sources[_kv(msg).get("source", "?")] += 1
        elif "summary" in msg:
            total += int(_kv(msg).get("total_mismatches", "0"))
        elif msg.startswith("MISMATCH"):
            mismatch_lines.append(f"{t} {msg[:160]}")
        elif "error" in msg or "step failed" in msg:
            r.errors.append(f"{t} {msg[:160]}")
    r.disagreements = mismatch_lines
    r.seen = {f"session[{s}]x{n}" for s, n in sources.items()}
    r.missing = set() if sessions else {"session"}
    r.detail = f"{sessions} song session(s)" + (f": {dict(sources)}" if sources else "")
    r.verdict = _verdict(r, {"session"} if not sessions else set())
    reports.append(r)

    # drive decisions (Rust answers only when it agrees with Python; Python wins every disagreement)
    r = Report("TRANSPORT-DRIVE")
    counts, bad = Counter(), Counter()
    for t, msg in by_tag.get("TRANSPORT-DRIVE", []):
        kv = _kv(msg)
        # an internal drive error is written as "decision=<name> fallback=python error=..."; it is NOT a successful decision
        if "fallback=python" in msg or "error=" in msg:
            r.errors.append(f"{t} {msg[:160]}")
            continue
        name = kv.get("decision")
        if not name:
            continue
        counts[name] += 1
        if kv.get("disagreement") == "1":
            bad[name] += 1
            r.disagreements.append(f"{t} {msg[:160]}")
    r.enabled = bool(counts)
    r.seen = set(counts)
    r.missing = DRIVE_REQUIRED - r.seen
    r.optional_missing = DRIVE_OPTIONAL - r.seen
    r.detail = ", ".join(f"{k}: {counts[k]} decision(s), {bad[k]} disagreement(s)" for k in sorted(counts))
    r.verdict = _verdict(r, DRIVE_REQUIRED)
    if not r.enabled and not r.errors:
        r.verdict = "NOT EXERCISED"  # the drive flags live in settings; no decision line means no song reached those decisions
    reports.append(r)
    return reports


def _verdict(r, required):
    if r.disagreements:
        return "DISAGREED"
    if r.errors:
        return "ERROR"
    if not r.enabled:
        return "NOT ENABLED"
    if not r.available:
        return "UNAVAILABLE"
    if not r.seen:
        return "NOT EXERCISED"
    return "PARTIAL" if r.missing else "CLEAN"


def render(reports):
    out = []
    width = max(len(r.name) for r in reports)
    for r in reports:
        seen = ", ".join(sorted(r.seen)) or "-"
        out.append(f"{r.name:<{width}}  {r.verdict:<13}  saw: {seen}")
        if r.detail:
            out.append(f"{'':<{width}}  {'':<13}  {r.detail}")
        for item in sorted(r.missing):
            out.append(f"{'':<{width}}  {'':<13}  not seen: {item}" + (f"  ({_HINTS[item]})" if item in _HINTS else ""))
        for item in sorted(r.optional_missing):
            out.append(f"{'':<{width}}  {'':<13}  not seen (rare, optional): {item}" + (f"  ({_HINTS[item]})" if item in _HINTS else ""))
        for line in r.disagreements[:5]:
            out.append(f"{'':<{width}}  {'':<13}  DISAGREEMENT {line}")
        if len(r.disagreements) > 5:
            out.append(f"{'':<{width}}  {'':<13}  ... and {len(r.disagreements) - 5} more")
        for line in r.errors[:3]:
            out.append(f"{'':<{width}}  {'':<13}  ERROR {line}")
    return "\n".join(out)


def exit_status(reports, require_all):
    if any(r.verdict in ("DISAGREED", "ERROR") for r in reports):
        return 1
    if require_all and any(r.verdict != "CLEAN" for r in reports):
        return 3
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="*", help="log files (default: the newest ~/SingWS/logs/singws_*.log)")
    parser.add_argument("--all", action="store_true", help="read every app launch in the logs, not only the last one")
    parser.add_argument("--require-all", action="store_true", help="exit 3 unless every shadow is CLEAN")
    parser.add_argument("--settings", default=os.path.expanduser("~/SingWS/settings.json"),
                        help="settings.json to read (read-only) so a silent shadow is NOT ENABLED (off) or NOT EXERCISED (on)")
    args = parser.parse_args(argv)
    paths = args.logs or sorted(glob.glob(os.path.expanduser("~/SingWS/logs/singws_*.log")))[-1:]
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        print("no log file found", file=sys.stderr)
        return 2
    settings = None
    try:
        with open(args.settings, encoding="utf-8") as handle:
            loaded = json.load(handle)
        settings = loaded if isinstance(loaded, dict) else None
    except (OSError, ValueError):
        settings = None
    reports = analyse(read_lines(paths, last_launch_only=not args.all), settings)
    print(f"logs: {', '.join(os.path.basename(p) for p in paths)}  ({'all launches' if args.all else 'last app launch only'})")
    print(render(reports))
    worst = [r for r in reports if r.verdict in ("DISAGREED", "ERROR")]
    if worst:
        print(f"RESULT: {len(worst)} shadow(s) need attention: {', '.join(r.name for r in worst)}")
    elif all(r.verdict == "CLEAN" for r in reports):
        print("RESULT: every shadow saw all its expected decisions and none disagreed")
    else:
        print("RESULT: nothing disagreed, but not every decision type was exercised (see 'not seen')")
    return exit_status(reports, args.require_all)


if __name__ == "__main__":
    sys.exit(main())
