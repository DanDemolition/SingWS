#!/usr/bin/env python3
"""Gate ledger for the Rust migration (operator's policy, 2026-10-26): ONE clean show per decision, rehearsals count as shows, gates run side by side with the build.

    python3 tools/gate_ledger.py add LOG [LOG ...] --label "rehearsal 2026-10-28 intel"    record what the logs prove (last app launch in each log; --all for every launch)
    python3 tools/gate_ledger.py add singws-show-20261028-2145.zip --label "rehearsal intel"   the zip made by after_show.sh (every launch in its logs, its reduced settings.json)
    python3 tools/gate_ledger.py status                                                    one line per decision: how many clean shows, READY or what is missing
    python3 tools/gate_ledger.py status --json

A decision is CLEAN in a session when its shadow row saw it and that row had no disagreement and no error (a row with a disagreement or error clears none of its
decisions; the TRANSPORT-DRIVE row is judged per decision name). The ledger is docs/gates/ledger.json (committed, so the evidence survives), one entry per clean session with
its label, date and log names. READY = at least REQUIRED_CLEAN (1) clean sessions. READY means the decision may be considered for the next step (drive on by default / delete the
Python rule); it never changes anything by itself, and a later disagreement is recorded too and shown as REGRESSED until another clean session follows it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shadow_report as sr  # noqa: E402

REQUIRED_CLEAN = 1
LEDGER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "gates", "ledger.json")

# every decision the gates know about (a decision not listed here but seen in a log is added to the ledger as well)
KNOWN = (
    [f"SETTINGS-SHADOW:{k}" for k in ("load", "defaults", "save")]
    + [f"QUEUE-SHADOW:{k}" for k in ("tombstone", "terminal_stale")]
    + [f"PLAN-SHADOW:{k}" for k in ("window", "crossfade")]
    + [f"STORE-SHADOW:{k}" for k in ("queue.json",)]
    + [f"WIRE-SHADOW:{k}" for k in ("get", "form")]
    + ["TRANSPORT-SHADOW:session"]
    + [f"TRANSPORT-DRIVE:{k}" for k in ("karafun_end", "media_end", "watchdog")]
    + [f"START-SHADOW:{k}" for k in ("route:mp4", "route:mp3", "route:cdg_mp3_dual", "route:mp3g_zip", "route:external_karafun", "engine", "pick", "display", "rotation",
                                                 "transport_args", "zip_args", "confirmed", "performance:confirmed", "performance:media_end", "performance:stop")]
    + ["SHELL-COMMANDS:dry_run_command"]
)


def _group_start(item):
    """START-SHADOW decision lines name a route; fold the non-route kinds into their decision."""
    if item.startswith("engine"):
        return "engine"
    if item in ("pick", "pick_none"):
        return "pick"
    if item in ("display", "rotation", "confirmed", "zip_args"):
        return item
    if item.startswith("transport_"):
        return "transport_args"
    if item.startswith("performance_"):
        for event in ("media_end", "confirmed", "stop"):
            if item.startswith(f"performance_{event}_"):
                return f"performance:{event}"
        return "performance:other"
    return f"route:{item}"


def decisions_proven(reports):
    """{decision: True when clean in these logs}, only for decisions that were seen. A decision that was seen in a row with a disagreement or error maps to False."""
    out = {}
    for r in reports:
        if r.name == "SHELL-COMMANDS":
            if r.seen and not r.errors:
                out["SHELL-COMMANDS:dry_run_command"] = True
            elif r.errors:
                out["SHELL-COMMANDS:dry_run_command"] = False
            continue
        row_bad = bool(r.disagreements or r.errors)
        if r.name == "TRANSPORT-DRIVE":
            bad = set()
            for line in list(r.disagreements) + list(r.errors):
                m = re.search(r"decision=([a-z_]+)", line)
                bad.add(m.group(1) if m else "*")
            for name in r.seen:
                out[f"TRANSPORT-DRIVE:{name}"] = not (name in bad or "*" in bad)
            continue
        if r.name == "TRANSPORT-SHADOW":
            if r.seen:
                out["TRANSPORT-SHADOW:session"] = not row_bad
            continue
        for item in r.seen:
            key = _group_start(item) if r.name == "START-SHADOW" else item
            decision = f"{r.name}:{key}"
            out[decision] = out.get(decision, True) and not row_bad
    return out


def expand_inputs(paths, workdir):
    """Log files from the arguments; an `after_show.sh` zip contributes the logs inside it and its reduced settings.json. Returns (logs, settings_path_or_None)."""
    logs, settings = [], None
    for path in paths:
        if path.lower().endswith(".zip") and os.path.isfile(path):
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    name = os.path.basename(info.filename)
                    if not name or info.is_dir():
                        continue
                    if re.fullmatch(r"singws_[0-9-]+\.log(\.[A-Za-z0-9]+)?", name) or name == "settings.json":
                        target = os.path.join(workdir, f"{len(logs)}_{name}" if name != "settings.json" else "settings.json")
                        with archive.open(info) as src, open(target, "wb") as dst:
                            dst.write(src.read())
                        if name == "settings.json":
                            settings = target
                        else:
                            logs.append(target)
        elif os.path.isfile(path):
            logs.append(path)
    return logs, settings


def load(path=LEDGER):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("decisions"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"policy": "one clean show or rehearsal per decision", "decisions": {}}


def save(data, path=LEDGER):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def record(data, proven, label, logs, when=None):
    """Add one session to the ledger. Returns (newly_clean, newly_bad) decision lists."""
    when = when or time.strftime("%Y-%m-%d")
    entry = {"label": label, "date": when, "logs": sorted(re.sub(r"^\d+_", "", os.path.basename(p)) for p in logs)}
    good, bad = [], []
    for decision, clean in sorted(proven.items()):
        row = data["decisions"].setdefault(decision, {"clean": [], "bad": []})
        (row["clean"] if clean else row["bad"]).append(entry)
        (good if clean else bad).append(decision)
    return good, bad


def status(data):
    rows = []
    names = list(KNOWN) + sorted(set(data["decisions"]) - set(KNOWN))
    for name in names:
        row = data["decisions"].get(name, {"clean": [], "bad": []})
        clean, bad = row.get("clean", []), row.get("bad", [])
        last_clean = max((e["date"] + "|" + e["label"] for e in clean), default="")
        last_bad = max((e["date"] + "|" + e["label"] for e in bad), default="")
        if bad and last_bad >= last_clean:
            verdict = "REGRESSED"
        elif len(clean) >= REQUIRED_CLEAN:
            verdict = "READY"
        else:
            verdict = "NEEDS A CLEAN SHOW"
        rows.append({"decision": name, "clean": len(clean), "bad": len(bad), "verdict": verdict, "last_clean": clean[-1]["label"] if clean else ""})
    return rows


def render(rows):
    width = max(len(r["decision"]) for r in rows)
    out = []
    for r in rows:
        extra = f"  last clean: {r['last_clean']}" if r["last_clean"] else ""
        out.append(f"{r['decision']:<{width}}  {r['verdict']:<18}  clean={r['clean']} bad={r['bad']}{extra}")
    ready = sum(1 for r in rows if r["verdict"] == "READY")
    out.append(f"RESULT: {ready} of {len(rows)} decisions have {REQUIRED_CLEAN} clean show(s)" + (f"; {sum(1 for r in rows if r['verdict'] == 'REGRESSED')} REGRESSED" if any(r["verdict"] == "REGRESSED" for r in rows) else ""))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="record a show or rehearsal from its logs")
    add.add_argument("logs", nargs="+")
    add.add_argument("--label", required=True)
    add.add_argument("--all", action="store_true", help="read every app launch in the logs, not only the last one")
    add.add_argument("--settings", default=os.path.expanduser("~/SingWS/settings.json"))
    add.add_argument("--ledger", default=LEDGER)
    add.add_argument("--date", default=None)
    st = sub.add_parser("status", help="show the ledger")
    st.add_argument("--json", action="store_true")
    st.add_argument("--ledger", default=LEDGER)
    args = parser.parse_args(argv)
    if args.cmd == "status":
        rows = status(load(args.ledger))
        print(json.dumps(rows, indent=2) if args.json else render(rows))
        return 1 if any(r["verdict"] == "REGRESSED" for r in rows) else 0
    work = tempfile.TemporaryDirectory()
    paths, zip_settings = expand_inputs(args.logs, work.name)
    if not paths:
        print("no log file found", file=sys.stderr)
        return 2
    settings = None
    try:
        with open(zip_settings or args.settings, encoding="utf-8") as handle:
            loaded = json.load(handle)
        settings = loaded if isinstance(loaded, dict) else None
    except (OSError, ValueError):
        settings = None
    every_launch = args.all or bool(zip_settings)   # an after_show zip holds the whole evening, usually more than one launch
    reports = sr.analyse(sr.read_lines(paths, last_launch_only=not every_launch), settings)
    proven = decisions_proven(reports)
    data = load(args.ledger)
    good, bad = record(data, proven, args.label, paths, args.date)
    save(data, args.ledger)
    print(f"recorded {args.label!r}: {len(good)} clean decision(s), {len(bad)} with a disagreement or error")
    for d in good:
        print(f"  clean  {d}")
    for d in bad:
        print(f"  BAD    {d}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
