#!/usr/bin/env python3
"""Stage D3b parity: does the Rust ``singws-dbw`` WRITER produce databases logically identical to the ones the app's own Python code builds?

TEMP DATABASES ONLY. Every database is created in a private temp directory; the Rust CLI itself refuses any path outside the system temp
directory or inside the live profile, and this tool checks that refusal too. Nothing under ~/SingWS is opened.

Oracle = the app's REAL modules (``song_index.py``, ``phrase_markers.py``) run with ``time.time`` and ``uuid.uuid4`` frozen to values that are
also given to Rust. Two databases are "identical" when singws-db-cli reports the same schema (``sqlite_master`` text, header pragmas such as the
persistent journal mode, per-table columns and indexes) and the same rows (every table incl. ``sqlite_sequence``, by rowid, with INTEGER / REAL /
TEXT / NULL kept apart). Byte-identical files are not required (page layout belongs to the SQLite build).

Checks
  text      normalize_text / fuzzy_match_key for EVERY Unicode code point and for random tricky strings; int(float(x)) on a corpus of values;
            ``errors="ignore"`` UTF-8 decoding on random and crafted bytes
  rebuild   rebuild_from_tracks_json on fuzzed tracks.json (types, falsy values, key aliases, duplicates, real files for the stat fallbacks)
  update    update_from_tracks_json deltas (changed + removed), the no-op case, and the missing-database fallback
  legacy    init_schema upgrading older databases (no norm columns, no provider columns, schema_version != 5, NULL/empty provider)
  markers   phrase_markers: schema + uuid backfill on a legacy database, then random upsert / default / clear / delete / BPM / analysis sequences
  errors    inputs Python rejects must be rejected (declined) by Rust, never silently written
  guard     the CLI refuses a live-profile path and a path outside the temp directory

Usage:  python3 tools/dbw_parity.py [--cases N] [--seed S] [--skip-unicode]      Exit status 1 on any difference.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import random
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_HOME = tempfile.mkdtemp(prefix="dbw_parity_home_")
os.environ["SINGWS_HOME"] = _HOME  # belt and braces: no default path can resolve to the live profile
sys.path.insert(0, str(ROOT))

import phrase_markers  # noqa: E402
import song_index  # noqa: E402

TARGET = os.path.expanduser("~/.cache/singws-rust-target/release")
DBW_DEFAULT = os.path.join(TARGET, "singws-dbw-cli")
DB_DEFAULT = os.path.join(TARGET, "singws-db-cli")
NOW = 1_760_000_000

failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 40:
        print("DIFF:", msg)


def tally(name, n=1):
    counts[name] = counts.get(name, 0) + n


# ---- plumbing ----------------------------------------------------------------------------------------------------------------------

def dbw(cli, request, timeout=600):
    out = subprocess.run([cli], input=json.dumps(request), capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"dbw cli failed: {out.stderr[:300]}")
    return json.loads(out.stdout)


def db_cli(cli, path, request):
    out = subprocess.run([cli, str(path)], input=json.dumps(request), capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(f"db cli failed: {out.stderr[:300]}")
    r = json.loads(out.stdout)
    if not r.get("ok"):
        raise RuntimeError(f"db cli error: {r.get('error')}")
    return r["result"]


def view(db_cli_path, path):
    """Everything that defines a database logically: schema, header pragmas and every row of every table."""
    v = {"schema": db_cli(db_cli_path, path, {"op": "schema"})}
    for t in sorted(v["schema"]["tables"]):
        v[t] = db_cli(db_cli_path, path, {"op": "dump", "table": t})
    seq = db_cli(db_cli_path, path, {"op": "query", "sql": "SELECT name FROM sqlite_master WHERE name='sqlite_sequence'"})
    if seq["rows"]:
        v["sqlite_sequence"] = db_cli(db_cli_path, path, {"op": "query", "sql": "SELECT name, seq FROM sqlite_sequence ORDER BY name"})
    return v


def first_difference(a, b, path=""):
    if type(a) is not type(b):
        return f"{path}: {a!r} != {b!r}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}/{k}: present on one side only"
            d = first_difference(a[k], b[k], f"{path}/{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_difference(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"


@contextlib.contextmanager
def frozen(now=NOW, uuids=None):
    """time.time() and uuid.uuid4() as the Rust side is told to see them."""
    import time
    import uuid
    real_time, real_uuid = time.time, uuid.uuid4
    time.time = lambda: float(now)
    it = iter(uuids or [])
    uuid.uuid4 = lambda: types.SimpleNamespace(hex=next(it))
    try:
        yield
    finally:
        time.time, uuid.uuid4 = real_time, real_uuid


# ---- text helpers ------------------------------------------------------------------------------------------------------------------

TRICKY = ["", "Nirvana", "The Beatles", "the", "The", "B.O.B. (Bombs Over Baghdad)", "Matchbox Twenty", "3 AM", "3AM", "Guns N' Roses", "Beyoncé",
          "Sigur Rós", "ÅNGSTRÖM", "Smith, John Michael", "John Michael Smith", "Gonna Fly Now", "Wanna Be Startin' Somethin'", "Rock & Roll",
          "AC/DC", "Ünï cødé", "tab\tsep", "  padded  ", "UPPER lower", "İstanbul", "ΑΣ", "ΣΊΣΥΦΟΣ ΑΣ Β", "ß ẞ", "日本語", "😀 emoji",
          "x[y]z (a) {b}", "unclosed ( bracket", "nest ((a) b)", "[]", "(", ")", "}{", "Fifty Cent", "ninety nine", "one hundred", "KK",
          " sp ace", "a\x1fb\x1cc\x1dd\x1e", "line\nbreak\r\n", "quote's \"dq\"", "%_ wild", "back\\slash", "dir/Song Name.kfn",
          "fiﬁ ligature", "İı", "Á", "ǅ", "ᾈ", "twenty-one", "and", "&&", "the the", "the 1", "(the) b", "2 [x] 3"]


def rand_text(rng):
    r = rng.random()
    if r < 0.55:
        return rng.choice(TRICKY)
    if r < 0.85:
        return " ".join(rng.choice(TRICKY) for _ in range(rng.randint(1, 4)))
    n = rng.randint(0, 12)
    pool = [chr(c) for c in (list(range(32, 127)) + [0xa0, 0x85, 0x2028, 0x2029, 0x3000, 0x1c, 0x1f, 0xe9, 0x3a3, 0x130, 0x212a, 0x1f600, 0xff21, 0x660, 0x661])]
    return "".join(rng.choice(pool) for _ in range(n))


SKEW_BLOCKS = [(0xA7C0, 0xA7FF), (0x10D40, 0x10D8F), (0x16E40, 0x16EFF)]


def skew_block(cp):
    return any(lo <= cp <= hi for lo, hi in SKEW_BLOCKS)


def check_text(cli, rng, n_random, skip_unicode):
    if not skip_unicode:
        bad = 0
        chunk = []
        for c in range(0x110000):
            if 0xD800 <= c <= 0xDFFF:
                continue
            chunk.append(f"a{chr(c)}b")
            if len(chunk) == 50_000 or c == 0x10FFFF:
                for fn, req_op in ((song_index.normalize_text, "py_normalize_text"), (song_index.fuzzy_match_key, "py_fuzzy_match_key")):
                    got = dbw(cli, {"op": req_op, "texts": chunk})["result"]
                    for t, g in zip(chunk, got):
                        if isinstance(g, dict):  # declined: only allowed for the Unicode-table-skew blocks
                            tally("code points declined (Unicode skew)")
                            if not skew_block(ord(t[1])):
                                fail(f"{req_op} declined U+{ord(t[1]):04X} outside the skew blocks: {g}")
                        elif fn(t) != g:
                            bad += 1
                            if bad <= 5:
                                fail(f"{req_op} differs for U+{ord(t[1]):04X}: py={fn(t)!r} rust={g!r}")
                tally("code points", len(chunk))
                chunk = []
        # whole-string context (final sigma etc.): random strings
    texts = [rand_text(rng) for _ in range(n_random)]
    for fn, req_op in ((song_index.normalize_text, "py_normalize_text"), (song_index.fuzzy_match_key, "py_fuzzy_match_key")):
        got = dbw(cli, {"op": req_op, "texts": texts})["result"]
        for t, g in zip(texts, got):
            tally(req_op)
            if isinstance(g, dict):
                fail(f"{req_op} declined {t!r}: {g}")
            elif fn(t) != g:
                fail(f"{req_op} differs for {t!r}: py={fn(t)!r} rust={g!r}")

    # int(float(x)) as the app evaluates it inside try/except
    values = [0, 1, -1, 7, 2**31, 2**53, 2**62, 2**63 - 1, -(2**63), 2**63, -(2**63) - 1, 2**70, 0.0, -0.0, 1.5, -1.5, 0.999, 1e15, 1e18, 9.3e18, 9.223372036854776e18,
              -9.3e18, 1e300, 5e-324, True, False, None, [], [1], {}, {"a": 1},
              "", " ", "0", "12", " 12 ", "\t7\n", "12.9", "-12.9", "+3", ".5", "5.", "1e3", "1E3", "1e+3", "1e-3", "1_000", "1__0", "_1", "1_", "1_.5", "1._5",
              "1e1_0", "1_0e2", "0x10", "abc", "1,5", "nan", "NaN", "-nan", "inf", "Infinity", "-inf", "+inf", "1e400", "-1e400", "9223372036854775807",
              "9223372036854775808", "-9223372036854775809", "1e19", "٣", "１２", "12 ", "  4  ", "\x1c5\x1f", "4\x00", "1 2", "--1", "+-1",
              "0.0000001", "123456789012345678", "3.99999999999999999999"]
    got = dbw(cli, {"op": "py_int_of_float", "values": values})["result"]
    for v, g in zip(values, got):
        tally("int_of_float")
        try:
            expected = int(float(v))
        except Exception:  # noqa: BLE001 - mirrors the app's `except Exception: pass`
            expected = None
        if isinstance(g, dict):  # Rust declined: only allowed where Python cannot be matched by an i64 or the text is non-ASCII
            ok = (expected is not None and not -(2**63) <= expected < 2**63) or (isinstance(v, str) and not v.isascii())
            tally("int_of_float declined")
            if not ok:
                fail(f"int_of_float declined {v!r} (python {expected!r}): {g}")
        elif g != expected:
            fail(f"int_of_float differs for {v!r}: py={expected!r} rust={g!r}")

    # errors="ignore" decoding
    crafted = [b"", b"abc", b"\xff", b"a\xffb", b"\xc3", b"a\xc3", b"\xc3(", b"\xe2\x82", b"\xe2\x82(", b"\xf0\x9f\x98", b"\xf0\x9f\x98\x80", b"\xed\xa0\x80",
               b"\xf4\x90\x80\x80", b"\xc0\xaf", b"\x80\x80x", b"\xe0\x80\x80", b"ok\xe2\x82\xac\xff\xfe!"]
    for _ in range(300):
        crafted.append(bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 24))))
    for b in crafted:
        got = dbw(cli, {"op": "py_decode_ignore", "hex": b.hex()})["result"]
        tally("decode_ignore")
        if got != b.decode("utf-8", "ignore"):
            fail(f"decode_ignore differs for {b!r}: py={b.decode('utf-8', 'ignore')!r} rust={got!r}")


# ---- song_index --------------------------------------------------------------------------------------------------------------------

def rand_scalar(rng, textual=0.8):
    r = rng.random()
    if r < textual:
        return rand_text(rng)
    return rng.choice([None, 0, 1, 42, -7, 3.0, 0.1 + 0.2, 1e22, 1e-7, -0.0, 2.5, True, False, "", 123456789, 2**40, 12.5e9, 0.0])


def rand_numeric(rng):
    return rng.choice([None, 0, 1, 90, 215, 215.9, -4, -0.5, 3600.4, "180", " 240 ", "1_0", "12.5", "abc", "", "-3", "1e2", True, False, [], {}, "nan", "inf",
                       1e10, 2**33, "٣" if False else "7"])


def make_tracks(rng, workdir, n):
    tracks = []
    paths = []
    files = []
    for i in range(max(3, n // 4)):  # real files so the os.path.getmtime / getsize fallbacks have something to read
        f = Path(workdir) / f"real_{i}.mp3"
        f.write_bytes(b"x" * rng.randint(0, 5000))
        mt = rng.choice([1_600_000_000, 1_700_000_123, 1_234_567_890, 946_684_800, 1_000_000_000]) + rng.choice([0, 0.5, 0.9999999999, 0.25])
        os.utime(f, ns=(int(mt * 1e9), int(mt * 1e9)))
        files.append(str(f))
    for i in range(n):
        t = {}
        r = rng.random()
        if r < 0.3 and files:
            p = rng.choice(files)
        elif r < 0.4 and paths:
            p = rng.choice(paths)  # duplicate path -> INSERT OR REPLACE / ON CONFLICT
        else:
            p = f"/lib/{rng.choice(['A', 'B', 'ü', 'x y'])}/song_{i}_{rng.randint(0, 9)}.kfn"
        paths.append(p)
        pk = rng.choice(["path", "path", "path", "file"])
        t[pk] = p
        if rng.random() < 0.05:
            t.pop(pk)
            t[rng.choice(["path", "file"])] = rng.choice(["", None, 0, False])
        if rng.random() < 0.03:
            t["path"] = rng.choice([12345, 1.5, True])  # str() of a non-string path
        for key_variants in (("artist", "Artist"), ("title", "Title"), ("discid", "disc_id", "DiscID", "Vendor"), ("display",), ("type", "song_type")):
            if rng.random() < 0.9:
                for k in rng.sample(key_variants, rng.randint(1, len(key_variants))):
                    t[k] = rand_scalar(rng)
        for key_variants in (("duration_secs", "duration", "dur", "length_secs", "len_secs"), ("scan_mtime", "mtime", "modified", "last_modified"),
                             ("scan_size", "size", "bytes")):
            if rng.random() < 0.7:
                for k in rng.sample(key_variants, rng.randint(1, 2)):
                    t[k] = rand_numeric(rng)
        for k in ("provider", "provider_track_id", "provider_url", "local_reference_path", "authorization_requirement", "availability_status"):
            if rng.random() < 0.4:
                t[k] = rng.choice(["", "  ", " Spotify ", "LOCAL", "youtube", "id-42", " https://x/y?z ", "/dir/ref.kfn", None, 0, 5, "Ünï", " nb "])
        if rng.random() < 0.15:
            t["unused_container"] = rng.choice([[1, 2], {"a": [1]}, None])
        if rng.random() < 0.02:
            tracks.append(rng.choice([None, 5, "str", [1], []]))  # non-dict entries are skipped
        tracks.append(t)
    return tracks


def write_tracks(path, tracks, raw_bytes=None):
    Path(path).write_bytes(raw_bytes if raw_bytes is not None else json.dumps(tracks, ensure_ascii=rng_ascii[0]).encode("utf-8"))


rng_ascii = [False]


def py_rebuild(tracks_path, db):
    with frozen():
        return song_index.rebuild_from_tracks_json(Path(tracks_path), Path(db), verbose=False)[0]


def py_update(tracks_path, db, changed, removed):
    with frozen():
        r = song_index.update_from_tracks_json(Path(tracks_path), Path(db), changed_paths=changed, removed_paths=removed)
        return r[0], r[1]


def compare_dbs(label, dbcli, a, b):
    va, vb = view(dbcli, a), view(dbcli, b)
    d = first_difference(va, vb)
    tally(f"db compared ({label.split()[0]})")
    if d:
        fail(f"{label}: {d}")
    # the file must also be a sound SQLite database
    con = sqlite3.connect(b)
    try:
        ic = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    if ic != "ok":
        fail(f"{label}: integrity_check on the Rust-written database says {ic}")


def check_rebuild_update(cli, dbcli, rng, cases):
    with tempfile.TemporaryDirectory(prefix="dbw_parity_") as d:
        for case in range(cases):
            sub = Path(d) / f"c{case}"
            sub.mkdir()
            rng_ascii[0] = rng.random() < 0.5
            tracks = make_tracks(rng, sub, rng.randint(1, 60))
            tp = sub / "tracks.json"
            write_tracks(tp, tracks)
            pdb, rdb = str(sub / "py.db"), str(sub / "rs.db")
            # -- rebuild, twice (second run replaces the rows; AUTOINCREMENT keeps counting) --
            for round_no in (1, 2):
                if round_no == 2:
                    tracks = make_tracks(rng, sub, rng.randint(0, 40))
                    write_tracks(tp, tracks)
                try:
                    n_py = py_rebuild(tp, pdb)
                    py_err = None
                except Exception as exc:  # noqa: BLE001
                    n_py, py_err = None, exc
                r = dbw(cli, {"op": "index_rebuild", "db": rdb, "tracks": str(tp), "now": NOW})
                if py_err is not None:
                    tally("rebuild both-refuse" if not r["ok"] else "rebuild python-only-error")
                    if r["ok"]:
                        fail(f"case {case}: python raised {py_err!r} but rust wrote a database")
                    break
                if not r["ok"]:
                    fail(f"case {case} round {round_no}: rust refused ({r['error']}) where python succeeded")
                    break
                if r["result"]["rows"] != n_py:
                    fail(f"case {case}: rows {r['result']['rows']} != python {n_py}")
                compare_dbs(f"rebuild case {case} round {round_no}", dbcli, pdb, rdb)
            else:
                # -- update: same base on both sides, then a delta --
                base_py, base_rs = str(sub / "upy.db"), str(sub / "urs.db")
                shutil.copy(pdb, base_py)
                shutil.copy(pdb, base_rs)
                tracks = make_tracks(rng, sub, rng.randint(1, 30))
                write_tracks(tp, tracks)
                known = [p for t in tracks if isinstance(t, dict) for p in [t.get("path") or t.get("file")] if isinstance(p, str) and p]
                ch = rng.sample(known, min(len(known), rng.randint(0, 6))) + rng.choice([[], ["/never/seen.kfn"], [""]])
                rm = rng.sample(known, min(len(known), rng.randint(0, 4))) + rng.choice([[], ["/gone/1.kfn", "/gone/0.kfn"], [""]])
                try:
                    c_py = py_update(tp, base_py, ch, rm)
                    py_err = None
                except Exception as exc:  # noqa: BLE001
                    c_py, py_err = None, exc
                r = dbw(cli, {"op": "index_update", "db": base_rs, "tracks": str(tp), "changed": ch, "removed": rm, "now": NOW})
                if py_err is not None:
                    tally("update both-refuse" if not r["ok"] else "update python-only-error")
                    if r["ok"]:
                        fail(f"update case {case}: python raised {py_err!r} but rust wrote")
                    continue
                if not r["ok"]:
                    fail(f"update case {case}: rust refused ({r['error']}) where python succeeded")
                    continue
                if (r["result"]["changed"], r["result"]["removed"]) != c_py:
                    fail(f"update case {case}: counts {r['result']} != python {c_py}")
                compare_dbs(f"update case {case}", dbcli, base_py, base_rs)
                # -- no-op delta leaves the file untouched --
                before = Path(base_rs).read_bytes()
                dbw(cli, {"op": "index_update", "db": base_rs, "tracks": str(tp), "changed": [], "removed": [], "now": NOW + 5})
                if Path(base_rs).read_bytes() != before:
                    fail(f"update case {case}: an empty delta modified the database")
                tally("update no-op")

        # -- update against a missing database falls back to a rebuild --
        sub = Path(d) / "missing"
        sub.mkdir()
        tracks = make_tracks(rng, sub, 20)
        tp = sub / "tracks.json"
        write_tracks(tp, tracks)
        py_update(tp, str(sub / "py.db"), ["/x"], [])
        r = dbw(cli, {"op": "index_update", "db": str(sub / "rs.db"), "tracks": str(tp), "changed": ["/x"], "removed": [], "now": NOW})
        if not r["ok"]:
            fail(f"update-missing-db: rust refused: {r['error']}")
        else:
            compare_dbs("update on a missing database", dbcli, str(sub / "py.db"), str(sub / "rs.db"))

        # -- tracks.json oddities: top-level shapes, invalid UTF-8, BOM, empty file --
        for name, raw in [("empty", b""), ("dict", b'{"a": 1}'), ("null", b"null"), ("zero", b"0"), ("str", b'"abc"'), ("emptylist", b"[]"),
                          ("ignore-bytes", b'[{"path": "/a/\xff\xfeb.kfn", "artist": "Caf\xc3\xa9 \xe2\x82"}]'),
                          ("bom", b'\xef\xbb\xbf[{"path": "/a.kfn"}]'), ("badjson", b"[{"), ("number", b"5"), ("true", b"true"),
                          ("bigint", b'[{"path": "/a.kfn", "size": 18446744073709551616}]'), ("nested", b'[{"path": "/a.kfn", "artist": ["x"]}]'),
                          ("u64", b'[{"path": "/a.kfn", "artist": 18446744073709551615}]'), ("surrogate", b'[{"path": "/a.kfn", "artist": "\\ud800"}]'),
                          ("bigdur", b'[{"path": "/a.kfn", "duration": 1e30}]'), ("hugefloat", b'[{"path": "/a.kfn", "artist": 1e400}]')]:
            sub = Path(d) / f"shape_{name}"
            sub.mkdir()
            tp = sub / "tracks.json"
            tp.write_bytes(raw)
            try:
                py_rebuild(tp, str(sub / "py.db"))
                py_err = None
            except Exception as exc:  # noqa: BLE001
                py_err = exc
            r = dbw(cli, {"op": "index_rebuild", "db": str(sub / "rs.db"), "tracks": str(tp), "now": NOW})
            if py_err is not None:
                tally(f"shape both-refuse")
                if r["ok"]:
                    fail(f"shape {name}: python raised {py_err!r} but rust wrote a database")
                continue
            if not r["ok"]:
                # Rust may be STRICTER (declines); it must say so explicitly, never fail obscurely
                tally("shape rust-declines")
                if not (r["error"].startswith("decline:") or r["error"].startswith("json:")):
                    fail(f"shape {name}: rust failed without a decline: {r['error']}")
                continue
            compare_dbs(f"shape {name}", dbcli, str(sub / "py.db"), str(sub / "rs.db"))

        # -- a missing tracks.json is an error on both sides --
        try:
            py_rebuild(Path(d) / "absent.json", str(Path(d) / "x.db"))
            fail("python accepted a missing tracks.json")
        except FileNotFoundError:
            pass
        r = dbw(cli, {"op": "index_rebuild", "db": str(Path(d) / "y.db"), "tracks": str(Path(d) / "absent.json"), "now": NOW})
        if r["ok"] or "not found" not in r["error"]:
            fail(f"missing tracks.json: rust said {r}")
        tally("missing tracks.json")


LEGACY_V1 = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    artist TEXT, title TEXT, discid TEXT, duration_secs INTEGER, mtime INTEGER, size_bytes INTEGER,
    song_type TEXT, display TEXT, searchstring TEXT NOT NULL
);
CREATE INDEX idx_songs_artist ON songs(artist);
"""
LEGACY_V3 = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    artist TEXT, title TEXT, discid TEXT, duration_secs INTEGER, mtime INTEGER, size_bytes INTEGER,
    song_type TEXT, display TEXT, searchstring TEXT NOT NULL, artist_norm TEXT, title_norm TEXT
);
INSERT INTO meta VALUES ('schema_version', '3');
"""
LEGACY_V4 = LEGACY_V3.replace("'3'", "'4'") + "ALTER TABLE songs ADD COLUMN provider TEXT DEFAULT 'local';\nALTER TABLE songs ADD COLUMN provider_track_id TEXT;\n"


def make_legacy(db, ddl, rng, with_bad_artist=False):
    con = sqlite3.connect(db)
    con.executescript(ddl)
    cols = {r[1] for r in con.execute("PRAGMA table_info(songs)")}
    for i in range(rng.randint(0, 12) if cols else 0):
        row = {"path": f"/old/{i}.kfn", "artist": rng.choice([None, "The Who", "B.O.B. (x)", "", "Ünï"]), "title": rng.choice([None, "3 AM", "Gonna Go", "T"]),
               "searchstring": "x", "provider": rng.choice([None, "", "local", "spotify"])}
        row = {k: v for k, v in row.items() if k in cols}
        con.execute(f"INSERT INTO songs ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
    if with_bad_artist:
        con.execute("INSERT INTO songs (path, artist, searchstring) VALUES ('/old/int.kfn', 12345, 'x')")
    con.commit()
    con.close()


def check_legacy(cli, dbcli, rng, cases):
    with tempfile.TemporaryDirectory(prefix="dbw_parity_") as d:
        for i in range(cases):
            ddl = rng.choice([("v1", LEGACY_V1), ("v3", LEGACY_V3), ("v4", LEGACY_V4), ("empty", ""), ("v5", None)])
            for bad in (False, True) if ddl[0] in ("v1", "v3", "v4") and i % 3 == 0 else (False,):
                sub = Path(d) / f"l{i}{'b' if bad else ''}"
                sub.mkdir()
                py, rs = str(sub / "py.db"), str(sub / "rs.db")
                if ddl[1] is None:  # an already-current database built by Python
                    tp = sub / "t.json"
                    tp.write_text(json.dumps(make_tracks(rng, sub, 5)))
                    py_rebuild(tp, py)
                    shutil.copy(py, rs)
                    # stale norm columns must NOT be recomputed when schema_version is already 5
                    for p in (py, rs):
                        con = sqlite3.connect(p)
                        con.execute("UPDATE songs SET artist_norm='stale'")
                        con.commit()
                        con.close()
                else:
                    make_legacy(py, ddl[1], random.Random(i), bad)
                    Path(py).touch()
                    shutil.copy(py, rs)
                try:
                    with frozen():
                        con = song_index._connect(Path(py), read_only=False)
                        try:
                            song_index.init_schema(con)
                        finally:
                            con.close()
                    py_err = None
                except Exception as exc:  # noqa: BLE001
                    py_err = exc
                r = dbw(cli, {"op": "index_init", "db": rs})
                tally(f"legacy {ddl[0]}{' +bad' if bad else ''}")
                if py_err is not None:
                    if r["ok"]:
                        fail(f"legacy {ddl[0]}: python raised {py_err!r} but rust succeeded")
                elif not r["ok"]:
                    fail(f"legacy {ddl[0]}: rust failed: {r['error']}")
                    continue
                compare_dbs(f"legacy {ddl[0]}{' +bad' if bad else ''} case {i}", dbcli, py, rs)


# ---- phrase_markers ----------------------------------------------------------------------------------------------------------------

LEGACY_MARKERS = """
CREATE TABLE markers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, song_path TEXT NOT NULL, song_key TEXT, kind TEXT NOT NULL, seconds REAL NOT NULL, bars INTEGER, bpm REAL,
    label TEXT, source TEXT, is_default INTEGER NOT NULL DEFAULT 0, created_at INTEGER, updated_at INTEGER
);
INSERT INTO markers (song_path, kind, seconds) VALUES ('/a.kfn', 'bar4', 12.5), ('/a.kfn', 'custom', 30), ('/b.kfn', 'bar8', 1);
CREATE TABLE song_meta (song_path TEXT PRIMARY KEY, bpm REAL, updated_at INTEGER);
INSERT INTO song_meta VALUES ('/a.kfn', 120.5, 5);
"""


def hexid(n):
    return f"{n:032x}"


def rand_marker_ops(rng, n):
    paths = ["/a.kfn", "/b.kfn", "/dir/ü.kfn", "/c d.kfn"]
    ops = []
    inserted = 0  # custom markers always insert and bar markers usually do, so ids are drawn from what plausibly exists
    for _ in range(n):
        known = max(1, inserted)
        kind = rng.choice(["upsert", "upsert", "upsert", "set_default", "clear_default", "delete", "set_bpm", "set_analysis"])
        p = rng.choice(paths)
        if kind == "upsert":
            op = {"op": "upsert", "path": p, "kind": rng.choice(["bar4", "bar8", "bar16", "custom", "custom"]),
                  "seconds": rng.choice([0.0, -3.5, 12.25, 99.999, 1e-9, 1e9, 0.1 + 0.2, 61.0]),
                  "bars": rng.choice([None, 4, 8, 16]), "bpm": rng.choice([None, 120.0, 90.5, 128]),
                  "label": rng.choice([None, "", "Chorus", "ünï", "a'b"]), "source": rng.choice(["manual", "bpm"]),
                  "song_key": rng.choice(["", "k1", "k2"]), "make_default": rng.random() < 0.3}
            if rng.random() < 0.25:
                op["marker_id"] = rng.randint(1, known + 1)
            else:
                inserted += 1
            ops.append(op)
        elif kind == "set_default":
            ops.append({"op": kind, "path": p, "marker_id": rng.randint(1, known)})
        elif kind == "clear_default":
            ops.append({"op": kind, "path": p})
        elif kind == "delete":
            ops.append({"op": kind, "marker_id": rng.randint(1, known)})
        elif kind == "set_bpm":
            ops.append({"op": kind, "path": rng.choice(paths + [""]), "bpm": rng.choice([0, -1, 0.0, 120, 128.5, 60.25, 1e-3])})
        else:
            ops.append({"op": kind, "path": rng.choice(paths + [""]), "bpm": rng.choice([0, -2, 100, 127.5]),
                        "first_beat": rng.choice([None, 0.0, 0.25, 1.5]), "confidence": rng.choice([0.0, -0.0, 0.5, 0.93, 1]),
                        "version": rng.choice([0, 1, 3])})
    return ops


def py_marker_op(op, db):
    d = Path(db)
    if op["op"] == "upsert":
        return phrase_markers.upsert_marker(op["path"], kind=op["kind"], seconds=op["seconds"], bars=op["bars"], bpm=op["bpm"], label=op["label"],
                                            source=op["source"], song_key=op["song_key"], make_default=op["make_default"], marker_id=op.get("marker_id"), dbfile=d)
    if op["op"] == "set_default":
        return phrase_markers.set_default_marker(op["path"], op["marker_id"], dbfile=d)
    if op["op"] == "clear_default":
        return phrase_markers.clear_default(op["path"], dbfile=d)
    if op["op"] == "delete":
        return phrase_markers.delete_marker(op["marker_id"], dbfile=d)
    if op["op"] == "set_bpm":
        return phrase_markers.set_song_bpm(op["path"], op["bpm"], dbfile=d)
    return phrase_markers.set_song_analysis(op["path"], op["bpm"], op["first_beat"], op["confidence"], op["version"], dbfile=d)


def check_markers(cli, dbcli, rng, cases):
    with tempfile.TemporaryDirectory(prefix="dbw_parity_") as d:
        for i in range(cases):
            sub = Path(d) / f"m{i}"
            sub.mkdir()
            py, rs = str(sub / "py.db"), str(sub / "rs.db")
            legacy = i % 3 == 0
            if legacy:
                con = sqlite3.connect(py)
                con.executescript(LEGACY_MARKERS)
                con.commit()
                con.close()
                shutil.copy(py, rs)
            ops = rand_marker_ops(rng, rng.randint(1, 25))
            uuids = [hexid(1000 * i + k) for k in range(80)]
            with frozen(NOW, uuids):
                phrase_markers._connect(Path(py)).close()  # first connection: schema (+ uuid backfill on the legacy database)
                py_results = [py_marker_op(op, py) for op in ops]
            r0 = dbw(cli, {"op": "markers_init", "db": rs, "uuids": uuids})
            if not r0["ok"]:
                fail(f"markers {i}: init failed {r0['error']}")
                continue
            # the uuids consumed by the init must be skipped on the Rust side exactly as the Python run consumed them
            used = 3 if legacy else 0
            r = dbw(cli, {"op": "markers_ops", "db": rs, "now": NOW, "uuids": uuids[used:], "ops": ops})
            if not r["ok"]:
                fail(f"markers {i}: ops failed {r['error']}")
                continue
            for op, a, b in zip(ops, py_results, r["result"]):
                if op["op"] == "upsert" and a != b:
                    fail(f"markers {i}: upsert id python={a} rust={b} for {op}")
            tally("marker ops", len(ops))
            compare_dbs(f"markers case {i}{' (legacy)' if legacy else ''}", dbcli, py, rs)

        # a fresh database: schema only
        sub = Path(d) / "fresh"
        sub.mkdir()
        py, rs = str(sub / "py.db"), str(sub / "rs.db")
        with frozen(NOW, []):
            phrase_markers._connect(Path(py)).close()
        dbw(cli, {"op": "markers_init", "db": rs})
        compare_dbs("markers fresh schema", dbcli, py, rs)


# ---- refusals ----------------------------------------------------------------------------------------------------------------------

def check_guard(cli):
    live = os.path.expanduser("~/SingWS")
    cases = [(os.path.join(live, "singws.db"), "live profile"), ("/usr/local/nope.db", "outside temp"), ("relative-name-in-cwd.db", "cwd is not temp")]
    for path, why in cases:
        before = os.path.exists(path)
        r = dbw(cli, {"op": "index_init", "db": path})
        tally("guard")
        if r["ok"] or "refused" not in r["error"]:
            fail(f"guard: {why}: expected a refusal, got {r}")
        if os.path.exists(path) != before:
            fail(f"guard: {why}: the refused path was created")
    # a path under temp that is INSIDE the live profile (when the profile lives under temp) is covered by a Rust unit test
    with tempfile.TemporaryDirectory(prefix="dbw_parity_") as d:
        r = dbw(cli, {"op": "index_init", "db": os.path.join(d, "..", "..", "etc", "x.db")})
        if r["ok"]:
            fail("guard: a ../ escape from the temp dir was accepted")
        tally("guard")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument("--skip-unicode", action="store_true", help="skip the exhaustive code-point sweep (about a minute)")
    ap.add_argument("--cli", default=os.environ.get("SINGWS_DBW_CLI") or DBW_DEFAULT)
    ap.add_argument("--db-cli", default=os.environ.get("SINGWS_DB_CLI") or DB_DEFAULT)
    args = ap.parse_args(argv)
    for p in (args.cli, args.db_cli):
        if not os.path.exists(p):
            print(f"missing {p}; build with: cd rust && cargo build --release -p singws-dbw -p singws-db", file=sys.stderr)
            return 2
    rng = random.Random(args.seed)
    check_text(args.cli, rng, 4000, args.skip_unicode)
    check_rebuild_update(args.cli, args.db_cli, rng, args.cases)
    check_legacy(args.cli, args.db_cli, rng, max(12, args.cases // 2))
    check_markers(args.cli, args.db_cli, rng, max(12, args.cases // 2))
    check_guard(args.cli)
    shutil.rmtree(_HOME, ignore_errors=True)
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if failures:
        print(f"RESULT: {len(failures)} DIFFERENCE(S)")
        return 1
    print("RESULT: IDENTICAL (every database the Rust writer produced is logically equal to the Python-built one)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
