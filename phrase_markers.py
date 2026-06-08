"""Phrase-Aligned Song Start — persistent per-song phrase markers.

Pure-Python (no Qt / no GStreamer) so it is unit-testable in isolation. Stores
phrase-start markers in their OWN SQLite database, separate from the song search
index (song_index.py rebuilds that from tracks.json, which would wipe markers).
Markers are keyed primarily by the song's file path, with a normalized
artist|title|discid fallback so they can be re-associated if a path changes.

A "marker" is a candidate start position for a song:
  * bar markers (4/8/16 bars in) derived from BPM, and
  * custom markers placed by hand while previewing.

4 bars = 16 beats, 8 bars = 32 beats, 16 bars = 64 beats (assumes 4/4 time).
seconds = beats * 60 / bpm
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    # Reuse the app's data dir + text normalizer when available.
    from song_index import user_singws_dir, normalize_text
except Exception:  # pragma: no cover - fallback for standalone use
    def user_singws_dir() -> Path:
        p = Path.home() / ".singws"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def normalize_text(s: str) -> str:
        return " ".join((s or "").lower().split())

BEATS_PER_BAR = 4  # 4/4 time
_BAR_LABELS = {4: "4 Bar", 8: "8 Bar", 16: "16 Bar"}


def db_path() -> Path:
    return user_singws_dir() / "phrase_markers.db"


# ───────────────────────── math / metadata helpers ──────────────────────────

def bars_to_seconds(bars: int, bpm: float) -> Optional[float]:
    """Seconds offset for `bars` bars at `bpm` (4/4). None if bpm is invalid."""
    try:
        bars = int(bars)
        bpm = float(bpm)
    except (TypeError, ValueError):
        return None
    if bars <= 0 or bpm <= 0:
        return None
    beats = bars * BEATS_PER_BAR
    return beats * 60.0 / bpm


def bar_label(bars: int) -> str:
    return _BAR_LABELS.get(int(bars), f"{int(bars)} Bar")


def bar_kind(bars: int) -> str:
    return f"bar{int(bars)}"


def song_key_for(path: str = "", artist: str = "", title: str = "", discid: str = "") -> str:
    """Stable normalized identity used as a fallback when the path is unknown."""
    parts = [normalize_text(artist), normalize_text(title), normalize_text(discid)]
    key = "|".join(parts).strip("|")
    if key.replace("|", "") == "" and path:
        # No metadata — fall back to the file name (sans extension).
        return normalize_text(Path(str(path)).stem)
    return key


def read_bpm_from_tags(path: str) -> Optional[float]:
    """Best-effort BPM from embedded tags (ID3 TBPM, MP4 tmpo, Vorbis BPM).

    Returns a positive float or None. Never raises — a missing/unsupported tag
    (e.g. a bare CDG graphic) just yields None and the caller falls back to a
    manual marker.
    """
    try:
        import mutagen  # type: ignore
    except Exception:
        return None
    try:
        audio = mutagen.File(str(path))
    except Exception:
        return None
    if audio is None:
        return None

    def _coerce(val: Any) -> Optional[float]:
        try:
            if isinstance(val, (list, tuple)) and val:
                val = val[0]
            f = float(str(val).strip())
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    tags = getattr(audio, "tags", None)
    # ID3 (MP3): TBPM text frame.
    try:
        if tags is not None and hasattr(tags, "getall"):
            frames = tags.getall("TBPM")
            if frames:
                bpm = _coerce(frames[0].text if hasattr(frames[0], "text") else frames[0])
                if bpm:
                    return bpm
    except Exception:
        pass
    # Generic mapping access (MP4 'tmpo', Vorbis/FLAC 'bpm'/'BPM').
    for key in ("tmpo", "bpm", "BPM", "TBPM"):
        try:
            if tags is not None and key in tags:
                bpm = _coerce(tags[key])
                if bpm:
                    return bpm
        except Exception:
            continue
    return None


# ───────────────────────────── database layer ───────────────────────────────

def _connect(dbfile: Optional[Path] = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(dbfile or db_path()))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    init_schema(con)
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS markers (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            song_path  TEXT NOT NULL,
            song_key   TEXT,
            kind       TEXT NOT NULL,          -- 'bar4' | 'bar8' | 'bar16' | 'custom'
            seconds    REAL NOT NULL,
            bars       INTEGER,                -- 4/8/16, NULL for custom
            bpm        REAL,                   -- bpm used for bar markers, NULL for manual
            label      TEXT,
            source     TEXT,                   -- 'bpm' | 'manual'
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER,
            updated_at INTEGER
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_markers_path ON markers(song_path)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_markers_key ON markers(song_key)")
    con.commit()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def list_markers(path: str, song_key: str = "", *, dbfile: Optional[Path] = None) -> List[Dict[str, Any]]:
    """All markers for a song (by path, or by song_key fallback), ordered by time."""
    con = _connect(dbfile)
    try:
        if path:
            rows = con.execute(
                "SELECT * FROM markers WHERE song_path = ? ORDER BY seconds ASC", (str(path),)
            ).fetchall()
            if rows:
                return [_row_to_dict(r) for r in rows]
        if song_key:
            rows = con.execute(
                "SELECT * FROM markers WHERE song_key = ? ORDER BY seconds ASC", (str(song_key),)
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        return []
    finally:
        con.close()


def upsert_marker(
    path: str,
    *,
    kind: str,
    seconds: float,
    bars: Optional[int] = None,
    bpm: Optional[float] = None,
    label: Optional[str] = None,
    source: str = "manual",
    song_key: str = "",
    make_default: bool = False,
    marker_id: Optional[int] = None,
    dbfile: Optional[Path] = None,
) -> int:
    """Insert or update a marker. Bar markers (kind != 'custom') are unique per
    (song_path, kind) and updated in place; custom markers insert a new row
    unless `marker_id` is given (edit). Returns the marker id.
    """
    con = _connect(dbfile)
    try:
        now = int(time.time())
        seconds = max(0.0, float(seconds))
        existing_id: Optional[int] = None
        if marker_id is not None:
            existing_id = int(marker_id)
        elif kind != "custom":
            row = con.execute(
                "SELECT id FROM markers WHERE song_path = ? AND kind = ?", (str(path), str(kind))
            ).fetchone()
            if row:
                existing_id = int(row["id"])

        if existing_id is not None:
            con.execute(
                """UPDATE markers SET song_key=?, kind=?, seconds=?, bars=?, bpm=?, label=?,
                          source=?, updated_at=? WHERE id=?""",
                (song_key, kind, seconds, bars, bpm, label, source, now, existing_id),
            )
            mid = existing_id
        else:
            cur = con.execute(
                """INSERT INTO markers (song_path, song_key, kind, seconds, bars, bpm, label,
                          source, is_default, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,0,?,?)""",
                (str(path), song_key, kind, seconds, bars, bpm, label, source, now, now),
            )
            mid = int(cur.lastrowid)

        con.commit()
        if make_default:
            _set_default(con, str(path), mid)
        return mid
    finally:
        con.close()


def _set_default(con: sqlite3.Connection, path: str, marker_id: int) -> None:
    con.execute("UPDATE markers SET is_default=0 WHERE song_path=?", (path,))
    con.execute("UPDATE markers SET is_default=1 WHERE id=?", (int(marker_id),))
    con.commit()


def set_default_marker(path: str, marker_id: int, *, dbfile: Optional[Path] = None) -> None:
    con = _connect(dbfile)
    try:
        _set_default(con, str(path), int(marker_id))
    finally:
        con.close()


def clear_default(path: str, *, dbfile: Optional[Path] = None) -> None:
    con = _connect(dbfile)
    try:
        con.execute("UPDATE markers SET is_default=0 WHERE song_path=?", (str(path),))
        con.commit()
    finally:
        con.close()


def delete_marker(marker_id: int, *, dbfile: Optional[Path] = None) -> None:
    con = _connect(dbfile)
    try:
        con.execute("DELETE FROM markers WHERE id=?", (int(marker_id),))
        con.commit()
    finally:
        con.close()


def default_marker(path: str, song_key: str = "", *, dbfile: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The marker to reuse automatically when a queued song has no per-instance
    choice: the explicitly-flagged default, else the most recently updated one.
    """
    con = _connect(dbfile)
    try:
        row = con.execute(
            "SELECT * FROM markers WHERE song_path=? AND is_default=1 ORDER BY updated_at DESC LIMIT 1",
            (str(path),),
        ).fetchone()
        if row:
            return _row_to_dict(row)
        row = con.execute(
            "SELECT * FROM markers WHERE song_path=? ORDER BY updated_at DESC LIMIT 1", (str(path),)
        ).fetchone()
        if row:
            return _row_to_dict(row)
        if song_key:
            row = con.execute(
                "SELECT * FROM markers WHERE song_key=? ORDER BY is_default DESC, updated_at DESC LIMIT 1",
                (str(song_key),),
            ).fetchone()
            if row:
                return _row_to_dict(row)
        return None
    finally:
        con.close()


# ───────────────────────────── start resolution ─────────────────────────────

def clamp_start_seconds(seconds: Optional[float], duration: Optional[float]) -> float:
    """Clamp a requested start to a safe range: never negative, and at least 2s
    before the end so a song can't start past its own outro."""
    try:
        s = float(seconds) if seconds is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    if s <= 0.0:
        return 0.0
    try:
        d = float(duration) if duration is not None else 0.0
    except (TypeError, ValueError):
        d = 0.0
    if d > 2.0:
        s = min(s, d - 2.0)
    return max(0.0, s)


def resolve_start_seconds(
    entry_override: Optional[float],
    default_seconds: Optional[float],
    duration: Optional[float],
) -> float:
    """Precedence: an explicit per-instance choice (including 0.0 = Beginning)
    wins; otherwise the song's saved default marker is reused; otherwise 0.0.
    Always clamped to a safe range.
    """
    chosen = entry_override if entry_override is not None else default_seconds
    return clamp_start_seconds(chosen, duration)
