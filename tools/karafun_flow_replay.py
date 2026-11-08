#!/usr/bin/env python3
"""Replay harness for the part of KaraokeApp._automate_karafun_search_and_play AFTER the search ladder (activation, hand-off ordering, play, verify, key/tempo).

The 32 statements from the `_require_current_session()` before "activating KaraFun result" to `result = {"ok": True, ...}` are read out of the method by AST and executed
UNCHANGED inside a wrapper function (the app is not imported), against a recording fake world with SCRIPTED answers and a fake clock. What it records:
every call the flow makes (name + arguments), every log line, every sleep, the entry's final fields, and the result dict. The Rust twin (`singws-karafun` flow.rs) runs the
same scenario with the same answers; tools/karafun_flow_parity.py compares the two traces.

  python3 tools/karafun_flow_replay.py [--seeds N]        (statistics about what the random scenarios reach)
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
FREE = ("_diag", "_native_double_click_result", "_require_current_session", "_run_session_script", "_schedule_bgm_fade", "_schedule_early_handoff", "_watch_karafun_start",
        "active_session", "adjustment_signature", "entry", "handoff_dispatched", "karafun_was_running", "needs_adjustment", "parts", "queued_for_adjustment", "renderer_raise_script",
        "requested_key", "requested_tempo", "selected_query", "self", "time", "title")


def load():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_automate_karafun_search_and_play":
                    method = sub
    assert method is not None, "the Python moved"

    def find(body):
        for i, st in enumerate(body):
            if isinstance(st, ast.Expr) and "activating KaraFun result" in ast.unparse(st):
                return body, i
            for field in ("body", "orelse", "finalbody"):
                sub = getattr(st, field, None)
                if isinstance(sub, list):
                    r = find(sub)
                    if r:
                        return r
            if isinstance(st, ast.Try):
                for h in st.handlers:
                    r = find(h.body)
                    if r:
                        return r
    body, i = find(method.body)
    first = i - 1
    assert ast.unparse(body[first]) == "_require_current_session()", "the segment start moved"
    last = next(j for j in range(i, len(body)) if isinstance(body[j], ast.Assign) and ast.unparse(body[j]).startswith("result = {'ok': True"))
    segment = body[first:last + 1]
    # the free names must be exactly the ones the harness provides (a new dependency in the Python must show up here)
    stores, loads = set(), set()
    for st in segment:
        for n in ast.walk(st):
            if isinstance(n, ast.Name):
                (stores if isinstance(n.ctx, (ast.Store, ast.Del)) else loads).add(n.id)
            elif isinstance(n, ast.FunctionDef):
                stores.add(n.name)
    free = sorted(x for x in loads - stores if not hasattr(builtins, x) and x != "karafun_flow_shadow")
    assert free == sorted(FREE), f"the segment's outside names changed: {free}"
    wrapper = ast.parse(f"def _flow({', '.join(FREE)}):\n    pass\n").body[0]
    wrapper.body = [ast.Try(body=segment + [ast.parse("return result").body[0]],
                            handlers=[ast.ExceptHandler(type=ast.Name(id="Exception", ctx=ast.Load()), name="e",
                                                        body=[ast.parse('return {"ok": False, "message": str(e)}').body[0]])], orelse=[], finalbody=[])]
    mod = ast.Module(body=[wrapper], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {}
    exec(compile(mod, "flow", "exec"), ns)
    return ns["_flow"], len(segment)


class Recorder:
    """The scripted world. `answers` is filled lazily from the RNG (or replayed from a list)."""

    def __init__(self, scenario, rng=None, answers=None):
        self.sc = scenario
        self.rng = rng
        self.answers = answers if answers is not None else []
        self.pos = 0
        self.calls = []
        self.log = []
        self.t = 100.0
        self.sleeps = []
        self.requires = 0

    # -- answers
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

        @property
        def _karafun_handoff_in_progress(self):
            return R.ask("in_progress", lambda r: True if sc.get("stuck_handoff") else r.random() < 0.6)

        @property
        def _karafun_handoff_complete(self):
            return R.ask("handoff_complete", lambda r: r.random() < 0.8)

        def _karafun_start_probe_script(self):
            R.call("start_probe_script")
            return "probe-script"

        def _karafun_renderer_ready_script(self):
            R.call("renderer_ready_script")
            return "ready-script"

        def _karafun_set_key_tempo_script(self, key, tempo):
            R.call("set_key_tempo_script", key, tempo)
            return f"kt-script:{key}:{tempo}"

        def _karafun_press_play_control(self):
            v = R.ask("press", lambda r: [r.random() < 0.8, r.choice(["", "no play button", "AX -1719"])])
            R.call("press_play")
            return (bool(v[0]), v[1] or None)

        def _macos_native_mouse_click(self, x, y, clicks=1):
            R.call("mouse_click", x, y, clicks=clicks)
            return R.ask("mouse_click", lambda r: r.random() < 0.85)

        def _karafun_activate_result_for_playback(self, point, reuse_point=False):
            R.call("activate_result", list(point), reuse_point=reuse_point)
            v = R.ask("activate", lambda r: [r.random() < 0.85, r.choice(["", "result moved", "timeout"])])
            return (bool(v[0]), v[1] or None)

        def _karafun_playback_menu_state(self):
            R.call("menu_state")
            return R.ask("menu_state", lambda r: r.choice(["PLAYING", "IDLE", "IDLE", "UNKNOWN"]))

        def _karafun_run_window_script(self, lines, timeout=None):
            R.call("window_script", lines, timeout=timeout)
            return None

        def _run_on_ui_thread(self, fn):
            R.call("ui_thread")

        def _activate_host_during_karafun_capture(self, session):
            R.call("activate_host")

        def _start_karafun_completion_monitor(self, entry):
            R.call("start_monitor")

    me = FakeSelf()

    def run_session_script(script, timeout=None):
        R.call("session_script", script, timeout=timeout)
        v = R.ask("session_script", lambda r: [r.random() < 0.8, r.choice(["READY", "PLAYING", "IDLE", "UNKNOWN", "ERROR|KaraFun control window not found", "ADJUSTED|true|true",
                                                                             "ADJUSTED|true|false", "ADJUSTED|false|true", "ADJUSTED|false|false", "", "weird"]),
                                              r.choice(["", "timeout", "AX error", "e" * 200, "x" * 180, "y" * 181])])
        return (bool(v[0]), v[1], v[2])

    class Clock:
        @staticmethod
        def monotonic():
            return R.t

        @staticmethod
        def time():
            return 1_700_000_000.0 + R.t

        @staticmethod
        def sleep(s):
            R.sleeps.append(s)
            R.t += s

    class Event:
        @staticmethod
        def wait(timeout=None):
            ok = R.ask("event_wait", lambda r: r.random() < 0.8)
            R.call("event_wait", timeout=timeout)
            R.t += 0.2 if ok else (timeout or 0.0)
            return ok

    def require():
        R.requires += 1
        if sc["end_session_at"] is not None and R.requires >= sc["end_session_at"]:
            raise RuntimeError("KaraFun playback session ended")

    def diag(msg):
        R.log.append(msg)

    def schedule_bgm(reason):
        R.call("schedule_bgm_fade", reason)

    def schedule_early(reason):
        R.call("schedule_early_handoff", reason)

    def watch_start(point):
        R.call("watch_start", list(point))

    def native_double_click():
        R.call("native_double_click")
        return None

    def renderer_raise_script():
        R.call("renderer_raise_script")
        return ["raise-script"]

    shadow = types.ModuleType("karafun_flow_shadow")
    shadow.startflow = lambda settings, log, **kw: R.call("shadow_startflow", **{k: (v if isinstance(v, (bool, int, float, str)) or v is None else str(v)) for k, v in kw.items()})
    sys.modules["karafun_flow_shadow"] = shadow
    return dict(_diag=diag, _native_double_click_result=native_double_click, _require_current_session=require, _run_session_script=run_session_script,
                _schedule_bgm_fade=schedule_bgm, _schedule_early_handoff=schedule_early, _watch_karafun_start=watch_start, active_session={"id": 1},
                adjustment_signature=sc["adjustment_signature"], entry=dict(sc["entry"]), handoff_dispatched=Event, karafun_was_running=sc["karafun_was_running"],
                needs_adjustment=sc["needs_adjustment"], parts=sc["parts"], queued_for_adjustment=sc["queued_for_adjustment"], renderer_raise_script=renderer_raise_script,
                requested_key=sc["requested_key"], requested_tempo=sc["requested_tempo"], selected_query=sc["selected_query"], self=me, time=Clock, title=sc["title"])


def run(flow, scenario, rng=None, answers=None):
    rec = Recorder(scenario, rng, answers)
    world = build_world(rec)
    result = flow(**world)
    entry = world["entry"]
    return {"result": result, "calls": rec.calls, "log": rec.log, "sleeps": rec.sleeps, "entry": entry, "final_clock": rec.t, "answers": rec.answers}


def random_scenario(r):
    capture = r.random() < 0.3
    entry = {}
    if r.random() < 0.2:
        entry["karafun_explicit_play_sent"] = True
    sig = r.choice(["k+2/t90", "k0/t100", "k-1/t110"])
    if r.random() < 0.25:
        entry["karafun_adjustment_applied"] = sig if r.random() < 0.6 else "other"
    if r.random() < 0.15:
        entry["karafun_playback_clock_started_at"] = r.choice([0.0, 1.0, 50.0, 120.5])
    return {"settings": {"karafun_dual_renderer_capture": capture, "karafun_manage_show_screen": r.random() < 0.7, "karafun_fast_start_enabled": r.random() < 0.7},
            "parts": [r.choice(["FOUND", "TITLE_ONLY", "FIRST"]), r.choice(["404", "404.7", "0", "-5", "1200"]), r.choice(["202", "202.2", "0", "88"]), r.choice(["3:41", ""])],
            "queued_for_adjustment": r.random() < 0.2, "needs_adjustment": r.random() < 0.4, "adjustment_signature": sig,
            "requested_key": r.choice([0, 0, 2, -1, 3]), "requested_tempo": r.choice([100, 100, 90, 110, 75]), "karafun_was_running": r.random() < 0.5,
            "selected_query": r.choice(["Memory Sugarcult", "it's", "Zoë"]), "title": r.choice(["Memory", "Don't Stop", "Zoë (live)"]), "entry": entry,
            "end_session_at": r.choice([None] * 8 + [1, 2, 3, 5, 9, 14]), "stuck_handoff": r.random() < 0.05}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2000)
    args = ap.parse_args(argv)
    flow, n = load()
    print(f"segment: {n} statements; outside names verified")
    stats = {"ok": 0, "failed": 0, "calls": {}, "messages": {}}
    for seed in range(args.seeds):
        r = random.Random(seed)
        out = run(flow, random_scenario(r), r)
        stats["ok" if out["result"]["ok"] else "failed"] += 1
        for c in out["calls"]:
            stats["calls"][c[0]] = stats["calls"].get(c[0], 0) + 1
        if not out["result"]["ok"]:
            m = out["result"]["message"][:60]
            stats["messages"][m] = stats["messages"].get(m, 0) + 1
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
