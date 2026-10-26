#!/usr/bin/env python3
"""Stage E panel 4 parity: the Rust background-music playlist view against the app's REAL playlist code.

Oracle = the app module: ``BackgroundMusicManager._make_playlist_item`` builds every row exactly as ``load_data`` does (``track['filename']`` / ``track['path']``, so a
missing key or a non-text filename raises, as it does at launch), then ``_apply_playlist_filter`` / ``update_playlist_count`` / ``_playlist_filter_terms`` run
on a bare app whose list widget is a real ``QListWidget`` (offscreen), whose search box answers the typed text and whose counter records ``setText``.
With no player playlist the app hides no played rows (``_playlist_current_row`` is -1), which is the shell's view too.

Compared for fuzzed playlists (ASCII, accents, Turkish dotted I, German sharp s, Greek sigma, CJK, emoji, duplicate names, empty lists, malformed entries) and
queries (words taken from the names, partial words, garbage, Python-only whitespace such as U+001C and U+3000): the visible filenames in order and the counter
text. Where Python raises, Rust must report an error. Text from the Unicode blocks the Rust side declines (case tables differ between Python releases) is counted
as declined, not compared.

Run with the app's venv Python:  QT_QPA_PLATFORM=offscreen ~/.venvs/singws/.venv-build-arm64/bin/python tools/music_parity.py [--cases N] [--seed S]
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
import history_parity as hp  # noqa: E402  (sets a temp SINGWS_HOME and loads the app module)

CLI = os.environ.get("SINGWS_MUSIC_CLI") or os.path.expanduser("~/.cache/singws-rust-target/release/singws-music-cli")
QAPP = None
failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 25:
        print("DIFF:", msg[:900])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def rust(req):
    out = subprocess.run([CLI], input=json.dumps(req), capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


SKEW = [(0xA7C0, 0xA7FF), (0x10D40, 0x10D8F), (0x16E40, 0x16EFF)]


def skewed(s):
    return any(lo <= ord(c) <= hi for c in s for lo, hi in SKEW)


def python_view(playlist, query):
    """What the app shows. Returns (filenames, counter text) or raises exactly where load_data / the filter would."""
    from PyQt6.QtWidgets import QApplication, QListWidget

    global QAPP
    if QApplication.instance() is None:
        QAPP = QApplication([])  # keep a reference: a collected QApplication takes every widget with it
    app_mod = hp.app()
    ka = app_mod.BackgroundMusicManager
    f = ka.__new__(ka)
    f.playlist_list = QListWidget()
    f.current_playlist = playlist
    shown = {"text": ""}
    f.playlist_count = types.SimpleNamespace(setText=lambda t: shown.__setitem__("text", t))
    f.playlist_search = types.SimpleNamespace(text=lambda: query)
    f.bg_music = types.SimpleNamespace(playlist=[], current_index=0)
    f._refresh_workspace_meta = lambda: None
    f._playlist_filter_terms = lambda: ka._playlist_filter_terms(f)
    f._playlist_current_row = lambda: ka._playlist_current_row(f)
    f.update_playlist_count = lambda: ka.update_playlist_count(f)
    for track in f.current_playlist:  # load_data's loop
        f.playlist_list.addItem(ka._make_playlist_item(f, track["filename"], track["path"]))
    ka._apply_playlist_filter(f)
    names = [f.playlist_list.item(i).text() for i in range(f.playlist_list.count()) if not f.playlist_list.item(i).isHidden()]
    return names, shown["text"]


NAMES = ["Adele - Hello.mp3", "ADELE - Skyfall.MP3", "Zara Larsson - Lush Life.flac", "İstanbul Nights.mp3", "Straße der Liebe.mp3", "ΟΔΟΣ - Σίσυφος.mp3",
         "東京ブギウギ.mp3", "Café del Mar.m4a", "Party 🎉 Mix.mp3", "Queen - Bohemian Rhapsody.mp3", "queen - bohemian rhapsody.mp3", "ǅemal.mp3", "ǈubav.mp3", "Ünal.mp3",
         "a.mp3", "A B C.mp3", "Mixed Space.mp3", "Ideo　Space.mp3"]
SEPS = [" ", "  ", "\t", " ", "　", "\u001c", " ", "\n"]


def rand_name(rng):
    base = rng.choice(NAMES)
    return base if rng.random() < 0.7 else rng.choice(NAMES) + " " + rng.choice(["Live", "Remix", "2019", "ÉDITION", "vol.2", ""]) + rng.choice([".mp3", ".flac", ""])


def rand_playlist(rng):
    n = rng.choice([0, 1, 2, 5, 12, 40])
    out = []
    for i in range(n):
        e = {"path": f"/music/{i}/" + rng.choice(["a", "b"]), "filename": rand_name(rng), "title": "t"}
        r = rng.random()
        if r < 0.03:
            del e["filename"]
        elif r < 0.05:
            del e["path"]
        elif r < 0.07:
            e["filename"] = rng.choice([7, None, ["x"], {"a": 1}])
        elif r < 0.08:
            e = rng.choice(["a.mp3", 5, None, ["a"]])
        elif r < 0.10:
            e["path"] = rng.choice([5, None, ["p"]])
        out.append(e)
    return out


def rand_query(rng, playlist):
    words = []
    for e in playlist:
        if isinstance(e, dict) and isinstance(e.get("filename"), str):
            words += e["filename"].replace("-", " ").split()
    words += ["adele", "SKY", "qu", "ß", "σ", "i̇", "xyz", "", "ÜNAL", "mp3", "ǆ"]
    k = rng.choice([0, 1, 1, 2, 3])
    parts = []
    for _ in range(k):
        w = rng.choice(words) if words else "x"
        if len(w) > 2 and rng.random() < 0.4:
            a = rng.randrange(0, len(w) - 1)
            w = w[a:a + rng.randrange(1, len(w) - a + 1)]
        parts.append(w)
    q = rng.choice(SEPS).join(parts)
    if rng.random() < 0.2:
        q = rng.choice(SEPS) + q + rng.choice(SEPS)
    return q


def run_case(rng):
    playlist = rand_playlist(rng)
    query = rand_query(rng, playlist)
    text = json.dumps(playlist, ensure_ascii=rng.random() < 0.5)
    skew = skewed(text) or skewed(query)
    try:
        py = python_view(playlist, query)
        py_err = None
    except Exception as exc:  # noqa: BLE001
        py, py_err = None, repr(exc)
    r = rust({"playlist_text": text, "query": query})
    if "error" in r:
        if skew and "skew" in r["error"]:
            tally("declined_skew")
            return
        if py_err is None:
            fail(f"Rust errored but Python showed a list: {r['error']} q={query!r} playlist={text[:200]}")
        else:
            tally("both_error")
        return
    if py_err is not None:
        fail(f"Python raised ({py_err}) but Rust showed a list: q={query!r} playlist={text[:200]}")
        return
    names, counter = py
    if r["filenames"] != names or r["count_label"] != counter:
        fail(f"q={query!r}\n python={names!r} {counter!r}\n rust  ={r['filenames']!r} {r['count_label']!r}")
    else:
        tally("same")
        tally("filtered" if query.split() else "unfiltered")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=600)
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
