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

import json
import sqlite3
import time
import uuid as _uuid
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
    # Phase 2 migration (idempotent): a cross-machine stable id for sync, and a
    # soft-delete tombstone so deletions can propagate through last-write-wins.
    existing = {r[1] for r in con.execute("PRAGMA table_info(markers)").fetchall()}
    if "uuid" not in existing:
        con.execute("ALTER TABLE markers ADD COLUMN uuid TEXT")
    if "deleted_at" not in existing:
        con.execute("ALTER TABLE markers ADD COLUMN deleted_at INTEGER")
    # Backfill uuids for any pre-Phase-2 rows.
    for row in con.execute("SELECT id FROM markers WHERE uuid IS NULL OR uuid = ''").fetchall():
        con.execute("UPDATE markers SET uuid=? WHERE id=?", (_uuid.uuid4().hex, row[0]))
    con.execute("CREATE INDEX IF NOT EXISTS idx_markers_path ON markers(song_path)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_markers_key ON markers(song_key)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_markers_uuid ON markers(uuid)")
    # Per-song detected BPM cache (local only) so auto-tempo is computed once and
    # reused instantly by the bar shortcuts and the dialog.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS song_meta (
            song_path  TEXT PRIMARY KEY,
            bpm        REAL,
            updated_at INTEGER
        )
        """
    )
    # Beat-grid analysis columns (idempotent): downbeat phase + confidence so
    # loops can snap to the bar grid. Added after the original bpm-only table.
    _sm_cols = {r[1] for r in con.execute("PRAGMA table_info(song_meta)").fetchall()}
    if "first_beat" not in _sm_cols:
        con.execute("ALTER TABLE song_meta ADD COLUMN first_beat REAL")
    if "confidence" not in _sm_cols:
        con.execute("ALTER TABLE song_meta ADD COLUMN confidence REAL")
    if "analyzed_at" not in _sm_cols:
        con.execute("ALTER TABLE song_meta ADD COLUMN analyzed_at INTEGER")
    if "analysis_version" not in _sm_cols:
        con.execute("ALTER TABLE song_meta ADD COLUMN analysis_version INTEGER")
    con.commit()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def list_markers(path: str, song_key: str = "", *, dbfile: Optional[Path] = None) -> List[Dict[str, Any]]:
    """All markers for a song (by path, or by song_key fallback), ordered by time."""
    con = _connect(dbfile)
    try:
        if path:
            rows = con.execute(
                "SELECT * FROM markers WHERE song_path = ? AND deleted_at IS NULL ORDER BY seconds ASC",
                (str(path),),
            ).fetchall()
            if rows:
                return [_row_to_dict(r) for r in rows]
        if song_key:
            rows = con.execute(
                "SELECT * FROM markers WHERE song_key = ? AND deleted_at IS NULL ORDER BY seconds ASC",
                (str(song_key),),
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
            # Re-activate if it was a tombstone being overwritten.
            con.execute(
                """UPDATE markers SET song_key=?, kind=?, seconds=?, bars=?, bpm=?, label=?,
                          source=?, updated_at=?, deleted_at=NULL WHERE id=?""",
                (song_key, kind, seconds, bars, bpm, label, source, now, existing_id),
            )
            mid = existing_id
        else:
            cur = con.execute(
                """INSERT INTO markers (song_path, song_key, kind, seconds, bars, bpm, label,
                          source, is_default, uuid, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,0,?,?,?)""",
                (str(path), song_key, kind, seconds, bars, bpm, label, source,
                 _uuid.uuid4().hex, now, now),
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
    """Soft-delete (tombstone) so the deletion can propagate through sync."""
    con = _connect(dbfile)
    try:
        now = int(time.time())
        con.execute(
            "UPDATE markers SET deleted_at=?, updated_at=?, is_default=0 WHERE id=?",
            (now, now, int(marker_id)),
        )
        con.commit()
    finally:
        con.close()


def default_marker(path: str, song_key: str = "", *, dbfile: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The marker to reuse automatically when a queued song has no per-instance
    choice: the explicitly-flagged default, else the most recently updated one.
    Tombstoned markers are ignored.
    """
    con = _connect(dbfile)
    try:
        row = con.execute(
            "SELECT * FROM markers WHERE song_path=? AND is_default=1 AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 1",
            (str(path),),
        ).fetchone()
        if row:
            return _row_to_dict(row)
        row = con.execute(
            "SELECT * FROM markers WHERE song_path=? AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 1",
            (str(path),),
        ).fetchone()
        if row:
            return _row_to_dict(row)
        if song_key:
            row = con.execute(
                "SELECT * FROM markers WHERE song_key=? AND deleted_at IS NULL ORDER BY is_default DESC, updated_at DESC LIMIT 1",
                (str(song_key),),
            ).fetchone()
            if row:
                return _row_to_dict(row)
        return None
    finally:
        con.close()


# ──────────────────────── sync + file backup (Phase 2) ───────────────────────
# Wire/file record shape mirrors the table columns. Sync identity is `uuid`;
# `song_key` is how the same song is matched across machines (paths differ).

_SYNC_FIELDS = ("uuid", "song_path", "song_key", "kind", "seconds", "bars", "bpm",
                "label", "source", "is_default", "deleted_at", "created_at", "updated_at")


def export_all(*, include_deleted: bool = True, dbfile: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every marker as plain dicts (for sync push / file export)."""
    con = _connect(dbfile)
    try:
        sql = "SELECT * FROM markers"
        if not include_deleted:
            sql += " WHERE deleted_at IS NULL"
        return [_row_to_dict(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def changed_since(ts: float, *, dbfile: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Markers (incl. tombstones) updated after `ts` — the sync push delta."""
    con = _connect(dbfile)
    try:
        rows = con.execute(
            "SELECT * FROM markers WHERE updated_at > ? ORDER BY updated_at ASC", (int(ts or 0),)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        con.close()


def apply_remote(records: List[Dict[str, Any]], *, dbfile: Optional[Path] = None) -> int:
    """Merge incoming marker records (from server pull or file import) by uuid,
    last-write-wins on `updated_at`. Tombstones (deleted_at set) win the same
    way. Returns the number of rows inserted/updated."""
    if not records:
        return 0
    con = _connect(dbfile)
    applied = 0
    try:
        for rec in records:
            uid = str(rec.get("uuid") or "").strip()
            if not uid:
                uid = _uuid.uuid4().hex
            try:
                incoming_upd = int(rec.get("updated_at") or 0)
            except (TypeError, ValueError):
                incoming_upd = 0
            existing = con.execute(
                "SELECT id, updated_at FROM markers WHERE uuid=?", (uid,)
            ).fetchone()
            if existing is not None and int(existing["updated_at"] or 0) >= incoming_upd:
                continue  # ours is newer-or-equal → keep it
            vals = {
                "uuid": uid,
                "song_path": str(rec.get("song_path") or ""),
                "song_key": str(rec.get("song_key") or ""),
                "kind": str(rec.get("kind") or "custom"),
                "seconds": float(rec.get("seconds") or 0.0),
                "bars": rec.get("bars"),
                "bpm": rec.get("bpm"),
                "label": rec.get("label"),
                "source": rec.get("source") or "manual",
                "is_default": int(rec.get("is_default") or 0),
                "deleted_at": rec.get("deleted_at"),
                "created_at": int(rec.get("created_at") or incoming_upd or time.time()),
                "updated_at": incoming_upd or int(time.time()),
            }
            if existing is not None:
                con.execute(
                    """UPDATE markers SET song_path=:song_path, song_key=:song_key, kind=:kind,
                       seconds=:seconds, bars=:bars, bpm=:bpm, label=:label, source=:source,
                       is_default=:is_default, deleted_at=:deleted_at, updated_at=:updated_at
                       WHERE uuid=:uuid""",
                    vals,
                )
            else:
                con.execute(
                    """INSERT INTO markers (uuid, song_path, song_key, kind, seconds, bars, bpm,
                       label, source, is_default, deleted_at, created_at, updated_at)
                       VALUES (:uuid,:song_path,:song_key,:kind,:seconds,:bars,:bpm,:label,
                       :source,:is_default,:deleted_at,:created_at,:updated_at)""",
                    vals,
                )
            applied += 1
        con.commit()
        return applied
    finally:
        con.close()


def get_song_bpm(path: str, *, dbfile: Optional[Path] = None) -> Optional[float]:
    """Cached auto-detected BPM for a song, or None."""
    if not path:
        return None
    con = _connect(dbfile)
    try:
        row = con.execute("SELECT bpm FROM song_meta WHERE song_path=?", (str(path),)).fetchone()
        if row and row["bpm"]:
            try:
                v = float(row["bpm"])
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None
        return None
    finally:
        con.close()


def set_song_bpm(path: str, bpm: float, *, dbfile: Optional[Path] = None) -> None:
    """Cache an auto-detected BPM for a song (local only)."""
    if not path or not bpm or bpm <= 0:
        return
    con = _connect(dbfile)
    try:
        con.execute(
            "INSERT INTO song_meta (song_path, bpm, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(song_path) DO UPDATE SET bpm=excluded.bpm, updated_at=excluded.updated_at",
            (str(path), float(bpm), int(time.time())),
        )
        con.commit()
    finally:
        con.close()


def get_song_analysis(path: str, *, dbfile: Optional[Path] = None) -> Optional[dict]:
    """Cached beat-grid analysis for a song: {bpm, first_beat, confidence} or None
    (None only when there's no usable BPM). `first_beat` may be None."""
    if not path:
        return None
    con = _connect(dbfile)
    try:
        row = con.execute(
            "SELECT bpm, first_beat, confidence, analysis_version FROM song_meta WHERE song_path=?",
            (str(path),),
        ).fetchone()
        if not row or not row["bpm"]:
            return None
        try:
            bpm = float(row["bpm"])
        except (TypeError, ValueError):
            return None
        if bpm <= 0:
            return None
        fb = row["first_beat"]
        return {
            "bpm": bpm,
            "first_beat": (float(fb) if fb is not None else None),
            "confidence": (float(row["confidence"]) if row["confidence"] is not None else 0.0),
            "version": (int(row["analysis_version"]) if row["analysis_version"] is not None else 0),
        }
    finally:
        con.close()


def set_song_analysis(path: str, bpm: float, first_beat=None, confidence: float = 0.0,
                      version: int = 0, *, dbfile: Optional[Path] = None) -> None:
    """Cache full beat-grid analysis for a song (local only)."""
    if not path or not bpm or bpm <= 0:
        return
    con = _connect(dbfile)
    try:
        now = int(time.time())
        fb = float(first_beat) if first_beat is not None else None
        con.execute(
            "INSERT INTO song_meta (song_path, bpm, first_beat, confidence, analysis_version, updated_at, analyzed_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(song_path) DO UPDATE SET bpm=excluded.bpm, first_beat=excluded.first_beat, "
            "confidence=excluded.confidence, analysis_version=excluded.analysis_version, "
            "updated_at=excluded.updated_at, analyzed_at=excluded.analyzed_at",
            (str(path), float(bpm), fb, float(confidence or 0.0), int(version or 0), now, now),
        )
        con.commit()
    finally:
        con.close()


def export_to_json(path: str, *, dbfile: Optional[Path] = None) -> int:
    """Write all markers to a JSON backup file. Returns the count."""
    records = export_all(include_deleted=True, dbfile=dbfile)
    payload = {"version": 1, "exported_at": int(time.time()), "markers": records}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return len(records)


def import_from_json(path: str, *, dbfile: Optional[Path] = None) -> int:
    """Merge a JSON backup file (last-write-wins). Returns rows applied."""
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    records = payload.get("markers", payload) if isinstance(payload, dict) else payload
    return apply_remote(records, dbfile=dbfile)


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
