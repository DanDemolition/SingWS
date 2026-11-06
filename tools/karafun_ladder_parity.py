#!/usr/bin/env python3
"""Parity: the search LADDER of the real KaraokeApp._automate_karafun_search_and_play (0.2.18.1.py) vs the Rust `singws-karafun` ladder.

The eight consecutive statements from `found = ""` to the final `raise RuntimeError(last_error or "No acceptable KaraFun match")` are read out of the
method by AST and executed unchanged inside a small wrapper function (the app is not imported). Both sides get the same SCRIPTED ANSWERS for every
script they ask to run. Compared: the final found/selected_query/last_error, the error the app would raise, every log line, the scripts requested (with
their arguments) in order, and the number of sleeps.

  python3 tools/karafun_ladder_parity.py [--seeds N] [--binary PATH]
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

ROOT = Path(__file__).resolve().parent.parent
PREDICATES = ("_is_karafun_apple_events_error", "_is_karafun_accessibility_error", "_is_karafun_ui_not_ready_error")


def load():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    method, preds = None, {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_automate_karafun_search_and_play":
                    method = sub
                if isinstance(sub, ast.FunctionDef) and sub.name in PREDICATES:
                    sub.decorator_list = []
                    preds[sub.name] = sub
    assert method is not None and len(preds) == 3, "the Python moved"

    def find(body):
        for i, st in enumerate(body):
            if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name) and st.targets[0].id == "found" and isinstance(st.value, ast.Constant) and st.value.value == "":
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
    block = body[i:i + 8]
    assert isinstance(block[5], ast.For) and ast.unparse(block[-1]).startswith("if len(parts) < 3 or parts[0] not in"), "the ladder block changed shape"
    wrapper = ast.parse("def _ladder(search_queries, artist, safe_title, self, _run_session_script, _diag, time):\n    pass\n").body[0]
    wrapper.body = block + [ast.parse("return found, selected_query, last_error").body[0]]
    mod = ast.Module(body=list(preds.values()) + [wrapper], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {}
    exec(compile(mod, "ladder", "exec"), ns)
    return ns


def python_run(ns, queries, safe_title, artist, answers):
    log, requests, sleeps, it = [], [], [0], iter(answers)

    def nxt():
        a = next(it, None)
        return (a["ok"], a["text"], a["error"]) if a else (False, "", "answers exhausted")

    me = types.SimpleNamespace(
        _karafun_search_script=lambda **kw: f"search:{kw['query']}:{{retry}}:{kw['safe_title']}:{kw['safe_artist']}:{kw['require_exact_title']}",
        _karafun_search_diag_script=lambda title: f"diag:{title}",
        _run_karafun_applescript_sync=lambda script, **kw: (requests.append(script), nxt())[1],
        **{name: ns[name] for name in PREDICATES})
    import re

    def run_session_script(script, timeout=18):
        # the retry number is what the app just logged ("... ui_retry=N" for the 2nd and 3rd try)
        m = re.search(r" ui_retry=(\d+)$", log[-1]) if log else None
        retry = int(m.group(1)) - 1 if m else 0
        parts = script.split(":", 5)
        requests.append(f"search:{parts[1]}:{retry}:{parts[3]}:{parts[4]}")
        return nxt()

    clock = types.SimpleNamespace(sleep=lambda s: sleeps.__setitem__(0, sleeps[0] + 1))
    raised = None
    found = selected = last = ""
    try:
        found, selected, last = ns["_ladder"](queries, artist, safe_title, me, run_session_script, lambda m: log.append(m), clock)
    except RuntimeError as exc:
        raised = str(exc)
    return {"found": found, "selected_query": selected, "last_error": last, "raised": raised, "log": log, "requests": requests, "sleeps": sleeps[0]}


OK_TEXTS = ["FOUND|404|202|3:41", "TITLE_ONLY|1|2|", "FIRST|1|2|3:00", "ERROR|No usable KaraFun search result", "ERROR|KaraFun is not running", "SEARCHED", "", "FOUND|1",
            "FOUND||", "x|y|z", "ERROR|a|b|c", "FOUND|1|2|3:00|extra", "TITLE_ONLY|1|2", "FIRST|1|2", "FOUND|1|2", "ctrl\x01\x1b\x7fchars", "nbsp\xa0 soft\xad c1\x85",
            "d" * 1000]
ERRORS = ["", "AXError -1719", "Invalid index", "error -1743", "not allowed assistive access", "Can't get window 1", "can’t get x", "timed out", "-25211",
          "Apple events not permitted", "Accessibility is not allowed", "x is not defined", "not permitted", "apple events", "accessibility", "not allowed", "ctrl\x01", "nbsp\xa0 \xad \x85", "Apple Events", "érror", "it's broken", 'say "hi"', "line1\nline2\ttab", "e" * 300, "back\\slash"]
QUERIES = ["Sugarcult Memory", "Memory Sugarcult", "Memory", "Sugarcult", "", "it's", 'say "hi"', "Zoë", "a\\b"]
ARTISTS = ["", "Sugarcult", " Sugarcult ", "ZOE", "Zoë", "sugar"]


def answer(r):
    if r.random() < 0.55:
        text = r.choice(OK_TEXTS) if r.random() < 0.8 else r.choice(ERRORS)
        return {"ok": True, "text": text, "error": ""}
    return {"ok": False, "text": r.choice(["", "", "some text", "FOUND|1|2|3"]), "error": r.choice(ERRORS)}


def scripted():
    """The retry limit: three 'window not ready' failures in a row (the third is NOT retried), two then success, then a second query."""
    nr = {"ok": False, "text": "", "error": "AXError -1719"}
    good = {"ok": True, "text": "FOUND|1|2|3:00", "error": ""}
    out = []
    for queries in (["Memory"], ["Memory", "Sugarcult Memory"], ["Memory", "Memory"]):
        for answers in ([nr, nr, nr], [nr, nr, good], [nr, good], [nr, nr, nr, good], [nr, nr, nr, nr, nr, nr, good]):
            for artist in ("", "Sugarcult"):
                out.append({"queries": queries, "safe_title": "Memory", "safe_artist": artist, "answers": answers})
    return out


def cases(seeds):
    out = scripted()
    for s in range(seeds):
        r = random.Random(s)
        queries = [r.choice(QUERIES) for _ in range(r.randint(0, 5))]
        out.append({"queries": queries, "safe_title": r.choice(["Memory", "", "It's", "Zoë"]), "safe_artist": r.choice(ARTISTS), "answers": [answer(r) for _ in range(r.randint(0, 12))]})
    return out


def run_rust(binary, batch):
    proc = subprocess.run([str(binary)], input=json.dumps({"batch": batch}).encode(), capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode()[-400:])
    return json.loads(proc.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=6000)
    ap.add_argument("--binary", default=os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun"))
    args = ap.parse_args(argv)
    ns = load()
    cs = cases(args.seeds)
    got = run_rust(args.binary, [dict(c, op="ladder") for c in cs])
    diffs, declined, per_outcome = [], 0, {"found": 0, "raised": 0}
    for c, g in zip(cs, got):
        want = python_run(ns, c["queries"], c["safe_title"], c["safe_artist"], c["answers"])
        if "decline" in g:
            declined += 1
            continue
        per_outcome["found" if want["found"] else "raised"] += 1
        if want["raised"] is not None:
            want["last_error"] = g.get("last_error")  # the app raises before it returns this local: the raised text carries it
        if g != want:
            keys = [k for k in want if g.get(k) != want[k]]
            diffs.append((c, keys, {k: (g.get(k), want[k]) for k in keys[:2]}))
    print(f"scenarios={len(cs)} declined={declined} (a character whose Python repr is not modelled) found={per_outcome['found']} raised={per_outcome['raised']}")
    for c, keys, detail in diffs[:5]:
        print(f" - differs in {keys}: {json.dumps(c, ensure_ascii=False)[:260]}\n   {str(detail)[:500]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
