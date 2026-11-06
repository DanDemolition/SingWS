#!/usr/bin/env python3
"""The morning-after checklist: one PASS / FAIL / NOT EXERCISED / MANUAL line per thing a show is supposed to prove.

  python3 tools/show_checklist.py ~/Downloads/singws-show-*.zip      (or log files, or a folder of logs; same inputs as tools/show_report.py)

It reads the last app launch of the show (the launch group with the most songs, as show_report does) and answers, from the log alone:
  PASS            the thing happened and looked right
  FAIL            the thing happened and was wrong, or an error line proves a fault
  NOT EXERCISED   the show never reached that code path (a clean result is not evidence, it is silence); does not fail the run
  MANUAL          the log cannot tell (listen / look / seek by hand), the line says exactly what to do
After the checklist it lists the questions only the operator can answer, then prints the ledger command for a clean show.

READ-ONLY. Log lines never contain hosts, keys or values, so the output is safe to paste.
Options: --group NAME, --all (every launch), --strict (exit 3 if anything is NOT EXERCISED), --json.
Exit status: 0 unless some line is FAIL (1), or --strict and something is NOT EXERCISED (3); 2 when no logs were found.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shadow_report as sr  # noqa: E402
import show_report as rep  # noqa: E402

PASS, FAIL, NOTEX, MANUAL = "PASS", "FAIL", "NOT EXERCISED", "MANUAL"

_PRESTART = re.compile(r"\[CROSSFADE\] BG pre-start fired \(([0-9.]+)s remaining\)")
_FLOOR = re.compile(r"\[BG-ENGINE\] fade-in start floor (-?[0-9.]+) dB")
_MEM = rep._MEM_SONG


def _count(lines, needle):
    return sum(1 for x in lines if needle in x)


def _level_lines(lines, level):
    return [x for x in lines if re.match(r"^\[\d\d:\d\d:\d\d\] \[" + level + r"\]", x)]


def check_clean_run(lines):
    tb = _count(lines, "Traceback (most recent call last)")
    fr = _count(lines, "FREEZE_DETECTED")
    errs = len(_level_lines(lines, "ERROR"))
    if tb or fr or errs:
        return FAIL, f"{tb} traceback(s), {fr} FREEZE_DETECTED, {errs} ERROR line(s) (read them: grep -n 'Traceback\\|FREEZE_DETECTED\\|\\[ERROR\\]')"
    return PASS, "no traceback, no FREEZE_DETECTED, no ERROR line"


def check_crash_reports(lines):
    return MANUAL, "native crashes are not in this log: ls -lt ~/Library/Logs/DiagnosticReports | grep -i singws  (anything dated after the launch is a crash)"


def check_audio_end_rule(lines):
    runs = _count(lines, "[END-AUDIO] audio runs to the end of the file")
    none = _count(lines, "[END-AUDIO] no trailing silence found")
    last = _count(lines, "[END-AUDIO] last audible audio at")
    supp = _count(lines, "[END-AUDIO] trim suppressed")
    failed = _count(lines, "[END-AUDIO] scan failed")
    unk = _count(lines, "[END-AUDIO] duration unknown")
    facts = f"{last} scanned end(s), {runs} 'runs to the end', {none} 'no trailing silence', {supp} trim suppressed, {failed} scan failed, {unk} duration unknown"
    if failed or unk:
        return FAIL, facts + "  (a scan that fails leaves that song on the fallback pre-start; read the lines)"
    if runs:
        return PASS, facts + "  (the audio-end rule from aac7e52 proved a file end at least once)"
    if none:
        return MANUAL, facts + "  (songs without trailing silence were seen but none PROVED to run to the end: either their last half second was quiet, or the build predates aac7e52)"
    return NOTEX, facts + "  (no song ran to its last sample, or the build predates aac7e52)"


def check_prestart(lines):
    vals = [float(m.group(1)) for x in lines for m in [_PRESTART.search(x)] if m]
    failed = _count(lines, "[CROSSFADE] BG pre-start failed")
    if failed:
        return FAIL, f"{failed} 'BG pre-start failed' line(s)"
    if not vals:
        return NOTEX, "no song used the fallback pre-start (every song had a verified audio end, or music was already playing)"
    med = statistics.median(vals)
    lo, hi = min(vals), max(vals)
    facts = f"{len(vals)} fired, remaining {lo:.1f}..{hi:.1f} s, median {med:.1f} s"
    if 4.4 <= med <= 5.6:
        return PASS, facts + "  (bgm_fallback_prestart_sec 5.0 is in force)"
    return MANUAL, facts + "  (median is not near the 5.0 s default: check the setting and for stalls)"


def check_verified_fade(lines):
    ok = _count(lines, "[CROSSFADE] BG fade started at verified audio end")
    bad = _count(lines, "[CROSSFADE] verified-audio-tail BG fade failed")
    if bad:
        return FAIL, f"{bad} verified-tail fade failure(s)"
    if not ok:
        return NOTEX, "no song started its music fade from a verified audio end"
    return PASS, f"{ok} fade(s) started at a verified audio end, none failed"


def check_fade_floor(lines):
    vals = sorted({float(m.group(1)) for x in lines for m in [_FLOOR.search(x)] if m})
    if not vals:
        return NOTEX, "no 'fade-in start floor' line (a fade from silence never ran, or the build predates 1.0.2.8)"
    return (PASS if vals == [-40.0] else MANUAL), f"floor(s) seen: {', '.join(f'{v:g} dB' for v in vals)}  (the operator setting is -40)"


def check_voice_guard(lines):
    re_synced = _count(lines, "[KARAOKE-RUST] voice re-synced")
    kept = _count(lines, "[KARAOKE-RUST] voice kept its place")
    if not (re_synced or kept):
        return NOTEX, "the end-of-song guard never fired (no song had a picture clock far from the voice near its end)"
    return MANUAL, f"{kept} 'kept its place', {re_synced} 're-synced': ask the operator whether any song end had a blip (kept = no jump, which is the aim)"


def check_seek_guard(lines):
    return MANUAL, ("NOT OBSERVABLE from a log: cdg_seek_guard writes no line when it clamps a seek. To prove it, seek into the last seconds of a CDG with a long "
                    "frame-less stretch (the 'Let Down' disc) and confirm the song does not jump to its end")


def check_quiet_shadows(rows):
    text = sr.quiet_shadows(rows)
    out = []
    worst = PASS
    for tag in ("QUEUE-STATE-SHADOW", "UPDATE-SHADOW", "SETTINGS-MIGRATE-SHADOW"):
        line = next((x for x in text.splitlines() if x.strip().startswith(tag)), "")
        verdict = line.split("   (")[0].replace(tag, "").strip() or "no line"
        out.append(f"{tag}: {verdict}")
        if "DISAGREED" in verdict:
            worst = FAIL
        elif "RAN, AGREED" not in verdict and worst != FAIL:
            worst = NOTEX
    return worst, "; ".join(out)


def check_drives(lines):
    dt = rep.drive_table(lines)
    if not dt:
        return NOTEX, "no [TRANSPORT-DRIVE] decision logged"
    bad = {k: v for k, v in dt.items() if v[1]}
    facts = ", ".join(f"{k} {n} ({b} disagree)" for k, (n, b) in sorted(dt.items()))
    status = FAIL if bad else PASS
    return status, facts


def check_watchdog_drive(lines):
    dt = rep.drive_table(lines)
    n, bad = dt.get("watchdog", (0, 0))
    if not n:
        return NOTEX, "the KaraFun watchdog drive never decided (needs a KaraFun song that outlasts its expected duration)"
    return (FAIL if bad else PASS), f"{n} decision(s), {bad} disagreement(s)"


def check_shadows(rows, settings):
    try:
        reports = sr.analyse(rows, settings)
    except Exception as exc:  # noqa: BLE001
        return MANUAL, f"shadow_report failed: {exc!r}"
    bad = [r.name for r in reports if r.verdict in ("DISAGREED", "ERROR")]
    clean = [r.name for r in sr.counted(reports) if r.verdict == "CLEAN"]
    notseen = [r.name for r in sr.counted(reports) if r.verdict not in ("CLEAN", "DISAGREED", "ERROR")]
    facts = f"{len(clean)} CLEAN" + (f"; NOT SEEN: {', '.join(notseen)}" if notseen else "")
    if bad:
        return FAIL, f"DISAGREED/ERROR: {', '.join(bad)}; {facts}"
    return (PASS if not notseen else NOTEX), facts


def check_memory(lines):
    rows = [(int(m.group(3)), int(m.group(4)), m.group(2)) for x in lines for m in [_MEM.match(x)] if m]
    if len(rows) < 4:
        return NOTEX, "fewer than 4 [MEMORY-SONG] lines"
    first, last = rows[0][1], rows[-1][1]
    peak = max(r[1] for r in rows)
    songs = max(r[0] for r in rows)
    status = MANUAL if last > 4500 else PASS
    return status, f"{songs} songs, RSS {first} -> {last} MB (peak {peak} MB)" + ("  (over 4.5 GB: look at the native categories in show_report)" if last > 4500 else "")


OPERATOR_QUESTIONS = [
    "Dead air at song ends: how many songs, and was it KaraFun, CDG or MP4 (CDG/MP4 should be rare now)?",
    "Any blip, pop or jump in the last seconds of a CDG song (the end-of-song voice guard)?",
    "Did the music fade in at the right moment after songs (not too early over the last notes, not late)?",
    "Any seek that jumped to the end or restarted a song (the CDG seek guard cannot be seen in a log)?",
    "Anything on the audience screen or the ticker that looked wrong?",
]


def build(lines, rows, settings):
    return [
        ("Clean run", *check_clean_run(lines)),
        ("Native crashes", *check_crash_reports(lines)),
        ("Audio end rule (verified end at file end)", *check_audio_end_rule(lines)),
        ("Fallback pre-start (5 s)", *check_prestart(lines)),
        ("Music fade from a verified audio end", *check_verified_fade(lines)),
        ("Fade-in floor", *check_fade_floor(lines)),
        ("End-of-song voice guard", *check_voice_guard(lines)),
        ("CDG seek guard", *check_seek_guard(lines)),
        ("Queue-state, update + settings-migration shadows", *check_quiet_shadows(rows)),
        ("Transport drives agree with Python", *check_drives(lines)),
        ("KaraFun watchdog drive", *check_watchdog_drive(lines)),
        ("Other Rust shadows", *check_shadows(rows, settings)),
        ("Memory", *check_memory(lines)),
    ]


def render(results):
    out = []
    for name, status, text in results:
        out.append(f"[{status:<13}] {name}\n                {text}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--group", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="show_checklist_") as work:
        groups = rep.collect(args.inputs, work)
        if not groups:
            print("no singws_*.log files found in the input", file=sys.stderr)
            return 2
        info = []
        for name, g in groups.items():
            ll = rep.last_launch(g.text().splitlines())
            info.append((name, g, sum(rep.song_kinds(ll).values())))
        chosen = next((x for x in info if x[0] == args.group), None) if args.group else max(info, key=lambda x: x[2])
        if chosen is None:
            print(f"no group named {args.group}", file=sys.stderr)
            return 2
        name, g, songs = chosen
        joined = Path(work) / name
        joined.write_text(g.text(), encoding="utf-8")
        all_lines = g.text().splitlines()
        lines = all_lines if args.all else rep.last_launch(all_lines)
        rows = list(sr.read_lines([str(joined)], last_launch_only=not args.all))
        t, ver = rep.launch_info(lines)
        settings = None
        for z in Path(work).rglob("settings.json"):
            try:
                loaded = json.loads(z.read_text(encoding="utf-8"))
                settings = loaded if isinstance(loaded, dict) else None
            except (OSError, ValueError):
                pass
        results = build(lines, rows, settings)
    if args.json:
        print(json.dumps([{"check": n, "status": s, "detail": d} for n, s, d in results], indent=1))
    else:
        print(f"SHOW CHECKLIST: {name}, build {ver}, launched {t or '?'}, {songs} song(s){'  [all launches]' if args.all else ''}\n")
        print(render(results))
        counts = {k: sum(1 for r in results if r[1] == k) for k in (PASS, FAIL, NOTEX, MANUAL)}
        print(f"\nTOTAL: {counts[PASS]} PASS, {counts[FAIL]} FAIL, {counts[NOTEX]} NOT EXERCISED, {counts[MANUAL]} MANUAL")
        print("\nASK THE OPERATOR:")
        for q in OPERATOR_QUESTIONS:
            print(f"  - {q}")
        print("\nIF IT WAS CLEAN: python3 tools/gate_ledger.py add <log> --all --label \"show <date> <mac>\"   (then: python3 tools/gate_ledger.py status)")
    if any(r[1] == FAIL for r in results):
        return 1
    if args.strict and any(r[1] == NOTEX for r in results):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
