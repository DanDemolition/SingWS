#!/usr/bin/env python3
"""Parity of the Rust ``singws-scan`` library scan with the app's own Python.

Oracle = the REAL functions of ``0.2.18.1.py`` (extracted by ``ast``; the app is not imported, no Qt is needed):
``_build_library_scan_result`` (full mode, with the roots/locations/dedupe helpers it calls), ``parse_filename_stem``,
``_display_for_track``, ``fast_mp3_duration_from_zip`` / ``fast_mp3_duration_from_stream`` and the path normalisers.

Everything runs on SYNTHETIC trees built in a private temp directory (with HOME pointed at it). Optional ``--real-root PATH`` additionally
scans a real library read-only (it is only listed and stat'ed, never written; nothing under ~/SingWS is touched, the profile is not read).
The song-index step goes through ``singws-dbw`` into a TEMPORARY database and is compared with ``song_index.rebuild_from_tracks_json``.

Checks
  stem       parse_filename_stem on fuzzed names (unicode spaces, U+001F, extra separators) in all six formats + an unknown one
  normalize  the two path normalisers (symlinks, "..", "//", "~", relative paths)
  mp3        fast_mp3_duration_from_stream on fuzzed headers / ID3 tags / Xing, Info, VBRI blocks / size hints
  zip        fast_mp3_duration_from_zip on built archives (CDG size, MP3 fallback, chunks, last-member rules, damaged archives)
  scan       _build_library_scan_result on fuzzed trees: hidden files, case, links, hard links, roots (overlapping, missing, via symlink,
             "~"), old tracks (added/removed/outside/empty-root guard), settings (formats, migrated or not, location ids, stored dir sigs)
  index      the scanned tracks through dbw's rebuild vs song_index.rebuild_from_tracks_json
  real       (--real-root) a read-only scan of a real library

Usage:  python3 tools/scan_parity.py [--cases N] [--seed S] [--real-root PATH]...   Exit status 1 on any difference.
CARGO_TARGET_DIR selects the build (default ~/.cache/singws-rust-target-scan; singws-db-cli is taken from the same place).
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-scan")) + "/release"
CLI = TARGET + "/singws-scan-cli"
DBCLI = TARGET + "/singws-db-cli"
NOW = 1_760_000_000

failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 40:
        print("DIFF:", msg)


def tally(name, n=1):
    counts[name] = counts.get(name, 0) + n


# ---------------------------------------------------------------- the oracle: the app's own code, extracted by ast
WANTED = {
    "_normalize_library_location_path", "_normalize_scanned_library_path", "_scanned_library_path_key", "_library_location_key",
    "_library_location_id", "_migrate_library_locations", "_path_is_within_library", "_library_location_for_path",
    "_collapse_overlapping_library_roots", "_dedupe_library_tracks", "_build_library_scan_result", "_LibraryScanCancelled",
    "_display_for_track", "parse_filename_stem", "FILENAME_FORMATS", "DEFAULT_FILENAME_FORMAT",
    "_read_exact", "_skip_id3v2", "_parse_mpeg_frame_header", "_find_xing_vbri", "fast_mp3_duration_from_stream", "fast_mp3_duration_from_zip",
}


def load_oracle():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT))
    import song_index  # the quick scan sorts new files with song_index.normalize_text
    ns = {"song_index": song_index, "os": os, "time": time, "hashlib": hashlib, "zipfile": zipfile, "struct": struct, "io": io, "Path": Path,
          "TRACKS_PATH": Path("/nonexistent/tracks.json")}
    found = set()
    for node in tree.body:
        name = getattr(node, "name", None)
        if name is None and isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name is None and isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        if name in WANTED:
            exec(compile(ast.Module([node], []), "0.2.18.1.py", "exec"), ns)
            found.add(name)
    missing = WANTED - found
    if missing:
        raise SystemExit(f"oracle functions not found in 0.2.18.1.py: {sorted(missing)}")
    return ns


ORACLE = load_oracle()


def rust(request, timeout=900, cwd=None):
    out = subprocess.run([CLI], input=json.dumps(request), capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if out.returncode != 0:
        raise RuntimeError(f"scan cli failed: {out.stderr[:300]}")
    return json.loads(out.stdout)


def rust_ok(request, **kw):
    r = rust(request, **kw)
    return r["result"] if r.get("ok") else {"error": r.get("error", "")}


def declined(r):
    return isinstance(r, dict) and str(r.get("error", "")).startswith("decline:")


# ---------------------------------------------------------------- stem
SPACES = ["　", " ", "\x1f", "\x1c", "\t", " ", "​", "\u0085"]
TOKENS = [" - ", " - ", " - ", "-", " ", "  ", "A", "b", "KV", "é", "日本", "ß", "...", "x y", " -  ", "- "] + SPACES


def rand_stem(rng):
    return "".join(rng.choice(TOKENS) for _ in range(rng.randrange(0, 9)))


FORMATS = ["disc-artist-title", "artist-title-disc", "disc-title-artist", "title-artist-disc", "artist-title", "title-artist", "weird", ""]


def check_stem(rng, n):
    for fmt in FORMATS:
        stems = [rand_stem(rng) for _ in range(n)] + ["", " ", "a", "a - b", "a - b - c", "a - b - c - d", " - ", " -  - "]
        got = rust_ok({"op": "stem", "stems": stems, "fmt": fmt})
        for s, g in zip(stems, got):
            want = list(ORACLE["parse_filename_stem"](s, fmt or "disc-artist-title") if fmt else ORACLE["parse_filename_stem"](s, fmt))
            tally("stem")
            if g != want:
                fail(f"stem {s!r} fmt={fmt!r}: python {want} rust {g}")


# ---------------------------------------------------------------- mp3 stream
def mp3_header(rng):
    b = 0xFFE00000 | (rng.choice([0, 1, 2, 3, 3, 3]) << 19) | (rng.choice([0, 1, 1, 2, 3, 1]) << 17) | (1 << 16)
    b |= rng.randrange(0, 16) << 12 | rng.choice([0, 1, 2, 3, 0, 1]) << 10 | rng.randrange(0, 2) << 9
    if rng.random() < 0.05:
        b ^= 1 << rng.randrange(21, 32)
    return struct.pack(">I", b)


def mp3_blob(rng):
    parts = []
    if rng.random() < 0.3:
        size = rng.randrange(0, 40)
        sz = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
        parts.append(b"ID3\x04\x00" + bytes([rng.choice([0, 0x10, 0x80])]) + sz + bytes(rng.randrange(256) for _ in range(rng.randrange(0, size + 24))))
    elif rng.random() < 0.15:
        parts.append(bytes(rng.randrange(256) for _ in range(rng.randrange(0, 12))))
    parts.append(mp3_header(rng))
    body = bytearray(rng.randrange(256) if rng.random() < 0.3 else 0 for _ in range(rng.choice([0, 3, 40, 150, 250, 400, 2000])))
    for tag in (b"Xing", b"Info", b"VBRI"):
        if rng.random() < 0.4 and len(body) > 4:
            at = rng.randrange(0, len(body))
            blk = tag + struct.pack(">I", rng.choice([0, 1, 2, 3, 7, 0xFFFFFFFF])) + struct.pack(">I", rng.randrange(0, 5000)) + struct.pack(">I", rng.randrange(0, 1 << 20)) + bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
            body[at:at + len(blk)] = blk
    parts.append(bytes(body))
    return b"".join(parts)


def check_mp3(rng, n):
    cases, want = [], []
    fn = ORACLE["fast_mp3_duration_from_stream"]
    for _ in range(n):
        data = mp3_blob(rng)
        hint = rng.choice([None, 0, 1, 1000, 123456, 5_000_000, 2**40])
        cases.append({"op": "mp3_duration", "hex": data.hex(), "size_hint": hint})
        want.append(fn(io.BytesIO(data), file_size_hint=hint))
    # one process per request is slow; the CLI takes one request, so batch through a loop of subprocess calls on a thread pool
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(lambda c: rust_ok(c), cases))
    for c, w, g in zip(cases, want, got):
        tally("mp3")
        if g != w:
            fail(f"mp3 stream {c['hex'][:80]}.. hint={c['size_hint']}: python {w} rust {g}")


# ---------------------------------------------------------------- zip
def mp3_frames(n_frames=3000, vbr_tag=None):
    hdr = struct.pack(">I", 0xFFFB9000)  # MPEG1 layer 3 128 kbps 44.1 kHz
    payload = b"\x00" * 413
    out = hdr + payload
    if vbr_tag == "xing":
        out = hdr + b"\x00" * 32 + b"Xing" + struct.pack(">II", 1, n_frames) + b"\x00" * 200
    return out + (hdr + payload) * (n_frames if vbr_tag is None else 2)


def build_zip(path, rng, kind):
    comp = rng.choice([zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
    with zipfile.ZipFile(path, "w", comp) as z:
        cdg = bytes(96 * rng.randrange(0, 20000))
        if kind == "cdg+mp3":
            z.writestr("Song.mp3", mp3_frames(50)); z.writestr("Song.cdg", cdg)
        elif kind == "mp3 only":
            z.writestr("a/Song.mp3", mp3_frames(rng.choice([5, 50, 200, 400]))); z.writestr("readme.txt", b"hi")
        elif kind == "xing":
            z.writestr("Song.MP3", mp3_frames(vbr_tag="xing"))
        elif kind == "tiny cdg":
            z.writestr("s.cdg", bytes(96 * 74)); z.writestr("s.mp3", mp3_frames(50))
        elif kind == "empty cdg":
            z.writestr("s.cdg", b""); z.writestr("s.mp3", mp3_frames(50))
        elif kind == "last wins":
            z.writestr("one.cdg", bytes(96 * 75 * 10)); z.writestr("two.cdg", bytes(96 * 75 * 20)); z.writestr("two.mp3", mp3_frames(50)); z.writestr("one.mp3", mp3_frames(50))
        elif kind == "dirs":
            z.writestr("d/", b""); z.writestr("d/x.mp3", mp3_frames(50)); z.writestr("d/x.cdg.bak", b"x"); z.writestr("x.cdg/", b"")
        elif kind == "mp3 stem vs cdg":
            z.writestr("a.mp3", mp3_frames(50)); z.writestr("b.mp3", mp3_frames(60)); z.writestr("b-extra.cdg", b"")
        elif kind == "nothing":
            z.writestr("note.txt", b"x")
        elif kind == "empty":
            pass
        elif kind == "not mp3":
            z.writestr("Song.mp3", bytes(rng.randrange(256) for _ in range(5000)))
        elif kind == "big id3":
            z.writestr("Song.mp3", b"ID3\x04\x00\x00\x00\x00\x40\x00" + bytes(8192) + mp3_frames(50))
        elif kind == "unicode":
            z.writestr("Sång.mp3", mp3_frames(50))
        elif kind == "two mp3 no cdg":
            z.writestr("first.mp3", mp3_frames(50)); z.writestr("second.mp3", mp3_frames(400))
        elif kind == "stem picks the first":
            z.writestr("x.mp3", mp3_frames(50)); z.writestr("y.mp3", mp3_frames(400)); z.writestr("x.cdg", b"")
        elif kind == "xing zero frames":
            z.writestr("Song.mp3", struct.pack(">I", 0xFFFB9000) + b"\x00" * 32 + b"Xing" + struct.pack(">II", 1, 0) + b"\x00" * 200 + mp3_frames(5))
        elif kind == "very late header":
            n = 150 * 1024
            sz = bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])
            z.writestr("Song.mp3", b"ID3\x04\x00\x00" + sz + bytes(n) + mp3_frames(50))   # the header sits behind a 150 KB tag: only the third chunk reaches it
        elif kind == "late header":
            z.writestr("Song.mp3", bytes(70 * 1024) + mp3_frames(50))
    if kind == "bad crc":
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
            z.writestr("Song.mp3", mp3_frames(5))
        raw = bytearray(Path(path).read_bytes()); raw[40] ^= 0xFF; Path(path).write_bytes(bytes(raw))
    if kind == "truncated":
        build_zip(path, rng, "mp3 only")
        Path(path).write_bytes(Path(path).read_bytes()[:-30])
    if kind == "garbage":
        Path(path).write_bytes(bytes(rng.randrange(256) for _ in range(300)))


ZIP_KINDS = ["cdg+mp3", "mp3 only", "xing", "tiny cdg", "empty cdg", "last wins", "dirs", "mp3 stem vs cdg", "nothing", "empty", "not mp3",
             "big id3", "unicode", "late header", "two mp3 no cdg", "stem picks the first", "xing zero frames", "very late header", "bad crc", "truncated", "garbage"]


def check_zip(rng, work, n):
    fn = ORACLE["fast_mp3_duration_from_zip"]
    for i in range(n):
        kind = ZIP_KINDS[i % len(ZIP_KINDS)]
        p = str(Path(work) / f"z{i}.zip")
        build_zip(p, rng, kind)
        with contextlib.redirect_stdout(io.StringIO()):
            want = fn(p)
        got = rust_ok({"op": "zip_duration", "path": p, "cwd": work})
        tally("zip")
        if declined(got):
            tally("zip declined (non-ASCII name)")
            if kind != "unicode":
                fail(f"zip {kind}: unexpected decline {got}")
            continue
        w = {"duration": want[0], "cache_key": want[1]}
        if got != w:
            fail(f"zip {kind}: python {w} rust {got}")


# ---------------------------------------------------------------- normalize
def subprocess_normalize(paths, work):
    r = subprocess.run([CLI], input=json.dumps({"op": "normalize", "paths": paths, "cwd": work}), capture_output=True, text=True, cwd=work,
                       env={**os.environ, "HOME": os.path.join(work, "home")})
    return json.loads(r.stdout)["result"]


def check_normalize(rng, work, n):
    os.makedirs(f"{work}/real/sub", exist_ok=True)
    os.makedirs(f"{work}/home", exist_ok=True)
    for name, target in (("link", f"{work}/real"), ("rel", "real/sub"), ("loopish", "link")):
        if not os.path.lexists(f"{work}/{name}"):
            os.symlink(target, f"{work}/{name}")
    comps = ["a", "..", ".", "b", "", "~", "link", "real", "sub", "x y", "missing", "rel", " lead", "trail ", "　z"]
    paths = []
    for _ in range(n):
        p = "/".join(rng.choice(comps) for _ in range(rng.randrange(1, 7)))
        if rng.random() < 0.5:
            p = work + "/" + p
        if rng.random() < 0.1:
            p = "/" + p
        if rng.random() < 0.1:
            p = "~/" + p
        paths.append(p)
    got = subprocess_normalize(paths, work)
    here = os.getcwd()
    os.chdir(work)
    os.environ["HOME"] = os.path.join(work, "home")
    for p, g in zip(paths, got):
        tally("normalize")
        want = [ORACLE["_normalize_library_location_path"](p), ORACLE["_normalize_scanned_library_path"](p)]
        if isinstance(g, dict):
            tally("normalize declined")
            continue
        if g != want:
            fail(f"normalize {p!r}: python {want} rust {g}")
    os.chdir(here)


# ---------------------------------------------------------------- scan
NAME_PARTS = ["KV", "SC", "CC", "Linkin Park", "In The End", "Abba", "Dancing Queen", "Ünïcode", "日本語", "ß straße", "A  B", "x.y", "Mr. Brightside"]
EXTS = [".cdg", ".mp3", ".zip", ".mp4", ".CDG", ".Zip", ".MP4", ".txt", ".jpg", ".db", ""]


def rand_name(rng):
    n = rng.choice([1, 2, 3, 3, 4, 5])
    stem = " - ".join(rng.choice(NAME_PARTS) for _ in range(n))
    if rng.random() < 0.1:
        stem += rng.choice(SPACES) + rng.choice(["", " "])
    if rng.random() < 0.08:
        stem = " " + stem
    if rng.random() < 0.06:
        stem = stem + " "
    if rng.random() < 0.08:
        stem = "." + stem
    if rng.random() < 0.04:
        stem = "._" + stem
    return stem + rng.choice(EXTS)


def make_tree(rng, base):
    """Build a tree under base; returns the list of all regular file paths (for old-track generation)."""
    files, dirs = [], [base]
    for _ in range(rng.randrange(1, 7)):
        d = os.path.join(rng.choice(dirs), rng.choice(["Disc 1", "KV", ".hidden", "Ünï", "a b", "SC", "x", "日本"]) + (str(len(dirs)) if rng.random() < 0.6 else ""))
        os.makedirs(d, exist_ok=True)
        dirs.append(d)
    for d in dirs:
        for _ in range(rng.randrange(0, 9)):
            p = os.path.join(d, rand_name(rng))
            try:
                with open(p, "wb") as f:
                    f.write(bytes(rng.randrange(0, 40)))
                files.append(p)
                if rng.random() < 0.5:
                    os.utime(p, ns=(rng.randrange(10**18, 17 * 10**17 + 10**18), rng.choice([0, 999_999_999, 999_999_900, rng.randrange(10**18, 2 * 10**18)])))
            except OSError:
                pass
    media = [f for f in files if f.lower().endswith((".cdg", ".zip", ".mp4"))]
    for _ in range(rng.randrange(0, 4)):
        if media and rng.random() < 0.7:
            src = rng.choice(media)
            dst = os.path.join(rng.choice(dirs), "link " + rand_name(rng))
            try:
                if rng.random() < 0.5:
                    os.link(src, dst)  # same inode, two paths
                else:
                    os.symlink(src, dst)
                files.append(dst)
            except OSError:
                pass
    for _ in range(rng.randrange(0, 3)):
        d = rng.choice(dirs)
        try:
            os.symlink(os.path.join(base, "nope.cdg"), os.path.join(d, "broken" + rng.choice([".cdg", ".zip"])))
            os.symlink(rng.choice(dirs), os.path.join(d, "dirlink" + str(rng.randrange(99))))
        except OSError:
            pass
    return files


def old_track(path, rng):
    t = {"path": path, "type": "cdg", "artist": "Old", "title": "Old", "disc_id": "", "display": "Old - Old"}
    if rng.random() < 0.5:
        t.update(duration=rng.randrange(60, 400), scan_mtime=1, scan_size=2, library_location_id=rng.choice(["x", None, "library-0"]))
    return t


def make_case(rng, work, idx):
    case_dir = os.path.join(work, f"c{idx}")
    os.makedirs(case_dir)
    real = os.path.join(case_dir, "real")
    other = os.path.join(case_dir, "other")
    os.makedirs(real); os.makedirs(other)
    os.symlink(real, os.path.join(case_dir, "viaLink"))
    os.makedirs(os.path.join(case_dir, "home", "lib"))
    files = make_tree(rng, real)
    files2 = make_tree(rng, other) if rng.random() < 0.6 else []
    roots = []
    choice = rng.randrange(9)
    if choice == 0:
        roots = [real]
    elif choice == 1:
        roots = [real, other]
    elif choice == 2:
        roots = [os.path.join(case_dir, "viaLink"), real]       # same place twice
    elif choice == 3:
        roots = [real, os.path.join(real, sorted(os.listdir(real))[0])] if os.listdir(real) else [real]   # overlapping
    elif choice == 4:
        roots = [real, os.path.join(case_dir, "missing")]        # unavailable root => preserve
    elif choice == 5:
        roots = [real + "/", "  " + other + " ", ""]
    elif choice == 6:
        roots = []
    elif choice == 8:
        roots = [os.path.join(case_dir, "home", "lib"), real]
    else:
        roots = [os.path.join(case_dir, "viaLink") + "/./sub/..", "~/lib"] if rng.random() < 0.3 else [os.path.join(case_dir, "home", "lib"), real]
    old = []
    if rng.random() < 0.6:
        pool = files + files2
        old += [old_track(p, rng) for p in rng.sample(pool, min(len(pool), rng.randrange(0, 8)))]
        old += [old_track(os.path.join(case_dir, "gone", rand_name(rng)), rng) for _ in range(rng.randrange(0, 4))]
        old += [old_track("/somewhere/else/" + rand_name(rng), rng) for _ in range(rng.randrange(0, 3))]
        if rng.random() < 0.6:
            old += [old_track(os.path.join(case_dir, "home", "lib", "x.cdg"), rng)]       # tracks under an (empty) root => the guard
        if rng.random() < 0.15:
            old.append("junk"); old.append({"nopath": 1})
        if old and rng.random() < 0.3:
            old += [dict(rng.choice([o for o in old if isinstance(o, dict)] or [{"path": "/dup/x.cdg"}])) for _ in range(2)]   # the same path twice
        rng.shuffle(old)
    settings = {}
    if rng.random() < 0.7:
        settings["filename_format"] = rng.choice(FORMATS[:7] + ["bogus"])
    if rng.random() < 0.5:
        settings["karaoke_library_locations_migrated"] = True
        locs = [{"path": r, "id": rng.choice(["id-" + str(i), ""])} if rng.random() < 0.5 else r for i, r in enumerate(roots)]
        settings["karaoke_library_locations"] = locs
    elif rng.random() < 0.5:
        settings["karaoke_folder"] = real
        settings["karaoke_scan_roots"] = roots[:1]
    if rng.random() < 0.3:
        settings["_scan_preserve_outside_roots"] = True
    if rng.random() < 0.4:
        settings["karaoke_scan_dir_sigs"] = {p: [rng.randrange(10**18), rng.randrange(100)] for p in [real, other, "/zzz/none", os.path.join(real, "sub")]}
    return case_dir, roots, old, settings


def canon(v):
    return json.loads(json.dumps(v, default=str))


COMPARE_KEYS = ["ok", "quick_mode", "roots", "requested_roots", "unavailable_roots", "overlapping_roots_skipped", "dir_sigs", "tracks_added",
                "zip_count", "mp4_count", "cdg_count", "tracks_reused", "tracks_changed", "files_seen", "added_count", "removed_count",
                "index_changed_paths", "index_removed_paths", "reindex_needed", "suspect_empty_roots", "summary_text"]


def compare_scan(label, py, rs):
    bad = 0
    for k in COMPARE_KEYS:
        if py.get(k) != rs.get(k):
            fail(f"{label}: {k}: python {str(py.get(k))[:200]} rust {str(rs.get(k))[:200]}")
            bad += 1
    pt, rt = py["tracks"], rs["tracks"]
    if pt != rt:
        by_path_p = {json.dumps(t, sort_keys=True) for t in pt}
        by_path_r = {json.dumps(t, sort_keys=True) for t in rt}
        if by_path_p == by_path_r and len(pt) == len(rt):
            fail(f"{label}: tracks equal as a set but in a different ORDER")
        else:
            only_p = sorted(by_path_p - by_path_r)[:2]; only_r = sorted(by_path_r - by_path_p)[:2]
            fail(f"{label}: tracks differ ({len(pt)} vs {len(rt)}); python only {only_p} rust only {only_r}")
        bad += 1
    tally("scan compared")
    tally("  tracks seen", len(pt))
    if py.get("suspect_empty_roots"):
        tally("  cases with the mass-deletion guard")
    if py.get("unavailable_roots") or py.get("overlapping_roots_skipped"):
        tally("  cases with unavailable/overlapping roots")
    if py.get("added_count") and py.get("removed_count"):
        tally("  cases with both added and removed tracks")
    if len({(t.get("scan_device"), t.get("scan_inode")) for t in pt if t.get("scan_inode") is not None}) < len([t for t in pt if t.get("scan_inode") is not None]):
        tally("  cases where two tracks share an inode (a dedupe miss)")
    return bad == 0


def check_scan(rng, work, n, index_cases):
    sys.path.insert(0, str(ROOT / "tools"))
    import dbw_parity as dp
    fn = ORACLE["_build_library_scan_result"]
    ok_index = 0
    for i in range(n):
        case_dir, roots, old, settings = make_case(rng, work, i)
        os.environ["HOME"] = os.path.join(case_dir, "home")
        os.chdir(case_dir)
        try:
            py = canon(fn(roots, False, old, settings))
        except Exception as e:  # noqa: BLE001
            py = None
            tally("python raised")
        req = {"op": "scan", "roots": roots, "quick_mode": False, "old_tracks": old, "settings": settings, "cwd": case_dir}
        env_home = os.path.join(case_dir, "home")
        r = subprocess.run([CLI], input=json.dumps(req), capture_output=True, text=True, cwd=case_dir, env={**os.environ, "HOME": env_home})
        resp = json.loads(r.stdout)
        if py is None:
            if resp.get("ok"):
                fail(f"case {i}: Python raised but Rust answered")
            continue
        if not resp.get("ok"):
            if str(resp.get("error", "")).startswith("decline:"):
                tally("scan declined")
                continue
            fail(f"case {i}: rust error {resp.get('error')}")
            continue
        rs = resp["result"]
        same = compare_scan(f"case {i}", py, rs)
        if same and i < index_cases and py["tracks"]:
            tp = Path(case_dir) / "tracks.json"
            tp.write_text(json.dumps(py["tracks"]), encoding="utf-8")
            a, b = Path(case_dir) / "py.db", Path(tempfile.gettempdir()) / f"scan_parity_{os.getpid()}_{i}.db"
            dp.py_rebuild(tp, a)
            req2 = dict(req, index_db=str(b), now=dp.NOW)
            rs2 = subprocess.run([CLI], input=json.dumps(req2), capture_output=True, text=True, cwd=case_dir, env={**os.environ, "HOME": env_home})
            resp2 = json.loads(rs2.stdout)
            if not resp2.get("ok"):
                fail(f"case {i}: index step error {resp2.get('error')}")
            else:
                dp.compare_dbs(f"case {i} index", DBCLI, a, b)
                ok_index += 1
            for suffix in (".db", ".db-wal", ".db-shm", ".tracks.json"):
                with contextlib.suppress(OSError):
                    os.remove(str(b).replace(".db", suffix) if suffix != ".db" else str(b))
    tally("index compared", ok_index)
    failures.extend(dp.failures)


def mutate_disk(rng, case_dir, roots, files):
    """Change the disk the way a day of use does, so a quick update has real work: replaced, touched, removed, renamed, added files and folders."""
    media = [f for f in files if os.path.isfile(f) and f.lower().endswith((".cdg", ".zip", ".mp4")) and not os.path.basename(f).startswith(".")]
    dirs = sorted({os.path.dirname(f) for f in files if os.path.isdir(os.path.dirname(f))}) or [r for r in roots if os.path.isdir(r)]
    for _ in range(rng.randrange(0, 5)):
        op = rng.randrange(9)
        try:
            if op == 0 and media:                                   # content replaced: size and mtime change, the folder's mtime does not
                with open(rng.choice(media), "ab") as fh:
                    fh.write(b"x" * rng.randrange(1, 9))
            elif op == 1 and media:                                 # only the mtime moves
                f = rng.choice(media)
                st = os.stat(f)
                os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + rng.choice([10**9, 1, -5 * 10**9])))
            elif op == 2 and media:                                 # removed
                os.remove(rng.choice(media))
            elif op == 3 and media:                                 # renamed in place
                f = rng.choice(media)
                os.rename(f, os.path.join(os.path.dirname(f), "renamed " + rand_name(rng)))
            elif op == 4 and dirs:                                  # added file
                with open(os.path.join(rng.choice(dirs), "new " + rand_name(rng)), "wb") as fh:
                    fh.write(bytes(rng.randrange(0, 30)))
            elif op == 5 and dirs:                                  # added folder with files
                d = os.path.join(rng.choice(dirs), "Fresh" + str(rng.randrange(99)))
                os.makedirs(d, exist_ok=True)
                for _i in range(rng.randrange(1, 4)):
                    with open(os.path.join(d, "n " + rand_name(rng)), "wb") as fh:
                        fh.write(b"y")
            elif op == 6 and dirs and len(dirs) > 1:                # folder removed
                shutil.rmtree(rng.choice(dirs[1:]), ignore_errors=True)
            elif op == 7 and media:                                 # moved to another folder
                f = rng.choice(media)
                os.rename(f, os.path.join(rng.choice(dirs), "moved " + os.path.basename(f)))
            elif op == 8 and dirs:                                  # a folder touched without a change
                os.utime(rng.choice(dirs), None)
        except OSError:
            pass


def check_quick(rng, work, n):
    """Quick (incremental) update: a full scan gives the old library and the stored folder signatures, the disk then changes, and the app's own quick scan is compared with the Rust one."""
    fn = ORACLE["_build_library_scan_result"]
    for i in range(n):
        case_dir, roots, _old, settings = make_case(rng, work, 5000 + i)
        os.environ["HOME"] = os.path.join(case_dir, "home")
        os.chdir(case_dir)
        settings.pop("karaoke_scan_dir_sigs", None)
        try:
            first = canon(fn(roots, False, [], settings))
        except Exception:  # noqa: BLE001
            tally("quick: first scan raised")
            continue
        old = [dict(t) for t in first["tracks"]]
        for t in old:                                               # the odd tracks older libraries hold
            r = rng.random()
            if r < 0.08:
                for k in ("scan_mtime", "scan_mtime_ns", "scan_size", "scan_device", "scan_inode"):
                    t.pop(k, None)
            elif r < 0.14:
                t.pop("scan_mtime_ns", None)
            elif r < 0.2:
                t["artist"] = ""
                t.pop("display", None)
            elif r < 0.25:
                t["scan_mtime"] = float(t.get("scan_mtime", 0)) + 0.5
            if rng.random() < 0.3:
                t["duration"] = rng.randrange(60, 400)
        if rng.random() < 0.15:
            old.append({"path": os.path.join(case_dir, "home", "lib", ".hidden.cdg"), "type": "cdg", "artist": "x", "title": "y", "display": "x - y"})
        if rng.random() < 0.15:
            old.append(old_track("/somewhere/else/" + rand_name(rng), rng))
        settings = dict(settings)
        sigs = dict(first["dir_sigs"])
        if rng.random() < 0.2:
            sigs = {}
        elif rng.random() < 0.2 and sigs:
            for k in rng.sample(sorted(sigs), min(len(sigs), 2)):
                sigs[k] = [sigs[k][0] + rng.choice([0, 7, -3]), sigs[k][1] + rng.choice([0, 0, 1])]
        if sigs:
            settings["karaoke_scan_dir_sigs"] = sigs
        tp = Path(case_dir) / "tracks.json"
        baseline = 0
        if rng.random() < 0.7:
            tp.write_text("[]", encoding="utf-8")
            os.utime(tp, ns=(10**18, time.time_ns() + rng.choice([-10**11, 0, 10**11])))
            baseline = tp.stat().st_mtime_ns
        ORACLE["TRACKS_PATH"] = tp
        files = [t["path"] for t in first["tracks"]]
        mutate_disk(rng, case_dir, roots, files)
        try:
            py = canon(fn(roots, True, old, settings))
        except Exception:  # noqa: BLE001
            tally("quick: python raised")
            continue
        req = {"op": "scan", "roots": roots, "quick_mode": True, "old_tracks": old, "settings": settings, "cwd": case_dir, "tracks_baseline_ns": baseline}
        r = subprocess.run([CLI], input=json.dumps(req), capture_output=True, text=True, cwd=case_dir, env={**os.environ, "HOME": os.path.join(case_dir, "home")})
        resp = json.loads(r.stdout)
        if not resp.get("ok"):
            if str(resp.get("error", "")).startswith("decline:"):
                tally("quick declined")
                continue
            fail(f"quick case {i}: rust error {resp.get('error')}")
            continue
        rs = resp["result"]
        if compare_scan(f"quick case {i}", py, rs):
            tally("quick compared")
            tally("  quick: tracks reused", py["tracks_reused"])
            tally("  quick: tracks changed", py["tracks_changed"])
            if py["added_count"] or py["removed_count"]:
                tally("  quick: cases with added or removed tracks")
    ORACLE["TRACKS_PATH"] = Path("/nonexistent/tracks.json")


def check_real(roots):
    fn = ORACLE["_build_library_scan_result"]
    for root in roots:
        t0 = time.time()
        py = canon(fn([root], False, [], {}))
        t1 = time.time()
        req = {"op": "scan", "roots": [root], "quick_mode": False, "old_tracks": [], "settings": {}}
        rs = rust_ok(req, timeout=3600)
        t2 = time.time()
        if "error" in rs:
            if str(rs["error"]).startswith("decline:"):
                print(f"real {root}: Rust declined: {rs['error']}")
                continue
            fail(f"real {root}: rust error {rs['error']}")
            continue
        print(f"real {root}: {len(py['tracks'])} tracks; python {t1 - t0:.1f}s, rust {t2 - t1:.1f}s")
        compare_scan(f"real {root}", py, rs)
        tally("real tracks", len(py["tracks"]))


def main(argv=None):
    global rng
    failures.clear()
    counts.clear()
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--real-root", action="append", default=[])
    ap.add_argument("--index-cases", type=int, default=25)
    ap.add_argument("--quick-cases", type=int, default=150)
    a = ap.parse_args(argv)
    if not Path(CLI).exists():
        raise SystemExit(f"build first: cargo build --release -p singws-scan -p singws-db -p singws-dbw  (looked for {CLI})")
    rng = random.Random(a.seed)
    work = os.path.realpath(tempfile.mkdtemp(prefix="scan_parity_"))
    saved_home = os.environ.get("HOME")
    saved_cwd = os.getcwd()
    try:
        check_stem(rng, 400)
        check_mp3(rng, 600)
        check_zip(rng, work, 84)
        check_normalize(rng, work, 500)
        check_scan(rng, work, a.cases, a.index_cases)
        check_quick(rng, work, a.quick_cases)
        os.environ["HOME"] = saved_home
        check_real(a.real_root)
    finally:
        if saved_home:
            os.environ["HOME"] = saved_home
        os.chdir(saved_cwd)
        shutil.rmtree(work, ignore_errors=True)
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if failures:
        print(f"{len(failures)} DIFFERENCES")
        return 1
    print("IDENTICAL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
