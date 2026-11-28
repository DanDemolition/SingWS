#!/usr/bin/env python3
"""SingWS self-check for the Intel Mac (works on Apple Silicon too). Read-only: it changes nothing in SingWS, its settings or its queue, plays no sound and never signs in
to KaraFun. It checks the INSTALLED app, runs its bundled Rust programs on small known cases, reads the settings file and the newest log, and tells you what to look at.

    python3 intel_selfcheck.py                      # the installed app, ~/SingWS, expecting this Mac's architecture
    python3 intel_selfcheck.py --expect-version 1.0.4.5
    python3 intel_selfcheck.py --app /path/SingWS.app --home /path/to/SingWS --expect-arch x86_64

Each line is [PASS], [WARN] (look at it, not necessarily wrong) or [FAIL] (something is wrong). The report is also written to ~/Desktop/SingWS-intel-checks-<time>.txt.
Only the Python 3 that macOS ships with is needed.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import plistlib
import re
import subprocess
import sys
import time
from pathlib import Path

HELPERS = ["singws-karafun-svc", "singws-karafun-display", "singws-queue-state", "singws-songend", "singws-settingsmig", "singws-update", "singws-mac-probe", "singws-netslice",
           "singws-relaymsg", "singws-logpack", "singws-scan-cli", "singws-analyze"]
LIBS = ["libsingws_dsp_ffi.dylib", "libsingws_bgm_ffi.dylib", "libsingws_mpv_bridge.dylib", "libsingws_video.dylib", "libEGL.dylib", "libGLESv2.dylib", "libvk_swiftshader.dylib"]

# settings that must be ON in a profile that has run the current version (the one-time migrations turn them on; a later "off" is the operator's own choice: WARN, not FAIL)
EXPECTED_ON = ["transport_shadow", "transport_drive", "transport_drive_media_end", "transport_drive_watchdog", "queue_shadow", "plan_shadow", "plan_shadow_live", "start_shadow", "start_drive",
               "settings_shadow", "store_shadow", "wire_shadow", "platform_shadow", "logpack_shadow", "settings_migrate_shadow", "songend_shadow", "songend_drive_prestart",
               "songend_drive_floors", "songend_drive_gates", "songend_drive_steps", "relay_msg_shadow", "net_slice_shadow", "queue_state_shadow", "update_shadow", "phrase_shadow",
               "publish_live_state", "mac_shim_keep_awake", "karafun_prewarm"]

# lines the newest launch should have written (pattern, what it proves)
LAUNCH_LINES = [
    (r"\[BG-ENGINE\] Rust background engine ready", "the Rust background-music engine started"),
    (r"\[KEEP-AWAKE\] .*held by (rust|pyobjc)", "the Mac is kept awake (and by which code)"),
    (r"\[SCREEN\] initial topology", "the screens were read"),
    (r"\[SCREEN\] refresh rates:", "each screen's refresh rate was logged (1.0.4.4+)"),
    (r"\[STALL\] main-thread watchdog armed", "the freeze watchdog is running"),
    (r"\[KARAFUN-WEB\] engine=rust", "the Rust driver runs the KaraFun player"),
    (r"\[QUEUE-STATE-SHADOW\] enabled available=1", "the queue-state shadow found its Rust helper"),
    (r"\[SONGEND-SHADOW\] enabled available=1", "the song-end shadow found its Rust helper"),
    (r"\[PLATFORM-SHADOW\] enabled available=1", "the platform shadow found its Rust helper"),
    (r"\[SETTINGS-SHADOW\] enabled available=1", "the settings shadow found its Rust helper"),
]


class Report:
    def __init__(self):
        self.lines: list[str] = []
        self.counts = {"PASS": 0, "WARN": 0, "FAIL": 0}

    def add(self, level: str, text: str) -> None:
        self.counts[level] += 1
        self.lines.append(f"[{level}] {text}")

    def head(self, text: str) -> None:
        self.lines.append("")
        self.lines.append(f"== {text}")

    def note(self, text: str) -> None:
        self.lines.append(f"       {text}")

    passed = lambda self, t: self.add("PASS", t)      # noqa: E731
    warn = lambda self, t: self.add("WARN", t)        # noqa: E731
    fail = lambda self, t: self.add("FAIL", t)        # noqa: E731

    def text(self) -> str:
        c = self.counts
        verdict = "ALL GOOD" if c["FAIL"] == 0 and c["WARN"] == 0 else ("NEEDS A LOOK (warnings only)" if c["FAIL"] == 0 else "PROBLEMS FOUND")
        return "\n".join(self.lines + ["", f"RESULT: {verdict}   pass={c['PASS']} warn={c['WARN']} fail={c['FAIL']}"])


def run(cmd, timeout=30, stdin=None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return subprocess.CompletedProcess(cmd, 99, "", repr(exc))


def archs_of(path: Path) -> list[str]:
    out = run(["lipo", "-archs", str(path)]).stdout.split()
    return out


def native_arch() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"


def under(arch_expected: str) -> list[str]:
    """The prefix that runs a program of the expected architecture on this Mac (Rosetta on Apple Silicon for an Intel build)."""
    return ["arch", "-x86_64"] if (arch_expected == "x86_64" and native_arch() == "arm64") else []


# ----------------------------------------------------------------------------------------------------------------------------------------------------- the app
def check_app(r: Report, app: Path, expect_version: str, expect_arch: str) -> dict:
    r.head("1. The installed app")
    info = {}
    plist = app / "Contents" / "Info.plist"
    if not plist.is_file():
        r.fail(f"{app} is not a SingWS app (no Info.plist)")
        return info
    info = plistlib.loads(plist.read_bytes())
    version = str(info.get("CFBundleShortVersionString", ""))
    if expect_version and version != expect_version:
        r.fail(f"version is {version}, expected {expect_version}")
    else:
        r.passed(f"version {version}")
    exe = app / "Contents" / "MacOS" / "SingWS"
    a = archs_of(exe)
    (r.passed if expect_arch in a else r.fail)(f"main program architecture {', '.join(a) or '?'} (expected {expect_arch})")
    sig = run(["codesign", "--verify", "--deep", "--strict", str(app)], timeout=120)
    (r.passed if sig.returncode == 0 else r.fail)("signature is valid" if sig.returncode == 0 else f"signature check failed: {sig.stderr.strip()[:200]}")
    need = str(info.get("LSMinimumSystemVersion", ""))
    have = platform.mac_ver()[0]
    if need and have:
        ok = tuple(int(x) for x in re.findall(r"\d+", have)[:2] + ["0"] * 2)[:2] >= tuple(int(x) for x in re.findall(r"\d+", need)[:2] + ["0"] * 2)[:2]
        (r.passed if ok else r.fail)(f"macOS {have} (the app needs {need} or newer)")
    return info


def check_helpers(r: Report, app: Path, expect_arch: str) -> None:
    r.head("2. The bundled Rust programs and libraries")
    fw = app / "Contents" / "Frameworks"
    missing, wrong = [], []
    for name in HELPERS + LIBS:
        p = fw / name
        if not p.is_file():
            missing.append(name)
            continue
        if expect_arch not in archs_of(p):
            wrong.append(name)
    if missing:
        r.fail("missing: " + ", ".join(missing))
    if wrong:
        r.fail(f"not built for {expect_arch}: " + ", ".join(wrong))
    if not missing and not wrong:
        r.passed(f"all {len(HELPERS)} programs and {len(LIBS)} libraries are there and built for {expect_arch}")
    kf = sorted(fw.glob("SingWS KaraFun Helper*.app"))
    bad = [p.name for p in kf if expect_arch not in archs_of(p / "Contents" / "MacOS" / p.name.replace(".app", ""))]
    if len(kf) < 5:
        r.fail(f"KaraFun's browser helpers: found {len(kf)} of 5 (a missing one gives a black KaraFun picture)")
    elif bad:
        r.fail("KaraFun browser helpers not built for " + expect_arch + ": " + ", ".join(bad))
    else:
        r.passed("the 5 KaraFun browser helpers are there and built for " + expect_arch)


# ----------------------------------------------------------------------------------------------------------------------------------------- the Rust rules
SONGEND_CASES = [
    ({"op": "prestart_due", "crossfade": True, "remaining_ns": 3_000_000_000, "lead_s": 5.0, "prefired": False, "has_playlist": True, "bg_playing": False, "bg_enabled": True, "bg_autoplay": True}, True),
    ({"op": "prestart_due", "crossfade": True, "remaining_ns": 150_000_000, "lead_s": 5.0, "prefired": False, "has_playlist": True, "bg_playing": False, "bg_enabled": True, "bg_autoplay": True}, False),
    ({"op": "effective_duration", "dur_ns": 234_250_000_000, "audio_s": 256.76}, 256_760_000_000),
    ({"op": "audio_end", "duration": 200.0, "trailing": 20.0, "runs_to_end": False}, 192.0),
    ({"op": "verified_end_allowed", "auto_advance": False, "crossfade": True, "prefired": False, "has_playlist": True, "bg_playing": False, "bg_enabled": True, "bg_autoplay": True}, True),
    ({"op": "lyric_cue_ready", "enabled": True, "cdg_mode": True, "cue": 100.0, "delay": 3.0, "need": 4.0, "elapsed": 103.0, "remain": 4.0}, True),
    ({"op": "visual_end_trusted", "safe": True, "visual_end": 12.5, "confidence": 0.85}, True),
    ({"op": "visual_end_trusted", "safe": True, "visual_end": 12.5, "confidence": 0.84}, False),
    ({"op": "eos_step", "guard": 1, "near_end": True, "audio_live": False}, {"guard": 0, "fire": True}),
    ({"op": "stall_step", "last_pos": 1000, "ticks": 19, "pos": 1000, "paused": False, "audio_live": False, "karaoke_playing": True, "near_end": False},
     {"last_pos": 1000, "ticks": 0, "fire": True}),
    ({"op": "overlap_window", "overlap_s": 2.0, "trim_tail": True, "audio_end": 100.0, "elapsed": 98.0}, True),
    ({"op": "tail_cleared", "elapsed": 103.0, "audio_end": 100.0, "threshold_s": 2.5, "db": None, "db_threshold": -38.0}, True),
    ({"op": "visual_end_reached", "visual_end": 100.0, "confidence": 0.9, "elapsed": 99.0}, False),
]


def check_songend_library(r: Report, app: Path, expect_arch: str) -> None:
    lib_path = app / "Contents" / "Frameworks" / "libsingws_dsp_ffi.dylib"
    if not lib_path.is_file():
        return
    if native_arch() != expect_arch and not under(expect_arch):
        r.warn(f"the song-end library check needs a {expect_arch} Python; skipped on this {native_arch()} Mac")
        return
    # run the library check in a child of the right architecture (an Intel library cannot be loaded by an Apple Silicon Python)
    code = "import ctypes,json,sys\nlib=ctypes.CDLL(sys.argv[1]);f=lib.singws_songend_json;f.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_size_t];f.restype=ctypes.c_int64\n" \
           "out=[]\nfor req in json.loads(sys.stdin.read()):\n    b=ctypes.create_string_buffer(512);n=f(json.dumps(req).encode(),b,512);out.append(json.loads(b.raw[:n].decode()) if n>=0 else {'err':n})\n" \
           "print(json.dumps(out))"
    res = run(under(expect_arch) + [sys.executable, "-c", code, str(lib_path)], stdin=json.dumps([c[0] for c in SONGEND_CASES]), timeout=60)
    try:
        answers = json.loads(res.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        r.fail(f"the song-end rules could not be asked of the bundled library: {(res.stderr or res.stdout).strip()[:200]}")
        return
    wrong = [SONGEND_CASES[i][0]["op"] for i, a in enumerate(answers) if a.get("v") != SONGEND_CASES[i][1]]
    if wrong:
        r.fail("the bundled library answers these song-end rules wrongly: " + ", ".join(sorted(set(wrong))))
    else:
        r.passed(f"the bundled library answers all {len(SONGEND_CASES)} song-end rule checks correctly")


def check_keep_awake(r: Report, app: Path, expect_arch: str, use_pmset: bool) -> None:
    lib_path = app / "Contents" / "Frameworks" / "libsingws_dsp_ffi.dylib"
    if not use_pmset or not lib_path.is_file():
        return
    if native_arch() != expect_arch and not under(expect_arch):
        return
    code = ("import ctypes,subprocess,sys,json\nlib=ctypes.CDLL(sys.argv[1]);f=lib.singws_macshim_keep_awake;f.argtypes=[ctypes.c_int32,ctypes.c_char_p];f.restype=ctypes.c_int32\n"
            "tag=b'SINGWS-SELFCHECK'\nheld=lambda: tag.decode() in subprocess.run(['pmset','-g','assertions'],capture_output=True,text=True).stdout\n"
            "begun=f(1,tag);during=held();f(0,None);after=held()\nprint(json.dumps({'begun':begun,'during':during,'after':after}))")
    res = run(under(expect_arch) + [sys.executable, "-c", code, str(lib_path)], timeout=60)
    try:
        a = json.loads(res.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        r.fail(f"the Rust keep-awake could not be tried: {(res.stderr or res.stdout).strip()[:200]}")
        return
    if a.get("begun") == 1 and a.get("during") and not a.get("after"):
        r.passed("the Rust keep-awake takes a real macOS assertion and releases it")
    else:
        r.fail(f"the Rust keep-awake did not behave (started={a.get('begun')}, visible while held={a.get('during')}, still there after={a.get('after')})")


def check_karafun_machine(r: Report, app: Path, expect_arch: str) -> None:
    svc = app / "Contents" / "Frameworks" / "singws-karafun-svc"
    if not svc.is_file():
        return
    world = {"service_present": True, "service_state": "ready", "has_web": True, "entry_current": True, "active_same": True}
    for title in ("Tonghua (童话)", "Señorita", "plain title", "It's 'quoted'"):
        req = {"kind": "host", "events": [{"ev": "start", "t": 100.0, "song_id": 3268517, "enabled": True, "key": None, "tempo": 100, "title": title, "world": world}]}
        res = run(under(expect_arch) + [str(svc), "--batch"], stdin=json.dumps(req, ensure_ascii=False) + "\n", timeout=30)
        try:
            ans = json.loads(res.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            r.fail(f"the KaraFun song machine did not answer for {title!r}: {(res.stderr or res.stdout).strip()[:160]}")
            continue
        logs = [a[1] for a in (ans.get("trace") or [[]])[0] if a and a[0] == "log"]
        if "declined" in ans or not any(str(x).startswith("start id=") for x in logs):
            r.fail(f"the KaraFun song machine refuses the title {title!r} ({ans.get('declined', 'no start line')}): a song with such a title would not start")
        else:
            r.passed(f"a KaraFun song titled {title!r} starts")


def check_queue_state(r: Report, app: Path, expect_arch: str) -> None:
    helper = app / "Contents" / "Frameworks" / "singws-queue-state"
    if not helper.is_file():
        return
    load = {"op": "load", "settings": {"queue_mode": "rotation", "limit_pending_max": 20, "requests_accepting": True}, "queue": [], "tombstones": {}, "removed": [], "handled": [],
            "queue_revision": 0, "pending_order_syncs": {}, "terminal_titles": {}, "aliases": {}}
    row = {"request_id": 5001, "singer": "dAN sMITH", "artist": "Band", "title": "Song", "key": 0, "tempo": 0}
    lines = [load, {"op": "remote_arrive", "requests": [row], "now": 1.0}, {"op": "remote_arrive", "requests": [dict(row, state="completed")], "now": 2.0}]
    res = run(under(expect_arch) + [str(helper)], stdin="\n".join(json.dumps(x) for x in lines) + "\n", timeout=30)
    try:
        out = [json.loads(x) for x in res.stdout.splitlines() if x.strip()]
        first, second = out[1]["snapshot"]["queue"], out[2]["snapshot"]["queue"]
    except Exception:  # noqa: BLE001
        r.fail(f"the queue-state helper did not answer: {(res.stderr or res.stdout).strip()[:160]}")
        return
    name = first[0]["name"] if first else None
    if name == "Dan Smith":
        r.passed("the queue-state helper writes singer names as the app does (first letter capital)")
    else:
        r.fail(f"the queue-state helper wrote the singer as {name!r}, expected 'Dan Smith'")
    songs_before = sum(len(s["songs"]) for s in first)
    songs_after = sum(len(s["songs"]) for s in second)
    (r.passed if (songs_before == 1 and songs_after == 0) else r.fail)(f"a completed row from the server removes the song (before {songs_before}, after {songs_after})")


# ----------------------------------------------------------------------------------------------------------------------------------------- settings and logs
def check_settings(r: Report, home: Path) -> dict:
    r.head("4. The saved settings (~/SingWS/settings.json)")
    path = home / "settings.json"
    if not path.is_file():
        r.warn(f"no settings file at {path}: SingWS has not been run on this Mac yet")
        return {}
    try:
        s = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        r.fail(f"settings.json cannot be read: {exc!r}")
        return {}
    if not s.get("songend_gates_default_on_migrated"):
        r.warn("the one-time 1.0.4.5 switch-on has not run on this profile yet (open SingWS once on the new version, then run this again)")
    off = [k for k in EXPECTED_ON if k in s and s[k] is not True]
    absent = [k for k in EXPECTED_ON if k not in s]
    (r.passed if not off else r.warn)(f"{len(EXPECTED_ON) - len(off) - len(absent)} of {len(EXPECTED_ON)} shadow/drive settings are on" if not off else "switched OFF in this profile: " + ", ".join(off))
    if absent:
        r.note("not saved yet (they take their default, ON): " + ", ".join(absent))
    engine = s.get("karafun_web_engine", "rust")
    (r.passed if engine == "rust" else r.warn)(f"KaraFun player engine: {engine}" + ("" if engine == "rust" else " (the Rust driver is the default; 'python' is the fallback)"))
    cmds = s.get("shell_commands", "apply")
    (r.passed if cmds == "apply" else r.warn)(f"phone/DAW queue commands: {cmds}")
    qd = s.get("queue_state_driver", "dry_run")
    r.note(f"queue_state_driver = {qd} (dry_run is intended until a clean show)")
    return s


def newest_log(home: Path) -> Path | None:
    logs = sorted((home / "logs").glob("singws_20*.log"), key=lambda p: p.stat().st_mtime) if (home / "logs").is_dir() else []
    return logs[-1] if logs else None


def check_log(r: Report, home: Path, expect_version: str) -> None:
    r.head("5. The newest log (what the last launch and show did)")
    log = newest_log(home)
    if log is None:
        r.warn("no log yet: open SingWS, then run this again")
        return
    text = log.read_text(errors="replace")
    launches = re.findall(r'\[LAUNCH\] \{"build_id":"([^"]*)"', text)
    if not launches:
        r.warn(f"{log.name}: no launch line (an old log, or SingWS has not started today)")
        return
    last_start = text.rfind('[LAUNCH] {"build_id"')
    since = text[last_start:]
    build = launches[-1]
    (r.passed if (not expect_version or build == expect_version) else r.fail)(f"{log.name}: last launch was build {build}" + (f" (expected {expect_version})" if expect_version and build != expect_version else ""))
    tb = since.count("Traceback")
    (r.passed if tb == 0 else r.fail)("no Python tracebacks since the last launch" if tb == 0 else f"{tb} Python traceback(s) since the last launch: send the log")
    for pattern, why in LAUNCH_LINES:
        (r.passed if re.search(pattern, since) else r.warn)(f"{why}" if re.search(pattern, since) else f"line not seen: {why}   ({pattern})")
    # the show: what happened after launch (only reported when there is something)
    starts = len(re.findall(r"\[KARAFUN-WEB\] started after", since))
    if starts:
        times = [float(x) for x in re.findall(r"\[KARAFUN-WEB\] started after ([\d.]+)s", since)]
        r.passed(f"{starts} KaraFun song(s) started; start times {', '.join(f'{t:.0f}s' for t in times[:8])}" + (" (a first start over 30 s means the pre-warm did not help)" if times and times[0] > 30 else ""))
    pre = len(re.findall(r"prewarm requested", since))
    r.note(f"KaraFun pre-warm requests: {pre}")
    declined = len(re.findall(r"song machine declined", since))
    (r.passed if declined == 0 else r.fail)("no KaraFun song was refused by the song machine" if declined == 0 else f"{declined} KaraFun song(s) refused by the song machine ('declined'): send the log")
    disagree = len(re.findall(r"\[SONGEND-DRIVE\].*disagreement=1", since))
    (r.passed if disagree == 0 else r.warn)("Rust and Python never disagreed about a song-end decision" if disagree == 0 else f"{disagree} song-end disagreement(s) (Python's answer was used): send the log")
    gone = re.findall(r"not checked so far: (.*)", since)
    if gone:
        r.note("queue-state shadow skips: " + gone[-1][:160])
    fps = [float(x) for x in re.findall(r"effect ran [\d.]+s, \d+ frames \(([\d.]+) fps\)", since)]
    if fps:
        fps.sort()
        r.note(f"transitions logged: {len(fps)}, median {fps[len(fps) // 2]:.0f} fps, slowest {fps[0]:.0f} fps")
    stalls = re.findall(r"GUI thread recovered after ~(\d+)ms", since)
    if stalls:
        big = sorted((int(x) for x in stalls), reverse=True)
        r.note(f"freezes: {len(stalls)} (over a second: {sum(1 for x in big if x >= 1000)}; worst {big[0]} ms)")
    for pattern, label in ((r"Chromium's page stopped", "KaraFun's page stopped"), (r"the player has not come up", "the player never came up"), (r"clock has not moved", "a song's clock stuck at 0"),
                           (r"ignored \d stops", "the player ignored stops")):
        n = len(re.findall(pattern, since))
        if n:
            r.warn(f"{label}: {n} time(s) (the app restarted the player for it: say if a song was lost)")


CHECKLIST = """
== 6. What to try by hand (a few minutes; these need the screens and a KaraFun login)
  1. Plug in the HDMI audience screen and the AirPlay screen BEFORE opening SingWS.
  2. Queue a KaraFun song whose title has an accent or non-English letters (for example 'Señorita'): it must start (1.0.4.4 could not).
  3. The first KaraFun song of the night: note how long until it plays. The player is warmed up when the request enters the queue (it was 33-44 s before).
  4. Put the audience window full screen, then windowed, during a KaraFun song: the picture must show both times (it was black full screen on 2026-11-22).
  5. After the show, quit SingWS and run after_show.sh; send the zip. Then run this check again: section 5 summarises the show.
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", default="/Applications/SingWS.app")
    ap.add_argument("--home", default=os.environ.get("SINGWS_HOME") or str(Path.home() / "SingWS"))
    ap.add_argument("--expect-version", default="", help="fail unless the app and the last launch are this version")
    ap.add_argument("--expect-arch", default=native_arch(), choices=["x86_64", "arm64"])
    ap.add_argument("--no-pmset", action="store_true", help="skip the keep-awake test (it briefly holds and releases a macOS assertion)")
    ap.add_argument("--no-report-file", action="store_true")
    args = ap.parse_args(argv)
    r = Report()
    app, home = Path(args.app), Path(args.home)
    r.lines.append(f"SingWS self-check  {time.strftime('%Y-%m-%d %H:%M:%S')}  Mac: {native_arch()} macOS {platform.mac_ver()[0]}   app: {app}   expecting {args.expect_arch}")
    check_app(r, app, args.expect_version, args.expect_arch)
    if (app / "Contents" / "Frameworks").is_dir():
        check_helpers(r, app, args.expect_arch)
        r.head("3. The Rust rules, run on known cases with the app's own programs")
        check_songend_library(r, app, args.expect_arch)
        check_keep_awake(r, app, args.expect_arch, not args.no_pmset)
        check_karafun_machine(r, app, args.expect_arch)
        check_queue_state(r, app, args.expect_arch)
    check_settings(r, home)
    check_log(r, home, args.expect_version)
    r.lines.append(CHECKLIST)
    text = r.text()
    print(text)
    if not args.no_report_file:
        try:
            out = Path.home() / "Desktop" / f"SingWS-intel-checks-{time.strftime('%Y%m%d-%H%M')}.txt"
            out.write_text(text + "\n")
            print(f"\n(report saved: {out})")
        except OSError:
            pass
    return 1 if r.counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
