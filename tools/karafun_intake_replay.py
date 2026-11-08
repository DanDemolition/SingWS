#!/usr/bin/env python3
"""Replay harness for the INTAKE of KaraokeApp._automate_karafun_search_and_play: the duplicate guard, the title check, the key/tempo clamps, the entry's pending state and the
logged search queries (from `state = ...` to the `playback search queries` log line). Read out of the method by AST and executed UNCHANGED against a recording fake world (the app
is not imported). Rust twin: `singws-karafun` intake.rs; parity: tools/karafun_intake_parity.py.

  python3 tools/karafun_intake_replay.py [--seeds N]
"""
import argparse
import ast
import builtins
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FREE = ("_diag", "entry", "key", "self", "tempo_percent", "time")


def load():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_automate_karafun_search_and_play":
                    method = sub
    assert method is not None, "the Python moved"
    body = method.body
    first = next(i for i, st in enumerate(body) if isinstance(st, ast.Assign) and ast.unparse(st).startswith("state = str(entry.get('karafun_submission_state')"))
    last = next(i for i, st in enumerate(body) if isinstance(st, ast.Expr) and "playback search queries" in ast.unparse(st))
    segment = body[first:last + 1]
    stores, loads = set(), set()
    for st in segment:
        for n in ast.walk(st):
            if isinstance(n, ast.Name):
                (stores if isinstance(n.ctx, (ast.Store, ast.Del)) else loads).add(n.id)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                stores.add(n.name)
    free = sorted(x for x in loads - stores if not hasattr(builtins, x))
    assert free == sorted(FREE), f"the segment's outside names changed: {free}"
    ret = ("return {'returned': None, 'requested_key': requested_key, 'requested_tempo': requested_tempo, 'adjustment_signature': adjustment_signature, "
           "'needs_adjustment': needs_adjustment, 'request_uid': request_uid, 'safe_title': safe_title, 'search_queries': search_queries}")
    wrapper = ast.parse(f"def _intake({', '.join(FREE)}):\n    pass\n").body[0]
    # an early `return False` of the method becomes {'returned': False}
    class R(ast.NodeTransformer):
        def visit_Return(self, node):
            return ast.parse("return {'returned': False}").body[0]
    seg = [R().visit(st) for st in segment]
    wrapper.body = [ast.Try(body=seg + [ast.parse(ret).body[0]],
                            handlers=[ast.ExceptHandler(type=ast.Name(id="Exception", ctx=ast.Load()), name="e", body=[ast.parse("return {'raised': str(e)}").body[0]])],
                            orelse=[], finalbody=[])]
    mod = ast.Module(body=[wrapper], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {}
    exec(compile(mod, "intake", "exec"), ns)
    return ns["_intake"], len(segment)


class Recorder:
    def __init__(self, scenario):
        self.sc = scenario
        self.calls, self.log = [], []

    def call(self, name, *args, **kw):
        self.calls.append([name, list(args), {k: kw[k] for k in sorted(kw)}])


def build_world(rec):
    sc = rec.sc
    R = rec

    class FakeSelf:
        def _karafun_entry_artist_title(self, entry):
            return sc["artist"], sc["title"]

        def _set_karafun_entry_status(self, entry, status, message=None):
            R.call("set_status", status, message=message)

        def _ensure_queue_entry_id(self, entry):
            R.call("ensure_id")
            return sc["request_uid"]

        def _karafun_search_queries_for_entry(self, entry):
            R.call("queries_for_entry")
            return list(sc["queries"])

    class Clock:
        @staticmethod
        def time():
            return 1_700_000_100.0

    return dict(_diag=lambda m: R.log.append(m), entry=dict(sc["entry"]), key=sc["key"], self=FakeSelf(), tempo_percent=sc["tempo"], time=Clock)


def run(intake, scenario):
    rec = Recorder(scenario)
    world = build_world(rec)
    result = intake(**world)
    return {"result": result, "calls": rec.calls, "log": rec.log, "entry": world["entry"]}


KEYS = [0, 2, -3, 12, 13, -13, 99, None, "", "3", " 4 ", "-5", "+6", "abc", "1.5", 2.7, -2.7, 0.0, 1e30, -1e30, True, False, "000000000000000000000000000012", "99999999999999999999999999", "-99999999999999999999999999"]
TEMPOS = [None, 100, 90, 110, 49, 50, 200, 201, 0, -5, "", "90", " 120 ", "abc", "99.5", 49.9, 200.5, 1e30, -1e30, True, False, "000000000000000000000000000090", "99999999999999999999999999"]


def random_scenario(r):
    entry = {}
    if r.random() < 0.6:
        entry["karafun_submission_state"] = r.choice(["karafun_queued", "karafun_completed", "karafun_pending", " Karafun_Pending ", "KARAFUN_QUEUED", "", None, "other", "karafun_failed"])
    if r.random() < 0.7:
        entry["karafun_pending_at"] = r.choice([0, 0.0, None, 1_700_000_100.0 - 5, 1_700_000_100.0 - 29.5, 1_700_000_100.0 - 30, 1_700_000_100.0 - 31, 1_700_000_100,
                                                "1700000090.5", "abc", "", " 1700000000 ", True, 5, -3.5])
    if r.random() < 0.3:
        entry["karafun_explicit_play_sent"] = True
    artist, title = r.choice([("Sugarcult", "Memory"), ("", "Memory"), ("Zoë", ""), ("a", "b"), ("", ""), ("A", "Zoë (live)")])
    queries = r.choice([[], ["Memory Sugarcult", "Memory"], ["it's"], [], [], ["Zoë"]])
    return {"entry": entry, "artist": artist, "title": title, "key": r.choice(KEYS), "tempo": r.choice(TEMPOS), "request_uid": r.choice(["uid-1", "uid-é"]), "queries": queries}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2000)
    args = ap.parse_args(argv)
    intake, n = load()
    print(f"segment: {n} statements; outside names verified")
    stats = {}
    for seed in range(args.seeds):
        out = run(intake, random_scenario(random.Random(seed)))
        k = "raised" if "raised" in out["result"] else ("returned" if "returned" in out["result"] and out["result"]["returned"] is not None else "ok")
        stats[k] = stats.get(k, 0) + 1
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
