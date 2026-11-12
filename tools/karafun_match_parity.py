#!/usr/bin/env python3
"""Parity of the Rust song-matching rules (rust/crates/singws-karafun-svc, src/matching.rs, milestone W7) against the REAL JavaScript of karafun_bridge.js.
tools/karafun_match_oracle.js cuts the matching block (`QUAL` .. `choose`) and the search-term ladder out of the page script's own text and runs them in Node;
the same requests go to the Rust CLI and every answer must be equal. Requests: `words`, `title_key`, `artist_matches`, `terms`, `choose` over adversarial text
(qualifier brackets, "&"/"and", accents, curly apostrophes, CJK, Cyrillic, Greek final sigma, Turkish dotted I, ligatures, full-width forms, astral letters,
combining marks, NBSP/BOM/NEL, empty) plus random code points. Needs: node, and  cd rust && cargo build --release -p singws-karafun-svc
  .venv-test-arm64-fresh/bin/python tools/karafun_match_parity.py [--cases N] [--seed S]      exit 1 on any difference
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def find_cli() -> str:
    for c in (os.environ.get("SINGWS_KF_SVC_BIN", ""), os.path.join(os.environ.get("CARGO_TARGET_DIR", ""), "release", "singws-karafun-svc"),
              os.path.expanduser("~/.cache/singws-rust-target-karafun-svc/release/singws-karafun-svc"), str(ROOT / "rust/target/release/singws-karafun-svc")):
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("singws-karafun-svc not built")


WORDS = ["love", "Love", "the", "The", "and", "feat", "featuring", "a", "of", "you", "Me", "NOFX", "Linoleum", "Memory", "Sugarcult", "Cats", "Don’t", "Don't", "Rock`n`Roll",
         "AC/DC", "Guns N' Roses", "Beyoncé", "Café", "naïve", "Zoë", "Ünïcode", "Ångström", "Σίσυφος", "ΟΔΥΣΣΕΥΣ", "İstanbul", "Straße", "ﬁnal", "ＡＢＣ", "２０２４", "²", "½",
         "日本語", "한국어", "Привет", "мир", "😀", "𝕒bc", "x́", "ä", "ḋ", "कि", "ab cd", "﻿bom", "nel\u0085x", "ß", "Ǆ", "ǅ", "Voilà", "cre\u0300me", "crème", "e\u0300x", "ʻokina", "N°1", "#1", "100%",
         "50", "7", "Mr.", "Dr.", "Ms", "O'Brien", "ft.", "soft", "deliver", "Mix", "Remix", "Karaoke", "Version", "Live", "Radio Edit", "Single", "Acoustic", "Explicit", "Clean", "Remaster"]
QUALS = ["(Live)", "[Live]", "(Karaoke Version)", "[Remix]", "(feat. Someone)", "(ft. X)", "(Radio Edit)", "(2011 Remaster)", "(Acoustic)", "(Single Version)", "(Explicit)",
         "(Clean)", "(From the film)", "(Part 1)", "[Mix]", "(delivered)", "(soft)", "()", "( )", "(unclosed", "closed)", "[unclosed", "(a (live) b)", "(LIVE)", "[KARAOKE]", "(Kelvin Karaoke)",
         "(ſ)", "(re mix)"]
EDGE_SPACE = [chr(c) for c in (0x1680, 0x1681, 0x167f, 0x2000, 0x2001, 0x200a, 0x200b, 0x2027, 0x2028, 0x2029, 0x202a, 0x202f, 0x2030, 0x205f, 0x2060, 0x3000, 0x3001, 0xb, 0xc, 0xa0, 0xfeff, 0x85, 0x180e)]
SEPS = [" ", " ", " ", " - ", " & ", " and ", ", ", "  ", "\t", "-", "/"]


def text(rng, maxw=4, quals=True):
    parts = [rng.choice(WORDS) for _ in range(rng.randint(0, maxw))]
    out = ""
    for p in parts:
        out += (rng.choice(SEPS) if out else "") + p
        if quals and rng.random() < 0.2:
            out += " " + rng.choice(QUALS)
    if rng.random() < 0.03:
        out = "".join(chr(rng.choice([rng.randint(0x20, 0x24f), rng.randint(0x370, 0x4ff), rng.randint(0x3040, 0x30ff), rng.randint(0x1d400, 0x1d7ff), rng.randint(0x2000, 0x20ff)])) for _ in range(rng.randint(0, 6)))
    return out


def variants(rng, base_t, base_a):
    t = base_t + (" " + rng.choice(QUALS) if rng.random() < 0.4 else "")
    a = base_a if rng.random() < 0.6 else (text(rng, 2, False) if rng.random() < 0.5 else base_a + " " + rng.choice(WORDS))
    return t, a


def request(rng) -> dict:
    r = rng.random()
    if r < 0.12:
        return {"op": "words", "t": text(rng)}
    if r < 0.22:
        return {"op": "title_key", "t": text(rng)}
    if r < 0.35:
        return {"op": "artist_matches", "have": text(rng, 3, False), "want": text(rng, 3, False)}
    if r < 0.5:
        qs = [rng.choice([text(rng, 3), rng.choice(EDGE_SPACE), "  ", " ", "﻿", "", "\u0085", "a?", "What?!", "It's (Live)"]) for _ in range(rng.randint(1, 5))]
        if rng.random() < 0.3:
            qs.append(qs[0])
        return {"op": "terms", "queries": qs}
    bt, ba = text(rng, 3), text(rng, 2, False)
    lst = []
    for i in range(rng.randint(0, 6)):
        t, a = variants(rng, bt, ba)
        lst.append({"id": rng.randint(1, 8), "title": t, "artist": a})
    ident = rng.randint(1, 9)
    want_title = rng.choice([bt, bt, text(rng, 3), ""])
    return {"op": "choose", "list": lst, "id": ident, "want_artist": rng.choice([ba, ba, text(rng, 2, False), ""]), "want_title": want_title,
            "query": rng.choice([bt + " " + ba, ba + " " + bt, text(rng, 4), ""])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    reqs = [request(rng) for _ in range(a.cases)]
    node_in = "\n".join(json.dumps(r) for r in reqs) + "\n"
    rust_in = "\n".join(json.dumps(dict(r, kind="match")) for r in reqs) + "\n"
    node = subprocess.run(["node", str(ROOT / "tools" / "karafun_match_oracle.js")], input=node_in, capture_output=True, text=True, timeout=600)
    if node.returncode != 0:
        print(node.stderr[-800:])
        return 2
    rust = subprocess.run([find_cli(), "--batch"], input=rust_in, capture_output=True, text=True, timeout=600)
    nl, rl = node.stdout.strip().split("\n"), rust.stdout.strip().split("\n")
    if len(nl) != len(reqs) or len(rl) != len(reqs):
        print("answer counts differ", len(nl), len(rl), len(reqs))
        return 2
    per: dict = {}
    first = []
    diff = declined = 0
    for q, n, r in zip(reqs, nl, rl):
        n, r = json.loads(n), json.loads(r)
        if "declined" in r:
            declined += 1
            continue
        per.setdefault(q["op"], [0, 0])[0] += 1
        if n != r:
            diff += 1
            per[q["op"]][1] += 1
            if len(first) < 4:
                first.append((q, n, r))
    print(f"requests {a.cases}: different {diff}, declined {declined}; per op (total, different): {json.dumps(per, sort_keys=True)}")
    print("choose results: found", sum(1 for q, n in zip(reqs, nl) if q["op"] == "choose" and json.loads(n)["v"] >= 0), "none", sum(1 for q, n in zip(reqs, nl) if q["op"] == "choose" and json.loads(n)["v"] < 0))
    for q, n, r in first:
        print("--- request:", json.dumps(q, ensure_ascii=True)[:600], "\n node:", json.dumps(n, ensure_ascii=True)[:300], "\n rust:", json.dumps(r, ensure_ascii=True)[:300])
    print("IDENTICAL" if diff == 0 else "DIFFERENT")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
