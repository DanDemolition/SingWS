#!/usr/bin/env python3
"""Replay harness for the SETUP at the top of KaraokeApp._automate_karafun_search_and_play's worker: stale per-attempt flags are cleared, KaraFun is launched when it is not
running, the cold-start wait and the Apple-events and audio-output checks run. The statements from the first `_require_current_session()` to `if not audio_ok:` are read out of
the method by AST and executed UNCHANGED against a recording fake world with SCRIPTED answers (the app is not imported). Rust twin: `singws-karafun` setup.rs; parity:
tools/karafun_setup_parity.py.

  python3 tools/karafun_setup_replay.py [--seeds N]
"""
import argparse
import ast
import builtins
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FREE = ("_require_current_session", "_session_is_current", "entry", "self")
STALE = ("karafun_native_retry_sent", "karafun_native_first", "karafun_ax_retry_sent", "karafun_display_started_at", "karafun_display_duration", "karafun_paused_at",
         "karafun_queued_for_adjustment", "karafun_kt_dirty", "karafun_kt_busy", "karafun_native_reclick")


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
    body = next(s for s in worker.body if isinstance(s, ast.Try)).body
    assert ast.unparse(body[0]) == "_require_current_session()", "the segment start moved"
    last = next(j for j, st in enumerate(body) if isinstance(st, ast.If) and ast.unparse(st.test) == "not audio_ok")
    segment = body[:last + 1]
    stores, loads = set(), set()
    for st in segment:
        for n in ast.walk(st):
            if isinstance(n, ast.Name):
                (stores if isinstance(n.ctx, (ast.Store, ast.Del)) else loads).add(n.id)
    free = sorted(x for x in loads - stores if not hasattr(builtins, x))
    assert free == sorted(FREE), f"the segment's outside names changed: {free}"
    wrapper = ast.parse(f"def _setup({', '.join(FREE)}):\n    pass\n").body[0]
    wrapper.body = [ast.Try(body=segment + [ast.parse("return {'ok': True, 'was_running': karafun_was_running}").body[0]],
                            handlers=[ast.ExceptHandler(type=ast.Name(id="Exception", ctx=ast.Load()), name="e",
                                                        body=[ast.parse("return {'ok': False, 'message': str(e)}").body[0]])], orelse=[], finalbody=[])]
    mod = ast.Module(body=[wrapper], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {}
    exec(compile(mod, "setup", "exec"), ns)
    return ns["_setup"], len(segment)


class Recorder:
    def __init__(self, scenario, rng=None, answers=None):
        self.sc = scenario
        self.rng = rng
        self.answers = answers if answers is not None else []
        self.pos = 0
        self.calls, self.requires = [], 0

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
        if sc.get("launch_error") is not None:
            _last_karafun_launch_error = sc["launch_error"]

        def _karafun_process_running(self):
            R.call("process_running")
            return R.ask("running", lambda r: r.random() < 0.5)

        def _run_on_ui_thread(self, fn):
            R.call("ui_thread")

        def _set_karafun_entry_status(self, *a, **k):
            R.call("set_status")

        def _open_karafun_for_entry(self, entry):
            R.call("open_karafun")
            return R.ask("open", lambda r: r.random() < 0.85)

        def _karafun_wait_until_ready(self, was_running=None, session_is_current=None):
            R.call("wait_until_ready", was_running=was_running, session_current=bool(session_is_current()))
            err = R.ask("wait", lambda r: r.choice([None, None, None, "KaraFun never became ready", "session over"]))
            if err:
                raise RuntimeError(err)

        def _karafun_apple_events_preflight(self):
            R.call("preflight")
            v = R.ask("preflight", lambda r: [r.random() < 0.85, r.choice(["", "no automation", "Zoë"])])
            return (bool(v[0]), v[1])

        def _ensure_karafun_audio_output(self):
            R.call("audio_output")
            v = R.ask("audio", lambda r: [r.random() < 0.85, r.choice(["", "wrong device", "Zoë"])])
            return (bool(v[0]), v[1])

    def require():
        R.requires += 1
        if sc["end_session_at"] is not None and R.requires >= sc["end_session_at"]:
            raise RuntimeError("KaraFun playback session ended")

    return dict(_require_current_session=require, _session_is_current=lambda: R.ask("session_current", lambda r: r.random() < 0.9), entry=dict(sc["entry"]), self=FakeSelf())


def run(setup, scenario, rng=None, answers=None):
    rec = Recorder(scenario, rng, answers)
    world = build_world(rec)
    result = setup(**world)
    return {"result": result, "calls": rec.calls, "entry": world["entry"], "answers": rec.answers}


def random_scenario(r):
    entry = {k: r.choice([1, "x", 0.0, True]) for k in STALE if r.random() < 0.5}
    for k in ("karafun_key_current", "karafun_tempo_current", "other", "karafun_submission_state"):
        if r.random() < 0.4:
            entry[k] = r.choice([5, "keep", None, 100])
    return {"entry": entry, "launch_error": r.choice([None, None, "", "KaraFun is not installed", "Zoë missing"]), "end_session_at": r.choice([None] * 7 + [1, 2, 3, 4, 5])}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2000)
    args = ap.parse_args(argv)
    setup, n = load()
    print(f"segment: {n} statements; outside names verified")
    stats = {"ok": 0, "failed": 0, "messages": {}}
    for seed in range(args.seeds):
        r = random.Random(seed)
        out = run(setup, random_scenario(r), r)
        stats["ok" if out["result"]["ok"] else "failed"] += 1
        if not out["result"]["ok"]:
            m = out["result"]["message"][:50]
            stats["messages"][m] = stats["messages"].get(m, 0) + 1
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
