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
METHODS = ("_karafun_wait_until_ready", "_karafun_press_play_control", "_karafun_playback_menu_state", "_karafun_activate_result_for_playback")
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


OSA_TEXTS = ["ALREADY_PLAYING", "MENU_PLAY", "PLAY_NEXT|404.7|202", "PLAY|10|20", "PLAY|1.5|2.5|3", "ERROR|no control", "", "x|y", "FOUND|404|202|3:41", "FOUND|1|2", "FOUND|7.9|8.1|3:00", "FOUND||",
             "DOUBLE_CLICKED", "double_clicked", " DOUBLE_CLICKED ", "PLAYING", "idle", "Idle", "weird", "ERROR|a|b", "NOT_FOUND"]
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

    g = {"time": Clock, "_diag": diag, "KARAFUN_READY_TIMEOUT_S": consts["KARAFUN_READY_TIMEOUT_S"], "KARAFUN_COLD_SETTLE_S": consts["KARAFUN_COLD_SETTLE_S"], "RuntimeError": RuntimeError}
    g.update(ns_extra)
    return g, R


def make_app(cls, rec):
    sc = rec.sc
    R = rec

    class App(cls):
        settings = dict(sc.get("settings", {}))

        def __init__(self):
            self._active_external_karafun = sc.get("active")

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

        def _karafun_raise_results_window(self):
            R.call("raise_results")

        def _karafun_frontmost_app_name(self):
            R.call("frontmost")
            return R.ask("frontmost", lambda r: r.choice(["KaraFun", "SingWS", "Finder", "it's"]))

    return App()


def run(method, scenario, rng=None, answers=None, loaded=None):
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
        else:
            point = scenario.get("point")
            v = app._karafun_activate_result_for_playback(None if point is None else tuple(point), scenario.get("reuse_point", False))
            out["value"] = list(v)
    except RuntimeError as exc:
        out["raised"] = str(exc)
    active = app._active_external_karafun
    entry = active.get("entry") if isinstance(active, dict) else None
    ent = json.loads(json.dumps(entry)) if isinstance(entry, dict) else None
    return {"out": out, "calls": rec.calls, "log": rec.log, "sleeps": rec.sleeps, "final_clock": rec.t, "entry": ent, "answers": rec.answers, "consumed": rec.pos}


def scenario_for(method, r):
    if method == "_karafun_wait_until_ready":
        return {"was_running": r.random() < 0.5, "timeout": r.choice([None, None, 60.0, 5.0, 0.0, 2.7, 1.2])}
    if method in ("_karafun_press_play_control", "_karafun_playback_menu_state"):
        return {}
    entry = {"artist": r.choice(["Sugarcult", "", "Zoë"]), "title": r.choice(["Memory", "Memory", "", "it's"])}
    active = r.choice([{"entry": entry}] * 7 + [None, {"entry": None}, {"x": 1}, "not a dict"])
    return {"active": active, "reuse_point": r.random() < 0.4, "point": r.choice([None, [404, 202], [10, 20], [0, 0]]),
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
