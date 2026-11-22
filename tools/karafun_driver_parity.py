#!/usr/bin/env python3
"""Parity of the Rust KaraFun DRIVER (rust/crates/singws-karafun-svc, src/driver.rs: the glue W10) against the REAL Python `KaraFunWebHost` + `KaraFunWebService` together.

The two machines are proven on their own (karafun_service_parity.py, karafun_host_parity.py). This tool proves the code that JOINS them: the routing of the player's
messages (service first, then the song machine), the timers (the 1 s start watchdog, the countdown hold) and what is done with each machine's answers (display commands,
the set-up, the saved login, the restart of a dead player). The Python side is the real host with its real service: a scripted clock, a scripted QTimer, a fake player
object, a fake app, a fake saved-login reader. A whole show is replayed as a list of timed events (a song asked for, what the player says, time passing, a pause, a
seek, a finish...), and what happens outside the two sides (player commands, entry writes, app calls, log lines) is compared event by event.
No network, no Chrome, no Keychain, no Qt window. Needs:  cd rust && cargo build --release -p singws-karafun-svc
  SINGWS_HOME=$(mktemp -d) .venv/bin/python tools/karafun_driver_parity.py [--cases N] [--seed S]      exit 1 on any difference
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if not os.environ.get("SINGWS_HOME") or Path(os.environ["SINGWS_HOME"]).resolve() == (Path.home() / "SingWS").resolve():
    os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="kf_driver_parity_home_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))
import karafun_web  # noqa: E402
import karafun_web_host  # noqa: E402

# the Python host has neither of the 2026-11-22 rules (a song clock stuck at 0, a player that ignores repeated stops): the Rust program is told to leave them out here
os.environ["SINGWS_KF_OLD_RULES"] = "1"

WALL_FIELDS = {"karafun_pending_at", "karafun_submitted_at", "karafun_play_started_at"}
CREDS = ("a@example.com", "not-a-real-password")
SONG_ID = 3241559
ACTIVE = {"_shadow_id": None, "active": True}
OTHER_ACTIVE = {"other": True}


def find_cli() -> str:
    for c in (os.environ.get("SINGWS_KF_SVC_BIN", ""), os.path.join(os.environ.get("CARGO_TARGET_DIR", ""), "release", "singws-karafun-svc"),
              os.path.expanduser("~/.cache/singws-rust-target-karafun-svc/release/singws-karafun-svc"), str(ROOT / "rust/target/release/singws-karafun-svc")):
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("singws-karafun-svc not built")


class InlineThread:
    """The set-up thread (and the profile-reset worker) run at once, on start()."""

    def __init__(self, target=None, name=None, daemon=None, **kw):
        self.target = target

    def start(self):
        self.target()

    def is_alive(self):
        return False


def python_trace(sc: dict) -> dict:
    log: list = []
    clock = [0.0]
    timers: list = []          # [due, order, kind, fn]
    order = [0]
    state = {"web": None, "entry": None, "entry_current": True}
    other_entry = {}

    class Entry(dict):
        def __setitem__(self, k, v):
            log.append(["entry_wall", k] if k in WALL_FIELDS else ["entry_set", k, v])
            super().__setitem__(k, v)

        def pop(self, k, *d):
            log.append(["entry_pop", k])
            return super().pop(k, *d)

    class Web:
        def __init__(self, on_message=None, on_log=None, helper_path=None, **kw):
            self.on_message = on_message

        def start(self): pass
        def sign_in(self): log.append(["sign_in"])
        def login(self, email, password):
            assert (email, password) == CREDS
            log.append(["login"])
        def command(self, name, **kw): log.append([name])
        def close(self):
            log.append(["close"])
            if state["web"] is self:
                state["web"] = None
            if state.get("in_recover") and state.get("restart_error"):
                raise RuntimeError(state["restart_error"])
        def play(self, song_id, query=None, queries=None, artist="", title=""): log.append(["web_play", song_id])
        def pause(self): log.append(["web_pause"])
        def resume(self): log.append(["web_resume"])
        def stop(self): log.append(["web_stop"])
        def seek(self, s): log.append(["web_seek", s])
        def set_key(self, k): log.append(["web_set_key", k])
        def set_tempo(self, t): log.append(["web_set_tempo", t])
        def audience(self, on): pass
        def start_screencast(self, *a): pass
        def stop_screencast(self): pass

    def make_web(**kw):
        w = Web(**kw)
        state["web"] = w
        return w

    def find_browser(progress, allow_download=False):
        log.append(["display_start"])
        if not sc["chromium"]:
            raise karafun_web.BrowserMissing("KaraFun's player needs Chromium, which is not on this Mac yet")
        return Path("/x/display")

    class App:
        settings = {}
        _active_external_karafun = None
        def _run_on_ui_thread(self, fn): fn()
        def _karafun_active_entry(self): return state["entry"] if state["entry_current"] else other_entry
        def _karafun_entry_artist_title(self, entry): return ("Artist", entry.get("title_for_test", "Title"))
        def _karafun_search_queries_for_entry(self, entry): return []
        def _is_external_karafun_entry(self, entry): return False
        def _set_karafun_entry_status(self, entry, status, message=None, notify=True): log.append(["entry_status", status, message, notify])
        def _show_processing_notification(self, text, level="info"): log.append(["notify", text, level])
        def _apply_verified_duration(self, entry, n, source=""): log.append(["verified_duration", n])
        def _fade_bg_for_external_karafun(self): log.append(["fade_bg"])
        def _karafun_show_key_tempo(self, k, t): log.append(["show_key_tempo", k, t])
        def _finish_external_karafun_playback(self, action, expected_active=None): log.append(["finish_" + action])
        def _schedule_save_data(self, n): log.append(["save"])
        def update_queue_display(self): log.append(["update_queue"])
        def _transport_shadow(self): return None

    app = App()

    def diag(text):
        pre = "[KARAFUN-WEB] "
        if text.startswith(pre):
            t = text[len(pre):]
            if " | last status:" in t:
                t = t.split(" | last status:")[0]
            log.append(["log", t])
        else:
            log.append(["diag", text])

    host = karafun_web_host.KaraFunWebHost(app, diag=diag)
    host._shadow = None
    host.enabled = lambda: True
    host._start_frames = lambda show=True: log.append(["start_frames"] if show else ["start_frames_hidden"])
    host._stop_frames = lambda: log.append(["stop_frames"])
    real_recover = host._recover

    def recover(why):
        state["in_recover"] = True
        try:
            real_recover(why)
        finally:
            state["in_recover"] = False

    host._recover = recover
    host._ask_for_browser = lambda: None            # the dialog is the app's; the machine's answer to "needs_browser" is to wait

    def saved_login():
        log.append(["read_saved_login"])
        mode = sc["saved"]
        if mode == "found":
            return CREDS
        if mode == "error":
            raise RuntimeError("secret-ish text must not be logged")
        return None

    host._saved_login = saved_login
    profile = Path(tempfile.mkdtemp(prefix="kf_driver_profile_"))
    host._factory = lambda on_state, on_message, on_log, credentials: karafun_web.KaraFunWebService(
        on_state, on_message=on_message, on_log=on_log, credentials=credentials, profile_dir=profile, make_web=make_web, find_browser=find_browser)

    def single_shot(ms, fn):
        order[0] += 1
        hold = "_hold_over" in getattr(fn, "__code__", None).co_names
        timers.append([clock[0] + ms / 1000.0, order[0], hold, fn])

    def rename(src, dst):
        log.append(["reset_profile"])

    def fire_due(upto):
        while True:
            due = [t for t in timers if t[0] <= upto]
            if not due:
                return
            t = min(due, key=lambda x: (x[0], x[1]))
            timers.remove(t)
            clock[0] = t[0]
            t[3]()

    traces = []
    from PyQt6.QtCore import QTimer
    with mock.patch.object(karafun_web_host.time, "monotonic", lambda: clock[0]), mock.patch.object(karafun_web_host.time, "time", lambda: 1000.5), \
            mock.patch.object(karafun_web.time, "monotonic", lambda: clock[0]), mock.patch.object(karafun_web.threading, "Thread", InlineThread), \
            mock.patch.object(karafun_web.os, "rename", rename), mock.patch.object(QTimer, "singleShot", staticmethod(single_shot)):
        for e in sc["events"]:
            mark = len(log)
            fire_due(e["t"])
            clock[0] = e["t"]
            after_timers = len(log)       # what the timers that fell due before this event did stays in its trace
            state["entry_current"] = e.get("entry_current", True)
            state["restart_error"] = e.get("restart_error")
            app._active_external_karafun = ACTIVE if e.get("active_same", True) else OTHER_ACTIVE
            kind = e["ev"]
            ret = None
            if kind == "start":
                entry = Entry()
                entry["title_for_test"] = e["title"]
                del log[after_timers:]       # building the entry is the test's, not the host's
                prev, state["entry"] = state["entry"], entry
                app._active_external_karafun = ACTIVE
                ret = host.start(entry, key=e["key"], tempo_percent=e["tempo"], song_path=f"karafun_streaming:kf_{e['song_id']}")
                if not ret:
                    state["entry"] = prev
                app._active_external_karafun = ACTIVE if e.get("active_same", True) else OTHER_ACTIVE
            elif kind == "msg":
                web = state["web"]
                if web is not None:
                    web.on_message(dict(e["message"]))
            elif kind == "tick":
                pass
            elif kind == "toggle_pause":
                ret = host.toggle_pause()
            elif kind == "seek":
                ret = host.seek_to(e["target"])
            elif kind == "key_tempo":
                host.apply_key_tempo(state["entry"] if state["entry"] is not None else Entry(), e["key"], e["tempo"])
                ret = True
            elif kind == "finish":
                ret = host.on_finish()
            if kind == "key_tempo":
                ret = None
            if ret is not None:
                log.append(["returns", ret])
            traces.append([list(x) for x in log[mark:]])
    return {"trace": traces}


def rust_traces(scenarios, cli: str):
    lines = "\n".join(json.dumps({"kind": "driver", **sc}) for sc in scenarios) + "\n"
    p = subprocess.run([cli, "--batch"], input=lines, capture_output=True, text=True, timeout=600)
    return [json.loads(x) for x in p.stdout.splitlines() if x.strip()]


def norm(x):
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, list):
        return [norm(i) for i in x]
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items()}
    return x


TITLES = ["Memory", "Don't Stop", "It's \"Quoted\"", "A" * 30, "Plain"]
ERRTEXTS = ["no internet", "Play failed: no match", "Play nothing", "KaraFun's web player stopped", "Chrome stopped", "", "x" * 300, "Plain"]


def status_msg(rng, asked_id, signed_in, playing, pos, dur):
    song = rng.choice([{"id": asked_id}] * 6 + [{"id": asked_id + 1}, None])
    m = {"type": "status", "signedIn": signed_in, "plan": rng.choice(["Plus", None, "Pro", 3]), "playing": playing, "position": pos, "duration": dur,
         "state": rng.choice(["playing", "idle", "paused", "loading"]), "song": song}
    if rng.random() < 0.15:
        m["moving"] = rng.random() < 0.5
    if rng.random() < 0.1:
        del m["plan"]
    return m


def gen(rng: random.Random) -> dict:
    sc = {"chromium": rng.random() < 0.92, "saved": rng.choice(["none", "found", "found", "error"]), "events": []}
    t = rng.choice([0.0, 100.0])
    signed_in = rng.random() < 0.6
    playing, pos, dur = False, 0.0, 120.0
    n = rng.randint(8, 70)
    asked = SONG_ID

    def add(**ev):
        ev["t"] = round(t, 3)
        if rng.random() < 0.04:
            ev["entry_current"] = False
        if rng.random() < 0.03:
            ev["active_same"] = False
        if rng.random() < 0.05:
            ev["restart_error"] = rng.choice(["the player would not close", "x" * 40, "boom"])
        sc["events"].append(ev)

    add(ev="start", song_id=asked, key=rng.choice([0, 0, 2, -3, 5]), tempo=rng.choice([None, 100, 100, 110, 90]), title=rng.choice(TITLES))
    for _ in range(n):
        r = rng.random()
        t += rng.choice([0.5, 0.5, 0.5, 1.0, 1.0, 2.0, 3.6, 10.0, 70.0, 160.0]) if r < 0.6 else rng.choice([0.0, 0.1, 0.5])
        if r < 0.5:
            if rng.random() < 0.05:
                signed_in = not signed_in
            if playing:
                pos = min(dur + 1, pos + rng.choice([0.5, 1.0, 1.0, 5.0]))
            elif rng.random() < 0.25:
                playing = True
            if playing and rng.random() < 0.04:
                playing = False
            add(ev="msg", message=status_msg(rng, asked, signed_in, playing, round(pos, 2), dur))
        elif r < 0.56:
            add(ev="msg", message={"type": "error", "message": rng.choice(ERRTEXTS)})
        elif r < 0.60:
            add(ev="msg", message={"type": rng.choice(["log", "ready", "helper"]), "message": rng.choice(["hello", "", "RuntimeError: memory access out of bounds"]),
                                   "state": rng.choice(["window", "no_frames", "started"]), "id": "audience", "number": 7})
        elif r < 0.72:
            add(ev="tick")
        elif r < 0.80:
            add(ev="toggle_pause")
        elif r < 0.86:
            add(ev="seek", target=rng.choice([0.0, 30.0, 500.0, -4.0, 112.5]))
        elif r < 0.91:
            add(ev="key_tempo", key=rng.choice([0, 2, -2]), tempo=rng.choice([100, 105, 90]))
        elif r < 0.95:
            add(ev="finish")
        elif r < 0.98:
            add(ev="start", song_id=asked, key=0, tempo=100, title=rng.choice(TITLES))
        else:
            add(ev="msg", message={"type": "error", "message": "KaraFun's web player stopped"})
    return sc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261116)
    ap.add_argument("--show", type=int, default=2, help="how many differences to print")
    args = ap.parse_args()
    cli = find_cli()
    rng = random.Random(args.seed)
    scenarios = [gen(rng) for _ in range(args.cases)]
    rust = rust_traces(scenarios, cli)
    if len(rust) != len(scenarios):
        print(f"the Rust program answered {len(rust)} of {len(scenarios)} scenarios")
        return 1
    same = declined = different = 0
    shown = 0
    for i, (sc, rs) in enumerate(zip(scenarios, rust)):
        if "declined" in rs:
            declined += 1
            continue
        py = python_trace(sc)
        if norm(py["trace"]) == norm(rs["trace"]):
            same += 1
            continue
        different += 1
        if shown < args.show:
            shown += 1
            for k, (a, b) in enumerate(zip(py["trace"], rs["trace"])):
                if norm(a) != norm(b):
                    print(f"--- scenario {i} (seed {args.seed}) event {k}: {sc['events'][k]}  chromium={sc['chromium']} saved={sc['saved']}")
                    print("  python:", json.dumps(a)[:900])
                    print("  rust:  ", json.dumps(b)[:900])
                    break
    print(f"{same} identical, {different} different, {declined} declined of {len(scenarios)} (seed {args.seed})")
    return 1 if different else 0


if __name__ == "__main__":
    sys.exit(main())
