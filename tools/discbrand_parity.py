#!/usr/bin/env python3
"""Parity: the karaoke disc / brand rules of 0.2.18.1.py (the block from DISC_ID_PRIORITY to pick_by_disc_priority, executed unchanged) vs the Rust `singws-discbrand`, on random disc ids
(real brand names and aliases, coded discs like "KV 43403", paths, separators, mixed case, accents, empty values, unknown companies), request dicts, priority lists and libraries.
Compared: every answer, exactly. Values that are not text are not generated (the Rust side declines them).

  CARGO_TARGET_DIR=... python3 tools/discbrand_parity.py [--seeds N]
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-karafun")) + "/release/singws-discbrand"


def load():
    lines = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8").split("\n")
    a = next(i for i, l in enumerate(lines) if l.startswith("DISC_ID_PRIORITY = ["))
    b = next(i for i, l in enumerate(lines) if l.startswith("# Lead-silence detection for better BG->karaoke"))
    ns = {"re": re, "os": os, "Path": Path}
    exec(compile("\n".join(lines[a:b]), "0.2.18.1.py", "exec"), ns)
    return ns


BRANDS = ["KV", "kv", "Karaoke Version", "KARAOKE-VERSION", "karaoke_version", "SC", "Sound Choice", "SoundChoice", "sound-choice", "CC", "cc", "CB", "Chartbuster", "Chart Buster",
          "ZM", "Zoom", "Zoom Karaoke", "ZDL", "PT", "Party Tyme", "PartyTime", "SF", "Sunfly", "Sun Fly", "SBI", "SBI Karaoke", "KARAFUN", "Kara Fun", "ME", "Mr Entertainer", "MRENTERTAINER"]
UNKNOWN = ["XYZ", "ABC123", "NewCo", "AB-12", "Zoë Records", "K", "", "  ", "THE", "LONGBRANDNAME12", "ABCDEFGHIJK1", "x1", "123", "-", "TS", "PHM"]
CODES = ["", " 00", " 43403", "-12", "_7", "123", " 4 5", "x", "VOL2", " Disc 1"]
SEPS = [" - ", "/", "|", "(", ")", "[", "]", "{", "}", " ", "\\", "  -  ", " -", "- "]


def disc(r):
    k = r.random()
    base = r.choice(BRANDS) if k < 0.6 else r.choice(UNKNOWN)
    v = base + r.choice(CODES) if r.random() < 0.5 else base
    if r.random() < 0.15:
        v = r.choice(["", "x ", "\t"]) + v
    if r.random() < 0.2:
        v = v + r.choice(SEPS) + r.choice(["Artist", "Song Title", "SC12345", "Song (Live)", "a.zip", "KV 1"])
    if r.random() < 0.1:
        v = r.choice(["/music/", "C:\\x\\", "a/b/", "./"]) + v + r.choice([".zip", ".cdg", ".mp3", "", "."])
    return v


def track(r):
    t = {}
    for k in ("disc_id", "discid", "DiscID", "Vendor", "vendor", "display", "filename", "file", "path"):
        if r.random() < 0.35:
            t[k] = r.choice([disc(r), None, "", disc(r)])
    return t


def case(r):
    kind = r.choice(["info", "canonical", "canonical", "segments", "priority", "matches", "candidates", "effective", "choices", "preferred", "pick", "report"])
    if kind in ("info", "segments"):
        return {"op": kind, "value": disc(r)}
    if kind == "canonical":
        return {"op": kind, "value": disc(r), "allow_unknown": r.random() < 0.6}
    if kind == "priority":
        value = ",".join(disc(r) for _ in range(r.randrange(0, 5))) if r.random() < 0.5 else [r.choice([disc(r), None, ""]) for _ in range(r.randrange(0, 12))]
        return {"op": kind, "value": value, "max": r.choice([10, 10, 3, 1, 2])}
    if kind == "matches":
        return {"op": kind, "value": disc(r), "brand": r.choice(BRANDS + [disc(r)])}
    if kind == "candidates":
        return {"op": kind, "track": track(r)}
    if kind == "effective":
        return {"op": kind, "host": [disc(r) for _ in range(r.randrange(0, 5))], "raw": r.choice(["", disc(r), "  ", r.choice(BRANDS)]), "override": [disc(r) for _ in range(r.randrange(0, 3))], "max": r.choice([10, 3])}
    if kind == "choices":
        return {"op": kind, "tracks": [r.choice([track(r), track(r), {"disc_id": disc(r)}, {"disc_id": r.choice(BRANDS)}, 5, "x", None, []]) for _ in range(r.randrange(0, 25))]}
    if kind == "preferred":
        keys = ["preferred_brand", "preferred_version", "preferred_disc", "preferred_disc_id", "preferred_disc_priority", "brand", "version", "disc_id", "discid", "other"]
        req = {k: r.choice([disc(r), "", None, [disc(r), disc(r)], [], disc(r) + "," + disc(r), " , " + disc(r)]) for k in keys if r.random() < 0.3}
        return {"op": kind, "req": req}
    if kind == "pick":
        return {"op": kind, "matches": [track(r) for _ in range(r.randrange(0, 6))], "priority": [r.choice(BRANDS + [disc(r)]) for _ in range(r.randrange(0, 4))]}
    return {"op": "report", "tracks": [r.choice([track(r), track(r), track(r), 5, None]) for _ in range(r.randrange(0, 25))], "max": r.choice([50, 3, 1])}


def run_python(ns, c):
    op = c["op"]
    if op == "info":
        return ns["karaoke_brand_match_info"](c["value"])
    if op == "canonical":
        return ns["canonical_disc_brand"](c["value"], allow_unknown=c["allow_unknown"])
    if op == "segments":
        return ns["_disc_brand_value_segments"](c["value"])
    if op == "priority":
        return ns["normalize_disc_priority"](c["value"], max_items=c["max"])
    if op == "matches":
        return ns["disc_value_matches_brand"](c["value"], c["brand"])
    if op == "candidates":
        return ns["disc_brand_candidate_values"](c["track"])
    if op == "effective":
        return ns["effective_disc_priority"](c["host"], c["raw"], c["override"], max_items=c["max"])
    if op == "choices":
        return ns["library_brand_choices"](c["tracks"])
    if op == "preferred":
        return ns["request_preferred_disc_brand"](c["req"])
    if op == "pick":
        m = c["matches"]
        if not m:
            return None
        chosen = ns["pick_by_disc_priority"](m, c["priority"])
        return next(i for i, x in enumerate(m) if x is chosen)
    return ns["build_disc_brand_scan_report"](c["tracks"], max_unknowns=c["max"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=6000)
    a = ap.parse_args(argv)
    ns = load()
    cases = [case(random.Random(i)) for i in range(a.seeds)]
    want = [run_python(ns, c) for c in cases]
    out = subprocess.run([CLI], input=json.dumps({"batch": cases}), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("cli failed: " + out.stderr[:300])
    got = json.loads(out.stdout)
    diffs, declined, kinds = [], 0, {}
    for c, w, g in zip(cases, want, got):
        if "declined" in g:
            declined += 1
            continue
        kinds[c["op"]] = kinds.get(c["op"], 0) + 1
        if json.loads(json.dumps(w)) != g["v"]:
            diffs.append((c, w, g["v"]))
    print(f"cases={len(cases)} declined={declined} compared: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    for c, w, g in diffs[:4]:
        print(f" - {json.dumps(c)[:260]}\n   py={json.dumps(w)[:260]}\n   rs={json.dumps(g)[:260]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
