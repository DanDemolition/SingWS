#!/usr/bin/env python3
"""Replay harness for the part of KaraokeApp._automate_karafun_search_and_play BETWEEN the search ladder and the activation: the `found` answer is checked, the
verified duration replaces the estimate, the match is logged, and (when the song needs a key/tempo change) the result is queued paused and the Playback menu is driven.

The statements from `parts = found.split("|")` to the end of `if needs_adjustment:` are read out of the method by AST and executed UNCHANGED inside a wrapper (the app is
not imported) against a recording fake world with SCRIPTED answers and a fake clock. The Rust twin is `singws-karafun` prelude.rs; tools/karafun_prelude_parity.py compares them.

  python3 tools/karafun_prelude_replay.py [--seeds N]
"""
import argparse
import ast
import builtins
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FREE = ("_diag", "_require_current_session", "_run_session_script", "adjustment_signature", "entry", "found", "last_error", "needs_adjustment", "requested_key",
        "requested_tempo", "selected_query", "self", "time")


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
            if isinstance(st, ast.Assign) and ast.unparse(st) == "parts = found.split('|')":
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
    body, first = find(method.body)
    last = next(j for j in range(first, len(body)) if isinstance(body[j], ast.If) and ast.unparse(body[j].test) == "needs_adjustment")
    segment = body[first:last + 1]
    stores, loads = set(), set()
    for st in segment:
        for n in ast.walk(st):
            if isinstance(n, ast.Name):
                (stores if isinstance(n.ctx, (ast.Store, ast.Del)) else loads).add(n.id)
            elif isinstance(n, ast.FunctionDef):
                stores.add(n.name)
    free = sorted(x for x in loads - stores if not hasattr(builtins, x))
    assert free == sorted(FREE), f"the segment's outside names changed: {free}"
    wrapper = ast.parse(f"def _prelude({', '.join(FREE)}):\n    pass\n").body[0]
    wrapper.body = [ast.Try(body=segment + [ast.parse("return {'ok': True, 'queued': queued_for_adjustment}").body[0]],
                            handlers=[ast.ExceptHandler(type=ast.Name(id="Exception", ctx=ast.Load()), name="e",
                                                        body=[ast.parse("return {'ok': False, 'message': str(e)}").body[0]])], orelse=[], finalbody=[])]
    mod = ast.Module(body=[wrapper], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {}
    exec(compile(mod, "prelude", "exec"), ns)
    return ns["_prelude"], len(segment)


class Recorder:
    def __init__(self, scenario, rng=None, answers=None):
        self.sc = scenario
        self.rng = rng
        self.answers = answers if answers is not None else []
        self.pos = 0
        self.calls, self.log, self.sleeps = [], [], []
        self.t = 100.0
        self.requires = 0

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


CLOCKS = [0.0, 221.0, 3.0, 0.5, 600.0, 1.0, 59.9, -1.0]


def build_world(rec):
    sc = rec.sc
    R = rec

    class FakeSelf:
        settings = dict(sc["settings"])

        def _karafun_clock_seconds(self, text):
            R.call("clock_seconds", text)
            return R.ask("clock", lambda r: r.choice(CLOCKS + [None]))

        def _apply_verified_duration(self, entry, seconds, source=None):
            R.call("apply_verified_duration", seconds, source=source)
            return R.ask("apply_duration", lambda r: r.random() < 0.6)

        def _macos_native_mouse_click(self, x, y, clicks=1):
            R.call("mouse_click", x, y, clicks=clicks)
            return R.ask("mouse_click", lambda r: r.random() < 0.85)

        def _karafun_play_now_queue_script(self):
            R.call("play_now_queue_script")
            return "play-now-queue-script"

        def _show_processing_notification(self, message, level=None):
            R.call("notify", message, level=level)

        def _karafun_adjust_menu_script(self, key_item, key_steps, tempo_item, tempo_steps):
            R.call("adjust_menu_script", key_item, key_steps, tempo_item, tempo_steps)
            return f"adjust:{key_item}:{key_steps}:{tempo_item}:{tempo_steps}"

        def _run_on_ui_thread(self, fn):
            R.call("ui_thread")

        def _karafun_show_key_tempo(self, key, tempo):
            R.call("show_key_tempo", key, tempo)

    me = FakeSelf()

    def run_session_script(script, **kw):
        R.call("session_script", script, **kw)
        ks, ts = sc["requested_key"], sc["requested_tempo"]
        want = f"ADJUSTED|{abs(int(ks))}|{abs(int(ts) - 100)}"
        v = R.ask("session_script", lambda r: [r.random() < 0.8, r.choice(["QUEUED_PAUSED", "QUEUED_PAUSED", "QUEUED", "", " QUEUED_PAUSED ", "weird", want, want, want, " " + want + " ",
                                                                             "ADJUSTED|0|0", "ADJUSTED|1|1"]),
                                              r.choice(["", "timeout", "AX error", "e" * 200, "x" * 130, "y" * 81, "z" * 80, "w" * 79, "Zoë"])])
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

    def require():
        R.requires += 1
        if sc["end_session_at"] is not None and R.requires >= sc["end_session_at"]:
            raise RuntimeError("KaraFun playback session ended")

    def diag(msg):
        R.log.append(msg)

    return dict(_diag=diag, _require_current_session=require, _run_session_script=run_session_script, adjustment_signature=sc["adjustment_signature"], entry=dict(sc["entry"]),
                found=sc["found"], last_error=sc["last_error"], needs_adjustment=sc["needs_adjustment"], requested_key=sc["requested_key"], requested_tempo=sc["requested_tempo"],
                selected_query=sc["selected_query"], self=me, time=Clock)


def run(prelude, scenario, rng=None, answers=None):
    rec = Recorder(scenario, rng, answers)
    world = build_world(rec)
    result = prelude(**world)
    return {"result": result, "calls": rec.calls, "log": rec.log, "sleeps": rec.sleeps, "entry": world["entry"], "final_clock": rec.t, "answers": rec.answers}


def random_scenario(r):
    kind = r.choice(["FOUND", "FOUND", "TITLE_ONLY", "FIRST", "NOPE", ""])
    parts = [kind, r.choice(["404", "404.7", "0", "-5"]), r.choice(["202", "202.2", "0", "88"]), r.choice(["3:41", "", "0:00", "x"])]
    n = r.choice([4, 4, 4, 3, 2, 1, 5])
    found = "|".join((parts + ["extra"])[:n]) if n > 1 else kind
    entry = {}
    if r.random() < 0.2:
        entry["karafun_adjustment_applied"] = "old"
    sig = r.choice(["k+2/t90", "k0/t100", "k-1/t110"])
    return {"settings": {"karafun_auto_key_tempo": r.random() < 0.8},
            "found": found, "last_error": r.choice(["", "boom", "window not ready"]), "needs_adjustment": r.random() < 0.6, "adjustment_signature": sig,
            "requested_key": r.choice([0, 2, -1, 3, 0, -12, 24, 1]), "requested_tempo": r.choice([100, 90, 110, 75, 125, 101, 99, 250]),
            "selected_query": r.choice(["Memory Sugarcult", "it's", "Zoë"]), "entry": entry,
            "end_session_at": r.choice([None] * 8 + [1, 2, 3, 4, 5])}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2000)
    args = ap.parse_args(argv)
    prelude, n = load()
    print(f"segment: {n} statements; outside names verified")
    stats = {"ok": 0, "failed": 0, "queued": 0, "messages": {}}
    for seed in range(args.seeds):
        r = random.Random(seed)
        out = run(prelude, random_scenario(r), r)
        stats["ok" if out["result"]["ok"] else "failed"] += 1
        stats["queued"] += int(bool(out["result"].get("queued")))
        if not out["result"]["ok"]:
            m = out["result"]["message"][:50]
            stats["messages"][m] = stats["messages"].get(m, 0) + 1
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
