"""
song_index.py — SingWS library search index (SQLite)

- Reads:  ~/SingWS/tracks.json
- Writes: ~/SingWS/singws.db

This module contains ZERO Qt / UI / GStreamer code.

Run standalone:
    python song_index.py build
    python song_index.py search "nirvana apologies" --limit 50
"""

from __future__ import annotations
from pathlib import Path
from collections import OrderedDict
import json, os, re, sqlite3, time
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple, Callable

APP_DIRNAME = "SingWS"

def _perf_log_if_slow(name: str, ms: float, threshold_ms: float = 100.0):
    try:
        if float(ms) >= float(threshold_ms):
            print(f"[PERF] db_{name} took {float(ms):.0f}ms")
    except Exception:
        pass

# Set SINGWS_SEARCH_DIAG=1 to print per-query timing (query, hits, strict/fuzzy/total ms).
_SEARCH_DIAG = os.environ.get("SINGWS_SEARCH_DIAG") == "1"
_SEARCH_CACHE_MAX = 128
_LOOKUP_CACHE_MAX = 512
_SEARCH_CACHE: "OrderedDict[Tuple[Any, ...], List[Dict[str, Any]]]" = OrderedDict()
_LOOKUP_CACHE: "OrderedDict[Tuple[Any, ...], Any]" = OrderedDict()
_CACHE_LOCK = threading.RLock()

def user_singws_dir() -> Path:
    p = Path.home() / APP_DIRNAME
    p.mkdir(parents=True, exist_ok=True)
    return p

def tracks_json_path() -> Path:
    return user_singws_dir() / "tracks.json"

def db_path() -> Path:
    return user_singws_dir() / "singws.db"

def _db_signature(dbfile: Path) -> Tuple[str, int, int]:
    """Stable cache signature for read-only lookup results.

    Search runs in worker threads while playback is active.  Keying the cache by
    database path plus mtime/size lets repeated live-search queries return
    without re-opening SQLite or re-running the fuzzy scan, while an index
    rebuild naturally invalidates cached rows.
    """
    try:
        st = dbfile.stat()
        return (str(dbfile), int(st.st_mtime_ns), int(st.st_size))
    except Exception:
        return (str(dbfile), 0, 0)

def _copy_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]

def _cache_get(cache: OrderedDict, key):
    with _CACHE_LOCK:
        if key not in cache:
            return None
        value = cache.pop(key)
        cache[key] = value
        return value

def _cache_set(cache: OrderedDict, key, value, max_size: int) -> None:
    with _CACHE_LOCK:
        cache[key] = value
        while len(cache) > max_size:
            cache.popitem(last=False)

def _clear_read_caches() -> None:
    with _CACHE_LOCK:
        _SEARCH_CACHE.clear()
        _LOOKUP_CACHE.clear()

def _connect(dbfile: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        try:
            con = sqlite3.connect(f"file:{dbfile}?mode=ro", uri=True)
            con.execute("SELECT 1 FROM sqlite_master LIMIT 1;")
        except sqlite3.OperationalError:
            # Restricted test runners may allow reading the DB file but deny
            # SQLite's sidecar-journal probes. Immutable mode keeps search
            # genuinely read-only in that environment.
            try:
                con.close()
            except Exception:
                pass
            con = sqlite3.connect(f"file:{dbfile}?mode=ro&immutable=1", uri=True)
    else:
        con = sqlite3.connect(str(dbfile))
    con.row_factory = sqlite3.Row
    if not read_only:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA temp_store=MEMORY;")
    if not read_only:
        con.execute("PRAGMA foreign_keys=ON;")
    con.execute("PRAGMA busy_timeout=5000;")
    return con

def normalize_text(s: str) -> str:
    return " ".join((s or "").lower().replace("\u00A0"," ").split())

# --- Fuzzy matching for external requests -----------------------------------
# Tolerates the common differences between a web request and the local library:
# bracketed subtitles, punctuation, "&"/"and", a leading "the", and number
# words vs digits.
_NUMWORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90", "hundred": "100",
}
_BRACKETS_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_ALNUM_RE = re.compile(r"[a-z0-9]+")

def fuzzy_match_key(s: str) -> str:
    """Aggressive normalization for tolerant artist/title matching.

    e.g. 'B.O.B. (Bombs Over Baghdad)' -> 'bob',
         'Matchbox Twenty' -> 'matchbox20', '3 AM'/'3AM' -> '3am'.
    """
    s = (s or "").lower().replace("\u00A0", " ")
    s = _BRACKETS_RE.sub(" ", s)            # drop "(...)", "[...]", "{...}"
    s = s.replace("&", " and ")
    tokens = _ALNUM_RE.findall(s)
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]                 # ignore a leading "the"
    tokens = [_NUMWORDS.get(t, t) for t in tokens]
    return "".join(tokens)

def _get_track_path(track: Dict[str, Any]) -> str:
    return str(track.get("path") or track.get("file") or "")

def _get_duration_secs(track: Dict[str, Any]) -> Optional[int]:
    for k in ("duration_secs","duration","dur","length_secs","len_secs"):
        v = track.get(k)
        if v is None: 
            continue
        try:
            iv = int(float(v))
            if iv >= 0: 
                return iv
        except Exception:
            pass
    return None

def _get_mtime(track: Dict[str, Any]) -> Optional[int]:
    for k in ("mtime","modified","last_modified"):
        v = track.get(k)
        if v is None: 
            continue
        try:
            return int(float(v))
        except Exception:
            pass
    p = _get_track_path(track)
    try:
        return int(os.path.getmtime(p))
    except Exception:
        return None

def _get_size(track: Dict[str, Any]) -> Optional[int]:
    for k in ("size","bytes"):
        v = track.get(k)
        if v is None:
            continue
        try:
            return int(float(v))
        except Exception:
            pass
    p = _get_track_path(track)
    try:
        return int(os.path.getsize(p))
    except Exception:
        return None

def build_search_string(track: Dict[str, Any]) -> str:
    """Build searchable text blob from all relevant fields."""
    artist = str(track.get("artist") or track.get("Artist") or "")
    title  = str(track.get("title")  or track.get("Title")  or "")
    # FIXED: Handle all disc_id variations properly
    discid = str(track.get("discid") or track.get("disc_id") or track.get("DiscID") or track.get("Vendor") or "")
    display = str(track.get("display") or "")
    path = _get_track_path(track)
    blob = f"{artist} {title} {discid} {display} {os.path.basename(path)}"
    return normalize_text(blob)

def init_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        );
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            artist TEXT,
            title TEXT,
            discid TEXT,
            duration_secs INTEGER,
            mtime INTEGER,
            size_bytes INTEGER,
            song_type TEXT,
            display TEXT,
            searchstring TEXT NOT NULL
        );
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_songs_discid ON songs(discid);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs(artist);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_songs_title  ON songs(title);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_songs_mtime  ON songs(mtime);")
    # Composite index for fast exact artist+title lookups (external requests)
    con.execute("CREATE INDEX IF NOT EXISTS idx_songs_artist_title ON songs(artist COLLATE NOCASE, title COLLATE NOCASE);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_songs_path ON songs(path);")

    # --- Fuzzy-match columns (schema v3): normalized artist/title for tolerant
    #     external-request lookups. Added/backfilled once for existing DBs. ---
    existing_cols = {row[1] for row in con.execute("PRAGMA table_info(songs)").fetchall()}
    if "artist_norm" not in existing_cols:
        con.execute("ALTER TABLE songs ADD COLUMN artist_norm TEXT;")
    if "title_norm" not in existing_cols:
        con.execute("ALTER TABLE songs ADD COLUMN title_norm TEXT;")
    con.execute("CREATE INDEX IF NOT EXISTS idx_songs_fuzzy ON songs(artist_norm, title_norm);")

    cur_ver = None
    try:
        r = con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
        cur_ver = r[0] if r else None
    except Exception:
        cur_ver = None
    if cur_ver != "3":
        # One-time backfill of normalized columns for an existing library.
        try:
            con.create_function("fuzzykey", 1, fuzzy_match_key, deterministic=True)
        except TypeError:
            con.create_function("fuzzykey", 1, fuzzy_match_key)
        con.execute("UPDATE songs SET artist_norm = fuzzykey(artist), title_norm = fuzzykey(title);")
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version','3');")
    con.commit()

def rebuild_from_tracks_json(
    tracks_path: Optional[Path] = None, 
    dbfile: Optional[Path] = None, 
    *, 
    verbose: bool = True,
    progress_callback: Optional[Callable[[str, int, int], None]] = None
) -> Tuple[int, float]:
    """
    Rebuild the SQLite database from tracks.json.
    
    Args:
        tracks_path: Path to tracks.json (defaults to ~/SingWS/tracks.json)
        dbfile: Path to database (defaults to ~/SingWS/singws.db)
        verbose: Print progress to console
        progress_callback: Optional callback(message, current, total) for UI updates
    
    Returns:
        (rows_indexed, time_elapsed)
    """
    tracks_path = tracks_path or tracks_json_path()
    dbfile = dbfile or db_path()
    if not tracks_path.exists():
        raise FileNotFoundError(f"tracks.json not found at: {tracks_path}")

    t0 = time.time()
    
    # Load tracks
    if progress_callback:
        progress_callback("Loading tracks.json...", 0, 0)
    tracks = json.loads(tracks_path.read_text(encoding="utf-8", errors="ignore") or "[]") or []
    
    total_tracks = len([t for t in tracks if isinstance(t, dict) and _get_track_path(t)])
    
    con = _connect(dbfile, read_only=False)
    init_schema(con)

    # One explicit transaction
    con.execute("BEGIN;")
    con.execute("DELETE FROM songs;")

    batch = []
    rows = 0
    
    for idx, track in enumerate(tracks):
        if not isinstance(track, dict):
            continue
        path = _get_track_path(track)
        if not path:
            continue
            
        artist = track.get("artist") or track.get("Artist")
        title  = track.get("title")  or track.get("Title")
        # FIXED: Handle all disc_id field variations
        discid = track.get("discid") or track.get("disc_id") or track.get("DiscID") or track.get("Vendor")
        display = track.get("display")
        song_type = track.get("type") or track.get("song_type")
        dur = _get_duration_secs(track)
        mt  = _get_mtime(track)
        sz  = _get_size(track)
        ss  = build_search_string(track)
        an  = fuzzy_match_key(str(artist or ""))
        tn  = fuzzy_match_key(str(title or ""))
        batch.append((path, artist, title, discid, dur, mt, sz, song_type, display, ss, an, tn))
        rows += 1

        if len(batch) >= 2000:
            con.executemany("""
                INSERT OR REPLACE INTO songs
                (path, artist, title, discid, duration_secs, mtime, size_bytes, song_type, display, searchstring, artist_norm, title_norm)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch)
            batch.clear()
            
            # Progress updates
            if verbose and rows % 5000 == 0:
                print(f"  indexed {rows:,}/{total_tracks:,}...")
            if progress_callback and rows % 1000 == 0:
                progress_callback(f"Indexing songs... {rows:,}/{total_tracks:,}", rows, total_tracks)

    if batch:
        con.executemany("""
            INSERT OR REPLACE INTO songs
            (path, artist, title, discid, duration_secs, mtime, size_bytes, song_type, display, searchstring, artist_norm, title_norm)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, batch)

    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('last_rebuild_epoch', ?);", (str(int(time.time())),))
    con.execute("COMMIT;")
    con.commit()
    con.close()
    _clear_read_caches()
    
    elapsed = time.time() - t0
    
    if progress_callback:
        progress_callback(f"Search index ready ({rows:,} songs)", rows, total_tracks)

    return rows, elapsed

# Run the tolerant fuzzy fallback only when the strict substring search returns
# fewer than this many rows (a typo usually returns 0).  Keeps exact, high-yield
# searches fast and unchanged while rescuing misspelled / near-miss queries.
_FUZZY_TRIGGER = 25


def _bounded_levenshtein(a: str, b: str, maxd: int) -> int:
    """Levenshtein edit distance with early exit.

    Returns the true distance if it is <= maxd, otherwise any value > maxd.
    The early exit makes the common 'clearly different' case cheap.
    """
    la, lb = len(a), len(b)
    if abs(la - lb) > maxd:
        return maxd + 1
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        row_min = cur[0]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < row_min:
                row_min = cur[j]
        if row_min > maxd:
            return maxd + 1
        prev = cur
    return prev[lb]


def _fuzzy_threshold(word: str) -> int:
    """How many typos to tolerate, scaled to word length (short words get 0 to
    avoid matching everything)."""
    n = len(word)
    if n < 3:
        return 0
    if n < 6:
        return 1
    return 2


def _word_matches_tokens(word: str, tokens: Sequence[str], maxd: int) -> bool:
    if maxd <= 0 or not word:
        return False
    for t in tokens:
        if abs(len(t) - len(word)) > maxd:
            continue
        if _bounded_levenshtein(word, t, maxd) <= maxd:
            return True
    return False


def _canon_token(token: str) -> str:
    """Canonicalize one token for tolerant matching: lowercased, punctuation
    removed, '&' -> 'and', and number-words mapped to digits.

    e.g. 'Twenty' -> '20', 'B.O.B.' -> 'bob', "Don't" -> 'dont', '&' -> 'and'.
    Lets the search treat 'matchbox 20' / 'matchbox twenty' and 'B.O.B' / 'BOB'
    as the same thing.
    """
    t = (token or "").lower().replace("&", " and ")
    parts = _ALNUM_RE.findall(t)
    parts = [_NUMWORDS.get(p, p) for p in parts]
    return "".join(parts)


def _rank_key(row: Dict[str, Any], q: str):
    """Sort key so the best matches surface first:
    0 = exact artist/title/combined, 1 = starts-with, 2 = word starts-with,
    3 = plain contains. Cheap — only applied to the already-filtered result set.
    """
    a = normalize_text(str(row.get("artist") or ""))
    t = normalize_text(str(row.get("title") or ""))
    combined = (a + " " + t).strip()
    if q == a or q == t or q == combined:
        tier = 0
    elif a.startswith(q) or t.startswith(q) or combined.startswith(q):
        tier = 1
    elif (" " + q) in (" " + combined):
        tier = 2
    else:
        tier = 3
    return (tier, a, t)


def search_songs(query: str, *, limit: int=500, dbfile: Optional[Path]=None,
                 fuzzy: bool=True) -> List[Dict[str,Any]]:
    """
    Search songs in the database.

    Each whitespace-separated query word must match (strict substring search).
    When that yields few results (e.g. a misspelling returns nothing), a
    tolerant fuzzy pass is run so near-spellings still surface every available
    version.  Pass fuzzy=False to force strict-only behavior.

    Returns list of dicts with keys: id, path, artist, title, discid, duration_secs,
    mtime, size_bytes, song_type, display, searchstring
    """
    _perf_t0 = time.time()
    dbfile = dbfile or db_path()
    if not dbfile.exists():
        return []
    q = normalize_text(query)
    if not q:
        return []

    words = [w for w in q.split() if w]
    cache_key = (*_db_signature(dbfile), int(limit), bool(fuzzy), q)
    cached = _cache_get(_SEARCH_CACHE, cache_key)
    if cached is not None:
        return _copy_rows(cached)

    con = _connect(dbfile, read_only=True)
    # Do NOT call init_schema here — search is read-only.  Schema migrations
    # run only inside rebuild_from_tracks_json.  Calling init_schema here would
    # attempt a write-transaction (the schema-v3 backfill UPDATE) which blocks
    # the search thread when a rebuild is simultaneously in progress, causing
    # "database is locked" timeouts and silently empty results.

    _t_strict = time.time()
    where = " AND ".join(["searchstring LIKE ?"] * len(words))
    params = [f"%{w}%" for w in words] + [int(limit)]
    try:
        rows = [dict(r) for r in con.execute(
            f"SELECT * FROM songs WHERE {where} ORDER BY artist, title LIMIT ?", params
        ).fetchall()]
    except Exception:
        con.close()
        return []
    # Rank so exact/prefix matches show before plain "contains" matches. Only
    # touches the already-filtered result set (<= limit rows), so it's cheap.
    rows.sort(key=lambda r: _rank_key(r, q))
    strict_ms = (time.time() - _t_strict) * 1000.0

    # Fuzzy fallback runs ONLY when the strict substring search found NOTHING
    # (i.e. a typo / no match). Normal searches that match anything never pay the
    # cost of the full-table fuzzy scan — that was the main source of slowness.
    # The scan is also time-budgeted so it can't stall on a huge library.
    _FUZZY_SCAN_CAP = 150_000
    _FUZZY_TIME_BUDGET_S = 0.25
    fuzzy_ms = 0.0
    if fuzzy and len(rows) == 0 and words:
        _t_fuzzy = time.time()
        terms = [(w, _canon_token(w), _fuzzy_threshold(_canon_token(w))) for w in words]
        extra: List[Dict[str, Any]] = []
        scanned = 0
        for r in con.execute(f"SELECT * FROM songs LIMIT {_FUZZY_SCAN_CAP}"):
            scanned += 1
            # Cheap periodic time check so a giant library can't hang the thread.
            if (scanned & 2047) == 0 and (time.time() - _t_fuzzy) > _FUZZY_TIME_BUDGET_S:
                break
            d = dict(r)
            ss = str(d.get("searchstring") or "")
            ctokens = [_canon_token(tok) for tok in ss.split()]
            ctoken_set = set(ctokens)
            ok = True
            for w, cw, maxd in terms:
                if w in ss:
                    continue                      # raw substring
                if cw and cw in ctoken_set:
                    continue                      # number-word / punctuation match
                if _word_matches_tokens(cw, ctokens, maxd):
                    continue                      # typo within edit distance
                ok = False
                break
            if ok:
                extra.append(d)
                if len(extra) >= int(limit):
                    break
        extra.sort(key=lambda d: _rank_key(d, q))
        rows.extend(extra)
        fuzzy_ms = (time.time() - _t_fuzzy) * 1000.0

    con.close()
    total_ms = (time.time() - _perf_t0) * 1000.0
    _perf_log_if_slow("search_songs", total_ms, 50.0)
    if _SEARCH_DIAG:
        print(f"[SEARCH] q={query!r} hits={len(rows)} strict={strict_ms:.1f}ms "
              f"fuzzy={fuzzy_ms:.1f}ms total={total_ms:.1f}ms")
    _cache_set(_SEARCH_CACHE, cache_key, _copy_rows(rows), _SEARCH_CACHE_MAX)
    return rows

def find_by_artist_title(artist: str, title: str, *, dbfile: Optional[Path]=None) -> List[Dict[str,Any]]:
    """
    Find songs by exact artist and title match (case-insensitive).
    
    This is optimized for external request processing - uses composite index
    for instant lookups even with 100k+ tracks.
    
    Returns list of matching tracks (usually 1-3 for different disc versions).
    """
    _perf_t0 = time.time()
    dbfile = dbfile or db_path()
    if not dbfile.exists():
        return []
    cache_key = (*_db_signature(dbfile), "artist_title", normalize_text(artist), normalize_text(title))
    cached = _cache_get(_LOOKUP_CACHE, cache_key)
    if cached is not None:
        return _copy_rows(cached)
    
    con = _connect(dbfile, read_only=True)
    
    # Case-insensitive exact match using the composite index
    rows = con.execute(
        "SELECT * FROM songs WHERE LOWER(artist) = LOWER(?) AND LOWER(title) = LOWER(?)",
        (artist, title)
    ).fetchall()

    # Fallback: tolerant normalized match (parentheticals, punctuation,
    # number-words, leading "the") via the indexed *_norm columns.
    if not rows:
        ak = fuzzy_match_key(artist)
        tk = fuzzy_match_key(title)
        if ak and tk:
            try:
                rows = con.execute(
                    "SELECT * FROM songs WHERE artist_norm = ? AND title_norm = ?",
                    (ak, tk)
                ).fetchall()
            except sqlite3.OperationalError:
                # Read-only lookup path: do not run schema migrations here.
                # Older DBs without *_norm columns simply skip the fuzzy
                # fallback until the next explicit index rebuild.
                rows = []

    con.close()
    result = [dict(r) for r in rows]
    _perf_log_if_slow("find_by_artist_title", (time.time() - _perf_t0) * 1000.0, 100.0)
    _cache_set(_LOOKUP_CACHE, cache_key, _copy_rows(result), _LOOKUP_CACHE_MAX)
    return result

def find_by_path(path: str, *, dbfile: Optional[Path]=None) -> Optional[Dict[str,Any]]:
    """
    Find a single song by exact path match.
    
    Optimized for lookup_display_name() calls during UI updates.
    Returns None if not found.
    """
    _perf_t0 = time.time()
    dbfile = dbfile or db_path()
    if not dbfile.exists():
        return None
    cache_key = (*_db_signature(dbfile), "path", str(path or ""))
    cached = _cache_get(_LOOKUP_CACHE, cache_key)
    if cached is not None:
        return dict(cached) if cached else None
    
    con = _connect(dbfile, read_only=True)
    
    row = con.execute("SELECT * FROM songs WHERE path = ?", (path,)).fetchone()
    con.close()
    result = dict(row) if row else None
    _perf_log_if_slow("find_by_path", (time.time() - _perf_t0) * 1000.0, 100.0)
    _cache_set(_LOOKUP_CACHE, cache_key, dict(result) if result else None, _LOOKUP_CACHE_MAX)
    return result

def main(argv: Optional[Sequence[str]]=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="song_index.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=50)
    args = ap.parse_args(argv)

    if args.cmd == "build":
        tp = tracks_json_path()
        dbf = db_path()
        print(f"SingWS folder: {user_singws_dir()}")
        print(f"tracks.json:   {tp}")
        print(f"singws.db:     {dbf}")
        rows, dt = rebuild_from_tracks_json(tp, dbf, verbose=True)
        print(f"✅ Indexed {rows:,} songs into {dbf} in {dt:.2f}s")
        return 0

    if args.cmd == "search":
        t0 = time.time()
        rows = search_songs(args.query, limit=int(args.limit))
        dt = (time.time()-t0)*1000.0
        print(f"{len(rows)} results in {dt:.1f} ms")
        for r in rows[: min(20,len(rows))]:
            print(f"- {r.get('artist','')} - {r.get('title','')} | {r.get('discid','')} | {r.get('duration_secs')}")
        return 0
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
