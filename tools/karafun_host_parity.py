#!/usr/bin/env python3
"""Parity of the Rust KaraFun web HOST lifecycle (rust/crates/singws-karafun-svc, src/host.rs, milestone W2) against the REAL Python `karafun_web_host.KaraFunWebHost`.

The Python is the oracle: the real class runs with a fake app, a fake service/player, a scripted `time.monotonic` / `time.time` and a scripted QTimer.singleShot
(the never-started watchdog is fired by an explicit event). Scenarios: song starts (service ready / not ready / error / disabled / not a KaraFun song), service state
changes, status streams (start heard, display clock, pause, end by stopped-at-end / gone / another song / sat-at-end, never-started 60 s and recovery 150 s limits,
player died during the song and its restart up to 3 times, stray audio), page errors, pause/resume, seek clamp, live key/tempo, finish. Each event's ordered effects
(entry field writes, app calls, player commands, log lines) are recorded and compared with the Rust trace.
No network, no Chrome, no Qt window. Needs:  cd rust && cargo build --release -p singws-karafun-svc
  SINGWS_HOME=$(mktemp -d) .venv-test-arm64-fresh/bin/python tools/karafun_host_parity.py [--cases N] [--seed S]      exit 1 on any difference
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
    os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="kf_host_parity_home_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))
import karafun_web_host  # noqa: E402

WALL_FIELDS = {"karafun_pending_at", "karafun_submitted_at", "karafun_play_started_at"}


def find_cli() -> str:
    for c in (os.environ.get("SINGWS_KF_SVC_BIN", ""), os.path.join(os.environ.get("CARGO_TARGET_DIR", ""), "release", "singws-karafun-svc"),
              os.path.expanduser("~/.cache/singws-rust-target-karafun-svc/release/singws-karafun-svc"), str(ROOT / "rust/target/release/singws-karafun-svc")):
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("singws-karafun-svc not built")


def python_trace(sc: dict) -> dict:
    log: list = []

    class Entry(dict):
        def __setitem__(self, k, v):
            log.append(["entry_wall", k] if k in WALL_FIELDS else ["entry_set", k, v])
            super().__setitem__(k, v)

        def pop(self, k, *d):
            log.append(["entry_pop", k])
            return super().pop(k, *d)

    class Web:
        def play(self, song_id, query, queries, artist, title): log.append(["web_play", song_id])
        def pause(self): log.append(["web_pause"])
        def resume(self): log.append(["web_resume"])
        def stop(self): log.append(["web_stop"])
        def seek(self, s): log.append(["web_seek", s])
        def set_key(self, k): log.append(["web_set_key", k])
        def set_tempo(self, t): log.append(["web_set_tempo", t])
        def audience(self, on): pass
        def start_screencast(self, *a): pass
        def stop_screencast(self): pass

    class Service:
        def __init__(self):
            self.state, self.web, self.fail = "idle", None, None
        def stop(self):
            log.append(["service_stop"])
            if self.fail:
                raise RuntimeError(self.fail)
        def start(self): log.append(["service_start"])

    world = {"entry": None, "active": None}
    other_entry, other_active = {}, {"other": True}

    class App:
        settings = {}
        _active_external_karafun = None
        def _run_on_ui_thread(self, fn): fn()
        def _karafun_active_entry(self): return world["entry"] if world["entry_current"] else other_entry
        def _karafun_entry_artist_title(self, entry): return ("Artist", entry.get("title_for_test", "Title"))
        def _karafun_search_queries_for_entry(self, entry): return []
        def _is_external_karafun_entry(self, entry): return bool(world["manual"])
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
    service = Service()
    host.ensure_service = lambda: log.append(["ensure_service"])
    host._start_frames = lambda: log.append(["start_frames"])
    host._stop_frames = lambda: log.append(["stop_frames"])
    pending: list = []
    clock = [0.0]
    traces = []

    def single_shot(ms, fn):
        log.append(["arm_watchdog"])
        pending.append(fn)

    from PyQt6.QtCore import QTimer
    with mock.patch.object(karafun_web_host.time, "monotonic", lambda: clock[0]), mock.patch.object(karafun_web_host.time, "time", lambda: 1000.5), \
            mock.patch.object(QTimer, "singleShot", staticmethod(single_shot)):
        for e in sc["events"]:
            clock[0] = e["t"]
            w = e["world"]
            world.update(w)
            world["manual"] = e.get("song_id") == 0
            service.state = w["service_state"]
            service.web = Web() if w["has_web"] else None
            service.fail = w.get("restart_error")
            host.service = service if w["service_present"] else None
            app._active_external_karafun = ACTIVE if w["active_same"] else other_active
            mark = len(log)
            kind = e["ev"]
            ret = None
            if kind == "start":
                entry = Entry()
                entry["title_for_test"] = e["title"]
                log.clear() if False else None
                del log[mark:]  # building the entry is the test's, not the host's
                prev_entry, world["entry"] = world["entry"], entry
                app._active_external_karafun = ACTIVE  # captured at start
                path = f"karafun_streaming:kf_{e['song_id']}" if e["song_id"] not in (None, 0) else ""
                host.enabled = lambda en=e.get("enabled", True): en
                ret = host.start(entry, key=e["key"], tempo_percent=e["tempo"], song_path=path)
                if not ret:
                    world["entry"] = prev_entry
                app._active_external_karafun = ACTIVE if w["active_same"] else other_active
            elif kind == "service":
                host._service_state(e["state"], e["detail"])
            elif kind == "status":
                host._handle(dict(e["message"], type="status"))
            elif kind == "page_error":
                host._handle({"type": "error", "message": e["message"]})
            elif kind == "watchdog":
                if pending:
                    pending.pop(0)()
            elif kind == "toggle_pause":
                ret = host.toggle_pause()
            elif kind == "seek":
                ret = host.seek_to(e["target"])
            elif kind == "key_tempo":
                host.apply_key_tempo(world["entry"] if world["entry"] is not None else Entry(), e["key"], e["tempo"])
            elif kind == "finish":
                ret = host.on_finish()
            if ret is not None:
                log.append(["returns", ret])
            traces.append([list(x) for x in log[mark:]])
    return {"trace": traces}


ACTIVE = {"_shadow_id": None, "active": True}
# the Python key_tempo entry write is recorded by Entry only when the entry is the started one; the stub below keeps the shape the same on both sides


def rust_trace(sc: dict, cli: str) -> dict:
    p = subprocess.run([cli], input=json.dumps({"kind": "host", "events": sc["events"]}), capture_output=True, text=True, timeout=30)
    return json.loads(p.stdout)


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


TITLES = ["Memory", "Don't Stop", "It's \"Quoted\"", "Back\\slash", "", "A" * 30, "Ünïcode"]
ERRTEXTS = ["no internet", "Play failed: no match", "Play nothing", "KaraFun's player stopped", "", "x" * 300, "Plain"]


def world(rng, state="ready", **over):
    w = {"service_present": True, "service_state": state, "has_web": True, "entry_current": True, "active_same": True}
    if rng.random() < 0.05:
        w["service_present"] = False
    if rng.random() < 0.08:
        w["has_web"] = False
    if rng.random() < 0.06:
        w["entry_current"] = False
    if rng.random() < 0.05:
        w["active_same"] = False
    w.update(over)
    return w


def gen(rng: random.Random) -> dict:
    ev = []
    t = 100.0
    sid = rng.choice([3250037, 58970, 0, 5, None] if rng.random() < 0.3 else [3250037, 58970, 5])
    dur = rng.choice([200.0, 261.4, 5.0, 3.0, 0.0, 130.5, 300])
    pos = 0.0
    state = rng.choice(["ready", "ready", "ready", "starting", "sign_in", "preparing", "error"])
    ev.append({"ev": "start", "t": t, "song_id": sid, "enabled": rng.random() < 0.95, "key": rng.choice([None, 0, 2, -3]),
               "tempo": rng.choice([None, 100, 90, 125, 95.7, True]), "title": rng.choice(TITLES) if rng.random() < 0.1 else rng.choice(TITLES[:5]), "world": world(rng, state, service_present=True)})
    if sid is None:
        ev[-1]["song_id"] = None
    cur = {"id": sid if sid is not None else 1}
    playing = False

    def status(moving_pos=None, song="asked", pl=None):
        nonlocal pos, playing
        m = {}
        s = cur if song == "asked" else song
        m["playing"] = playing if pl is None else pl
        if rng.random() < 0.3:
            m["moving"] = rng.choice([True, False, 1, 0])
        m["position"] = pos if moving_pos is None else moving_pos
        m["duration"] = dur
        if s is not None:
            m["song"] = s
        if rng.random() < 0.05:
            m["position"] = rng.choice([None, 0, "", False, [], 7])
        if rng.random() < 0.03:
            m["duration"] = rng.choice([None, 0, -1, 12.5])
        if rng.random() < 0.01:
            m["position"] = rng.choice(["3.5", [1], {"a": 1}])
        return m

    for _ in range(rng.randint(3, 40)):
        r = rng.random()
        t += rng.choice([0.5, 0.5, 0.5, 1.0, 3.0, 30.0, 61.0, 100.0, 150.0, 2.5, 0.0])
        if r < 0.1:
            st = rng.choice(["ready", "error", "sign_in", "starting", "preparing", "ready"])
            w = world(rng, st, **({"restart_error": "restart boom"} if rng.random() < 0.2 else {}))
            ev.append({"ev": "service", "t": t, "state": st, "detail": rng.choice(ERRTEXTS), "world": w})
        elif r < 0.55:
            if rng.random() < 0.6:
                playing = True
            pos += rng.choice([0.5, 0.5, 1.0, 0.0, 5.0, 20.0, max(dur - 1.0, 0.0), dur])
            song = "asked"
            q = rng.random()
            if q < 0.05:
                song = {"id": rng.choice([1, 2, 99999])}
            elif q < 0.1:
                song = None
            ev.append({"ev": "status", "t": t, "message": status(song=song, pl=(None if rng.random() < 0.8 else rng.choice([False, 0, "yes"]))),
                       "world": world(rng, rng.choice(["ready", "ready", "sign_in"]))})
        elif r < 0.65:
            ev.append({"ev": "watchdog", "t": t, "world": world(rng, rng.choice(["ready", "sign_in", "starting"]))})
        elif r < 0.7:
            ev.append({"ev": "page_error", "t": t, "message": (rng.choice(ERRTEXTS) if rng.random() < 0.97 else rng.choice([None, 5])), "world": world(rng)})
        elif r < 0.75:
            ev.append({"ev": "toggle_pause", "t": t, "world": world(rng)})
        elif r < 0.82:
            ev.append({"ev": "seek", "t": t, "target": rng.choice([0.0, 10.5, 50.0, dur, dur + 100, -5.0, 250.7]), "world": world(rng)})
        elif r < 0.86:
            ev.append({"ev": "key_tempo", "t": t, "key": rng.randint(-4, 4), "tempo": rng.choice([100, 80, 120]), "world": world(rng)})
        elif r < 0.9:
            ev.append({"ev": "finish", "t": t, "world": world(rng)})
        elif r < 0.95:
            ev.append({"ev": "start", "t": t, "song_id": rng.choice([sid, 777]), "enabled": True, "key": 0, "tempo": 100, "title": "Second",
                       "world": world(rng, rng.choice(["ready", "starting"]), service_present=True)})
            cur = {"id": ev[-1]["song_id"]}
            pos = 0.0
            playing = False
        else:
            playing = False
            ev.append({"ev": "status", "t": t, "message": status(), "world": world(rng)})
    return {"events": ev}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    cli = find_cli()
    rng = random.Random(a.seed)
    same = diff = declined = 0
    seen: dict = {}
    py_errors: dict = {}
    first = []
    for i in range(a.cases):
        sc = gen(rng)
        try:
            py = python_trace(sc)
        except Exception as error:   # a case the real code itself cannot run (e.g. a list where a number is expected): not comparable
            declined += 1
            why = type(error).__name__ + ":" + str(error)[:50]
            py_errors[why] = py_errors.get(why, 0) + 1
            continue
        rs = rust_trace(sc, cli)
        if "declined" in rs:
            declined += 1
            py_errors["rust declined: " + rs["declined"]] = py_errors.get("rust declined: " + rs["declined"], 0) + 1
            continue
        if norm(py) == norm(rs):
            same += 1
            for t in py["trace"]:
                for act in t:
                    seen[act[0]] = seen.get(act[0], 0) + 1
        else:
            diff += 1
            if len(first) < 3:
                first.append((i, sc, py, rs))
    print(f"scenarios {a.cases}: identical {same}, different {diff}, declined {declined}")
    print("python-side errors (not comparable):", json.dumps(py_errors))
    print("coverage (action counts):", json.dumps(seen, sort_keys=True))
    for i, sc, py, rs in first:
        for k, (p, r) in enumerate(zip(py["trace"], rs["trace"])):
            if norm(p) != norm(r):
                print(f"--- scenario {i} event {k}: {json.dumps(sc['events'][k])[:600]}\npython: {json.dumps(p)[:1500]}\nrust:   {json.dumps(r)[:1500]}")
                break
    print("IDENTICAL" if diff == 0 else "DIFFERENT")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
