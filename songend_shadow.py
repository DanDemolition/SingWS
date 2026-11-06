"""Song-end shadow (Stage E): when does the music start at the end of a karaoke song, and does the Rust model of that decision (``singws-songend``) agree?

OPT-IN, DEFAULT OFF (setting ``songend_shadow``): it records a few facts on the song-end path, and AGENTS rule 9 says instrumentation near live playback stays off until
asked for. SHADOW ONLY: the app's own decision is always the one acted on; nothing here starts, stops or changes anything.

How it works. At a song start the app notes the song's facts (mode, length, the settings that drive the decision). When the app really starts the background music
at the song's end it notes the song position and the reason. After the song, a separate low-priority process (the Rust model in ``--lines`` mode) is fed an
IDEALISED run of the same song (a steady clock, a tick every 500 ms, scan answers delivered at once) built from the facts the app had after its scans, and the position at
which the model starts the music is compared with the position at which the app did. A song with a pause, a seek, a stall, a manual stop or a Skip is never compared
(the idealised run would not be that song). Only song LENGTHS and numbers are logged, never names, paths or ids:
  ``song N: app=<s>s (<reason>) rust=<s>s (<reason>)``     logged ONLY when they differ by more than the tolerance (1.5 s) or the reasons differ
  ``skipped: <why>``                                       not comparable, or the helper failed
Log lines carry the tag SONGEND-SHADOW; one ``enabled available=0/1`` line per launch and one ``first check clean`` once.
"""

from __future__ import annotations

import heapq
import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

HELPER_NAME = "singws-songend"
TAG = "SONGEND-SHADOW"
TOLERANCE_S = 1.5
T0_MS = 100_000
TICK_MS = 500
NS = 1_000_000_000
_clean_noted = False
_seen: set[str] = set()


def enabled(settings) -> bool:
    return bool((settings or {}).get("songend_shadow", False))


def find_helper() -> Path | None:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    candidates: list[Path] = []
    override = os.environ.get("SINGWS_SONGEND_BIN")
    if override:
        candidates.append(Path(override))
    for root in (getattr(sys, "_MEIPASS", None),
                 Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
                 Path(__file__).resolve().parent):
        if root:
            root = Path(root)
            candidates += [root / HELPER_NAME, root / "Frameworks" / HELPER_NAME, root / "native" / "singws_songend" / arch / HELPER_NAME]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def snapshot(spec: dict, now_ms: int) -> dict:
    """The machine's input at one instant of an idealised run (the same shape as tools/songend_scenarios.snapshot, steady clock, nothing paused or stalled)."""
    dur_ns = int(float(spec["media_dur_s"]) * NS)
    pos_ns = min(max(0, now_ms - T0_MS) * 1_000_000, dur_ns)
    return {
        "settings": spec.get("settings") or {},
        "times": [dur_ns, pos_ns],
        "level_db": spec.get("level_db"),
        "engine_at_end": spec.get("engine_at_end"),
        "paused": False,
        "gate": spec.get("gate", True),
        "generation": 0,
        "sectors": spec.get("sectors"),
        "karaoke_playing": True,
        "intro_loop": False,
        "bg": {"has": True, "playlist": bool(spec.get("bg_playlist", True)), "is_playing": False, "enabled": bool(spec.get("bg_enabled", True)),
               "audible": True, "starts_normal": False, "starts_allow": bool(spec.get("bg_enabled", True)), "recover_ok": True},
        "session_started": bool(spec.get("session_started", True)),
        "karaoke_played": False,
        "gen_current": 0,
        "pending_token": False,
        "next_in_progress": False,
        "stop_in_progress": False,
        "active_karafun": False,
        "db_duration": spec.get("db_duration"),
        "arm": spec.get("arm") or {},
        "audio_true_duration": {},
    }


def reason_of(logs) -> str:
    """The category of a music start from its log lines: the same wording on both sides, without the 'started / did not start' outcome (it depends on state this idealised run does not
    follow after the app stops playback)."""
    for line in logs or []:
        if "BG pre-start fired" in line:
            return "prestart"
        if "BG fade started at verified audio end" in line:
            return "verified_end"
        if "[BG-HANDOFF] fade_in" in line and "reason=" in line:
            return "handoff:" + line.split("reason=", 1)[1].split()[0]
    return "other"


def predict(spec: dict, helper: Path | None = None, max_s: float | None = None, timeout_s: float = 30.0) -> dict | None:
    """Drive the Rust model through an idealised run of ``spec`` and return ``{"pos_s", "logs", "t"}`` for the FIRST background-music start, or None when it never starts
    within the run. ``spec``: settings, mode, media_dur_s, arm, library_path, duration_hint, audio_path, visual_path, deliver_ms, level_db, engine_at_end, gate, sectors,
    db_duration, session_started."""
    helper = helper or find_helper()
    if helper is None:
        raise FileNotFoundError("singws-songend helper not found")
    dur_s = float(spec["media_dur_s"])
    end_ms = T0_MS + int((max_s if max_s is not None else dur_s + 15.0) * 1000)
    deliver_ms = int(spec.get("deliver_ms", 40))
    heap: list = []
    seq = 0
    cancelled: set[int] = set()
    timers: dict[str, int] = {}

    def schedule(t, kind, payload):
        nonlocal seq
        seq += 1
        heapq.heappush(heap, (t, seq, kind, payload))
        return seq

    n = 1
    while T0_MS + TICK_MS * n <= end_ms:
        schedule(T0_MS + TICK_MS * n, "tick", None)   # ticks are scheduled up front, so at a tie a tick comes before a later-scheduled delivery
        n += 1
    proc = subprocess.Popen([str(helper), "--lines"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                            preexec_fn=lambda: os.nice(10))
    timer = threading.Timer(timeout_s, proc.kill)
    timer.start()

    def send(t, ev, a):
        line = json.dumps({"t": t, "ev": ev, "a": a, "snap": snapshot(spec, t)})
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
        answer = proc.stdout.readline()
        if not answer:
            raise RuntimeError("helper closed")
        res = json.loads(answer)
        for c in res.get("cmds", []):
            name, args = c.get("c"), c.get("a") or []
            if name == "deliver_later" and args:
                schedule(t + deliver_ms, "deliver", {"id": args[0]})
            elif name == "timer" and len(args) >= 3:
                schedule(t + int(args[0]), "timer", {"kind": args[1], "args": args[2]})
            elif name == "timer_start" and len(args) >= 2:
                old = timers.pop(args[0], None)
                if old is not None:
                    cancelled.add(old)
                timers[args[0]] = schedule(t + int(args[1]), "qtimer", {"name": args[0]})
            elif name == "timer_stop" and args:
                old = timers.pop(args[0], None)
                if old is not None:
                    cancelled.add(old)
        return res

    try:
        start = {"mode": spec["mode"], "media_dur_s": dur_s, "arm": spec.get("arm") or {}, "library_path": spec.get("library_path", ""),
                 "duration_hint": spec.get("duration_hint"), "audio_path": spec.get("audio_path", "/t/x.mp3"), "visual_path": spec.get("visual_path", "/t/x.cdg")}
        results = [send(T0_MS, "song_start", start)]
        while heap:
            t, sq, kind, payload = heapq.heappop(heap)
            if t > end_ms:
                break
            if sq in cancelled:
                continue
            if kind == "tick":
                results.append(send(t, "tick", {}))
            elif kind == "deliver":
                results.append(send(t, "deliver", payload))
            elif kind == "timer":
                results.append(send(t, "timer", payload))
            elif kind == "qtimer":
                timers.pop(payload["name"], None)
                results.append(send(t, "qtimer", payload))
            r = results[-1]
            names = [c.get("c") for c in r.get("cmds", [])]
            if "bg:fade_in" in names:
                logs = [c["a"][0] for c in r["cmds"] if c.get("c") == "log" and c.get("a")]
                return {"t": r["t"], "pos_s": (r["t"] - T0_MS) / 1000.0, "logs": logs, "reason": reason_of(logs)}
            if "host:stop_playback" in names or "host:play_next_file" in names:
                return None   # the song ended (stopped or advanced) without the music having started first
        return None
    finally:
        timer.cancel()
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


# --------------------------------------------------------------------------------------------------------------------------------- live part
SETTING_KEYS = ("bg_autoplay_on_idle", "bg_enabled", "bg_volume", "bgm_fallback_prestart_sec", "bgm_overlap_before_audio_end_sec", "end_silence_trim_enabled",
                "end_silence_trim_threshold_sec", "karaoke_allow_early_silence_trim", "karaoke_auto_advance", "karaoke_bgm_crossfade_enabled",
                "karaoke_trim_verified_tail", "seamless_transitions_enabled")
FAKE_LIB = "/lib/s.zip"
FAKE_VIS = "/t/x.cdg"


def announce(settings, log) -> str:
    try:
        text = f"enabled available={int(find_helper() is not None)}" if enabled(settings) else "off (songend_shadow=false)"
        log(f"[{TAG}] {text}")
        return text
    except Exception:  # noqa: BLE001
        return ""


def build_spec(facts: dict) -> dict:
    """The idealised song for the model, from the facts the app had when it started the music. ``facts``: settings, mode, media_dur_s, audio_end_s, audio_duration_s,
    visual_end_s, session_started, bg_playlist."""
    media = float(facts["media_dur_s"])
    ae, ad = facts.get("audio_end_s"), facts.get("audio_duration_s")
    arm: dict = {}
    if ae is not None and ad:
        tail = float(ad) - float(ae)
        if tail >= 0.3:
            arm["cached"] = {FAKE_LIB: {"duration": float(ad), "audio_end": float(ae)}}
        else:   # the audio runs to (nearly) the end of the file: the model reaches that through its scan path
            arm.update({"scan_probe": float(ad), "scan_trailing": max(0.0, tail), "scan_audible_end": tail <= 0.0})
    elif ad:
        arm.update({"scan_probe": float(ad), "scan_trailing": 0.0, "scan_audible_end": False})
    else:
        arm.update({"scan_probe": "raise", "scan_trailing": "raise"})
    if facts.get("visual_end_s") is not None:
        arm.setdefault("cached", {})[FAKE_VIS] = {"visual_end": float(facts["visual_end_s"]), "visual_confidence": 1.0}
    return {"settings": {k: v for k, v in (facts.get("settings") or {}).items() if k in SETTING_KEYS}, "mode": facts.get("mode") or "cdg", "media_dur_s": media, "arm": arm,
            "library_path": FAKE_LIB if "cached" in arm and FAKE_LIB in arm["cached"] else "", "duration_hint": media, "audio_path": "/t/x.mp3", "visual_path": FAKE_VIS,
            "session_started": bool(facts.get("session_started", True)), "bg_playlist": bool(facts.get("bg_playlist", True))}


def compare(app_pos_s: float, app_reason: str, rs: dict | None) -> list[str]:
    if rs is None:
        return [f"app={app_pos_s:.1f}s ({app_reason}) rust=never started before the song ended"]
    out = []
    if abs(rs["pos_s"] - app_pos_s) > TOLERANCE_S or rs.get("reason") != app_reason:
        out.append(f"app={app_pos_s:.1f}s ({app_reason}) rust={rs['pos_s']:.1f}s ({rs.get('reason')})")
    return out


def run_shadow(facts: dict, app_pos_s: float, app_reason: str, log, helper: Path | None = None, number: int = 0) -> str:
    """One comparison, off the calling thread. Returns ``clean``, ``mismatch`` or ``skipped`` (for tests)."""
    global _clean_noted

    def say(text):
        try:
            log(f"[{TAG}] {text}")
        except Exception:
            pass

    try:
        helper = helper or find_helper()
        if helper is None:
            return "skipped"
        rs = predict(build_spec(facts), helper=helper)
        diffs = compare(app_pos_s, app_reason, rs)
        if not diffs:
            if not _clean_noted:
                _clean_noted = True
                say("first check clean (the Rust model starts the music where the app did)")
            return "clean"
        for d in diffs:
            say(f"MISMATCH song {number}: {d}")
        return "mismatch"
    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app
        say(f"skipped: {type(exc).__name__}: {str(exc)[:160]}")
        return "skipped"


def note_song_start(app, mode: str) -> None:
    """Called by the app after it armed a native karaoke song. Cheap, swallows everything, does nothing unless the setting is on."""
    try:
        s = getattr(app, "settings", None)
        if not enabled(s):
            app.__dict__.pop("_songend_shadow_rec", None)
            return
        n = int(app.__dict__.get("_songend_shadow_n", 0)) + 1
        app.__dict__["_songend_shadow_n"] = n
        app.__dict__["_songend_shadow_rec"] = {"n": n, "mode": str(mode or "cdg"), "noted": False}
    except Exception:  # noqa: BLE001
        pass


def note_music_start(app, kind: str, log=None) -> threading.Thread | None:
    """Called by the app just before it starts the background music at a song's end (kind: ``verified_end``, ``prestart`` or ``handoff:<reason>``). Only the FIRST start of a
    song counts. Reads the facts the app holds right now, then hands the comparison to a low-priority thread. Returns that thread (None when nothing was compared)."""
    try:
        rec = app.__dict__.get("_songend_shadow_rec")
        if not rec or rec.get("noted"):
            return None
        rec["noted"] = True
        transport = getattr(app, "karaoke_transport", None)
        times = transport.query_times_ns() if transport is not None else None
        if not times or not times[0] or times[1] is None:
            return None
        dur_ns, pos_ns = times
        settings = {k: app.settings.get(k) for k in SETTING_KEYS if k in app.settings}
        facts = {"settings": settings, "mode": rec["mode"], "media_dur_s": dur_ns / NS, "audio_end_s": app.__dict__.get("_karaoke_audio_end_s"),
                 "audio_duration_s": app.__dict__.get("_karaoke_audio_duration_s"), "visual_end_s": app.__dict__.get("_karaoke_visual_end_s"),
                 "session_started": bool(app.__dict__.get("_bgm_session_started", False)), "bg_playlist": bool(getattr(getattr(app, "bg_music", None), "playlist", None))}
        if not bool(settings.get("karaoke_trim_verified_tail", True)):
            return None   # the legacy meter detector is not modelled by an idealised run
        if find_helper() is None:
            return None
        thread = threading.Thread(target=run_shadow, args=(facts, pos_ns / NS, kind, log or (lambda text: None), None, rec["n"]), name="songend-shadow", daemon=True)
        thread.start()
        return thread
    except Exception:  # noqa: BLE001
        return None
