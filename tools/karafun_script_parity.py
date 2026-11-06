#!/usr/bin/env python3
"""Parity: the REAL KaraFun search script builder of 0.2.18.1.py vs the Rust crate ``singws-karafun``.

The Python functions are read out of 0.2.18.1.py by AST and executed alone (the app is never imported, nothing touches a profile or KaraFun):
``karafun_match_title``, ``KaraokeApp._karafun_applescript_literal`` and ``KaraokeApp._karafun_search_script`` with its duration handler. Every
corpus case must give the same title, the same literal and the SAME LIST OF SCRIPT LINES (same lines, same order, same text) on both sides.

  python3 tools/karafun_script_parity.py [--seeds N] [--binary PATH]
Build first:  CARGO_TARGET_DIR=~/.cache/singws-rust-target-karafun cargo build --release -p singws-karafun   (in rust/)
"""
import argparse
import ast
import json
import os
import random
import re
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_python():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    body, methods = [], {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "karafun_match_title":
            body.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "KARAFUN_DURATION_HANDLER_LINES" for t in node.targets):
            body.append(node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in ("_karafun_search_script", "_karafun_applescript_literal"):
                    methods[sub.name] = sub
    assert len(methods) == 2, "the Python builder moved"
    ns = {"re": re}
    exec(compile(ast.Module(body=body, type_ignores=[]), "0.2.18.1.py", "exec"), ns)
    # the two methods, compiled as plain functions (the literal one was a staticmethod: drop its decorator)
    for m in methods.values():
        m.decorator_list = []
    exec(compile(ast.Module(body=list(methods.values()), type_ignores=[]), "0.2.18.1.py", "exec"), ns)
    literal = ns["_karafun_applescript_literal"]
    me = types.SimpleNamespace(_karafun_applescript_literal=literal)
    return (ns["karafun_match_title"], literal,
            lambda **kw: ns["_karafun_search_script"](me, **kw))


TITLES = ["", " ", "Memory", "Just the Way You Are (live)", "Song [Remix] (Radio Edit)  ", "(Everything I Do) I Do It For You", "(live)", "[a]", "Open (never closed",
          "Hello (feat. X) [Live] (2011)", "A (b) C (d)", "x ( ) ", "()", "Tom's Diner", 'He said "hi" (live)', "Back\\slash [x]", "Zoë (live)", "日本語 (ライブ)",
          "a\tb (c)\n", " Padded (live) ", " em (x)", "(a)(b)", "(a) (b) tail", "tail (a) (b) ", "[x) y]", "word)(", "((nested))", "([mixed])", "a [b](c)",
          "no qualifier", "ends with space (live) ", "\x1ctrash (live)\x1d", "\x85next (x)", "a b (c)"]
ARTISTS = ["", "Sugarcult", "AC/DC", "Guns N' Roses", 'The "Quoted" One', "Back\\slash", "Zoë", "日本", "Line\nBreak", " ", "P!nk", "a\rb"]
# EVERY character Python treats as whitespace (str.isspace), at the edges of a title and between qualifier groups: the Rust side lists them by hand
SPACES = [chr(c) for c in range(0x110000) if chr(c).isspace()]
TITLES += [f"{c}Pad (live){c}" for c in SPACES] + [f"a{c}(b){c}[c]{c}" for c in SPACES] + [f"x{c}y (z)" for c in SPACES]
QUERIES = ["", "Sugarcult Memory", 'say "hi"', "back\\slash", "multi\nline\r\nquery", "Ünïcode ✓", "x" * 200, "tab\there"]


def random_text(r, extra=""):
    alphabet = "abcXY 09()[]\"'\\\n\r\t.-!?éß日  " + extra
    return "".join(r.choice(alphabet) for _ in range(r.randint(0, 24)))


def cases(seeds):
    out = []
    for t in TITLES:
        for a in ARTISTS[:6]:
            for exact in (False, True):
                for resolve in (False, True):
                    out.append({"query": QUERIES[1], "safe_title": t, "safe_artist": a, "require_exact_title": exact, "resolve_only": resolve})
    for q in QUERIES:
        for a in ARTISTS:
            out.append({"query": q, "safe_title": "Memory", "safe_artist": a, "require_exact_title": True, "resolve_only": False})
            out.append({"query": q, "safe_title": "", "safe_artist": a, "require_exact_title": True, "resolve_only": False})
    for s in range(seeds):
        r = random.Random(s)
        out.append({"query": random_text(r), "safe_title": random_text(r, "()[]"), "safe_artist": random_text(r),
                    "require_exact_title": r.random() < 0.5, "resolve_only": r.random() < 0.3})
    return out


def run_rust(binary, batch):
    proc = subprocess.run([str(binary)], input=json.dumps({"batch": batch}).encode(), capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode()[-400:])
    return json.loads(proc.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3000)
    ap.add_argument("--binary", default=os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun"))
    args = ap.parse_args(argv)
    match_title, literal, search = load_python()
    cs = cases(args.seeds)
    diffs = []
    # 1. search scripts
    got = run_rust(args.binary, [dict(c, op="search_script") for c in cs])
    for c, g in zip(cs, got):
        want = search(query=c["query"], safe_title=c["safe_title"], safe_artist=c["safe_artist"],
                      require_exact_title=c["require_exact_title"], resolve_only=c["resolve_only"])
        if g["lines"] != want:
            first = next((i for i, (x, y) in enumerate(zip(g["lines"], want)) if x != y), min(len(g["lines"]), len(want)))
            diffs.append(("search_script", c, f"first differing line {first}: rust={g['lines'][first:first+1]} python={want[first:first+1]}; lengths {len(g['lines'])} vs {len(want)}"))
    # 2. titles and literals on every distinct string seen
    strings = sorted({c["safe_title"] for c in cs} | set(TITLES))
    got = run_rust(args.binary, [{"op": "match_title", "title": s} for s in strings])
    for s, g in zip(strings, got):
        if g["title"] != match_title(s):
            diffs.append(("match_title", s, f"rust={g['title']!r} python={match_title(s)!r}"))
    lits = sorted({c["query"] for c in cs} | {c["safe_artist"] for c in cs})
    got = run_rust(args.binary, [{"op": "literal", "value": s} for s in lits])
    for s, g in zip(lits, got):
        if g["literal"] != literal(s):
            diffs.append(("literal", s, f"rust={g['literal']!r} python={literal(s)!r}"))
    print(f"search scripts={len(cs)} titles={len(strings)} literals={len(lits)}")
    for kind, c, why in diffs[:8]:
        print(f" - {kind}: {json.dumps(c, ensure_ascii=False)[:200]}\n   {why[:400]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
