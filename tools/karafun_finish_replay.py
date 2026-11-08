#!/usr/bin/env python3
"""Replay harness for `_finish`, the closure at the end of KaraokeApp._automate_karafun_search_and_play's worker: it turns the flow's result into the entry's final state
(queued / failed), the status, the notifications and the failure clean-up. The closure is read out of the method by AST and executed UNCHANGED (the app is not imported) against a
recording fake world with scripted answers. Rust twin: `singws-karafun` finish.rs; parity: tools/karafun_finish_parity.py.

  python3 tools/karafun_finish_replay.py [--seeds N]
"""
import argparse
import ast
import builtins
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FREE = ("_diag", "_session_is_current", "active_session", "adjustment_signature", "entry", "request_uid", "requested_key", "requested_tempo", "result", "self", "time", "title")


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
    finish = next(s for s in worker.body if isinstance(s, ast.FunctionDef) and s.name == "_finish")
    stores, loads = set(), set()
    for n in ast.walk(finish):
        if isinstance(n, ast.Name):
            (stores if isinstance(n.ctx, (ast.Store, ast.Del)) else loads).add(n.id)
        elif isinstance(n, ast.arg):
            stores.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            stores.add(n.name)
    free = sorted(x for x in loads - stores if not hasattr(builtins, x) and x != "_finish")
    assert free == sorted(FREE), f"the closure's outside names changed: {free}"
    outer = ast.parse(f"def _outer({', '.join(FREE)}):\n    pass\n").body[0]
    outer.body = [finish, ast.parse("return _finish").body[0]]
    mod = ast.Module(body=[outer], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {}
    exec(compile(mod, "finish", "exec"), ns)
    return ns["_outer"]


class Recorder:
    def __init__(self, scenario):
        self.sc = scenario
        self.calls, self.log = [], []
        self.t = 100.0

    def call(self, name, *args, **kw):
        self.calls.append([name, list(args), {k: kw[k] for k in sorted(kw)}])


def build_world(rec):
    sc = rec.sc
    R = rec

    class FakeSelf:
        def _set_karafun_entry_status(self, entry, status, message=None, notify=None):
            R.call("set_status", status, message=message, notify=notify)

        def _handoff_show_screen_to_karafun(self):
            R.call("handoff_show_screen")
            if sc["handoff_error"] is not None:
                raise RuntimeError(sc["handoff_error"])

        def _karafun_adjustment_needs_attention(self, key, tempo, message, applied):
            R.call("needs_attention", key, tempo, message, bool(applied))
            return sc["needs_attention"]

        def _show_processing_notification(self, message, level=None):
            R.call("notify", message, level=level)

        def _is_karafun_apple_events_error(self, message):
            return sc["apple_err"]

        def _is_karafun_accessibility_error(self, message):
            return sc["ax_err"]

        def _show_karafun_apple_events_setup(self, notify=False):
            R.call("apple_events_setup", notify=notify)
            return "apple events setup text"

        def _show_karafun_accessibility_setup(self, notify=False):
            R.call("accessibility_setup", notify=notify)
            return "accessibility setup text"

        def _activate_host_during_karafun_capture(self, session):
            R.call("activate_host")

        def _stop_karafun_dual_renderer_capture(self):
            R.call("stop_capture")

        def _schedule_save_data(self, delay):
            R.call("schedule_save", delay)

        def update_queue_display(self):
            R.call("update_queue_display")

    me = FakeSelf()
    if sc["capture_session"] is not None:
        me._karafun_capture_session = sc["capture_session"]

    class Clock:
        @staticmethod
        def time():
            return 1_700_000_000.0 + R.t

    def diag(msg):
        R.log.append(msg)

    return dict(_diag=diag, _session_is_current=lambda: sc["current"], active_session={"id": 1}, adjustment_signature=sc["adjustment_signature"], entry=dict(sc["entry"]),
                request_uid=sc["request_uid"], requested_key=sc["requested_key"], requested_tempo=sc["requested_tempo"], result=dict(sc["result"]), self=me, time=Clock, title=sc["title"])


def run(outer, scenario):
    rec = Recorder(scenario)
    world = build_world(rec)
    outer(**world)()
    return {"calls": rec.calls, "log": rec.log, "entry": world["entry"]}


def random_scenario(r):
    ok = r.random() < 0.45
    msg = r.choice(["ADJUSTED|true|true", "ADJUSTED|false|false", "", "x" * 300, "boom", "Not authorized to send Apple events", "Zoë broke", "e" * 240, "e" * 241])
    entry = {}
    for k in ("karafun_pending_at", "karafun_adjustment_applied", "other"):
        if r.random() < 0.5:
            entry[k] = r.choice(["sig", "k+2/t90", 5, None])
    sig = r.choice(["k+2/t90", "k0/t100"])
    return {"result": {"ok": ok, "message": msg}, "entry": entry, "current": r.random() < 0.9, "handoff_error": r.choice([None, None, None, "no show window", "Zoë"]),
            "needs_attention": r.random() < 0.3, "apple_err": r.random() < 0.15, "ax_err": r.random() < 0.15, "capture_session": r.choice([None, True, False]),
            "adjustment_signature": sig, "request_uid": r.choice(["uid-1", "uid-é"]), "requested_key": r.choice([0, 2, -1]), "requested_tempo": r.choice([100, 90, 110]),
            "title": r.choice(["Memory", "Don't Stop", "Zoë (live)"])}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2000)
    args = ap.parse_args(argv)
    outer = load()
    stats = {}
    for seed in range(args.seeds):
        out = run(outer, random_scenario(random.Random(seed)))
        for c in out["calls"]:
            stats[c[0]] = stats.get(c[0], 0) + 1
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
