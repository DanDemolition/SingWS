#!/usr/bin/env python3
"""Mutation test for singws-dbw: every mutant must be killed by the Rust unit tests or by tools/dbw_parity.py (quick settings).

Each mutant is one textual change to the crate source; the original file is restored afterwards (also on Ctrl-C). Equivalent mutants
(no observable difference in any database the writer can produce) are listed explicitly with the reason and are not counted as survivors.
Usage: python3 tools/mutate_dbw.py [--only N]      Exit status 1 when a non-equivalent mutant survives.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-dbw/src"
ENV = dict(os.environ, CARGO_TARGET_DIR=os.path.expanduser("~/.cache/singws-rust-target"))

# (file, old, new, equivalent-reason-or-None)
M = [
    ("pyval.rs", "|| ('\\u{1c}'..='\\u{1f}').contains(&c)", "|| false", None),
    ("pyval.rs", "                i != 0\n", "                i == 0\n", None),
    ("pyval.rs", "operands.last().copied()", "operands.first().copied()", None),
    ("pyval.rs", '"True"', '"true"', None),
    ("pyval.rs", "&& b[i + 1].is_ascii_digit()", "&& true", None),
    ("pyval.rs", "if t >= 9_223_372_036_854_775_808.0 ||", "if t > 9_223_372_036_854_775_808.0 ||", None),
    ("pyval.rs", "let t = f.trunc();", "let t = f.round();", None),
    ("pyval.rs", "i += off + 2;", "i += off + 1;", None),
    ("pyval.rs", "if tokens.len() > 1 && tokens[0] == \"the\"", "if tokens.len() >= 1 && tokens[0] == \"the\"", None),
    ("pyval.rs", 'tokens.push(numword("going").to_string());', 'tokens.push(numword("go").to_string());', None),
    ("pyval.rs", '("ninety", "90")', '("ninety", "80")', None),
    ("pyval.rs", "p.rsplit('/').next()", "p.split('/').next()", None),
    ("index.rs", "Some(n) => rest = &bad[n..],", "Some(_) => rest = &bad[1..],", "the bytes after the first are continuation bytes of an incomplete sequence, each invalid on its own and dropped by the next iteration"),
    ("pyval.rs", "(0xA7C0, 0xA7FF)", "(0xA7D0, 0xA7FF)", None),
    ("pyval.rs", ".replace('\\u{a0}', \" \")", "", "U+00A0 is whitespace for split() and not [a-z0-9] for the token regex, in Python as well, so the replace changes nothing observable"),
    ("pyval.rs", "c.is_whitespace() ||", "false ||", None),
    ("index.rs", 'con.query_row("PRAGMA journal_mode=WAL;", [], |_| Ok(())).map_err(e)?;', "", None),
    ("index.rs", "PRAGMA synchronous=NORMAL;", "PRAGMA synchronous=FULL;", None),
    ("index.rs", "PRAGMA foreign_keys=ON;", "PRAGMA foreign_keys=OFF;", "no foreign keys exist in either schema, and the pragma is per-connection state, so no database differs (a unit test pins it)"),
    ("index.rs", '"CREATE INDEX IF NOT EXISTS idx_songs_path ON songs(path);",', "", None),
    ("index.rs", 'cur_ver.as_deref() != Some("5")', 'cur_ver.as_deref() == Some("5")', None),
    ("index.rs", "UPDATE songs SET provider = COALESCE(NULLIF(provider, ''), 'local');", "", None),
    ("index.rs", "let low = py_strip(&s).to_lowercase();", "let low = py_strip(&s).to_string();", None),
    ("index.rs", "if !need_non_negative || i >= 0 {", "if !need_non_negative || i > 0 {", None),
    ("index.rs", "Some(f.trunc() as i64)", "Some(f.round() as i64)", None),
    ("index.rs", 'con.execute_batch("DELETE FROM songs;").map_err(e)?;', "", None),
    ("index.rs", 'format!("INSERT OR REPLACE INTO songs\\n            {COLUMNS}")', 'format!("INSERT INTO songs\\n            {COLUMNS}")', None),
    ("index.rs", "if changed.is_empty() && removed.is_empty() {", "if changed.is_empty() || removed.is_empty() {", None),
    ("index.rs", "VALUES('last_incremental_epoch', ?)", "VALUES('last_rebuild_epoch', ?)", None),
    ("index.rs", "artist_norm=excluded.artist_norm, title_norm=excluded.title_norm", "artist_norm=excluded.artist_norm", None),
    ("index.rs", 'let artist_s = py_str_or_empty(py_or(&[get(t, "artist"), get(t, "Artist"),', 'let artist_s = py_str_or_empty(py_or(&[get(t, "title"), get(t, "Artist"),', None),
    ("index.rs", '"{artist_s} {title_s} {discid_s} {display_s} {provider} {provider_track_id} {provider_url} {} {}"', '"{artist_s} {title_s} {discid_s} {display_s} {provider_track_id} {provider} {provider_url} {} {}"', None),
    ("index.rs", 'py_or(&[get(t, "path"), get(t, "file"), &Value::String(String::new())])', 'py_or(&[get(t, "file"), get(t, "path"), &Value::String(String::new())])', None),
    ("index.rs", 'get(t, "DiscID"),\n    get(t, "Vendor")]);' if False else 'let discid = py_or(&[get(t, "discid"), get(t, "disc_id"), get(t, "DiscID"), get(t, "Vendor")]);', 'let discid = py_or(&[get(t, "discid"), get(t, "disc_id"), get(t, "DiscID")]);', None),
    ("index.rs", "Value::Bool(b) => Sql::Integer(*b as i64),", "Value::Bool(_) => Sql::Integer(0),", None),
    ("index.rs", "Value::Null | Value::Object(_) | Value::String(_) | Value::Bool(false) => Ok(Vec::new()),", "Value::Null | Value::String(_) | Value::Bool(false) => Ok(Vec::new()),", None),
    ("index.rs", "let rows = rebuild(tracks_path, dbfile, now)?;\n        return Ok((rows, 0));", "return Ok((0, 0));", None),
    ("index.rs", "            k TEXT PRIMARY KEY,", "            k  TEXT PRIMARY KEY,", None),
    ("index.rs", "if !existing.iter().any(|c| c == \"title_norm\")", "if existing.iter().any(|c| c == \"title_norm\")", None),
    ("markers.rs", "{ m.seconds } else { 0.0 }", "{ m.seconds } else { m.seconds }", None),
    ("markers.rs", "con.execute(\"UPDATE markers SET uuid=? WHERE id=?\", rusqlite::params![uuids(), id]).map_err(e)?;", "let _ = (&uuids, id);", None),
    ("markers.rs", "con.execute(\"UPDATE markers SET is_default=0 WHERE song_path=?\", [path]).map_err(e)?;\n    con.execute(\"UPDATE markers SET is_default=1", "con.execute(\"UPDATE markers SET is_default=1", None),
    ("markers.rs", "SET deleted_at=?, updated_at=?, is_default=0 WHERE id=?", "SET deleted_at=?, updated_at=? WHERE id=?", None),
    ("markers.rs", "if path.is_empty() || bpm <= 0.0 {\n        return Ok(false);\n    }\n    con.execute(\n        \"INSERT INTO song_meta (song_path, bpm, updated_at)", "if path.is_empty() || bpm < 0.0 {\n        return Ok(false);\n    }\n    con.execute(\n        \"INSERT INTO song_meta (song_path, bpm, updated_at)", None),
    ("markers.rs", "if existing.is_none() && m.kind != \"custom\" {", "if existing.is_none() && m.kind == \"custom\" {", None),
    ("markers.rs", "rusqlite::params![m.song_key, m.kind, seconds, opt(m.bars), opt(m.bpm), opt(m.label.map(str::to_string)), m.source, now, id]", "rusqlite::params![m.song_key, m.kind, seconds, opt(m.bars), opt(m.bpm), opt(m.label.map(str::to_string)), m.source, 0, id]", None),
    ("markers.rs", "PRAGMA foreign_keys=OFF;", "", "no foreign keys in the markers schema and the pragma is not persisted; pinned by a unit test"),
    ("lib.rs", "if !parent.starts_with(&temp) {", "if false {", None),
    ("lib.rs", "if parent.starts_with(&l) {", "if false {", None),
]


def run(cmd, timeout=900):
    return subprocess.run(cmd, cwd=ROOT / "rust", env=ENV, capture_output=True, text=True, timeout=timeout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int)
    args = ap.parse_args()
    survivors, equivalents, killed = [], [], {"unit": 0, "parity": 0, "build": 0}
    for n, (fname, old, new, reason) in enumerate(M):
        if args.only is not None and n != args.only:
            continue
        path = SRC / fname
        text = path.read_text()
        if old not in text:
            print(f"[{n:02d}] PATTERN NOT FOUND in {fname}: {old[:60]!r}")
            survivors.append((n, "pattern not found"))
            continue
        try:
            path.write_text(text.replace(old, new, 1))
            r = run(["cargo", "test", "-p", "singws-dbw", "--lib", "--quiet"])
            if r.returncode != 0:
                if "error[" in r.stderr or "could not compile" in r.stderr:
                    killed["build"] += 1
                    print(f"[{n:02d}] killed (does not compile)  {fname}: {old[:50]!r}")
                else:
                    killed["unit"] += 1
                    print(f"[{n:02d}] killed by unit tests       {fname}: {old[:50]!r}")
                continue
            run(["cargo", "build", "--release", "-p", "singws-dbw"])
            p = subprocess.run([sys.executable, str(ROOT / "tools/dbw_parity.py"), "--skip-unicode", "--cases", "24"], capture_output=True, text=True, timeout=900)
            if p.returncode != 0:
                killed["parity"] += 1
                print(f"[{n:02d}] killed by parity           {fname}: {old[:50]!r}")
            elif reason:
                equivalents.append((n, reason))
                print(f"[{n:02d}] EQUIVALENT ({reason[:70]})")
            else:
                survivors.append((n, f"{fname}: {old[:60]!r}"))
                print(f"[{n:02d}] SURVIVED                   {fname}: {old[:60]!r}")
        finally:
            path.write_text(text)
    run(["cargo", "build", "--release", "-p", "singws-dbw"])
    print(f"killed: {killed}  equivalent: {len(equivalents)}  survivors: {len(survivors)}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
