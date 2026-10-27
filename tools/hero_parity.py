#!/usr/bin/env python3
"""Stage E hero-text parity: the Rust now-playing hero view model (singws-uimodel `hero`, `singws-hero-cli`) against the app's REAL Python.

Oracle = the app module: ``KaraokeApp._apply_redesign_hero_text`` (run on a stand-in whose subtitle / meta labels record ``setText``; it also reports the format badge
source ``_hero_format_text``), ``_hero_media_badge_text``, ``_hero_remaining_text`` (the time-left label's text) and ``_fmt_karaoke_time``, all the real methods bound
onto a plain object. Fuzzed: titles / artists / brands / paths of every kind (blank, padded, non-text, KaraFun streaming ids in odd case, every extension the badge
knows plus unknown and extensionless, dotted directories, hidden files, Unicode), label texts with and without the minus, and times (floats at the .5 rounding
points, negatives, numeric text, junk, booleans, huge). Where Python raises the Rust side must raise or decline.

Run:  QT_QPA_PLATFORM=offscreen /Users/daniel/Documents/SingWS/.venv/bin/python tools/hero_parity.py [--cases N] [--seed S]
Exit status 1 on any difference.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import history_parity as hp  # noqa: E402  (sets a scratch SINGWS_HOME and loads the app module)

CLI = os.environ.get("SINGWS_HERO_CLI") or os.path.join(os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target"), "release", "singws-hero-cli")
failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 25:
        print("DIFF:", msg[:900])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def rust(req):
    out = subprocess.run([CLI], input=json.dumps(req), capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


class Label:
    def __init__(self, text=""):
        self._t = text
        self._css = ""

    def setText(self, t):
        self._t = t

    def text(self):
        return self._t

    def styleSheet(self):
        return self._css

    def setStyleSheet(self, css):
        self._css = css


def fake():
    ka = hp.app().KaraokeApp
    f = types.SimpleNamespace(rotation_summary_subtitle=Label(), rotation_summary_meta=Label(), karaoke_remaining_label=Label())
    f._ui_redesign_on = lambda: True
    for name in ("_apply_redesign_hero_text", "_hero_media_badge_text", "_hero_remaining_text", "_fmt_karaoke_time"):
        setattr(f, name, types.MethodType(getattr(ka, name), f))
    return f


TEXTS = ["Radio Ga Ga", "  Padded  ", "", None, 0, 7, 2.5, True, "İstanbul", "Straße", "東京", "A · B", " ", "x\u3000", "\u001c", "ǅ"]
EXTS = [".mp4", ".MP4", ".mkv", ".MOV", ".zip", ".ZIP", ".cdg", ".mp3", ".Mp3", ".wav", ".flac", ".m4a", ".ogg", ".aif", ".tar.gz", ".", "", ".x.y", ".ß", ".mp4 ", ".MP3\t"]
DIRS = ["/music/", "C:\\x\\", "/a.b/", "", "./", "/a/.hidden/", "  /pad/", "/m/Song Name"]
BASES = ["song", ".hidden", "..dots", "a.b", "Ünal", "KaraFun_Streaming:123", "karafun_streaming:9", "KARAFUN_STREAMING:", "x"]


def rand_path(rng):
    r = rng.random()
    if r < 0.1:
        return rng.choice([None, "", " ", 0, False, 5])
    return rng.choice(DIRS) + rng.choice(BASES) + rng.choice(EXTS)


def rand_time(rng):
    return rng.choice([0, 59.5, 60.5, 61.5, 0.5, 1.5, 2.5, -4, -0.2, 3599, 3599.5, 6000, 123456.7, "125", " 90.5 ", "x", "", None, True, False, [], {}, "nan", "inf", 1e300, 10**15, "1e3"])


def check(label, py_fn, req, key, ctx):
    try:
        py = py_fn()
        err = None
    except Exception as exc:  # noqa: BLE001
        py, err = None, repr(exc)
    r = rust(req)
    if "error" in r:
        if "decline" in r["error"]:
            tally(label + "_declined")
            return
        if err is None:
            fail(f"{label}: Rust errored ({r['error']}) but Python returned {py!r}: {ctx}")
        else:
            tally(label + "_both_error")
        return
    if err is not None:
        fail(f"{label}: Python raised ({err}) but Rust returned {r!r}: {ctx}")
        return
    got = {k: r[k] for k in key} if isinstance(key, list) else r[key]
    if got != py:
        fail(f"{label}: {ctx}\n python={py!r}\n rust  ={got!r}")
    else:
        tally(label + "_same")


def run_case(rng):
    f = fake()
    title, artist, brand = rng.choice(TEXTS), rng.choice(TEXTS), rng.choice(TEXTS)
    path = rand_path(rng)

    def py_text():
        f2 = fake()
        f2._apply_redesign_hero_text(title, artist, brand, path)
        return {"subtitle": f2.rotation_summary_subtitle.text(), "meta": f2.rotation_summary_meta.text(), "format_text": f2._hero_format_text}

    check("text", py_text, {"op": "text", "title": title, "artist": artist, "brand": brand, "path": path}, ["subtitle", "meta", "format_text"], f"{title!r} {artist!r} {brand!r} {path!r}")
    check("badge", lambda: f._hero_media_badge_text(path), {"op": "badge", "path": path}, "badge", repr(path))
    label = rng.choice(["03:21", "-03:21", "", " ", "  -1:00", "- 5", "--", None, "x", "–3", "−3", 0])
    check("remaining", lambda: (lambda g: (g.karaoke_remaining_label.setText(label), g._hero_remaining_text())[1])(fake()), {"op": "remaining", "label": label}, "text", repr(label))
    t = rand_time(rng)
    check("time", lambda: f._fmt_karaoke_time(t), {"op": "time", "sec": t}, "text", repr(t))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261026)
    args = ap.parse_args(argv)
    if not os.path.exists(CLI):
        print(f"missing {CLI}; build with: cd rust && cargo build --release -p singws-uimodel", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    for _ in range(args.cases):
        run_case(rng)
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("RESULT: IDENTICAL" if not failures else f"RESULT: {len(failures)} DIFFERENCE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
