#!/usr/bin/env python3
"""Stage E panel 3 parity: the Rust rotation (audience) view against the app's REAL ``RotationView.update_rotation``.

Oracle = the app module: ``RotationView.update_rotation``, ``display_name_for_queue_entry`` and ``lookup_display_name`` run unbound on a recording fake
(labels, rail and lineup setters capture what the real window would have been given) whose "owner" answers ``lookup_display_name`` with the real
``KaraokeApp.lookup_display_name`` over a temp song index and an in-memory library. The queue's load-time migration (``load_data``) is a HAND COPY here
(that code lives inside a method that also reads the user's files); the Rust migration is compared with it, and that copy mirrors lines of 0.2.18.1.py
marked "DATA MIGRATION".

Compared for fuzzed queues (legacy tuple/list/string entries, dicts without song_info, skipped singers and songs, duets, rotation-start markers, odd
types, KaraFun and "unknown" display names, library hits in the index, library hits only in memory, misses): every row's number/singer/song/artist/duet/
combined/text, the count label, the title label, the next singer, the up-next and on-deck lineup, and the now/next strips, for several "now singing"
texts, a playing singer (skipped from the list) and a live title. Where Python raises, Rust must decline.

Run with the app's venv Python:  ~/.venvs/singws/.venv-build-arm64/bin/python tools/rotation_parity.py [--cases N] [--seed S]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import history_parity as hp  # noqa: E402  (sets a temp SINGWS_HOME and loads the app module)

CLI = os.environ.get("SINGWS_ROTATION_CLI") or os.path.expanduser("~/.cache/singws-rust-target/release/singws-rotation-cli")
failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 25:
        print("DIFF:", msg[:900])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def rust(req):
    out = subprocess.run([CLI], input=json.dumps(req), capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


# ---- the oracle ---------------------------------------------------------------------------------------------------------------------

def py_migrate(raw):
    """Hand copy of the DATA MIGRATION loop of the app's data loader, minus the random ids and the preference merge (neither shows in this view)."""
    valid = []
    for singer in raw:
        if not isinstance(singer, dict):
            continue
        if "skipped" not in singer:
            singer["skipped"] = False
        new_songs = []
        for song_idx, entry in enumerate(singer.get("songs", [])):
            if isinstance(entry, dict) and "song_info" in entry:
                if "skipped" not in entry:
                    entry["skipped"] = False
                new_songs.append(entry)
            else:
                if isinstance(entry, (tuple, list)):
                    d = {"song_info": entry[0] if len(entry) > 0 else None, "key": entry[1] if len(entry) > 1 else 0, "skipped": False}
                    if len(entry) >= 3:
                        d["duet_display"] = entry[2]
                    new_songs.append(d)
                else:
                    new_songs.append({"song_info": entry, "key": 0, "skipped": False})
        singer["songs"] = new_songs
        valid.append(singer)
    return valid


def name_key(name):
    try:
        return re.sub(r"\s+", " ", str(name or "").strip()).casefold()
    except Exception:  # noqa: BLE001
        return str(name or "").strip().lower()


def has_duplicates(queue):
    seen = set()
    for s in queue:
        k = name_key(s.get("name", ""))
        if not k:
            continue
        if k in seen:
            return True
        seen.add(k)
    return False


class Label:
    def __init__(self):
        self._t = ""

    def text(self):
        return self._t

    def setText(self, t):
        self._t = t


def py_view(queue, tracks, now_singing, playing, active_id, live_title, home):
    app = hp.app()
    os.environ["SINGWS_HOME"] = home
    app.song_index._clear_read_caches()
    host = types.SimpleNamespace(karaoke_playing=playing, _current_karaoke_singer_id=active_id, _current_karaoke_title=live_title,
                                 _display_name_cache={}, tracks=tracks)
    host.lookup_display_name = types.MethodType(app.KaraokeApp.lookup_display_name, host)
    out = {}
    rv = types.SimpleNamespace(rotation_rail=None, now_singing_surface=None, queue_items=[], now_singing_label=Label(), queue_count_label=Label(),
                               queue_title_label=Label())
    rv.rotation_rail = types.SimpleNamespace(_root=types.SimpleNamespace(celebrateSinger=lambda: None), set_items=lambda items: out.__setitem__("items", items))
    rv.parent = lambda: host
    rv.set_side_lineup = lambda a, b: out.__setitem__("lineup", (a, b))
    rv.set_now_playing_strip = lambda n, x: out.__setitem__("strips", (n, x))
    for name in ("display_name_for_queue_entry", "lookup_display_name"):
        setattr(rv, name, types.MethodType(getattr(app.RotationView, name), rv))
    app.RotationView.update_rotation(rv, queue, tracks, now_singing)
    return {"rows": [{"number": r["number"], "singer": r["singer"], "song": r["song"], "artist": r["artist"], "duet": r["duet"], "combined": r["combined"],
                      "text": t} for r, t in zip(out.get("items", []), rv.queue_items)],
            "count_label": rv.queue_count_label._t, "title_label": rv.queue_title_label._t,
            "up_next": list(out["lineup"][0]), "on_deck": list(out["lineup"][1]), "now_strip": out["strips"][0], "next_strip": out["strips"][1]}


# ---- generators ---------------------------------------------------------------------------------------------------------------------

NAMES = ["TJ", "Aaron", "alice", "ÉLAN", "Bob", "Straße", "Ünal", "Zoë", "日本", "O'Neil", "Mary Jane", "dan", "Dan Jr", "  spaced  name ", "X", "singer 9"]
ARTISTS = ["Garth Brooks", "ABBA", "Beyoncé", "AC/DC", "Guns N' Roses", "P!nk", "", "  "]
TITLES = ["Friends In Low Places", "Dancing Queen", "Halo", "Back In Black", "3 AM", "", "Gonna Fly"]
DISPLAYS = ["ABBA - Dancing Queen - SC1", "Garth Brooks - Friends In Low Places", "Just A Title", "A - B - C - D", "x - karafun", "Artist - Title - KaraFun",
            "unknown", "UNKNOWN", "", "  Spaced  -  Out  ", " - ", "Solo"]


def make_library(rng, n):
    tracks = []
    for i in range(n):
        t = {"path": rng.choice([f"/lib/Song {i}.zip", f"/Lib/SONG {i}.kfn", f"/lib/ü{i}.cdg"]), "artist": "x", "title": "y"}
        r = rng.random()
        if r < 0.75:
            t["display"] = rng.choice(DISPLAYS)
        elif r < 0.85:
            t["display"] = None
        elif r < 0.9:
            t["display"] = 5
        tracks.append(t)
    return tracks


def make_queue(rng, tracks):
    paths = [t["path"] for t in tracks] + ["/not/in/library.zip", "streaming:abc", "/Lib/song 3.KFN"]
    queue = []
    used = set()
    for i in range(rng.randint(0, 8)):
        name = rng.choice(NAMES + [f"s{i}"])
        if name.casefold().strip() in used and rng.random() < 0.9:
            name = f"{name}{i}"
        used.add(re.sub(r"\s+", " ", name.strip()).casefold())
        s = {"name": name, "singer_id": f"id{i}"}
        if rng.random() < 0.15:
            s["skipped"] = rng.choice([True, False, 1, 0, None])
        if rng.random() < 0.2:
            s["rotation_marker"] = rng.choice([True, False, 1])
        songs = []
        for j in range(rng.randint(0, 4)):
            p = rng.choice(paths)
            k = rng.random()
            if k < 0.55:
                e = {"song_info": rng.choice([p, p, [p, p.replace(".cdg", ".mp3")], [p]])}
                for key in ("artist", "title"):
                    if rng.random() < 0.5:
                        e[key] = rng.choice(ARTISTS if key == "artist" else TITLES)
                if rng.random() < 0.4:
                    e["display_name"] = rng.choice(DISPLAYS)
                if rng.random() < 0.25:
                    e["duet_display"] = rng.choice(["Al + Bo", "", "Cy & Di", 5, None])
                if rng.random() < 0.2:
                    e["skipped"] = rng.choice([True, False, 1, 0])
                e["key"] = rng.choice([0, 2, -1])
            elif k < 0.7:
                e = [p, rng.choice([0, 3]), rng.choice(["Duo Name", "x"])][: rng.randint(1, 3)]
            elif k < 0.8:
                e = p
            elif k < 0.85:
                e = {"artist": "No", "title": "Info"}  # a dict without song_info becomes song_info itself
            elif k < 0.9:
                e = {"song_info": None, "title": rng.choice(TITLES)}
            elif k < 0.95:
                e = {"song_info": rng.choice([5, True, 1.5, "xé"]), "artist": "A", "title": ""}
            else:
                e = rng.choice([[], None, 3])
            songs.append(e)
        if rng.random() < 0.97:
            s["songs"] = songs
        queue.append(s)
    return queue


def run_case(rng, workdir, idx):
    home = Path(workdir) / f"h{idx}"
    home.mkdir()
    tracks = make_library(rng, rng.randint(0, 12))
    in_index = [t for t in tracks if rng.random() < 0.6]
    # build the song index from `in_index` with the app's own indexer
    tp = home / "tracks.json"
    tp.write_text(json.dumps(in_index))
    if in_index:
        hp.app().song_index.rebuild_from_tracks_json(tp, home / "singws.db", verbose=False)
    db = str(home / "singws.db") if in_index else None
    queue_raw = make_queue(rng, tracks)
    raw_text = json.dumps(queue_raw, ensure_ascii=rng.random() < 0.5)
    singer_names = [str(s.get("name", "")) for s in queue_raw if isinstance(s, dict)]
    nows = ["Singer Rotation", "", rng.choice(singer_names + ["x"]), rng.choice(singer_names + ["x"]).upper(), "Someone Else  "]
    migrated_py = py_migrate(json.loads(raw_text))
    dup = has_duplicates(migrated_py)
    r = rust({"op": "migrate", "queue_text": raw_text})
    tally("queues")
    if dup:
        tally("duplicate-name queues")
        if r["ok"] or not r["error"].startswith("decline:"):
            fail(f"case {idx}: duplicate names should be declined, got {r}")
        return
    if not r["ok"]:
        # Python's loader would raise on a non-list `songs`
        try:
            py_migrate(json.loads(raw_text))
            fail(f"case {idx}: rust refused a queue Python migrates: {r['error']}")
        except Exception:  # noqa: BLE001
            tally("migration both refuse")
        return
    if r["result"] != migrated_py:
        fail(f"case {idx}: migrated queue differs\n py={json.dumps(migrated_py)[:400]}\n rs={json.dumps(r['result'])[:400]}")
        return
    for now in nows:
        for playing in (False, True):
            # the playing singer's id and the live title are offered even when nothing is playing: the app ignores them then
            active = str(rng.choice(migrated_py).get("singer_id", "")) if migrated_py and rng.random() < 0.8 else ""
            live = rng.choice(["", "Live Title", " padded title "])
            try:
                exp = py_view(copy.deepcopy(migrated_py), tracks, now, playing, active, live, str(home))
                py_err = None
            except Exception as exc:  # noqa: BLE001
                exp, py_err = None, exc
            got = rust({"op": "view", "queue_text": raw_text, "db": db, "tracks": tracks, "now_singing": now, "playing": playing,
                        "active_singer_id": active, "live_title": live})
            tally("views")
            if py_err is not None:
                if got["ok"]:
                    fail(f"case {idx}: python raised {py_err!r} but rust produced a view")
                else:
                    tally("python raises, rust declines")
                continue
            if not got["ok"]:
                if got["error"].startswith("decline:"):
                    tally("rust declined")
                    tally("declined: " + re.sub(r"[^a-z ]", "", got["error"][9:60]))
                    continue
                fail(f"case {idx}: rust error {got['error']}")
                continue
            g = got["result"]
            g_cmp = {k: g[k] for k in ("rows", "count_label", "title_label", "up_next", "on_deck", "now_strip", "next_strip")}
            if g_cmp != exp:
                diff = next((k for k in exp if exp[k] != g_cmp[k]), "?")
                fail(f"case {idx} now={now!r} playing={playing}: {diff} differs\n py={json.dumps(exp[diff], ensure_ascii=False)[:500]}\n rs={json.dumps(g_cmp[diff], ensure_ascii=False)[:500]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=120)
    ap.add_argument("--seed", type=int, default=20261023)
    args = ap.parse_args(argv)
    if not os.path.exists(CLI):
        print(f"missing {CLI}; build with: cd rust && cargo build --release -p singws-uimodel", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    hp.app()
    with tempfile.TemporaryDirectory(prefix="rotation_parity_") as d:
        for i in range(args.cases):
            run_case(rng, d, i)
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("RESULT: IDENTICAL" if not failures else f"RESULT: {len(failures)} DIFFERENCE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
