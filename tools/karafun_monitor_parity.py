#!/usr/bin/env python3
"""Parity: the completion-monitor decision core of the real KaraokeApp._start_karafun_completion_monitor (0.2.18.1.py) vs the Rust `singws-karafun` monitor.

The REAL Python method is executed (read by AST, the app is not imported) in the same kind of replay the test suite uses (MonitorReplay in
test_karafun_monitor_safety.py): scripted probe answers, a fake clock that advances by the probe time and the sleeps. The Rust machine gets the same events.
Compared: every log line, the calls the monitor makes (recovery Play press, operator warnings, completion), the entry fields it updates, how many key/tempo
reads it starts, the number of probes and the final clock.

  python3 tools/karafun_monitor_parity.py [--seeds N] [--binary PATH]
"""
import argparse
import ast
import json
import os
import random
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))


def load():
    import karafun_script_parity as par
    par.load_python()
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    monitor = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_start_karafun_completion_monitor")
    builder = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_karafun_monitor_probe_script")
    builder.decorator_list = []
    ns = {}
    exec(compile(ast.Module(body=[builder], type_ignores=[]), "monitor-script", "exec"), ns)
    code = compile(ast.Module(body=[monitor], type_ignores=[]), "monitor-replay", "exec")
    return code, ns["_karafun_monitor_probe_script"], par.PY["duration"]


class Replay:
    def __init__(self, loaded, c):
        code, script_builder, clock_seconds = loaded
        self.now = 100.0
        self.events = iter(c["events"])
        self.messages, self.workers, self.scripts = [], [], []
        self.status_calls, self.kt_started = [], 0
        entry = {"title": c["title"], "duration": c["duration"], "duration_estimated": c["duration_estimated"], "karafun_playback_assumed": c["playback_assumed"],
                 "karafun_handoff_timed_out_before_play": c["handoff_timed_out_before_play"]}
        if c["controlled_start"]:
            entry["karafun_playback_clock_started_at"] = self.now - c["started_offset"]
        if c["seek_offset"]:
            entry["karafun_seek_offset_s"] = c["seek_offset"]
        self.entry = entry
        self.active = {"entry": entry}
        press = mock.Mock(return_value=(c["press_ok"], c["press_error"]))
        self.handoff_left = c.get("handoff_polls", 0)
        replay = self

        class Host(types.SimpleNamespace):
            @property
            def _karafun_handoff_in_progress(self):
                if replay.handoff_left > 0:
                    replay.handoff_left -= 1
                    return True
                return False
        self.host = Host(
            _active_external_karafun=self.active, _karafun_capture_session=c["capture_session"], _run_karafun_applescript_sync=self.probe,
            _karafun_clock_seconds=clock_seconds, _karafun_monitor_probe_script=script_builder, _run_on_ui_thread=lambda fn: fn(),
            _karafun_activate_result_for_playback=mock.Mock(return_value=(True, "")), _karafun_press_play_control=press,
            _finish_external_karafun_playback=mock.Mock(),
            _karafun_fallback_remaining=lambda duration, elapsed, offset=0.0: int(duration) - int(elapsed) - int(float(offset or 0.0)),
            _set_karafun_entry_status=lambda e, status, message="", notify=False, level="info": self.status_calls.append(message),
            _karafun_display_times=lambda: tuple(c["display_times"]) if c["display_times"] else None,
            KARAFUN_PLAYBACK_RECOVERY_DELAY_S=12, KARAFUN_HANDOFF_TIMEOUT_RECOVERY_DELAY_S=6, KARAFUN_PLAYBACK_ALERT_DELAY_S=40)
        self.press = press

        def thread(target, **kw):
            return types.SimpleNamespace(start=lambda: self.workers.append(target))
        ns = {"uuid": types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(hex="monitor-token")),
              "time": types.SimpleNamespace(monotonic=lambda: self.now, sleep=lambda s: setattr(self, "now", self.now + s)),
              "threading": types.SimpleNamespace(Thread=thread, Timer=lambda s, cb: types.SimpleNamespace(start=lambda: None, cancel=lambda: None)),
              "_diag": self.messages.append, "karafun_confirmed_readback": lambda *a: None}
        exec(code, ns)
        ns["_start_karafun_completion_monitor"](self.host, self.entry)

    def probe(self, script, **kw):
        try:
            elapsed, raw = next(self.events)
        except StopIteration:
            self.host._active_external_karafun = None
            return False, "", "replay finished"
        self.now += elapsed
        return (False, "", "timeout") if raw is None else (True, raw, "")

    def run(self):
        self.workers[0]()
        calls = []
        return {"log": self.messages, "press": self.press.call_count, "status": self.status_calls, "complete": self.host._finish_external_karafun_playback.call_count,
                "now": self.now, "kt_reads": len(self.workers) - 1,
                "entry": {"display_started_at": self.entry.get("karafun_display_started_at"), "display_duration": self.entry.get("karafun_display_duration"),
                          "last_playing_ts": self.entry.get("karafun_last_playing_ts"), "paused_at": self.entry.get("karafun_paused_at")}}


ELAPSED = [0.5, 1, 2, 4, 4.5, 5, 5.1, 10, 12, 13, 15, 20, 30, 41, 45, 100, 250, 263, 300, 400]
SIMPLE = ["", None, "STATE|PLAYING", "STATE|IDLE", "STATE|ENDED", "STATE|IDLE\nSTATE|PLAYING", "STATE|PLAYING\nSTATE|ENDED", "abc", "1:2:3", "\u0085", "STATE|IDLE\r\n", "  STATE|PLAYING  \n\n",
          "STATE|ENDED\nSTATE|PLAYING", "STATE|PLAYING\rSTATE|IDLE", "STATE|PLAYING\x0bSTATE|IDLE", "STATE|PLAYING\x0cSTATE|IDLE", "STATE|PLAYING\x1cSTATE|IDLE",
          "STATE|PLAYING\x1dSTATE|IDLE", "STATE|PLAYING\x1eSTATE|IDLE", "STATE|PLAYING\u2028STATE|IDLE", "STATE|PLAYING\u2029STATE|IDLE", "STATE|PLAYING\x85STATE|IDLE",
          "STATE|IDLE\r", "a\r\rSTATE|IDLE", "STATE|IDLE\r\nSTATE|PLAYING\r\n", "STATE|IDLE\n\r\nSTATE|ENDED", "\r\nSTATE|IDLE\r\n\r\n", "STATE|IDLE\n"]
TITLES = ["Mr. Highway's Thinking About the End", "Memory", 'Say "hi"', "Zoë", "", "back\\slash"]


def clock_raw(r, total, current, state):
    lines = [f"{current // 60}:{current % 60:02d}", f"{total // 60}:{total % 60:02d}"]
    if state:
        lines.insert(r.randint(0, 2), state)
    return "\n".join(lines)


def base(**kw):
    c = {"events": [], "title": "Memory", "duration": 263, "duration_estimated": False, "playback_assumed": True, "handoff_timed_out_before_play": False, "controlled_start": False,
         "started_offset": 0, "capture_session": False, "seek_offset": 0, "display_times": None, "press_ok": True, "press_error": "", "handoff_polls": 0,
         "recovery_delay": 12, "handoff_recovery_delay": 6, "alert_delay": 40}
    c.update(kw)
    return c


def scripted():
    """Exact boundaries the random scenarios hit only by luck: each is a hand-built run around one threshold."""
    out = []
    # a display origin of exactly 0.0 (now == current), then a pause tracking pass through the falsy value
    out.append(base(events=[(0, "1:39\n4:23"), (0, "1:41\n4:23"), (0, "STATE|IDLE")]))
    out.append(base(events=[(3, "0:03\n4:23"), (0, "0:05\n4:23"), (0, "STATE|IDLE"), (0, "1:45\n4:23"), (0, "1:47\n4:23\nSTATE|PLAYING")]))
    # clock values of exactly 1 and 2 (the confirmation at current > 2 and the later remaining > 5 path), a clock of 1, a total of 1
    out.append(base(events=[(1, "0:01\n4:23"), (1, "0:02\n4:23"), (1, "STATE|IDLE")]))
    out.append(base(events=[(1, "0:01\n0:01"), (1, "0:01\n0:01")], duration=0))
    out.append(base(events=[(1, "0:01\n4:23"), (1, "0:03\n4:23"), (1, "STATE|IDLE")]))
    # remaining of exactly 6 and 5 from the clock, then idle (clock_near_end boundary)
    for cur in (256, 257, 258, 259):
        a, b = cur - 3, cur
        out.append(base(events=[(1, f"{a // 60}:{a % 60:02d}\n4:23"), (1, f"{b // 60}:{b % 60:02d}\n4:23"), (2, "STATE|IDLE"), (2, "STATE|IDLE")], duration=0))
    # expected end: fallback_remaining of exactly 5 and 6 at an idle poll (elapsed since the start 257.x -> 6, 258.x -> 5)
    for e in (245.0, 245.5, 245.99, 246.0, 246.5, 246.99, 247.0):
        out.append(base(events=[(5, "STATE|PLAYING"), (5, "STATE|PLAYING"), (e, "STATE|IDLE")]))
    # age of exactly 8.0 at an explicit end of a capture session
    out.append(base(events=[(3, "STATE|PLAYING"), (2, "STATE|PLAYING"), (1, "STATE|ENDED")], capture_session=True))
    out.append(base(events=[(3, "STATE|PLAYING"), (2, "STATE|PLAYING"), (1.5, "STATE|ENDED")], capture_session=True))
    # the key/tempo read: the 60 s interval exactly (now - last read == 60) and the 35 s window exactly
    for dt in ([0, 35], [0, 36], [0, 34], [5, 41], [5, 40]):
        out.append(base(events=[(8, "STATE|PLAYING"), (11, "STATE|PLAYING"), (1, "STATE|PLAYING")], display_times=dt))
        out.append(base(events=[(8, "STATE|PLAYING"), (10.5, "STATE|PLAYING"), (1, "STATE|PLAYING")], display_times=dt))
    # the clock stays fresh for exactly 20 s: an idle poll 19.0 / 19.5 / 18.9 s after the last clock progress (diff 20.0 / 20.5 / 19.9)
    for e in (18.9, 19.0, 19.5):
        out.append(base(events=[(1, "4:15\n4:23"), (1, "4:18\n4:23"), (e, "STATE|IDLE")], duration=0))
    # confirmation through "remaining above 5" with tiny clocks (current <= 2, so the current > 2 path does not fire): totals giving remaining 4, 5, 6, 7
    for total in (6, 7, 8, 9):
        out.append(base(events=[(1, "0:01\n0:0%d" % total), (1, "0:02\n0:0%d" % total), (1, "STATE|IDLE")], duration=0))
    # an early confirmation with the clock start in the future: launch overhead below 1 s is shown as 0.0 (negative is clamped)
    for off in (-3, -2.5, -4, -2):
        out.append(base(events=[(1, "STATE|PLAYING"), (1, "STATE|PLAYING")], controlled_start=True, started_offset=off))
    # a reversed clock pair (current above total) that repeats and grows: never accepted as progress
    out.append(base(events=[(1, "5:00\n4:00"), (1, "5:05\n4:00"), (1, "5:09\n4:00"), (1, "STATE|IDLE")], duration=0))
    # a duration of 1 second (the watchdog exists for any duration above 0)
    out.append(base(events=[(1, "STATE|PLAYING"), (1, "STATE|PLAYING"), (1, "STATE|IDLE")], duration=1))
    # a clock start in the future by 3 s and by 5 s (the guard allows now + 5), by 5.5 s (reset), and at/below zero
    for off in (-3, -5, -5.5, 100, 99.5, 150):
        out.append(base(events=[(10, "STATE|PLAYING"), (5, "STATE|PLAYING"), (5, "STATE|IDLE")], controlled_start=True, started_offset=off))
    return out


SCRIPTED = scripted()


def scenario(seed):
    if seed < len(SCRIPTED):
        return SCRIPTED[seed]
    r = random.Random(seed)
    total = r.choice([245, 263, 120, 300])
    clock_mode = r.random() < 0.5
    events, cur = [], r.randint(0, 5)
    for _ in range(r.randint(1, 12)):
        el = r.choice(ELAPSED)
        if clock_mode and r.random() < 0.85:
            cur = min(total, cur + (int(el) if r.random() < 0.8 else r.randint(-3, 3)))
            cur = max(0, cur)
            if r.random() < 0.06:
                raw = clock_raw(r, max(1, cur - r.randint(1, 5)), cur, "")      # current above total: not a pair
            elif r.random() < 0.06:
                raw = clock_raw(r, r.choice([1, 2, 3]), r.choice([1, 2, 3]), r.choice(["", "STATE|PLAYING"]))   # tiny clocks
            else:
                raw = clock_raw(r, total if r.random() < 0.95 else total + 7, cur, r.choice(["", "STATE|PLAYING", "STATE|IDLE", "STATE|PLAYING", "STATE|ENDED"]))
        else:
            raw = r.choice(SIMPLE)
        events.append((el, raw))
    return {"events": events, "title": r.choice(TITLES), "duration": r.choice([0, 1, 30, 100, total, total, 245]), "duration_estimated": r.random() < 0.2,
            "playback_assumed": r.random() < 0.8, "handoff_timed_out_before_play": r.random() < 0.5, "controlled_start": r.random() < 0.5,
            "started_offset": r.choice([-10, -5, -3, 0, 3, 20, 100, 150]), "handoff_polls": r.choice([0, 0, 0, 1, 3]), "capture_session": r.random() < 0.4, "seek_offset": r.choice([0, 0, 150, -60, 30.5]),
            "display_times": r.choice([None, None, [0, 240], [100, 240], [210, 240]]), "press_ok": r.random() < 0.7, "press_error": r.choice(["", "no control", "it's broken"]),
            "recovery_delay": 12, "handoff_recovery_delay": 6, "alert_delay": 40}


def rust_request(c):
    # the app reads `float(entry.get("karafun_playback_clock_started_at") or ... or now)`: a value of exactly 0.0 is "not set", and the controlled-start
    # test later is the truthiness of the same value
    value = 100.0 - c["started_offset"] if c["controlled_start"] else 0.0
    controlled = bool(value)
    return {"op": "monitor", "cfg": dict(c, controlled_start=controlled), "now": 100.0, "started": value if controlled else 100.0, "seek_offset": c["seek_offset"],
            "events": [{"elapsed": e, "raw": raw} for e, raw in c["events"]], "display_times": c["display_times"], "press_ok": c["press_ok"], "press_error": c["press_error"],
            "handoff_polls": c.get("handoff_polls", 0)}


def run_rust(binary, batch):
    proc = subprocess.run([str(binary)], input=json.dumps({"batch": batch}).encode(), capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode()[-400:])
    return json.loads(proc.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=4000)
    ap.add_argument("--binary", default=os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun"))
    args = ap.parse_args(argv)
    loaded = load()
    cs = [scenario(s) for s in range(args.seeds)]
    got = run_rust(args.binary, [rust_request(c) for c in cs])
    diffs, declined, completed = [], 0, 0
    seen = {"press": 0, "press failed": 0, "warnings": 0, "kt reads": 0, "slow probes": 0, "paused": 0, "explicit end": 0, "clock end": 0, "expected end": 0}
    for c, g in zip(cs, got):
        if "decline" in g:
            declined += 1
            continue
        want = Replay(loaded, c).run()
        mine = {"log": g["log"], "press": g["calls"].count("press_play"), "status": [x[len("status:"):] for x in g["calls"] if x.startswith("status:")],
                "complete": g["calls"].count("complete"), "now": g["now"], "kt_reads": g["kt_reads"], "entry": g["entry"]}
        completed += want["complete"]
        seen["press"] += want["press"] > 0
        seen["press failed"] += any("recovery play press failed" in l for l in want["log"])
        seen["warnings"] += bool(want["status"])
        seen["kt reads"] += want["kt_reads"] > 0
        seen["slow probes"] += any("slow playback probe" in l for l in want["log"])
        seen["paused"] += want["entry"]["paused_at"] is not None
        for key, needle in (("explicit end", "karaFun_explicit_end"), ("clock end", "karaFun_idle_clock_end"), ("expected end", "karaFun_idle_expected_end")):
            seen[key] += any(needle in l for l in want["log"])
        if mine != want:
            keys = [k for k in want if mine.get(k) != want[k]]
            diffs.append((c, keys, {k: (mine.get(k), want[k]) for k in keys[:2]}))
    print(f"scenarios={len(cs)} declined={declined} completions={completed}; scenarios that reached: " + ", ".join(f"{k}={v}" for k, v in seen.items()))
    for c, keys, detail in diffs[:4]:
        print(f" - differs in {keys}: {json.dumps(c, ensure_ascii=False)[:300]}\n   {str(detail)[:700]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
