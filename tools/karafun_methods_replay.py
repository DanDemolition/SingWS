#!/usr/bin/env python3
"""Replay harness for four KaraFun automation METHODS of the real 0.2.18.1.py, run UNCHANGED (their definitions are copied out of the class by AST into a small class whose other
methods are recording fakes): `_karafun_wait_until_ready`, `_karafun_press_play_control`, `_karafun_playback_menu_state`, `_karafun_activate_result_for_playback`.
The world is scripted: every AppleScript run, click and state read takes its answer from a list (drawn from an RNG the first time), and time is a fake clock. Recorded: the
return value or the raised error, every call with its arguments, every log line, every sleep and the entry's final fields. Rust twin: methods.rs; parity: karafun_methods_parity.py."""
import ast
import json
import random
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METHODS = ("_karafun_wait_until_ready", "_karafun_press_play_control", "_karafun_playback_menu_state", "_karafun_activate_result_for_playback", "_karafun_seek_to",
           "_karafun_apply_key_tempo")
CONSTS = ("KARAFUN_READY_TIMEOUT_S", "KARAFUN_COLD_SETTLE_S")


def load():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    consts, found = {}, {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in CONSTS for t in node.targets):
            consts[node.targets[0].id] = ast.literal_eval(node.value)
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in METHODS:
                    sub.decorator_list = []
                    found[sub.name] = sub
    assert set(found) == set(METHODS) and set(consts) == set(CONSTS), "a method or constant moved"
    # the default argument `timeout: float = KARAFUN_READY_TIMEOUT_S` is evaluated in the class body: keep the real expression
    cls = ast.ClassDef(name="_App", bases=[], keywords=[], decorator_list=[], body=[found[m] for m in METHODS], type_params=[])
    mod = ast.Module(body=[cls], type_ignores=[])
    ast.fix_missing_locations(mod)
    return mod, consts


class Rec:
    def __init__(self, sc, rng=None, answers=None):
        self.sc, self.rng = sc, rng
        self.answers = answers if answers is not None else []
        self.pos, self.calls, self.log, self.sleeps, self.t = 0, [], [], [], 100.0

    def ask(self, kind, make):
        if self.pos < len(self.answers):
            a = self.answers[self.pos]
            assert a["kind"] == kind, f"order differs: expected {kind} got {a['kind']}"
        else:
            a = {"kind": kind, "v": make(self.rng)}
            self.answers.append(a)
        self.pos += 1
        return a["v"]

    def call(self, name, *args, **kw):
        self.calls.append([name, list(args), {k: kw[k] for k in sorted(kw)}])


OSA_TEXTS = ["FOUND|\uff14\uff10\uff14|202|3:41", "FOUND|404|\uff12\uff10\uff12|3:41", "FOUND|\u0663|\u0664", "ALREADY_PLAYING", "MENU_PLAY", "PLAY_NEXT|404.7|202", "PLAY|10|20", "PLAY|1.5|2.5|3", "ERROR|no control", "", "x|y", "FOUND|404|202|3:41", "FOUND|1|2", "FOUND|7.9|8.1|3:00", "FOUND||",
             "DOUBLE_CLICKED", "double_clicked", " DOUBLE_CLICKED ", "PLAYING", "idle", "Idle", "weird", "ERROR|a|b", "NOT_FOUND",
             "ADJUSTED|2|3", "ADJUSTED|0|0", "ADJUSTED|1|1|x", "ADJUSTED|5|", "ADJUSTED|a|1", "ADJUSTED|12|7", "adjusted|1|1", "SEEKED|3", "SEEKED|0", "SEEKED|12", "SEEKED|", "seeked|1",
             "SEEKED|7|extra", "SEEKED|60", " SEEKED|4",
             "ADJUSTED|123456789012345678|1", "ADJUSTED|1234567890123456789|1", "ADJUSTED|1|123456789012345678", "ADJUSTED|1|1234567890123456789", "ADJUSTED|-5|3", "ADJUSTED|1|-3",
             "SEEKED|123456789012345678", "SEEKED|1234567890123456789", "ADJUSTED|\u0663|1", "ADJUSTED|1|\u0663", "SEEKED|\u0663", "ADJUSTED|1_0|1", "ADJUSTED| 1|1", "SEEKED|1_0",
             "ADJUSTED|2|3", "ADJUSTED|1|1", "ADJUSTED|-1|1", "SEEKED|5", "SEEKED|-1",
             "FOUND|\uff14\uff10\uff14|202|3:41", "FOUND|4_04|2_02|3:41", "FOUND|404.5|\uff12\uff10\uff12|3:41", "TITLE_ONLY|1_0|5|2:00", "FIRST|404|2_02|1:00", "FOUND| 404 | 202 |3:41",
             "PLAY|\uff11|2", "PLAY|1_0|2", "PLAY|1|2_0"]
OSA_ERRS = ["", "script failed", "timeout", "AX -1719", "boom"]


def build(rec, consts, ns_extra):
    sc = rec.sc
    R = rec

    class Clock:
        @staticmethod
        def monotonic():
            return R.t

        @staticmethod
        def sleep(s):
            R.sleeps.append(s)
            R.t += s

    def diag(msg):
        R.log.append(msg)

    class Thread:
        def __init__(self, target=None, daemon=None, name=None):
            self._t = target
            self._n = name

        def start(self):
            R.call("thread", thread_name=self._n)
            self._t()

    import re as _re
    g = {"re": _re, "threading": types.SimpleNamespace(Thread=Thread), "time": Clock, "_diag": diag, "KARAFUN_READY_TIMEOUT_S": consts["KARAFUN_READY_TIMEOUT_S"], "KARAFUN_COLD_SETTLE_S": consts["KARAFUN_COLD_SETTLE_S"], "RuntimeError": RuntimeError}
    g.update(ns_extra)
    return g, R


def make_app(cls, rec):
    sc = rec.sc
    R = rec

    class App(cls):
        settings = dict(sc.get("settings", {}))

        def __init__(self):
            self._active_external_karafun = sc.get("active")
            if "rearm_token" in sc:
                self._karafun_duration_rearm = (sc["rearm_token"], lambda: R.call("rearm"))
            self._karafun_seek_running = bool(sc.get("seek_running", False))

        def _karafun_press_play_script(self):
            return "press-play-script"

        def _karafun_playback_menu_state_script(self):
            return ["menu-state-script"]

        def _karafun_search_script(self, **kw):
            return f"resolve:{kw['safe_title']}:{kw['safe_artist']}:{kw['require_exact_title']}:{kw['resolve_only']}:{kw['query']}"

        def _karafun_double_click_script(self, x, y):
            return f"dbl:{x}:{y}"

        def _karafun_entry_artist_title(self, entry):
            return entry.get("artist", ""), entry.get("title", "")

        def _karafun_main_window_state(self):
            R.call("main_window_state")
            return R.ask("window_state", lambda r: r.choice(["READY", "READY", "STARTING", "NO_WINDOW", "UNKNOWN", "ERROR"]))

        def _run_karafun_applescript_sync(self, script, timeout=None):
            R.call("osa", script, timeout=timeout)
            v = R.ask("osa", lambda r: [r.random() < 0.8, r.choice(OSA_TEXTS), r.choice(OSA_ERRS)])
            if isinstance(script, str) and script.startswith("resolve:") and sc.get("change_session_on_resolve"):
                self._active_external_karafun = {"entry": {}}
            return (bool(v[0]), v[1], v[2])

        def _macos_native_mouse_click(self, x, y, clicks=1):
            R.call("mouse_click", x, y, clicks=clicks)
            return R.ask("mouse_click", lambda r: r.random() < 0.85)

        def _macos_native_double_click(self, x, y):
            R.call("native_double_click", x, y)
            return R.ask("double_click", lambda r: r.random() < 0.7)

        _karafun_seek_running = False

        def _karafun_active_entry(self):
            R.active_calls = getattr(R, "active_calls", 0) + 1
            R.call("active_entry")
            if sc.get("no_entry"):
                return None
            if R.active_calls >= 2 and sc.get("entry_changes"):
                return {}
            return sc["entry"]

        def _karafun_display_times(self):
            R.call("display_times")
            t = sc.get("times")
            return None if t is None else tuple(t)

        def _karafun_seek_script(self, count, item):
            return f"seek:{count}:{item}"

        def _karafun_apply_key_tempo_script(self, key_delta, tempo_delta, key_item, tempo_item):
            return f"kt:{key_delta}:{tempo_delta}:{key_item}:{tempo_item}"

        def _run_on_ui_thread(self, fn):
            R.call("ui_thread")
            fn()

        def _transport_shadow(self):
            return None

        def _karafun_raise_results_window(self):
            R.call("raise_results")

        def _karafun_frontmost_app_name(self):
            R.call("frontmost")
            return R.ask("frontmost", lambda r: r.choice(["KaraFun", "SingWS", "Finder", "it's"]))

    return App()


def run(method, scenario, rng=None, answers=None, loaded=None):
    import copy
    scenario = copy.deepcopy(scenario)   # the methods mutate the entry: the caller keeps the original to hand to the Rust twin
    mod, consts = loaded or load()
    rec = Rec(scenario, rng, answers)
    g, R = build(rec, consts, {})
    exec(compile(mod, "methods", "exec"), g)
    app = make_app(g["_App"], rec)
    out = {"raised": None, "value": None}
    try:
        if method == "_karafun_wait_until_ready":
            sess = lambda: R.ask("session_current", lambda r: r.random() < 0.93)
            kw = {"was_running": scenario["was_running"], "session_is_current": sess}
            if scenario.get("timeout") is not None:
                kw["timeout"] = scenario["timeout"]
            out["value"] = app._karafun_wait_until_ready(**kw)
        elif method == "_karafun_press_play_control":
            out["value"] = list(app._karafun_press_play_control())
        elif method == "_karafun_playback_menu_state":
            out["value"] = app._karafun_playback_menu_state()
        elif method == "_karafun_seek_to":
            out["value"] = app._karafun_seek_to(scenario["target"])
        elif method == "_karafun_apply_key_tempo":
            out["value"] = list(app._karafun_apply_key_tempo(scenario["entry"], scenario["target_key"], scenario["target_tempo"]))
        else:
            point = scenario.get("point")
            v = app._karafun_activate_result_for_playback(None if point is None else tuple(point), scenario.get("reuse_point", False))
            out["value"] = list(v)
    except RuntimeError as exc:
        out["raised"] = str(exc)
    active = app._active_external_karafun
    entry = active.get("entry") if isinstance(active, dict) else None
    if method in ("_karafun_seek_to", "_karafun_apply_key_tempo"):
        entry = scenario["entry"]
    ent = json.loads(json.dumps(entry)) if isinstance(entry, dict) else None
    flags = {"seek_running": bool(app._karafun_seek_running)} if method == "_karafun_seek_to" else {}
    return {"out": out, "flags": flags, "calls": rec.calls, "log": rec.log, "sleeps": rec.sleeps, "final_clock": rec.t, "entry": ent, "answers": rec.answers, "consumed": rec.pos}


def scenario_for(method, r):
    if method == "_karafun_wait_until_ready":
        return {"was_running": r.random() < 0.5, "timeout": r.choice([None, None, 60.0, 5.0, 0.0, 2.7, 1.2])}
    if method in ("_karafun_press_play_control", "_karafun_playback_menu_state"):
        return {}
    if method == "_karafun_seek_to":
        entry = {}
        if r.random() < 0.6:
            entry["karafun_display_started_at"] = r.choice([50.0, 99.0, 0.0, 120.5])
        if r.random() < 0.3:
            entry["karafun_paused_at"] = r.choice([90.0, 0.0, 130.0])
        if r.random() < 0.4:
            entry["karafun_seek_offset_s"] = r.choice([0.0, 30.0, -20.0, None])
        if r.random() < 0.5:
            entry["karafun_completion_monitor"] = r.choice(["tok", "other"])
        elapsed = r.choice([0.0, 12.5, 100.0, 249.9, 300.0, 5.0, 5.5, 10.0, 14.5, 15.5, 4.5, 620.0])
        duration = r.choice([263.0, 180.0, 20.0, 10.0, 600.0, 3000.0, 9.0, 8.5, 8.0, 7.0])
        sc = {"entry": entry, "times": r.choice([None] + [[elapsed, duration]] * 9), "target": r.choice([0.0, 33.3, 180.0, 255.0, 262.9, 270.0, 1e4, -5.0, 610.0, 5.5, 8.0, 0.5, 1.0, 12.0, 15.5, 14.9, 15.0, 25.0, 35.0, 45.0, 2.5, elapsed + 5.0, elapsed - 25.0]),
              "no_entry": r.random() < 0.05, "entry_changes": r.random() < 0.1, "seek_running": r.random() < 0.05}
        if r.random() < 0.7:
            sc["rearm_token"] = r.choice(["tok", "other"])
        return sc
    if method == "_karafun_apply_key_tempo":
        return {"entry": r.choice([{}, {"karafun_key_current": 2, "karafun_tempo_current": 90}, {"karafun_key_current": None, "karafun_tempo_current": 0}, {"karafun_key_current": -3, "karafun_tempo_current": 110}]),
                "target_key": r.choice([0, 2, -2, 5, -7, 12, 1, -1, 3, -4]), "target_tempo": r.choice([100, 90, 110, 75, 125, 100, 101, 99, 91, 89])}
    entry = {"artist": r.choice(["Sugarcult", "", "Zoë"]), "title": r.choice(["Memory", "Memory", "", "it's"])}
    active = r.choice([{"entry": entry}] * 7 + [None, {"entry": None}, {"x": 1}, "not a dict"])
    return {"active": active, "reuse_point": r.random() < 0.4, "point": r.choice([None, None, None, [404, 202], [10, 20], [0, 0]]),
            "settings": {"karafun_native_double_click": r.random() < 0.4}, "change_session_on_resolve": r.random() < 0.1}


def main(argv=None):
    loaded = load()
    stats = {}
    for method in METHODS:
        ok = 0
        for seed in range(300):
            r = random.Random(seed)
            out = run(method, scenario_for(method, r), r, loaded=loaded)
            ok += 1
        stats[method] = ok
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
