#!/usr/bin/env python3
"""Post-show / post-rehearsal report for the Rust-driven KaraFun player (`karafun_web_engine = "rust"`, the default since 1.0.3.10).

READ-ONLY over the show logs. The KFWEB-SHADOW row of tools/shadow_report.py compares the Rust twin with the Python host; with the Rust driver in charge there is
no Python answer to compare with, so this report reads what the player DID instead: which engine ran, how many songs started, played, ended, were skipped, seeked,
changed key/tempo, how the sign-in went, and whether anything went wrong (the driver stopping, a song that failed, no picture frames, the driver not answering).

One verdict:
  CLEAN          the Rust driver ran the player, every step below was seen, and nothing went wrong
  PARTIAL        nothing went wrong, but some steps were not seen this session (listed): rehearse them
  NOT EXERCISED  the Rust driver ran the player but no song was started
  NOT RUST       the Python host ran the player (setting off, or the Rust program was not found / could not start)
  ATTENTION      something went wrong (listed, with the log time)

Steps (a "step seen" needs the log line the app writes for it): song started, song playing, song ended by itself, a second song started after the first (the next
song), seek, key/tempo change, skip (Stop / return to queue), sign-in. Pause/resume writes no log line, so it is not judged: do it and say so.

By default only the LAST app launch in the newest log is read (the show you just ran); --all reads every launch in the given logs.

Usage:  python3 tools/karafun_rust_report.py [LOG ...] [--all] [--require-all] [--checklist]
Exit status: 0 CLEAN (or PARTIAL without --require-all); 1 ATTENTION; 2 no usable log; 3 NOT RUST / NOT EXERCISED, or PARTIAL with --require-all.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shadow_report  # noqa: E402  (the same log reader: last app launch, tag and time of every line)

CHECKLIST = """\
Rust KaraFun rehearsal (about 10 minutes). Queue at least two KaraFun songs.

  1. Start a song: audio AND picture.
  2. Pause, then resume.
  3. Seek forward and back (a click on the progress bar, or the seek control).
  4. Change key and tempo while it plays.
  5. Let the first song end by itself (or seek to its last seconds): the SECOND song must start without a click.
  6. Skip (Stop) a song partway through: the next song must start.
  7. Sign out and back in if you can (move ~/SingWS/KaraFun-Display aside, relaunch, play a song: the saved login signs in by itself).
  8. Then: python3 tools/karafun_rust_report.py --require-all     (CLEAN = ready to count as the clean show)
Pause/resume leave no log line: tell whoever reads the report that you did them.
"""

# what each step needs in the log
STEPS = (
    ("song started", "start_song"),
    ("song playing", "played"),
    ("song ended by itself", "completed"),
    ("a second song started (the next song)", "second_song"),
    ("seek", "seek"),
    ("key/tempo change", "key_tempo"),
    ("skip (Stop / return to queue)", "returned"),
    ("sign-in", "signin"),
)
# a sign-in is only judged when the player had to sign in: a run on a profile that was already signed in never writes one
OPTIONAL = {"signin"}

_PATTERNS = (
    ("engine_rust", "KARAFUN-WEB", re.compile(r"engine=rust: the Rust driver runs the player")),
    ("engine_python", "KARAFUN-WEB", re.compile(r"engine=rust: the Rust driver is not here|engine=rust could not start")),
    ("driver_started", "KARAFUN-WEB", re.compile(r"the Rust driver runs the KaraFun player \(pid ")),
    ("driver_stopped", "KARAFUN-WEB", re.compile(r"the Rust driver stopped")),
    ("start_song", "KARAFUN-WEB", re.compile(r"^start id=")),
    ("played", "KARAFUN-WEB", re.compile(r"^started after ")),
    ("completed", "KARAFUN", re.compile(r"^status=completed ")),
    ("returned", "KARAFUN", re.compile(r"^finish requested action='return_to_queue'")),
    ("seek", "KARAFUN-WEB", re.compile(r"^seek to ")),
    ("key_tempo", "KARAFUN-WEB", re.compile(r"^live key/tempo ")),
    ("signin", "KARAFUN-WEB", re.compile(r"signing in with the saved KaraFun login|^state=ready Signed in")),
    ("signin_page", "KARAFUN-WEB", re.compile(r"opening KaraFun's sign-in|sign-in page")),
    ("failed", "KARAFUN", re.compile(r"^status=failed ")),
    ("gave_up", "KARAFUN-WEB", re.compile(r"giving up on the song")),
    ("no_picture", "KARAFUN-WEB", re.compile(r"NO PICTURE FRAMES")),
    ("no_answer", "KARAFUN-WEB", re.compile(r"the Rust driver is not answering|the Rust driver did not answer")),
    ("effect_failed", "KARAFUN-WEB", re.compile(r"effect failed|unknown effect from the Rust driver")),
    ("stray_song", "KARAFUN-WEB", re.compile(r"the player was playing a song SingWS did not ask for")),
    ("stalled", "KARAFUN-WEB", re.compile(r"stalled|never started|did not start")),
)
# what each problem means, in the words the report prints
PROBLEMS = {
    "driver_stopped": "the Rust driver stopped (a song on at the time is failed; the next song starts a new driver)",
    "failed": "a song's KaraFun status went to failed",
    "gave_up": "the app gave up on a song",
    "no_picture": "no picture frames from the graphics chip",
    "no_answer": "the Rust driver did not answer a request",
    "effect_failed": "an effect from the Rust driver failed or was unknown",
    "stalled": "a song never started or stalled",
}
# not failures, but worth a look
NOTES = {
    "stray_song": "the player was playing a song SingWS did not ask for, and it was stopped",
}


@dataclass
class Report:
    verdict: str = "NOT RUST"
    counts: Counter = field(default_factory=Counter)
    problems: list = field(default_factory=list)       # (time, text)
    notes: list = field(default_factory=list)
    unseen: list = field(default_factory=list)
    detail: str = ""


def analyse(rows) -> Report:
    r = Report()
    first_time = {}
    for _path, t, tag, msg in rows:
        for key, want_tag, pattern in _PATTERNS:
            if tag == want_tag and pattern.search(msg):
                r.counts[key] += 1
                first_time.setdefault(key, t)
                if key in PROBLEMS:
                    r.problems.append((t, f"{PROBLEMS[key]}: {msg[:110]}"))
                if key in NOTES:
                    r.notes.append((t, f"{NOTES[key]}"))
        if tag in ("TRANSPORT-SHADOW", "KARAFUN") and msg.startswith("MISMATCH"):
            r.counts["mismatch"] += 1
            r.problems.append((t, f"a transport mismatch: {msg[:110]}"))
    c = r.counts
    c["second_song"] = 1 if c["start_song"] >= 2 else 0
    if c["engine_rust"] == 0:
        r.verdict = "NOT RUST"
        r.detail = "the Rust driver did not run the player in this launch" + (" (it was not found or could not start: the Python host ran)" if c["engine_python"] else " (no engine line: the player was not used, or the setting is shadow/python)")
        return r
    if c["start_song"] == 0:
        r.verdict = "NOT EXERCISED"
        r.detail = "the Rust driver ran the player but no KaraFun song was started"
        return r
    r.unseen = [label for label, key in STEPS if c[key] == 0 and key not in OPTIONAL]
    if r.problems:
        r.verdict = "ATTENTION"
    elif r.unseen:
        r.verdict = "PARTIAL"
        r.detail = "not seen: " + ", ".join(r.unseen)
    else:
        r.verdict = "CLEAN"
    return r


def render(r: Report) -> str:
    c = r.counts
    out = [f"KaraFun (Rust driver): {r.verdict}" + (f"  - {r.detail}" if r.detail else "")]
    out.append(f"  songs started {c['start_song']}, playing {c['played']}, ended by themselves {c['completed']}, skipped {c['returned']}, seeks {c['seek']}, key/tempo changes {c['key_tempo']}")
    out.append(f"  driver processes started {c['driver_started']}, stopped unexpectedly {c['driver_stopped']}, sign-ins with the saved login {c['signin']}")
    for t, text in r.problems:
        out.append(f"  PROBLEM {t}  {text}")
    for t, text in r.notes:
        out.append(f"  note    {t}  {text}")
    if r.verdict in ("CLEAN", "PARTIAL"):
        out.append("  pause/resume: not logged (do it in the rehearsal and say so)")
        if c["signin"] == 0:
            out.append("  sign-in: not seen this launch (the profile was already signed in)")
    return "\n".join(out)


def exit_status(r: Report, require_all: bool) -> int:
    if r.verdict == "ATTENTION":
        return 1
    if r.verdict in ("NOT RUST", "NOT EXERCISED"):
        return 3
    if r.verdict == "PARTIAL" and require_all:
        return 3
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="*", help="log files (default: the newest ~/SingWS/logs/singws_*.log)")
    parser.add_argument("--all", action="store_true", help="read every app launch in the logs, not only the last one")
    parser.add_argument("--require-all", action="store_true", help="exit 3 unless the verdict is CLEAN")
    parser.add_argument("--checklist", action="store_true", help="print the rehearsal steps and exit")
    args = parser.parse_args(argv)
    if args.checklist:
        print(CHECKLIST)
        return 0
    paths = args.logs or sorted(glob.glob(os.path.expanduser("~/SingWS/logs/singws_*.log")))[-1:]
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        print("no log file found", file=sys.stderr)
        return 2
    report = analyse(shadow_report.read_lines(paths, last_launch_only=not args.all))
    print(f"logs: {', '.join(os.path.basename(p) for p in paths)}  ({'all launches' if args.all else 'last app launch only'})")
    print(render(report))
    return exit_status(report, args.require_all)


if __name__ == "__main__":
    sys.exit(main())
