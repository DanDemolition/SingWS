#!/usr/bin/env python3
"""Stage E song-row text parity: the Rust row view model (singws-uimodel `rows`, `singws-rows-cli`) against the app's REAL Python.

Oracle = the app module's real methods bound onto a plain stand-in: ``_build_search_row_text`` and ``_build_queue_song_row_text`` (only the pixel-width fitting
``_two_col_text`` is stubbed out: left / right / tooltip are compared), ``_queue_singer_status_label``, ``_split_display_artist_title_disc``, ``_track_media_badge`` /
``_compact_media_badge`` / ``_hero_media_badge_text``, ``_fmt_right_time`` / ``_fmt_mmss`` and the module's ``normalize_karafun_duration_seconds``.
Fuzzed: library tracks and queue entries with missing / padded / non-text fields, both disc spellings, every provider spelling, KaraFun catalog rows, every media type and
extension, durations of every kind (ints, floats, clocks, text, bools), keys, speeds, estimated durations, duets, paths with odd directories. Where Python raises the Rust side
must raise or decline.

Run:  QT_QPA_PLATFORM=offscreen /Users/daniel/Documents/SingWS/.venv/bin/python tools/rows_parity.py [--cases N] [--seed S]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import history_parity as hp  # noqa: E402  (sets a scratch SINGWS_HOME and loads the app module)

CLI = os.environ.get("SINGWS_ROWS_CLI") or os.path.join(os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target"), "release", "singws-rows-cli")
failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 25:
        print("DIFF:", msg[:1100])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def rust(req):
    out = subprocess.run([CLI], input=json.dumps(req), capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


def fake():
    a = hp.app()
    ka = a.KaraokeApp
    f = types.SimpleNamespace(results_list=None, queue_display=None)
    for n in ("_build_search_row_text", "_build_queue_song_row_text", "_split_display_artist_title_disc", "_track_media_badge", "_compact_media_badge", "_hero_media_badge_text",
              "_fmt_right_time", "_fmt_mmss", "_fmt_m_ss"):
        setattr(f, n, types.MethodType(getattr(ka, n), f))
    f._two_col_text = lambda lw, left, right, *a, **k: "unused"
    return a, f


def compare(label, py_fn, req, extract, ctx):
    try:
        py, err = py_fn(), None
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
    got = extract(r)
    if got != py:
        fail(f"{label}: {ctx}\n python={py!r}\n rust  ={got!r}")
    else:
        tally(label + "_same")


TEXT = ["Queen", "  Padded  ", "", None, "Ünal", "東京", "A - B", "x　", 0, False]
ODD = [5, ["x"], {"a": 1}, True, 2.5]
DISC = ["SC8671", "KV-12", "", None, " cc 9 ", "ǅ"]
DUR = [245, 245.9, 0, -3, "3:45", "245", "", None, True, False, 59.5, 3725, 10 ** 12, 86401, [], "abc", 0.4]
PROV = [None, "", "local", "LOCAL", " Local ", "karafun", "KaraFun_Online", "my_stream", "x-y z", "a b_c", "o'neil_s", "3d_movies", "ünal"]
TYPES = [None, "", "mp3g", ".MP3+G", "movie", "Video", "kj", "karaoke", "zip", "cdg", "flacfilexx", "m4v", "  ", "Mp4", "ß", 5]
PATHS = [None, "", " ", "/a/b/c.zip", "/a/b/c.MP3", "x.mp4", "/a/b/", "a//b.cdg", ".", "/", "/a.b/noext", "c.flac", "./x.wav", "/a/.hidden", "d.mkv", "e.ogg", "/a/b.ß"]


def pick(rng, good, odd_p=0.04):
    return rng.choice(ODD) if rng.random() < odd_p else rng.choice(good)


def rand_track(rng):
    t = {}
    for k, vals in (("artist", TEXT), ("title", TEXT), ("display", TEXT), ("discid", DISC), ("disc_id", DISC), ("duration", DUR), ("duration_secs", DUR), ("provider", PROV), ("type", TYPES),
                    ("song_type", TYPES), ("path", PATHS)):
        if rng.random() < 0.55:
            t[k] = pick(rng, vals) if k in ("artist", "title", "display", "discid", "disc_id", "path") else rng.choice(vals)
    if rng.random() < 0.15:
        t["karafun_catalog_only"] = rng.choice([True, False, 1, 0, None])
    return t


def rand_entry(rng):
    e = {}
    for k, vals in (("display_name", ["Queen - Radio Ga Ga - SC1", "A - B", "Just a title", "  ", "", None, "A - B - C - D", "x - ", " - y", "Ünal - Song - KV9"]),
                    ("artist", TEXT), ("title", TEXT), ("disc_id", DISC), ("duet_display", ["", None, "Ann & Bo", " p "]), ("duration", DUR),
                    ("key", [0, 2, -1, "3", "x", None, 2.7, True, [], "+4", 12]), ("tempo_percent", [100, 90, 110, None, "95", "x", 99.9, 0, True, [], -5])):
        if rng.random() < 0.6:
            e[k] = pick(rng, vals) if k in ("display_name", "artist", "title", "disc_id") else rng.choice(vals)
    if rng.random() < 0.4:
        e["duration_estimated"] = rng.choice([True, False, 1, None])
    return e


def rand_singer(rng):
    r = rng.random()
    if r < 0.05:
        return rng.choice(["x", None, 5, []])
    s = {}
    if rng.random() < 0.15:
        s["skipped"] = rng.choice([True, 1, 0, None, False])
    if rng.random() < 0.9:
        s["songs"] = rng.choice([[], [{}], [{"skipped": True}], [{"skipped": True}, {}], [["a", 1, 2]], ["str"], None, 0, [{"skipped": 0}], [None]])
    return s


def case(rng):
    a, f = fake()
    t = rand_track(rng)
    compare("search", lambda: (lambda x: {"left": x[2], "right": x[3], "tooltip": x[1]})(f._build_search_row_text(t)), {"op": "search", "track": t},
            lambda r: {k: r[k] for k in ("left", "right", "tooltip")}, repr(t))
    e = rand_entry(rng)
    compare("queue_song", lambda: (lambda x: {"left": x[2], "right": x[3], "tooltip": x[1]})(f._build_queue_song_row_text(e)), {"op": "queue_song", "entry": e},
            lambda r: {k: r[k] for k in ("left", "right", "tooltip")}, repr(e))
    s = rand_singer(rng)
    flags = [rng.random() < 0.4 for _ in range(3)]
    compare("status", lambda: a.KaraokeApp._queue_singer_status_label(s, is_rotation_start=flags[0], is_current=flags[1], is_next=flags[2]),
            {"op": "status", "singer": s, "start": flags[0], "current": flags[1], "next": flags[2]}, lambda r: r["text"], f"{s!r} {flags}")
    d = rng.choice(DUR + ["1:02:03", "0:00", "3:60", "00:30", " 3:45 ", "225.5", "224.5", "1:2:3:4", "3:", ":30", "٣:٤٥", "1e3", "inf", "nan", "86400", "1:00:00:00"])
    compare("normalize", lambda: a.normalize_karafun_duration_seconds(d), {"op": "normalize", "value": d}, lambda r: r["seconds"], repr(d))
    v = rng.choice(DUR)
    compare("rtime", lambda: f._fmt_right_time(v), {"op": "rtime", "value": v}, lambda r: r["text"], repr(v))
    compare("badge", lambda: f._track_media_badge(t), {"op": "badge", "track": t}, lambda r: r["text"], repr(t))
    dn = rng.choice(["Queen - Radio Ga Ga - SC1", "A - B", "Just a title", "  ", "", "A - B - C - D", "x - ", " - y", "A  -  B", "a - - b - c"])
    fb = rng.choice(["", " SC9 ", "KV"])
    compare("split", lambda: list(f._split_display_artist_title_disc(dn, fb)), {"op": "split", "display": dn, "fallback": fb}, lambda r: r["parts"], f"{dn!r} {fb!r}")


LIB_ARTISTS = ["Queen", "Abba", "Adele", "Éva Ünal", "The Beatles", "Queen", "Journey"]
LIB_TITLES = ["Radio Ga Ga", "Dancing Queen", "Hello", "Don't Stop Believin'", "Let It Be", "Bohemian Rhapsody", "Ünal's Song"]


def library_case(rng, workdir):
    """The Library tab: a real song index built by `song_index`, searched by `song_index.search_songs`, each row through the real `_build_search_row_text`, against
    the Rust search + `providers_for` + `library_row` over the same file."""
    import song_index
    a, f = fake()
    tracks = []
    for i in range(rng.randint(3, 12)):
        t = {"path": rng.choice(["/lib/", "/lib/sub/", "/x y/"]) + f"f{i}" + rng.choice([".zip", ".mp3", ".mp4", ".cdg", ".MKV", "", ".flac"]),
             "artist": rng.choice(LIB_ARTISTS), "title": rng.choice(LIB_TITLES)}
        for k, vals in (("discid", DISC), ("duration", [245, 61, 0, "3:45", None, 3725, 245.5, "245"]), ("type", TYPES), ("provider", PROV), ("display", TEXT)):
            if rng.random() < 0.6:
                t[k] = rng.choice(vals)
        tracks.append(t)
    if rng.random() < 0.4:      # a song with only a display name: the row falls back to it
        tracks.append({"path": f"/lib/only-display-{rng.randint(0, 9)}.zip", "artist": "", "title": "", "display": "Zed Zorro Song", "type": rng.choice(TYPES[:5])})
    tj = Path(workdir) / "tracks.json"
    db = Path(workdir) / "singws.db"
    if db.exists():
        db.unlink()
    tj.write_text(json.dumps(tracks))
    song_index._clear_read_caches()
    song_index.rebuild_from_tracks_json(tj, db, verbose=False)
    for q in ("zorro", rng.choice(LIB_ARTISTS).lower(), rng.choice(LIB_TITLES).split()[0], "queen radio", "qeen", "zzzz", rng.choice(LIB_ARTISTS) + " " + rng.choice(LIB_TITLES).split()[0]):
        rows = song_index.search_songs(q, dbfile=db, limit=500)
        try:
            want = []
            for r in rows:
                x = f._build_search_row_text(r)
                want.append({"path": r["path"], "left": x[2], "right": x[3], "tooltip": x[1]})
            err = None
        except Exception as exc:  # noqa: BLE001
            want, err = None, repr(exc)
        got = rust({"op": "library_db", "db": str(db), "query": q})
        if "error" in got:
            if "decline" in got["error"]:
                tally("library_declined")
            elif err is None:
                fail(f"library: Rust errored ({got['error']}) but Python returned rows: query={q!r} tracks={tracks!r}")
            else:
                tally("library_both_error")
            continue
        if err is not None:
            fail(f"library: Python raised ({err}) but Rust returned rows: query={q!r} tracks={tracks!r}")
        elif got["rows"] != want:
            fail(f"library: query={q!r}\n python={want!r}\n rust  ={got['rows']!r}\n tracks={tracks!r}")
        else:
            tally("library_same")
            tally("library_rows", len(want))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=20261027)
    args = ap.parse_args(argv)
    if not os.path.exists(CLI):
        print(f"missing {CLI}; build with: cd rust && cargo build --release -p singws-uimodel", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    for _ in range(args.cases):
        case(rng)
    with tempfile.TemporaryDirectory() as workdir:
        for _ in range(max(10, args.cases // 15)):
            library_case(rng, workdir)
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("RESULT: IDENTICAL" if not failures else f"RESULT: {len(failures)} DIFFERENCE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
