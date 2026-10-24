#!/usr/bin/env python3
"""Stage E panel 2 parity: the Rust Singer History model (singws-uimodel `history`) against the app's REAL Python.

Oracle = the app module itself (loaded like the tests do): ``KaraokeApp._normalize_singer_history_store`` and its helpers, the
``SingerHistoryDirectoryBuildWorker`` / ``SingerHistorySongsBuildWorker`` classes (their ``run()`` is executed and the emitted result captured),
``_filter_singer_history_songs``, ``_singer_history_song_meta_text``, ``_format_history_timestamp`` and ``_history_song_last_sang_text``.
Compared, for fuzzed stores (key variants, falsy/odd types, duplicate and merged songs, brands, unicode) AND for a read-only copy of the real
``~/SingWS/singer_history.json``: the normalised store, the singer directory for many searches and limits, each singer's song rows for many
queries and limits (including the "Showing N of M" rows and the meta text), the "last sang" and date labels across time zones, and ``str.casefold``
for EVERY Unicode code point. Inputs Python rejects (an ``int()`` that raises) must be declined by Rust, never produce output.

Run with the app's venv Python:  ~/.venvs/singws/.venv-build-arm64/bin/python tools/history_parity.py [--cases N] [--seed S]
Exit status 1 on any difference.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
_HOME = tempfile.mkdtemp(prefix="history_parity_home_")
os.environ["SINGWS_HOME"] = _HOME
sys.path.insert(0, str(ROOT))

CLI = os.environ.get("SINGWS_HISTORY_CLI") or os.path.expanduser("~/.cache/singws-rust-target/release/singws-history-cli")
NOW = 1_792_000_000  # a fixed "current time" (2026-10-21 ...), shared by both sides
failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 30:
        print("DIFF:", msg[:700])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def load_app():
    spec = importlib.util.spec_from_file_location("singws_main_history_parity", str(ROOT / "0.2.18.1.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


APP = None


def app():
    global APP
    if APP is None:
        APP = load_app()

        class FixedDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromtimestamp(NOW)

        APP.datetime = FixedDT
    return APP


class Fake:
    """`self` for the app's history methods: the real methods bound onto a plain object (no QWidget needed)."""

    def __init__(self, query=""):
        K = app().KaraokeApp
        for name in ("_normalize_singer_pref_key", "_empty_singer_history_record", "_normalize_history_song_key", "_merge_history_song_records",
                     "_normalize_singer_history_store", "_filter_singer_history_songs", "_singer_history_song_meta_text", "_format_history_timestamp"):
            setattr(self, name, types.MethodType(getattr(K, name), self))
        self._q = query
        # what the app's `_singer_history_song_query` returns for the search box text
        self._singer_history_song_query = lambda: str(self._q or "").strip().casefold()


def py_normalize(raw):
    return Fake()._normalize_singer_history_store(raw)["singers"]


def py_directory(singers, search, render_limit, scan_limit):
    out = []
    w = app().SingerHistoryDirectoryBuildWorker(1, list(singers.items()), search, render_limit, scan_limit)
    w.finished.connect(lambda job, res: out.append(res))
    w.run()
    return out[0]


def py_songs(singers, key, query, render_limit):
    f = Fake(query)
    record = singers.get(key, {}) if key else {}
    if not isinstance(record, dict) or not key:
        return {"rows": [], "total": 0, "shown": 0, "total_unfiltered": 0, "filter_active": False, "meta": f._singer_history_song_meta_text(0)}
    songs = list((record.get("songs", {}) or {}).items())
    total_unfiltered = len(songs)
    songs = f._filter_singer_history_songs(songs)
    f._singer_history_song_total_unfiltered = total_unfiltered
    f._singer_history_song_filter_active = len(songs) != total_unfiltered or bool(f._singer_history_song_query())
    if not songs:
        return {"rows": [], "total": 0, "shown": 0, "total_unfiltered": total_unfiltered, "filter_active": f._singer_history_song_filter_active,
                "meta": f._singer_history_song_meta_text(0)}
    out = []
    w = app().SingerHistorySongsBuildWorker(1, key, songs, render_limit)
    w.finished.connect(lambda job, k, res: out.append(res))
    w.run()
    res = out[0]
    return {"rows": res["rows"], "total": res["total"], "shown": res["shown"], "total_unfiltered": total_unfiltered,
            "filter_active": f._singer_history_song_filter_active, "meta": f._singer_history_song_meta_text(res["total"], res["shown"])}


def py_summary(record, key):
    f = Fake()
    if not isinstance(record, dict) or not key:
        return {"name": "Select a singer", "summary": "No singer selected."}
    name = str(record.get("name", "") or key)
    total = int(record.get("total_performances") or 0)
    unique = int(record.get("unique_song_count") or len(record.get("songs", {}) or {}))
    last_seen = f._format_history_timestamp(record.get("last_seen_at") or 0)
    pref = str(record.get("preferred_disc_priority", "") or "").strip()
    summary = f"{total} total performances  ·  {unique} songs  ·  Last seen {last_seen}"
    if pref:
        summary += f"  ·  Preferred brand {pref}"
    return {"name": name, "summary": summary}


def rust(request, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    out = subprocess.run([CLI], input=json.dumps(request), capture_output=True, text=True, timeout=600, env=e)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


# ---- generators ---------------------------------------------------------------------------------------------------------------------

ARTISTS = ["Garth Brooks", "ABBA", "Beyoncé", "Sigur Rós", "AC/DC", "Guns N' Roses", "The Beatles", "STRASSE Band", "Straße Boys", "Ünal", "日本のバンド",
           "Smith, John", "P!nk", "Ørjan", "ǅ Group", "ΣΑΣ", "İstanbul Trio", "O'Brien & Sons", "Café Tacvba", "Matchbox 20", "", "  padded  "]
TITLES = ["Friends In Low Places", "Dancing Queen", "Halo (Live)", "Hoppípolla", "Back In Black", "Sweet Child O' Mine", "Let It Be", "Maß halten",
          "Ça Plane Pour Moi", "Über", "3 AM", "Gonna Fly", "Ünïcode ☃", "", "x" * 60]
DISCS = ["KV 07125", "SC9001", "CC", "zoom dl 33", "KARAFUN", "Party Tyme 12", "me 1", "", "  sbi karaoke  ", "SF-33", "weird disc", "ÅB 1"]
PREFS = ["", "KV", "kv, sc", "Karaoke Version 12, Zoom DL", "sc - cc", "unknown brand", "KARA FUN", ["kv", "sc", "KV"], "a,b,c,d,e,f,g,h,i,j,k,l,m", " ,, ,", 5, None,
         "Party Time / PT", "Sound Choice (SC)", "chartbuster2", "/lib/KV 12.zip", "a\\b - SC", "x/y/", "//", "./KV.v2", "dir/SC", "KV.", ".kv", "my/path/zoom dl.name.ext", "SC | CC", "dir/.kv", "x/KV/.", "a/b/", "dir/.sc.zip"]
INTLIKE = [None, 0, 1, 3, -2, 7, 12.9, "4", " 5 ", "0", "", True, False, 1700000000, "1_000", 1e9, 2.5e9]
TIMES = [0, 1, 86400, 946684800, 1500000000, 1700000000, NOW - 60, NOW - 3600 * 5, NOW - 86400 * 3, NOW - 86400 * 40, NOW - 86400 * 400, NOW, NOW + 86400, 4000000000]


def rand_song(rng, i):
    s = {}
    if rng.random() < 0.9:
        s["artist"] = rng.choice(ARTISTS)
    if rng.random() < 0.9:
        s["title"] = rng.choice(TITLES)
    if rng.random() < 0.7:
        s["songid"] = rng.choice(["", "KV 07125", "id9", 12])
    if rng.random() < 0.7:
        s["disc_id"] = rng.choice(DISCS)
    if rng.random() < 0.6:
        s["song_type"] = rng.choice(["zip", "CDG", "kfn", " mp4 ", ""])
    if rng.random() < 0.3:
        s["duet_display"] = rng.choice(["", "Alice + Bob", "Duet: x"])
    if rng.random() < 0.5:
        s["path"] = rng.choice(["", "/a/b/c.zip", "/Users/Daniel/x y.kfn"])
    if rng.random() < 0.4:
        s["provider"] = rng.choice(["local", "", "Spotify", " YouTube ", None, "karafun"])
        if rng.random() < 0.7:
            s["provider_track_id"] = rng.choice(["", "abc123", "X-1", 55])
    for k in ("provider_url", "local_reference_path", "authorization_requirement", "availability_status"):
        if rng.random() < 0.15:
            s[k] = rng.choice(["", " u ", "https://x"])
    s["last_key"] = rng.choice([0, 0, 2, -3, "1", None, 12.2, True])
    s["last_tempo_percent"] = rng.choice([100, 95, 110, None, 0, "105"])
    s["play_count"] = rng.choice([1, 2, 5, 0, -1, None, "3", 2.9])
    s["first_performed_at"] = rng.choice(TIMES)
    s["last_performed_at"] = rng.choice(TIMES)
    s["updated_at"] = rng.choice(TIMES)
    return s


def rand_store(rng, n_singers):
    singers = {}
    for i in range(n_singers):
        name = rng.choice(["TJ", "Aaron", "alice", "ÉLAN", "Bob", "straße fan", "Ünal", "Zoë", "日本", "O'Neil", "x", "Mary Jane", "ΣΑΣ", "dan", ""])
        key = rng.choice([name, name.lower(), f" {name} ", name.upper(), f"{name}{i}"])
        rec = {}
        if rng.random() < 0.9:
            rec["name"] = rng.choice([name, name.strip(), None, "", 7, f" {name} "])
        if rng.random() < 0.8:
            rec["preferred_disc_priority"] = rng.choice(PREFS)
        for k in ("updated_at", "last_seen_at", "total_performances", "unique_song_count"):
            if rng.random() < 0.7:
                rec[k] = rng.choice(INTLIKE) if k != "last_seen_at" else rng.choice(TIMES)
        songs = {}
        for j in range(rng.randint(0, 25)):
            sk = rng.choice([f"{rng.choice(ARTISTS).lower()}|{rng.choice(TITLES).lower()}", f"k{j}", f"/path/{j}.zip", "", "a|b|c"])
            songs[sk] = rand_song(rng, j) if rng.random() > 0.05 else rng.choice([None, 5, "x", []])
        if rng.random() < 0.95:
            rec["songs"] = songs
        singers[key] = rec if rng.random() > 0.04 else rng.choice([None, 3, "x", []])
    raw = {"singers": singers}
    if rng.random() < 0.5:
        raw["deletions"] = {"aaron": {"name": "Aaron", "deleted_at": 1788167444}}
    return raw


def would_python_raise(raw):
    try:
        py_normalize(raw)
        return False
    except Exception:  # noqa: BLE001
        return True


# ---- comparisons --------------------------------------------------------------------------------------------------------------------

def jsonable_store(singers):
    out = []
    for k, r in singers.items():
        d = dict(r)
        songs = d.pop("songs")
        out.append({"key": k, "name": d["name"], "preferred_disc_priority": d["preferred_disc_priority"], "updated_at": d["updated_at"],
                    "last_seen_at": d["last_seen_at"], "total_performances": d["total_performances"], "unique_song_count": d["unique_song_count"],
                    "songs": [dict(key=sk, **sv) for sk, sv in songs.items()]})
    return out


def compare_store(label, raw_text, queries, singer_queries, rng):
    raw = json.loads(raw_text)
    try:
        singers = py_normalize(raw)
        py_err = None
    except Exception as exc:  # noqa: BLE001
        singers, py_err = None, exc
    r = rust({"op": "normalize", "raw_text": raw_text})
    tally(f"{label} stores")
    if py_err is not None:
        if r["ok"]:
            fail(f"{label}: python raised {py_err!r} but rust produced a store")
        else:
            tally(f"{label} python-raises, rust declines")
        return
    if not r["ok"]:
        # Rust may only decline for the reasons it documents
        if not r["error"].startswith("decline:"):
            fail(f"{label}: rust failed without declining: {r['error']}")
        tally(f"{label} rust declined")
        return
    a, b = jsonable_store(singers), r["result"]
    if a != b:
        for x, y in zip(a, b):
            if x != y:
                diff = {k: (x.get(k), y.get(k)) for k in x if x.get(k) != y.get(k) and k != "songs"}
                if not diff:
                    for sx, sy in zip(x["songs"], y["songs"]):
                        if sx != sy:
                            diff = {k: (sx.get(k), sy.get(k)) for k in sx if sx.get(k) != sy.get(k)}
                            break
                fail(f"{label}: normalised store differs for {x['key']!r}: {diff}")
                break
        else:
            fail(f"{label}: normalised store length {len(a)} != {len(b)}")
        return
    # directory
    for search in singer_queries:
        for render_limit, scan in ((700, 500), (3, 2), (1, 1)):
            exp = py_directory(singers, search, render_limit, scan)
            got = rust({"op": "directory", "raw_text": raw_text, "search": search, "render_limit": render_limit, "song_scan_limit": scan})
            tally(f"{label} directory searches")
            if not got["ok"]:
                if not got["error"].startswith("decline:"):
                    fail(f"{label}: directory error {got['error']}")
                tally(f"{label} directory declined")
                continue
            g = got["result"]
            if [(x["text"], x["singer_key"], x["selectable"]) for x in exp["rows"]] != [(x["text"], x["singer_key"], x["selectable"]) for x in g["rows"]] or \
                    (exp["total_matches"], exp["shown"]) != (g["total_matches"], g["shown"]):
                fail(f"{label}: directory differs for search={search!r} limit={render_limit}: py={[x['text'] for x in exp['rows']][:3]} rust={[x['text'] for x in g['rows']][:3]}")
    # songs + summary for several singers
    keys = list(singers)
    for key in (rng.sample(keys, min(len(keys), 6)) + ["", "no such singer"]) if keys else ["", "no such singer"]:
        exp_sum = py_summary(singers.get(key, {}) if key else {}, key)
        got = rust({"op": "details", "raw_text": raw_text, "singer_key": key})
        tally(f"{label} summaries")
        if got["ok"] and got["result"] != exp_sum:
            fail(f"{label}: summary differs for {key!r}: py={exp_sum} rust={got['result']}")
        for q in queries:
            for limit in (500, 3):
                exp = py_songs(singers, key, q, limit)
                got = rust({"op": "songs", "raw_text": raw_text, "singer_key": key, "query": q, "render_limit": limit, "now": NOW})
                tally(f"{label} song views")
                if not got["ok"]:
                    if not got["error"].startswith("decline:"):
                        fail(f"{label}: songs error {got['error']}")
                    tally(f"{label} songs declined")
                    continue
                g = got["result"]
                if [(x["text"], x["song_key"], x["selectable"]) for x in exp["rows"]] != [(x["text"], x["song_key"], x["selectable"]) for x in g["rows"]] \
                        or any(exp[k] != g[k] for k in ("total", "shown", "total_unfiltered", "filter_active", "meta")):
                    bad = next(((x["text"], y["text"]) for x, y in zip(exp["rows"], g["rows"]) if x["text"] != y["text"]), None)
                    fail(f"{label}: songs differ singer={key!r} q={q!r} limit={limit}: {bad} meta py={exp['meta']!r} rust={g['meta']!r}")


def check_time(rng):
    stamps = list(TIMES) + [-1, -86400 * 365, 253402300799, 253402300800, 2**40, 1752000000, 1741000000, 1710000000 + 3600 * 8]
    stamps += [NOW - rng.randint(0, 86400 * 800) for _ in range(300)]
    # DST transition neighbourhoods (US, EU, Australia, NZ)
    for base in (1710054000, 1730620800, 1711846800, 1729990800, 1712419200, 1728147600):
        stamps += [base + d * 1800 for d in range(-6, 7)]
    py_f = Fake()
    for tz in ("America/Los_Angeles", "Asia/Kolkata", "UTC", "Pacific/Auckland", "Europe/London", "America/St_Johns"):
        os.environ["TZ"] = tz
        time.tzset()
        got = rust({"op": "last_sang", "timestamps": stamps, "now": NOW}, env={"TZ": tz})["result"]
        for ts, g in zip(stamps, got):
            tally("time labels")
            exp_ls = app()._history_song_last_sang_text(ts)
            exp_st = py_f._format_history_timestamp(ts)
            if exp_ls != g["last_sang"] or exp_st != g["stamp"]:
                fail(f"time ({tz}) ts={ts}: python last_sang={exp_ls!r} stamp={exp_st!r} rust={g}")
        # a "now" late in the evening and just after midnight, for the "tonight" rule
        for now2 in (NOW + 3600 * 9, NOW + 3600 * 14):
            got = rust({"op": "last_sang", "timestamps": stamps[:60], "now": now2}, env={"TZ": tz})["result"]

            class DT(datetime):
                @classmethod
                def now(cls, tz=None, _n=now2):
                    return datetime.fromtimestamp(_n)

            saved = app().datetime
            app().datetime = DT
            try:
                for ts, g in zip(stamps[:60], got):
                    tally("time labels (other now)")
                    if app()._history_song_last_sang_text(ts) != g["last_sang"]:
                        fail(f"time ({tz}) now={now2} ts={ts}: {app()._history_song_last_sang_text(ts)!r} vs {g['last_sang']!r}")
            finally:
                app().datetime = saved
    os.environ.pop("TZ", None)
    time.tzset()


def check_casefold():
    chunk = []
    skew = [(0xA7C0, 0xA7FF), (0x10D40, 0x10D8F), (0x16E40, 0x16EFF)]
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        chunk.append(f"a{chr(cp)}B")
        if len(chunk) == 60000 or cp == 0x10FFFF:
            got = rust({"op": "casefold", "texts": chunk})["result"]
            for t, g in zip(chunk, got):
                if isinstance(g, dict):
                    if not any(lo <= ord(t[1]) <= hi for lo, hi in skew):
                        fail(f"casefold declined U+{ord(t[1]):04X} outside the skew blocks")
                    tally("casefold declined (skew)")
                elif t.casefold() != g:
                    fail(f"casefold differs for U+{ord(t[1]):04X}: py={t.casefold()!r} rust={g!r}")
            tally("casefold code points", len(chunk))
            chunk = []
    for t in ["ΑΣ", "ΑΣ Β", "ΣΑΣ", "STRASSE", "Straße", "İstanbul", "ǅ", "ﬃ", "ᾈ", "ẞ", "ſ", "K", "Å", "ᏸ", "ꭰ"]:
        got = rust({"op": "casefold", "texts": [t]})["result"][0]
        tally("casefold words")
        if t.casefold() != got:
            fail(f"casefold differs for {t!r}: {t.casefold()!r} vs {got!r}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=25)
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument("--skip-casefold", action="store_true")
    ap.add_argument("--no-live", action="store_true", help="do not read the real ~/SingWS/singer_history.json")
    args = ap.parse_args(argv)
    if not os.path.exists(CLI):
        print(f"missing {CLI}; build with: cd rust && cargo build --release -p singws-uimodel", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    app()
    singer_queries = ["", "tj", "ALICE", "straße", "strasse", "kv", "karaoke version", "sc", "Zoom", "no match xyz", "  Garth ", "ünal", "日本", "%", "o'", "ΣΑΣ", "dan", "3 am"]
    song_queries = ["", "garth", "STRASSE", "straße", "low places", "kv 07125", "ZIP", "cdg kv", "Ünï", "ß", "ǅ", "σας", "o' mine", "nothing here", "  "]
    n_ok = 0
    for case in range(args.cases):
        raw = rand_store(rng, rng.randint(1, 18))
        text = json.dumps(raw, ensure_ascii=rng.random() < 0.5)
        compare_store("fuzz", text, song_queries, singer_queries, rng)
        n_ok += 1
    # the real store, read-only
    live = Path(os.path.expanduser("~/SingWS/singer_history.json"))
    if live.exists() and not args.no_live:
        text = live.read_bytes().decode("utf-8", "ignore")
        # queries drawn from the real data
        raw = json.loads(text)
        names = [r.get("name", "") for r in raw.get("singers", {}).values() if isinstance(r, dict)]
        words = []
        for r in raw.get("singers", {}).values():
            for s in (r.get("songs", {}) or {}).values():
                words += [w for w in f"{s.get('artist','')} {s.get('title','')}".split() if w]
        sq = singer_queries + names[:12] + [w for w in rng.sample(words, min(len(words), 12))]
        pq = song_queries + rng.sample(words, min(len(words), 10))
        compare_store("live", text, pq, sq, rng)
    check_time(rng)
    if not args.skip_casefold:
        check_casefold()
    import shutil
    shutil.rmtree(_HOME, ignore_errors=True)
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("RESULT: IDENTICAL" if not failures else f"RESULT: {len(failures)} DIFFERENCE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
