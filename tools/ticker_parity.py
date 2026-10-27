#!/usr/bin/env python3
"""Stage E ticker-text parity: the Rust ticker view model (singws-uimodel `ticker`, `singws-ticker-cli`) against the app's REAL Python.

Oracle = the app module: ``KaraokeApp.get_singer_names_for_ticker`` (with ``is_singer_active``), ``KaraokeApp._render_ticker_custom_message`` and
``Ticker._format_queue_text`` run as the real methods bound onto a plain stand-in object (no widgets), exactly as the ticker calls them: the queue's
singer list and custom message come from the first, the audience text from the second and third.

Fuzzed: queues with skipped singers, skipped songs, empty and falsy songs, old list-format songs, duet names, missing/None/numeric names; settings with the
custom message on/off, text with every %token, '|' separators, dangling '-' and bullets, Python-only whitespace (U+001C, U+0085, U+3000), tokens inside
substituted names; short/odd ``_now_singing_parts``. Where Python raises the Rust side must report an error (or decline); Unicode it cannot reproduce is declined.

Run:  QT_QPA_PLATFORM=offscreen /Users/daniel/Documents/SingWS/.venv/bin/python tools/ticker_parity.py [--cases N] [--seed S]
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

CLI = os.environ.get("SINGWS_TICKER_CLI") or os.path.join(os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target"), "release", "singws-ticker-cli")
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


def python_text(queue, settings, now_parts):
    app = hp.app()
    ka, tk = app.KaraokeApp, app.Ticker
    f = types.SimpleNamespace(queue=queue, settings=settings, _now_singing_parts=now_parts)
    for name in ("get_singer_names_for_ticker", "is_singer_active", "_ticker_state_signature", "_render_ticker_custom_message"):
        setattr(f, name, types.MethodType(getattr(ka, name), f))
    res = f.get_singer_names_for_ticker()
    t = types.SimpleNamespace(get_singer_list_callback=lambda: res)
    text = tk._format_queue_text(t)
    singers, msg = (res if isinstance(res, tuple) else (res, ""))
    return [str(x) for x in singers], msg, text


NAMES = ["Ann", "Bo", "Cy Young", "Zoë", "  Padded  ", "İstanbul", "O'Neil", "東京", "", "Mr. %curSinger"]
TOKENS = ["%m_curSinger", "%curSinger", "%curSong", "%m_curArtist", "%curArtist", "%m_curTitle", "%curTitle", "%nextSinger"]
SEPS = [" ", "  ", "\t", " ", "　", "\u001c", "\u0085", " | ", "|", " - ", " -- ", "- -", " • ", " •• ", "• •", "-•-", ""]
WORDS = ["Welcome", "Tip jar", "Wi-Fi", "Request a song!", "ÉDITION", "25% off", "x"]


def rand_song(rng):
    r = rng.random()
    if r < 0.12:
        return {"skipped": True}
    if r < 0.17:
        return {}
    if r < 0.22:
        return [rng.choice(["a", ""]), 1, rng.choice(["Old Duet", "", None, 7])]
    if r < 0.26:
        return rng.choice([["a"], ["a", 1], [], "str", 0, None, ["a", "b", ""]])
    s = {"title": "t", "skipped": rng.choice([False, False, False, 0, None])}
    if rng.random() < 0.3:
        s["duet_display"] = rng.choice(["Ann & Bo", "", None, 5, 1.5, "Duet %curSinger"])
    return s


def rand_queue(rng):
    q = []
    for _ in range(rng.choice([0, 1, 2, 3, 6, 12])):
        s = {"name": rng.choice(NAMES + [None, 7, 2.5, True])}
        if rng.random() < 0.05:
            del s["name"]
        s["songs"] = [rand_song(rng) for _ in range(rng.choice([0, 1, 1, 2, 3]))]
        if rng.random() < 0.1:
            s["skipped"] = rng.choice([True, 1, 0, None])
        q.append(s if rng.random() > 0.03 else rng.choice(["junk", 5, None, ["x"]]))  # not a dict: Python's .get raises
    return q


def rand_template(rng):
    parts = []
    for _ in range(rng.choice([0, 1, 2, 3, 5])):
        parts.append(rng.choice(TOKENS + WORDS + SEPS))
        parts.append(rng.choice(SEPS))
    return "".join(parts)


def rand_now(rng):
    r = rng.random()
    if r < 0.2:
        return None
    if r < 0.3:
        return ["only", "two"]
    if r < 0.35:
        return rng.choice(["abc", 5, {}])
    pick = lambda: rng.choice(["Ann", "Queen", "Radio Ga Ga", "", "  ", None, 3, "%nextSinger", "%curTitle", "a - b", "x　y", "İ"])  # noqa: E731
    return [pick(), pick(), pick()] + ([pick()] if rng.random() < 0.2 else [])


def run_case(rng):
    queue = rand_queue(rng)
    msg = rng.choice([rand_template(rng)] * 8 + ["", None, 0, 5, ["x"], "   "])
    settings = {}
    if rng.random() < 0.9:
        settings["ticker_custom_enabled"] = rng.choice([True] * 8 + [False, 1, 0, None, "yes"])
    if rng.random() < 0.95:
        settings["ticker_custom_message"] = msg
    now = rand_now(rng)
    try:
        py = python_text(queue, settings, now)
        py_err = None
    except Exception as exc:  # noqa: BLE001
        py, py_err = None, repr(exc)
    r = rust({"queue": queue, "settings": settings, "now_parts": now})
    if "error" in r:
        if "decline" in r["error"]:
            tally("declined")
            return
        if py_err is None:
            fail(f"Rust errored but Python gave text: {r['error']} q={queue!r} s={settings!r} now={now!r}")
        else:
            tally("both_error")
        return
    if py_err is not None:
        fail(f"Python raised ({py_err}) but Rust gave text: q={queue!r} s={settings!r} now={now!r}")
        return
    singers, message, text = py
    if (r["singers"], r["message"], r["text"]) != (singers, message, text):
        fail(f"q={queue!r}\n s={settings!r} now={now!r}\n python={(singers, message, text)!r}\n rust  ={(r['singers'], r['message'], r['text'])!r}")
    else:
        tally("same")
        tally("with_message" if message else "no_message")
        tally("empty_text" if not text else "has_text")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=3000)
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
