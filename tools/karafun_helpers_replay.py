#!/usr/bin/env python3
"""Replay harness for the five nested helpers of KaraokeApp._automate_karafun_search_and_play's worker: `_schedule_bgm_fade` and `_schedule_early_handoff` (each acts once per
song), `_native_double_click_result`, `_return_to_singws`, and `_watch_karafun_start` (the loop that waits for KaraFun's menu to read Pause, resends a native double-click, and LEARNS
that this Mac needs the native click: it writes a setting). The closures are read out of the method by AST and executed UNCHANGED (the app is not imported) in one wrapper, driven by a
scripted SEQUENCE of calls against a recording fake world with a fake clock. Rust twin: `singws-karafun` helpers.rs; parity: tools/karafun_helpers_parity.py.

  python3 tools/karafun_helpers_replay.py [--seeds N]
"""
import argparse
import ast
import builtins
import json
import random
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("_schedule_bgm_fade", "_native_double_click_result", "_return_to_singws", "_watch_karafun_start", "_schedule_early_handoff")
FREE = ("_diag", "_run_session_script", "_session_is_current", "active_session", "entry", "handoff_dispatched", "self", "threading", "time")


def load():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_automate_karafun_search_and_play":
                    method = sub
    assert method is not None, "the Python moved"
    worker = next(s for s in method.body if isinstance(s, ast.FunctionDef) and s.name == "_worker")
    defs = {s.name: s for s in worker.body if isinstance(s, ast.FunctionDef) and s.name in NAMES}
    assert set(defs) == set(NAMES), "a helper moved or was renamed"
    stores, loads = set(), set()
    for d in defs.values():
        for n in ast.walk(d):
            if isinstance(n, ast.Name):
                (stores if isinstance(n.ctx, (ast.Store, ast.Del)) else loads).add(n.id)
            elif isinstance(n, ast.FunctionDef):
                stores.add(n.name)
            elif isinstance(n, ast.arg):
                stores.add(n.arg)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                stores.add(n.name)
    free = sorted(x for x in loads - stores if not hasattr(builtins, x))
    assert free == sorted(FREE), f"the helpers' outside names changed: {free}"
    outer = ast.parse(f"def _helpers({', '.join(FREE)}):\n    bgm_fade_scheduled = False\n    handoff_scheduled = False\n").body[0]
    outer.body += [defs[n] for n in NAMES]
    outer.body.append(ast.parse("return {'bgm': _schedule_bgm_fade, 'dbl': _native_double_click_result, 'return': _return_to_singws, 'watch': _watch_karafun_start, 'early': _schedule_early_handoff}").body[0])
    mod = ast.Module(body=[outer], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {}
    exec(compile(mod, "helpers", "exec"), ns)
    return ns["_helpers"]


class Recorder:
    def __init__(self, scenario, rng=None, answers=None):
        self.sc = scenario
        self.rng = rng
        self.answers = answers if answers is not None else []
        self.pos = 0
        self.calls, self.log, self.sleeps = [], [], []
        self.t = 100.0

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


def build_world(rec):
    sc = rec.sc
    R = rec

    class FakeSelf:
        settings = dict(sc["settings"])

        def _karafun_playback_menu_state(self):
            R.call("menu_state")
            return R.ask("menu_state", lambda r: "PLAYING" if r.random() < sc["play_p"] else r.choice(["IDLE", "IDLE", "IDLE", "UNKNOWN"]))

        def _karafun_raise_results_window(self):
            R.call("raise_results")

        def _karafun_frontmost_app_name(self):
            R.call("frontmost")
            return R.ask("frontmost", lambda r: r.choice(["KaraFun", "SingWS", "it's", "Zoë"]))

        def _macos_native_double_click(self, x, y):
            R.call("native_double_click", x, y)

        def _karafun_click_point_script(self, point):
            R.call("click_point_script", list(point))
            return "click-script"

        def _run_on_ui_thread(self, fn):
            R.call("ui_thread")
            fn()

        def _schedule_save_settings(self):
            R.call("save_settings")

        def _fade_bg_for_external_karafun(self):
            R.call("fade_bg")

        def _handoff_show_screen_to_karafun(self):
            R.call("handoff_show_screen")

        def _activate_host_during_karafun_capture(self, session):
            R.call("activate_host", dict(session))

    class Clock:
        @staticmethod
        def monotonic():
            return R.t

        @staticmethod
        def sleep(s):
            R.sleeps.append(s)
            R.t += s

    class Thread:
        def __init__(self, target=None, daemon=None, name=None):
            self._t, self._n = target, name

        def start(self):
            R.call("thread", thread_name=self._n)
            self._t()

    class Event:
        @staticmethod
        def set():
            R.call("handoff_dispatched_set")

    def session_current():
        return R.ask("session_current", lambda r: r.random() < 0.97)

    def run_session_script(script, **kw):
        R.call("session_script", script, **kw)
        return (True, "", "")

    return dict(_diag=lambda m: R.log.append(m), _run_session_script=run_session_script, _session_is_current=session_current, active_session={"id": 1, "host_focus_restored": True},
                entry=dict(sc["entry"]), handoff_dispatched=Event, self=FakeSelf(), threading=types.SimpleNamespace(Thread=Thread), time=Clock)


def run(helpers, scenario, rng=None, answers=None):
    rec = Recorder(scenario, rng, answers)
    world = build_world(rec)
    fns = helpers(**world)
    for op in scenario["ops"]:
        fn = fns[op[0]]
        fn(*op[1:])
    return {"calls": rec.calls, "log": rec.log, "sleeps": rec.sleeps, "entry": world["entry"], "settings": world["self"].settings, "session": world["active_session"],
            "final_clock": rec.t, "answers": rec.answers}


def random_scenario(r):
    entry = {}
    for k in ("karafun_native_retry_sent", "karafun_queued_for_adjustment", "karafun_native_first", "karafun_native_reclick", "karafun_ax_retry_sent"):
        if r.random() < 0.4:
            entry[k] = True
    if r.random() < 0.8:
        entry["karafun_result_activation_point"] = r.choice([[404, 202], [10.7, 20.2], [0, 0], None, []])
    if r.random() < 0.2:
        entry["karafun_display_started_at"] = 5.0
    ops = []
    for _ in range(r.randint(1, 6)):
        ops.append(r.choice([["bgm", "r1"], ["bgm", "r2"], ["early", "e1"], ["early", "e2"], ["dbl"], ["return"], ["watch", [404, 202]], ["watch", [404, 202]]]))
    return {"settings": {"karafun_native_double_click": r.random() < 0.3}, "entry": entry, "ops": ops, "play_p": r.choice([0.02, 0.05, 0.1, 0.3, 0.6])}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=1000)
    args = ap.parse_args(argv)
    helpers = load()
    stats = {}
    for seed in range(args.seeds):
        r = random.Random(seed)
        out = run(helpers, random_scenario(r), r)
        for c in out["calls"]:
            stats[c[0]] = stats.get(c[0], 0) + 1
        for l in out["log"]:
            stats[l[:40]] = stats.get(l[:40], 0) + 1
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
