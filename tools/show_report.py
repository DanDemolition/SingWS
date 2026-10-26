#!/usr/bin/env python3
"""One command for the morning after a show:  python3 tools/show_report.py ~/Downloads/singws-show-*.zip   (or log files, or a folder of logs)

Does what used to be done by hand: unzips, joins each day's PRE-MIDNIGHT rotated log (singws_YYYY-MM-DD.log.YYYY-MM-DD) in front of the day's log so the whole
show is one stream, picks the show (the launch group with the most songs), and prints
  * the Rust shadow report for that launch (tools/shadow_report.py, unchanged),
  * a show summary (build, hours, songs by kind),
  * the drive decisions: how many Rust answers agreed with Python,
  * every KaraFun-end disagreement with the lines that explain it,
  * the memory trail (RSS per song, biggest steps) and the native-memory categories that grew ([MEMORY-NATIVE], builds from 1.0.1.7).
READ-ONLY. Log lines never contain hosts, keys or values (only endpoint file names), so the output is safe to paste.

Options: --settings FILE (a settings.json, or the reduced one inside the show zip; default: the one in the zip, else ~/SingWS/settings.json)
         --group NAME to choose a launch group yourself   --all to read every launch   --require-all to exit 3 unless every shadow is CLEAN
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from collections import Counter, OrderedDict
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shadow_report as sr  # noqa: E402

_NAME = re.compile(r"^(singws_\d{4}-\d{2}-\d{2}\.log)(?:\.(\d{4}-\d{2}-\d{2}))?$")
_LAUNCH_JSON = re.compile(r'^\[(\d\d:\d\d:\d\d)\] \[[A-Z]+\] \[LAUNCH\] (\{.*\})\s*$')
_MEM_SONG = re.compile(r"^\[(\d\d:\d\d:\d\d)\] \[[A-Z]+\] \[MEMORY-SONG\] phase=(\w+) n=(\d+) rss_mb=(\d+) d_rss=([+-]\d+) .*?singer='([^']*)'")
_MEM_NATIVE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] \[[A-Z]+\] \[MEMORY-NATIVE\] label=(\S+) footprint_mb=(\d+) top=(.*)$")
_TOP_ITEM = re.compile(r"^(.*?) (\d+)(?: \(([+-]\d+)\))?$")
_FINISH = re.compile(r"\[TRANSPORT-SHADOW\] finish session=(\d+) source=(\S+) action=(\w+)")
_SESSION_EVENT = re.compile(r"\[TRANSPORT-SHADOW\] event session=(\d+) source=(\S+)")
_DRIVE = re.compile(r"\[TRANSPORT-DRIVE\] decision=(\w+) python=(\d) rust=(\d) selected=(\d) owner=(\S+) disagreement=(\d)")


class Group:
    def __init__(self, base):
        self.base = base
        self.files = []  # (rotation date or "~" for the live file, path)

    def ordered_paths(self):
        return [p for _, p in sorted(self.files, key=lambda t: (t[0] == "~", t[0]))]

    def text(self):
        parts = []
        for p in self.ordered_paths():
            with open(p, encoding="utf-8", errors="replace") as fh:
                t = fh.read()
            parts.append(t if t.endswith("\n") or not t else t + "\n")
        return "".join(parts)


def collect(inputs, workdir):
    """Expand zips and folders; return {base log name: Group}."""
    paths = []
    for item in inputs:
        p = Path(item).expanduser()
        if p.is_dir():
            paths += [q for q in p.rglob("*") if q.is_file()]
        elif p.suffix.lower() == ".zip":
            dest = Path(workdir) / (p.stem + "_unzipped")
            with zipfile.ZipFile(p) as z:
                z.extractall(dest)
            paths += [q for q in dest.rglob("*") if q.is_file()]
        elif p.is_file():
            paths.append(p)
    groups = OrderedDict()
    for p in sorted(paths):
        m = _NAME.match(p.name)  # also drops the "._name" resource-fork files macOS adds to a zip
        if not m:
            continue
        g = groups.setdefault(m.group(1), Group(m.group(1)))
        g.files.append((m.group(2) or "~", str(p)))
    return groups


def last_launch(lines):
    start = 0
    for i, line in enumerate(lines):
        if sr._LAUNCH.match(line):
            start = i
    return lines[start:]


def launch_info(lines):
    for line in lines:
        m = _LAUNCH_JSON.match(line)
        if m:
            try:
                d = json.loads(m.group(2))
                return m.group(1), str(d.get("version") or d.get("build_id") or "?")
            except ValueError:
                return m.group(1), "?"
    return None, "?"


def song_kinds(lines):
    """Songs by kind from the transport shadow: one count per session, using the source of its first event."""
    src = {}
    for line in lines:
        m = _SESSION_EVENT.search(line)
        if m:
            src.setdefault(int(m.group(1)), m.group(2))
    return Counter(src.values())


def drive_table(lines):
    t = {}
    for line in lines:
        m = _DRIVE.search(line)
        if m:
            d = t.setdefault(m.group(1), [0, 0])
            d[0] += 1
            d[1] += int(m.group(6))
    return t


def karafun_details(lines):
    """For every karafun_end disagreement: what Python saw (its monitor line and completion reason) and what the song looked like when it started."""
    out = []
    for i, line in enumerate(lines):
        m = _DRIVE.search(line)
        if not (m and m.group(1) == "karafun_end" and m.group(6) == "1"):
            continue
        t = line[1:9]
        started = corrected = reason = monitor = ""
        for back in lines[max(0, i - 3000):i][::-1]:
            if not started and "completion monitor started" in back:
                started = re.sub(r"^\[[\d:]+\] \[[A-Z]+\] \[KARAFUN\] ", "", back)[:150]
            if not corrected and "duration corrected" in back:
                corrected = re.sub(r"^\[[\d:]+\] \[[A-Z]+\] \[KARAFUN\] ", "", back)[:150]
            if not monitor and "[KARAFUN] monitor state" in back:
                monitor = re.sub(r"^\[[\d:]+\] \[[A-Z]+\] \[KARAFUN\] ", "", back)[:150]
            if started and monitor and corrected:
                break
        for fwd in lines[i:i + 8]:
            if "completion event received" in fwd:
                reason = re.sub(r"^\[[\d:]+\] \[[A-Z]+\] \[KARAFUN\] ", "", fwd)[:150]
                break
        out.append((t, monitor, started, corrected, reason))
    return out


def memory_rows(lines):
    rows = []
    for line in lines:
        m = _MEM_SONG.match(line)
        if m and m.group(2) == "end":
            rows.append((m.group(1), int(m.group(4)), int(m.group(5)), m.group(6)))  # (time, rss_mb, change since the last line, singer)
    return rows


def native_series(lines):
    """[(time, label, total_mb, {category: mb})] from the [MEMORY-NATIVE] probe."""
    out = []
    for line in lines:
        m = _MEM_NATIVE.match(line)
        if not m:
            continue
        cats = {}
        for part in m.group(4).split(" | "):
            mm = _TOP_ITEM.match(part.strip())
            if mm:
                cats[mm.group(1)] = int(mm.group(2))
        out.append((m.group(1), m.group(2), int(m.group(3)), cats))
    return out


def render_memory(lines):
    out = []
    rows = memory_rows(lines)
    if rows:
        first, last = rows[0], rows[-1]
        peak = max(r[1] for r in rows)
        out.append(f"MEMORY (RSS at each song end, {len(rows)} songs): first {first[1]} MB at {first[0]}, last {last[1]} MB at {last[0]}, peak {peak} MB, growth {last[1] - first[1]:+d} MB")
        half = len(rows) // 2
        if half >= 2:
            out.append(f"  first half {rows[half - 1][1] - rows[0][1]:+d} MB over {half} songs, second half {rows[-1][1] - rows[half][1]:+d} MB over {len(rows) - half - 1} songs"
                       f"   (a leak keeps climbing; a cache flattens)")
        big = sorted(rows, key=lambda r: r[2], reverse=True)[:5]
        out.append("  biggest steps: " + ", ".join(f"{r[2]:+d} MB at {r[0]} ({r[3] or 'no singer'})" for r in big if r[2] > 20))
    else:
        out.append("MEMORY: no [MEMORY-SONG] lines in this launch")
    series = native_series(lines)
    if len(series) >= 2:
        (t0, _, tot0, c0), (t1, _, tot1, c1) = series[0], series[-1]
        out.append(f"NATIVE MEMORY (footprint categories, {len(series)} probes): {tot0} MB at {t0} -> {tot1} MB at {t1}")
        growth = sorted(((c1.get(k, 0) - c0.get(k, 0), k, c0.get(k, 0), c1.get(k, 0)) for k in set(c0) | set(c1)), reverse=True)
        out.append("  growth by category: " + " | ".join(f"{k} {a}->{b} ({d:+d})" for d, k, a, b in growth[:6] if d != 0))
        jumps = sorted(((series[i][2] - series[i - 1][2], series[i][0], series[i][1]) for i in range(1, len(series))), reverse=True)[:3]
        out.append("  biggest jumps: " + ", ".join(f"{d:+d} MB at {t} ({lab})" for d, t, lab in jumps if d > 20))
    elif series:
        out.append(f"NATIVE MEMORY: only one probe ({series[0][2]} MB); run a longer session")
    else:
        out.append("NATIVE MEMORY: no [MEMORY-NATIVE] lines (the build predates 1.0.1.7, or the probe is off)")
    return "\n".join(out)


def analyse_group(group, settings, all_launches):
    text = group.text()
    all_lines = text.splitlines()
    lines = all_lines if all_launches else last_launch(all_lines)
    return all_lines, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="a show zip from after_show.sh, log files, or a folder")
    ap.add_argument("--settings", default=None)
    ap.add_argument("--group", default=None, help="analyse this launch group (e.g. singws_2026-10-25.log) instead of the one with the most songs")
    ap.add_argument("--all", action="store_true", help="read every app launch in the group, not only the last")
    ap.add_argument("--require-all", action="store_true")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="show_report_") as work:
        groups = collect(args.inputs, work)
        if not groups:
            print("no singws_*.log files found in the input", file=sys.stderr)
            return 2
        settings_path = args.settings
        if settings_path is None:
            for item in args.inputs:
                for cand in [Path(item)] if Path(item).is_dir() else []:
                    for s in cand.rglob("settings.json"):
                        settings_path = str(s)
            if settings_path is None:
                for z in Path(work).rglob("settings.json"):
                    settings_path = str(z)
            if settings_path is None:
                settings_path = os.path.expanduser("~/SingWS/settings.json")
        settings = None
        try:
            with open(settings_path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            settings = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            pass

        info = []
        for name, g in groups.items():
            all_lines = g.text().splitlines()
            ll = last_launch(all_lines)
            songs = sum(song_kinds(ll).values())
            t, ver = launch_info(ll)
            info.append((name, g, songs, t, ver, len(g.files)))
        print("LOG GROUPS (each is one day's log with its pre-midnight part):")
        for name, g, songs, t, ver, nfiles in info:
            print(f"  {name}: {nfiles} file(s), last launch {ver} at {t or '?'}, {songs} song(s)")
        chosen = next((x for x in info if x[0] == args.group), None) if args.group else max(info, key=lambda x: x[2])
        if chosen is None:
            print(f"no group named {args.group}", file=sys.stderr)
            return 2
        name, g, songs, t, ver, _ = chosen
        print(f"\nANALYSING {name}  (build {ver}, launched {t or '?'}, {songs} songs){'  [all launches]' if args.all else ''}\n")
        joined = Path(work) / name
        joined.write_text(g.text(), encoding="utf-8")
        reports = sr.analyse(sr.read_lines([str(joined)], last_launch_only=not args.all), settings)
        print(sr.render(reports))
        ll = g.text().splitlines()
        ll = ll if args.all else last_launch(ll)
        kinds = song_kinds(ll)
        times = [m.group(1) for m in (sr._LINE.match(x) for x in ll) if m]
        print("\nSHOW: " + (f"{times[0]} to {times[-1]}, " if times else "") + f"{sum(kinds.values())} songs (" + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) + ")")
        dt = drive_table(ll)
        for k, (n, bad) in sorted(dt.items()):
            print(f"DRIVE {k}: {n} decisions, {bad} disagreement(s)" + ("  <- Rust agreed every time" if bad == 0 and n else ""))
        for t2, monitor, started, corrected, reason in karafun_details(ll):
            print(f"\nKARAFUN-END DISAGREEMENT at {t2}\n  python decided: {reason or '(no completion line)'}\n  monitor:        {monitor}\n  song start:     {started}\n  duration:       {corrected or '(no duration correction logged)'}")
        print("\n" + render_memory(ll))
        worst = [r for r in reports if r.verdict in ("DISAGREED", "ERROR")]
        print("\nRESULT: " + (f"{len(worst)} shadow(s) need attention: {', '.join(r.name for r in worst)}" if worst else
                              "every shadow saw all its expected decisions and none disagreed" if all(r.verdict == "CLEAN" for r in sr.counted(reports))
                              else "nothing disagreed, but not every decision type was exercised (see 'not seen')"))
        return sr.exit_status(reports, args.require_all)


if __name__ == "__main__":
    sys.exit(main())
