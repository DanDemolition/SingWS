#!/usr/bin/env python3
"""Stage E panel 1 parity: the Rust library search (singws-uimodel) must return the SAME rows in the SAME order as the app's
``song_index.search_songs(query, fuzzy=False)`` on the same database. Read-only on both sides; databases are built in a temp dir from fuzzed
tracks (never ~/SingWS). Queries come from the data (artist/title words, prefixes, multi-word) plus hostile terms (``%`` and ``_`` wildcards,
quotes, accents, very long, whitespace-only). Also checks the typo-fallback flag and that the database file is untouched.

Usage: python3 tools/uimodel_parity.py [--cases N] [--seed S]      Exit status 1 on any difference.
"""
from __future__ import annotations

import argparse
import hashlib
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
HOSTILE = ["%", "_", "100%", "a_b", "'", '"', "\\", "é", "ünï", "日本", "😀", "x" * 300, "  ", "\t", "the", "and", "3 am", "(live)", "a b c d e f g", "%%", "[a-z]", "a%b"]


def rust(db, queries, limit):
    out = subprocess.run([CLI, str(db)], input=json.dumps([{"query": q, "limit": limit} for q in queries]), capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20261023)
    args = ap.parse_args(argv)
    if not os.path.exists(CLI):
        print(f"missing {CLI}; build with: cd rust && cargo build --release -p singws-uimodel", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    bad = compared = 0
    with tempfile.TemporaryDirectory(prefix="uimodel_parity_") as d:
        for case in range(args.cases):
            sub = Path(d) / f"c{case}"
            sub.mkdir()
            tracks = dp.make_tracks(rng, sub, rng.randint(20, 150))
            tp = sub / "tracks.json"
            dp.write_tracks(tp, tracks)
            db = sub / "s.db"
            dp.py_rebuild(tp, str(db))
            words = []
            for t in tracks:
                if isinstance(t, dict):
                    for k in ("artist", "title", "display"):
                        words += [w for w in str(t.get(k) or "").split() if w]
            qs = list(HOSTILE)
            for _ in range(120):
                k = rng.random()
                w = rng.choice(words) if words else "a"
                if k < 0.3:
                    qs.append(w)
                elif k < 0.5:
                    qs.append(w[: rng.randint(1, max(1, len(w)))])
                elif k < 0.8:
                    qs.append(" ".join(rng.choice(words) for _ in range(rng.randint(2, 3))) if words else "a b")
                else:
                    qs.append(w.upper() + rng.choice(["", " ", "  x"]))
            before = hashlib.sha256(db.read_bytes()).hexdigest()
            for limit in (500, 3):
                got = rust(db, qs, limit)
                for q, g in zip(qs, got):
                    compared += 1
                    py = song_index.search_songs(q, limit=limit, dbfile=db, fuzzy=False)
                    if not g["ok"]:
                        bad += 1
                        print(f"DIFF: rust error for {q!r}: {g['error']}")
                        continue
                    py_ids = [r["id"] for r in py]
                    rs_ids = [r["id"] for r in g["rows"]]
                    if py_ids != rs_ids:
                        bad += 1
                        if bad <= 15:
                            print(f"DIFF: q={q!r} limit={limit}: python={py_ids[:8]} rust={rs_ids[:8]}")
                        continue
                    for a, b in zip(py, g["rows"]):
                        for k in ("path", "artist", "title", "discid", "duration_secs", "song_type", "display"):
                            pa = a[k]
                            pa = "" if pa is None and k not in ("duration_secs",) else pa
                            if k != "duration_secs" and pa is not None and not isinstance(pa, str):
                                pa = str(pa)
                            if pa != b[k]:
                                bad += 1
                                print(f"DIFF: q={q!r} id={a['id']} field {k}: python={pa!r} rust={b[k]!r}")
                    if g["fuzzy_fallback_needed"] != (len(py) == 0 and bool(song_index.normalize_text(q))):
                        bad += 1
                        print(f"DIFF: q={q!r} fuzzy flag {g['fuzzy_fallback_needed']} but python rows={len(py)}")
            if hashlib.sha256(db.read_bytes()).hexdigest() != before:
                bad += 1
                print("DIFF: the database file changed during read-only searches")
    print(f"compared {compared} searches")
    print("RESULT: IDENTICAL" if not bad else f"RESULT: {bad} DIFFERENCE(S)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
