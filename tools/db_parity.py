#!/usr/bin/env python3
"""Stage D3 parity: does a read-only Rust SQLite reader see EXACTLY what Python's sqlite3 sees in the app's databases?

Never touches the live databases: each is copied with SQLite's backup API from an immutable read-only connection (no sidecar files are
created or checkpointed) into a private temp directory, and every comparison runs on that snapshot.

For each database both engines (Python ``sqlite3`` and the ``singws-db-cli`` Rust binary, bundled SQLite) answer the same requests:
  * schema      sqlite_master, header pragmas (user_version, journal_mode, page_size, encoding, ...), columns and indexes per table
  * dump        every row of every table, by rowid, values encoded exactly (INTEGER vs REAL vs TEXT vs BLOB vs NULL kept apart)
  * app queries (singws.db) the app's real read shapes from song_index.py, with parameters drawn from the data itself plus hostile terms
                (``%``/``_`` wildcards that the app does not escape, accents, quotes, very long terms): the LIKE search, the exact
                artist/title lookup, the normalised-key lookup and the path lookup
Rows must be identical, in the same order. A difference that is only the order of rows with EQUAL sort keys is reported separately.

Usage:  python3 tools/db_parity.py [DB ...]      (default: ~/SingWS/singws.db ~/SingWS/phrase_markers.db)
Exit status 1 on any difference.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

CLI_DEFAULT = os.path.expanduser("~/.cache/singws-rust-target/release/singws-db-cli")


# ---- Python-side engine, encoding values exactly like the Rust crate ------------------------------------------------------------------

def _text(b):
    return ("$text", b)


def enc(v):
    if v is None or isinstance(v, int):
        return v
    if isinstance(v, float):
        if v != v:
            return {"$float": "nan"}
        if v in (float("inf"), float("-inf")):
            return {"$float": "inf" if v > 0 else "-inf"}
        return v
    if isinstance(v, tuple) and v and v[0] == "$text":
        try:
            return v[1].decode("utf-8")
        except UnicodeDecodeError:
            return {"$invalid_utf8": v[1].hex()}
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$blob": bytes(v).hex()}
    raise TypeError(f"unexpected sqlite value {v!r}")


def py_connect(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = _text
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA foreign_keys=OFF")  # explicit, exactly like the Rust reader and like song_index._connect(read_only=True)
    return con


def py_query(con, sql, params=()):
    cur = con.execute(sql, list(params))
    columns = [d[0] for d in cur.description]
    return {"columns": columns, "rows": [[enc(v) for v in row] for row in cur.fetchall()]}


def py_schema(con):
    pragmas = {}
    # database-level facts plus the connection's foreign_keys (both readers set it explicitly OFF, like the app's read connections)
    for name in ("user_version", "application_id", "journal_mode", "page_size", "encoding", "auto_vacuum", "foreign_keys"):
        pragmas[name] = enc(con.execute(f"PRAGMA {name}").fetchone()[0])
    master = py_query(con, "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")
    tables = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        t = enc(t)
        q = "'" + t.replace("'", "''") + "'"
        tables[t] = {
            "columns": py_query(con, f'SELECT cid, name, type, "notnull", dflt_value, pk FROM pragma_table_info({q})')["rows"],
            "indexes": py_query(con, f'SELECT name, "unique", origin, partial FROM pragma_index_list({q}) ORDER BY name')["rows"],
        }
    return {"pragmas": pragmas, "master": master["rows"], "tables": tables}


def py_dump(con, table):
    ident = '"' + table.replace('"', '""') + '"'
    try:
        return py_query(con, f"SELECT rowid, * FROM {ident} ORDER BY rowid")
    except sqlite3.OperationalError:
        return py_query(con, f"SELECT * FROM {ident} ORDER BY 1")


def py_handle(path, request):
    con = py_connect(path)
    try:
        op = request["op"]
        if op == "schema":
            return {"ok": True, "result": py_schema(con)}
        if op == "dump":
            return {"ok": True, "result": py_dump(con, request["table"])}
        return {"ok": True, "result": py_query(con, request["sql"], request.get("params", []))}
    except Exception as exc:  # noqa: BLE001 - the verdict compares success/failure, not messages
        return {"ok": False, "error": str(exc)}
    finally:
        con.close()


def rust_handle(cli, path, request):
    out = subprocess.run([cli, str(path)], input=json.dumps(request), capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        return {"ok": False, "error": f"cli exit {out.returncode}: {out.stderr[:200]}"}
    return json.loads(out.stdout)


# ---- snapshots ------------------------------------------------------------------------------------------------------------------------

def snapshot(src, dst):
    """Consistent copy of `src` that creates and changes NOTHING next to it (immutable read-only source, SQLite backup API)."""
    source = sqlite3.connect(f"file:{src}?mode=ro&immutable=1", uri=True)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


# ---- comparison -----------------------------------------------------------------------------------------------------------------------

def compare(name, py, rs, stats, bad, sort_cols=None):
    stats["requests"] += 1
    if py.get("ok") != rs.get("ok"):
        bad.append((name, f"python ok={py.get('ok')} rust ok={rs.get('ok')} ({py.get('error') or rs.get('error')})"))
        return
    if not py.get("ok"):
        stats["both_failed_alike"] += 1
        return
    if py["result"] == rs["result"]:
        stats["identical"] += 1
        return
    a, b = py["result"], rs["result"]
    if isinstance(a, dict) and "rows" in a and a.get("columns") == b.get("columns"):
        key = lambda r: json.dumps(r, sort_keys=True)
        if sorted(map(key, a["rows"])) == sorted(map(key, b["rows"])):
            stats["same_rows_different_order"] += 1
            bad.append((name, "same rows but a different order (ties in the sort key?)"))
            return
    bad.append((name, "results differ"))


def hostile_terms():
    return ["%", "_", "%_%", "a_c", "100%", "x_y", "é", "É", "ß", "日本", "ÀÉÎÕÜ", "o'brien", 'say "hi"', "back\\slash", "a" * 400,
            "​", "😀", " ", "ab%", "_b", "%%", "\\%", "SELECT", "1=1"]


def songs_requests(con, rng, cap=400):
    """The app's real read shapes with parameters drawn from the data (plus hostile terms)."""
    reqs = []
    try:
        rows = con.execute("SELECT artist, title, path, searchstring, artist_norm, title_norm FROM songs").fetchall()
    except sqlite3.OperationalError:
        try:
            rows = [r + (None, None) for r in con.execute("SELECT artist, title, path, searchstring FROM songs").fetchall()]
        except sqlite3.OperationalError:
            return reqs
    # a value that is NULL, a number or undecodable text (encoded as a marker object) cannot be used as a text parameter: use ""
    rows = [tuple(v if isinstance(v, str) else None for v in (enc(x) for x in r)) for r in rows]
    words = Counter()
    for r in rows:
        for w in re.split(r"\s+", (r[3] or "").lower()):
            if len(w) >= 2:
                words[w] += 1
    vocab = [w for w, _ in words.most_common(400)] or ["a"]
    for _ in range(cap):
        n = rng.choice([1, 1, 2, 2, 3])
        terms = [rng.choice(vocab)[: rng.randint(2, 12)] for _ in range(n)]
        reqs.append(("search", terms))
    for t in hostile_terms():
        reqs.append(("search", [t]))
        reqs.append(("search", [t, rng.choice(vocab)]))
    sample = rng.sample(rows, min(len(rows), cap))
    for r in sample:
        reqs.append(("artist_title", [r[0] or "", r[1] or ""]))
        reqs.append(("artist_title", [(r[0] or "").upper(), (r[1] or "").lower()]))
        reqs.append(("path", [r[2]]))
        if r[4] is not None:
            reqs.append(("norm", [r[5] or "", [r[4] or "", "nobody"]]))
    reqs += [("path", ["/definitely/not/here.mp3"]), ("artist_title", ["", ""]), ("artist_title", ["%", "_"])]
    out = []
    for kind, p in reqs:
        if kind == "search":
            where = " AND ".join(["searchstring LIKE ?"] * len(p))
            out.append((f"search{p}", {"op": "query", "sql": f"SELECT * FROM songs WHERE {where} ORDER BY artist, title LIMIT ?",
                                       "params": [f"%{w}%" for w in p] + [500]}))
        elif kind == "artist_title":
            out.append((f"artist_title{p}", {"op": "query", "sql": "SELECT * FROM songs WHERE LOWER(artist) = LOWER(?) AND LOWER(title) = LOWER(?)", "params": p}))
        elif kind == "path":
            out.append((f"path{p}", {"op": "query", "sql": "SELECT * FROM songs WHERE path = ?", "params": p}))
        else:
            title, artists = p
            ph = ",".join("?" for _ in artists)
            out.append((f"norm{p}", {"op": "query", "sql": f"SELECT * FROM songs WHERE title_norm = ? AND artist_norm IN ({ph})", "params": [title] + artists}))
    out.append(("fuzzy scan", {"op": "query", "sql": "SELECT * FROM songs LIMIT 20000", "params": []}))
    out.append(("count", {"op": "query", "sql": "SELECT COUNT(*) FROM songs", "params": []}))
    return out


def check_database(cli, src, seed=7):
    stats, bad = Counter(), []
    with tempfile.TemporaryDirectory(prefix="singws-dbparity-") as td:
        snap = Path(td) / "snapshot.db"
        snapshot(src, snap)
        compare("schema", py_handle(snap, {"op": "schema"}), rust_handle(cli, snap, {"op": "schema"}), stats, bad)
        tables = py_handle(snap, {"op": "query", "sql": "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"})
        names = [r[0] for r in tables["result"]["rows"]] if tables.get("ok") else []
        stats["tables"] = len(names)
        for t in names:
            req = {"op": "dump", "table": t}
            compare(f"dump {t}", py_handle(snap, req), rust_handle(cli, snap, req), stats, bad)
            n = py_handle(snap, {"op": "query", "sql": f'SELECT COUNT(*) FROM "{t}"'})
            stats[f"rows:{t}"] = n["result"]["rows"][0][0] if n.get("ok") else -1
        if "songs" in names:
            con = py_connect(snap)
            try:
                reqs = songs_requests(con, random.Random(seed))
            finally:
                con.close()
            for label, req in reqs:
                compare(label[:80], py_handle(snap, req), rust_handle(cli, snap, req), stats, bad)
    return stats, bad


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dbs", nargs="*")
    parser.add_argument("--cli", default=os.environ.get("SINGWS_DB_CLI") or CLI_DEFAULT)
    args = parser.parse_args(argv)
    if not Path(args.cli).exists():
        print(f"missing {args.cli}: build it with `cargo build --release -p singws-db` in rust/", file=sys.stderr)
        return 2
    dbs = args.dbs or [os.path.expanduser("~/SingWS/singws.db"), os.path.expanduser("~/SingWS/phrase_markers.db")]
    failed = 0
    for db in dbs:
        if not Path(db).exists():
            print(f"{db}: not found, skipped")
            continue
        stats, bad = check_database(args.cli, db)
        summary = {k: v for k, v in sorted(stats.items())}
        print(f"{os.path.basename(db)}: {json.dumps(summary)}")
        for name, why in bad[:8]:
            print(f"  DIFFERENCE {name}: {why}")
        failed += len(bad)
    print("DB PARITY OK" if not failed else f"DB PARITY: {failed} difference(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
