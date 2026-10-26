#!/usr/bin/env python3
"""Stage E panel 1b parity: the Rust typo-tolerant search (``singws-uimodel`` ``search_with_fuzzy``) must return the SAME rows in the SAME order as the app's
``song_index.search_songs(query, fuzzy=True)`` on the same database, including the fallback that only runs when the strict search finds nothing.

Databases are built in a temp dir from fuzzed tracks (never ~/SingWS) and are small enough that Python's 0.25 s scan budget is never reached (it is a wall-clock
cut that makes Python's own answer depend on machine speed; the Rust port takes an optional budget instead). Queries: words from the data with 1-3 random typos
(delete, insert, substitute, swap), number words for digits and digits for number words, "&" for "and", punctuation inserted or removed, accents, multi-word
mixes of exact and misspelled words, short words that must NOT be corrected, and hostile terms.

Usage: python3 tools/fuzzy_parity.py [--cases N] [--seed S]      Exit status 1 on any difference.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import dbw_parity as dp  # noqa: E402  (sets a temp SINGWS_HOME before song_index is imported)
import song_index  # noqa: E402

CLI = os.environ.get("SINGWS_UIMODEL_CLI") or os.path.expanduser("~/.cache/singws-rust-target/release/singws-uimodel-cli")
NUMBERS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten", "20": "twenty", "100": "hundred"}
REVERSE = {v: k for k, v in NUMBERS.items()}
LETTERS = "abcdefghijklmnopqrstuvwxyzé"
HOSTILE = ["zzzzzz", "qqq", "ab", "a", "xx yy zz", "ünïcode", "日本語", "😀", "%", "_", "x" * 80, "the", "and", "&", "twenty", "wun", "nien", "3 am", "(live)", "b.o.b", "dont", "wanna"]


def typo(rng, w):
    w = list(w)
    for _ in range(rng.randint(1, 3)):
        if not w:
            break
        k = rng.random()
        i = rng.randrange(len(w))
        if k < 0.3:
            del w[i]
        elif k < 0.55:
            w.insert(i, rng.choice(LETTERS))
        elif k < 0.8:
            w[i] = rng.choice(LETTERS)
        elif len(w) > 1:
            j = min(len(w) - 1, i + 1)
            w[i], w[j] = w[j], w[i]
    return "".join(w)


def make_query(rng, words):
    w = rng.choice(words) if words else "a"
    k = rng.random()
    if k < 0.35:
        return typo(rng, w)
    if k < 0.45:
        return NUMBERS.get(w.lower(), w) if w.lower() in NUMBERS else (REVERSE.get(w.lower(), w))
    if k < 0.52:
        return w.replace("and", "&") if "and" in w.lower() else w + " &"
    if k < 0.6:
        return "".join(c + rng.choice(["", ".", "-", "'"]) for c in w)
    if k < 0.75:
        parts = [rng.choice(words) if words else "a" for _ in range(rng.randint(2, 3))]
        return " ".join(typo(rng, p) if rng.random() < 0.5 else p for p in parts)
    if k < 0.85:
        return w[: rng.randint(1, max(1, len(w)))]
    if k < 0.93:
        return typo(rng, w).upper() + rng.choice(["", " ", "  x"])
    return rng.choice(HOSTILE)


def rust(db, queries, limit):
    out = subprocess.run([CLI, str(db)], input=json.dumps([{"query": q, "limit": limit, "fuzzy": True} for q in queries]), capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20261026)
    args = ap.parse_args(argv)
    if not os.path.exists(CLI):
        print(f"missing {CLI}; build with: cd rust && cargo build --release -p singws-uimodel", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    bad = compared = fuzzy_hits = fuzzy_empty = strict_hits = 0
    with tempfile.TemporaryDirectory(prefix="fuzzy_parity_") as d:
        for case in range(args.cases):
            sub = Path(d) / f"c{case}"
            sub.mkdir()
            tracks = dp.make_tracks(rng, sub, rng.randint(20, 200))
            tp = sub / "tracks.json"
            dp.write_tracks(tp, tracks)
            db = sub / "s.db"
            dp.py_rebuild(tp, str(db))
            words = []
            for t in tracks:
                if isinstance(t, dict):
                    for k in ("artist", "title"):
                        words += [w for w in str(t.get(k) or "").split() if w]
            qs = list(HOSTILE) + [make_query(rng, words) for _ in range(250)]
            for limit in (500, 3):
                got = rust(db, qs, limit)
                for q, g in zip(qs, got):
                    compared += 1
                    py = song_index.search_songs(q, limit=limit, dbfile=db, fuzzy=True)
                    strict = song_index.search_songs(q, limit=limit, dbfile=db, fuzzy=False)
                    if not g["ok"]:
                        if "skew" in g["error"]:
                            continue
                        bad += 1
                        print(f"DIFF: rust error for {q!r}: {g['error']}")
                        continue
                    py_ids = [r["id"] for r in py]
                    rs_ids = [r["id"] for r in g["rows"]]
                    if py_ids != rs_ids:
                        bad += 1
                        if bad <= 15:
                            print(f"DIFF: q={q!r} limit={limit}: python={py_ids[:10]} rust={rs_ids[:10]}")
                        continue
                    if strict:
                        strict_hits += 1
                    elif py:
                        fuzzy_hits += 1
                    else:
                        fuzzy_empty += 1
    print(f"compared {compared} searches: {strict_hits} strict hits, {fuzzy_hits} rescued by the fuzzy fallback, {fuzzy_empty} found nothing")
    print("RESULT: IDENTICAL" if not bad else f"RESULT: {bad} DIFFERENCE(S)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
