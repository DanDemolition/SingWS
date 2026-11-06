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
PY = {}  # the other real Python functions, filled by load_python()


METHODS = ("_karafun_search_script", "_karafun_applescript_literal", "_karafun_fallback_remaining", "_karafun_adjustment_needs_attention",
           "_karafun_clean_search_part", "_karafun_entry_artist_title", "_karafun_search_queries_for_entry")


def load_python():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    body, methods = [], {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("karafun_match_title", "normalize_karafun_duration_seconds"):
            body.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "KARAFUN_DURATION_HANDLER_LINES" for t in node.targets):
            body.append(node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in METHODS:
                    methods[sub.name] = sub
    assert len(methods) == len(METHODS), "a Python function moved: " + str(set(METHODS) - set(methods))
    import math
    ns = {"re": re, "math": math}
    exec(compile(ast.Module(body=body, type_ignores=[]), "0.2.18.1.py", "exec"), ns)
    # the two methods, compiled as plain functions (the literal one was a staticmethod: drop its decorator)
    for m in methods.values():
        m.decorator_list = []
    exec(compile(ast.Module(body=list(methods.values()), type_ignores=[]), "0.2.18.1.py", "exec"), ns)
    literal = ns["_karafun_applescript_literal"]
    me = types.SimpleNamespace(_karafun_applescript_literal=literal)
    for name in ("_karafun_clean_search_part", "_karafun_entry_artist_title", "_karafun_search_queries_for_entry"):
        setattr(me, name, (lambda f: (lambda *a, **k: f(me, *a, **k)))(ns[name]) if name != "_karafun_clean_search_part" else ns[name])
    me._karafun_clean_search_part = ns["_karafun_clean_search_part"]
    PY.update(duration=ns["normalize_karafun_duration_seconds"], remaining=ns["_karafun_fallback_remaining"], needs=ns["_karafun_adjustment_needs_attention"],
              clean=ns["_karafun_clean_search_part"], entry=lambda e: ns["_karafun_entry_artist_title"](me, e), queries=lambda e: ns["_karafun_search_queries_for_entry"](me, e))
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


DURATIONS = [None, True, False, 0, 1, 59.5, 60, 90, 86400, 86400.5, 86401, -5, 0.4, 0.5, 1.5, 2.5, 3.5, 1e-9, "", " ", "3:41", "03:41", "1:02:03", "0:00", "0:60", "1:60:00",
             "60:00", "1:2", "1:2:3:4", "12", "12.5", "12.", ".5", "1e3", "abc", "3:4x", "24:00:00", "24:00:01", "-3:00", "+3", " 3:41 ", "3 :41", "0:0:0:1", "0:0:1:1", "1:0:0:0", "0:0:5", "inf", "nan", "0", "0.0",
             "00:00:01", "1:00:00", "59:59", "0:59", "99999999999999999999:00", "5:99999999999999999999", "1:99999999999999999999:00", "0.5", "1.5", "2.5", "86400", "86400.4",
             "86400.6", "\u0663:\u0664\u0661", "\uff11\uff12", "\u0663", "3:\u0664\u0661", "3\u00a0", "\u00a03:41\n", "1_0", "0x10", "٣"]
PARTS = ["", "Song (Karaoke Version)", "Song [Explicit]", "Hello (feat. X)", "Hello feat. X", "Hello ft Someone", "Hello featuring Y", "Don't Stop Believin'", "Rock & Roll",
         "AC/DC", "Beyonc\u00e9 (Live)", "Zo\u00eb", "Mr. Brightside - Official Lyric Video HD", "KARAOKE", "x_y", "a\nfeat b\n", "Name feat\\x", "Na\u00efve (clean)",
         "(remastered)", "\u212aARAOKE song", "Stra\u00dfe (live)", "I\u2019m Yours", "back`tick", "Lyrics lyric video", "Remastered Remaster", "feat\\. x (feat\\. y)",
         "Title (live) [clean] version", "Don't (explicit", "100% Pure Love", "\u00c0\u00d6\u00d8\u00f6\u00f8\u00ff \u00d7\u00f7", "tab\tsep", "end feat. \n", "FT. Someone",
         "heyfeat. nobody", "The Lyrics", "HD", "hd video", "(feat. a", "[ft. b]", "\u0130stanbul", "caf\u00e9 \u212a", "\u03a3\u0391\u03a3 (live)"]


def entries():
    out = []
    for a in PARTS[:14] + [""]:
        for t_ in PARTS[:14] + [""]:
            out.append({"artist": a, "title": t_})
    out += [{"title": "", "display_name": "AC/DC - Back In Black"}, {"title": "", "display": "Queen - Don't Stop Me Now - Live"}, {"title": "", "display_name": "Just a title"},
            {"title": "", "display_name": " - "}, {"title": "", "display_name": "A - "}, {"title": "", "display": "A - B - C"}, {"artist": "Given", "title": "", "display_name": "Other - Name"},
            {"artist": None, "title": None, "display_name": None}, {}, {"artist": "  Pad  ", "title": "  Title  "}, {"title": "Same", "artist": "same"}, {"title": "SAME", "artist": "Same"},
            {"title": "x", "artist": "y"}, {"title": " ", "display_name": " ", "display": "Zed - Song"}, {"title": "", "display_name": "", "display": ""}]
    return out


KINDS = ["FOUND|10|20|3:41", "TITLE_ONLY|1|2|", "FIRST|1|2|3:00", "ERROR|No usable KaraFun search result", "ERROR|KaraFun is not running", "SEARCHED", "", "FOUND|1", "FOUND|1|2",
         "x|y|z", "FOUND||", "TITLE_ONLY|a|b", "|||", "found|1|2|3", "FIRST|1|2"]


def ref_classify(candidate, safe_artist, query):
    """The accept/reject rule exactly as written inline in KaraokeApp._automate_karafun_search_and_play (transcribed; test_karafun_script_rs.py guards the Python text)."""
    safe_artist_match = str(safe_artist or "").strip()
    query_key = query.casefold()
    query_has_artist = bool(safe_artist_match) and safe_artist_match.casefold() in query_key
    if safe_artist_match and not query_has_artist:
        accepted = {"FOUND"}
    else:
        accepted = {"FOUND", "TITLE_ONLY", "FIRST"}
    parts = str(candidate or "").split("|")
    kind = parts[0] if parts else ""
    if len(parts) >= 3 and kind in accepted:
        return {"verdict": "accepted", "parts": parts}
    if len(parts) >= 3 and kind in {"FOUND", "TITLE_ONLY", "FIRST"}:
        return {"verdict": "rejected", "kind": kind}
    return {"verdict": "no_match", "error": parts[-1] if parts else "KaraFun search failed"}


def check_pure(binary, diffs, seeds):
    r = random.Random(7)
    bad_decline = [0]

    def batch(reqs):
        return run_rust(binary, reqs)

    # durations
    vals = DURATIONS + ["".join(r.choice("0123456789:. ") for _ in range(r.randint(0, 9))) for _ in range(seeds)]
    for v, g in zip(vals, batch([{"op": "duration", "value": v} for v in vals])):
        want = PY["duration"](v)
        if "decline" in g:
            if not any(not c.isascii() and c.isnumeric() for c in str(v)):
                diffs.append(("duration", v, f"unexpected decline {g}"))
            continue
        if g["seconds"] != want:
            diffs.append(("duration", v, f"rust={g['seconds']!r} python={want!r}"))
    # remaining
    nums = [0, 1, 59, 60, 100, 100.9, -3, -3.9, 12345.678, 0.5, True, False]
    offs = [None, 0, 0.0, 5, -5, 5.9, -5.9, 100.5, True, False]
    reqs = [{"op": "remaining", "duration": d, "elapsed_wall": e, "seek_offset": o} for d in nums for e in nums for o in offs]
    for q, g in zip(reqs, batch(reqs)):
        want = PY["remaining"](q["duration"], q["elapsed_wall"], q["seek_offset"])
        if g["remaining"] != want:
            diffs.append(("remaining", q, f"rust={g['remaining']} python={want}"))
    # needs_attention
    msgs = ["ADJUSTED|true|true", "ADJUSTED|false|false", "ADJUSTED|true|false", "ADJUSTED|false|true", "adjusted|TRUE|true", "", "x|true|", "|true|", "true", "ADJUSTED|true|true|true", "A|True|TRUE"]
    reqs = [{"op": "needs_attention", "requested_key": k, "requested_tempo": tp, "message": m, "already_applied": ap}
            for k in (-2, 0, 1) for tp in (90, 100, 105) for m in msgs for ap in (False, True)]
    for q, g in zip(reqs, batch(reqs)):
        want = PY["needs"](q["requested_key"], q["requested_tempo"], q["message"], q["already_applied"])
        if g["needs_attention"] != want:
            diffs.append(("needs_attention", q, f"rust={g['needs_attention']} python={want}"))
    # clean part
    parts = PARTS + TITLES + ["".join(r.choice("abc xyzFfEeAaTt.()[]&'`\\\n\u00e9\u2019-_|feat ft live karaoke") for _ in range(r.randint(0, 30))) for _ in range(seeds)]
    for s, g in zip(parts, batch([{"op": "clean_part", "value": s} for s in parts])):
        if g["clean"] != PY["clean"](s):
            diffs.append(("clean_part", s, f"rust={g['clean']!r} python={PY['clean'](s)!r}"))
    # entry + query ladder
    es = entries() + [{"artist": r.choice(PARTS), "title": r.choice(PARTS), "display_name": r.choice(PARTS + [""]), "display": r.choice(PARTS + [""])} for _ in range(seeds)]
    for e, g in zip(es, batch([dict(e, op="entry") for e in es])):
        a, t_ = PY["entry"](e)
        if (g["artist"], g["title"]) != (a, t_):
            diffs.append(("entry", e, f"rust={(g['artist'], g['title'])!r} python={(a, t_)!r}"))
    for e, g in zip(es, batch([dict(e, op="queries") for e in es])):
        if g["queries"] != PY["queries"](e):
            diffs.append(("queries", e, f"rust={g['queries']!r} python={PY['queries'](e)!r}"))
    # accept/reject
    reqs = []
    for cand in KINDS:
        for art in ("", "Sugarcult", " Sugarcult ", "ZOE", "Stra\u00dfe", "STRASSE", "\u03a3", "\u00df"):
            for q in ("Sugarcult Memory", "Memory", "memory sugarcult", "Zo\u00eb song", "zoe", "strasse x", "Stra\u00dfe x", "\u03c3\u03c2", ""):
                reqs.append({"op": "classify", "candidate": cand, "safe_artist": art, "query": q})
    for q, g in zip(reqs, batch(reqs)):
        want = ref_classify(q["candidate"], q["safe_artist"], q["query"])
        if g != want:
            diffs.append(("classify", q, f"rust={g} python={want}"))
    return len(vals), len(reqs), len(parts), len(es)


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
    nd, nc, npart, ne = check_pure(args.binary, diffs, min(args.seeds, 1500))
    print(f"search scripts={len(cs)} titles={len(strings)} literals={len(lits)}; durations={nd} classify={nc} clean parts={npart} entries={ne}")
    for kind, c, why in diffs[:8]:
        print(f" - {kind}: {json.dumps(c, ensure_ascii=False)[:200]}\n   {why[:400]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
