"""Library-scan shadow: after the app's own scan, run the Rust scan (``singws-scan-cli``) on the same roots and LOG any difference.

SHADOW ONLY. The app's scan result is always the one used; the Rust helper runs in a separate low-priority process on a daemon thread
after the result has been handed to the UI, writes its song index only into a scratch database under the system temp directory (which it
deletes), and every failure is swallowed. Setting ``scan_engine``: ``"python"`` (default, nothing runs), ``"shadow"`` (this module),
``"rust"`` (NOT wired: treated as ``"python"`` and logged once, so the value cannot make the Rust scan the source of truth).

Log lines (grep ``SCAN-SHADOW``): ``clean`` (identical), ``MISMATCH`` (first differences), ``skipped`` (declined, or no helper).
A mismatch is not necessarily a bug: the two scans walk the disk at different moments, so a file added or touched in between shows up.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HELPER_NAME = "singws-scan-cli"
ENGINES = ("python", "shadow", "rust")
# The only settings the scan reads. Nothing else (no API key, no PIN, no URL) is ever sent to the helper.
SETTING_KEYS = (
    "filename_format", "karaoke_library_locations", "karaoke_scan_roots", "karaoke_library_locations_migrated",
    "karaoke_folder", "karaoke_library_folder", "library_folder", "library_path", "karaoke_scan_dir_sigs", "_scan_preserve_outside_roots",
)
COMPARE_KEYS = (
    "ok", "quick_mode", "roots", "requested_roots", "unavailable_roots", "overlapping_roots_skipped", "dir_sigs", "tracks_added",
    "zip_count", "mp4_count", "cdg_count", "tracks_reused", "tracks_changed", "files_seen", "added_count", "removed_count",
    "index_changed_paths", "index_removed_paths", "reindex_needed", "suspect_empty_roots", "summary_text",
)
TIMEOUT_S = 3600.0
_warned_rust = False


def normalize_engine(value) -> str:
    text = str(value or "").strip().lower()
    return text if text in ENGINES else "python"


DEFAULT_SHADOW_MAX_TRACKS = 20000


def effective_engine(settings) -> str:
    """What the app should do: ``"shadow"`` or ``"python"`` (``"rust"`` is deliberately not reachable)."""
    engine = normalize_engine((settings or {}).get("scan_engine", "python"))
    return "shadow" if engine == "shadow" else "python"


def find_helper() -> Path | None:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    candidates: list[Path] = []
    override = os.environ.get("SINGWS_SCAN_BIN")
    if override:
        candidates.append(Path(override))
    for root in (getattr(sys, "_MEIPASS", None),
                 Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
                 Path(__file__).resolve().parent):
        if root:
            root = Path(root)
            candidates += [root / HELPER_NAME, root / "Frameworks" / HELPER_NAME, root / "native" / "singws_scan" / arch / HELPER_NAME]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def build_request(roots, quick_mode, old_tracks, settings_snapshot, index_db: str | None = None) -> dict:
    snapshot = settings_snapshot if isinstance(settings_snapshot, dict) else {}
    request = {
        "op": "scan",
        "roots": [str(r) for r in (roots or []) if r],
        "quick_mode": bool(quick_mode),
        "old_tracks": list(old_tracks or []),
        "settings": {k: snapshot[k] for k in SETTING_KEYS if k in snapshot},
        "cwd": os.getcwd(),
    }
    if quick_mode:
        # The age of tracks.json (what the app's quick scan reads with TRACKS_PATH.stat()); read-only, 0 when the file is absent.
        try:
            tracks = Path(os.environ.get("SINGWS_HOME") or (Path.home() / "SingWS")) / "tracks.json"
            request["tracks_baseline_ns"] = int(tracks.stat().st_mtime_ns)
        except Exception:
            request["tracks_baseline_ns"] = 0
    if index_db:
        request["index_db"] = index_db
        request["now"] = 0
    return request


def compare_results(py: dict, rs: dict, limit: int = 5) -> list[str]:
    """Differences between the app's scan result and the Rust one (``scan_time`` is not compared)."""
    out: list[str] = []
    py = json.loads(json.dumps(py, default=str))
    for key in COMPARE_KEYS:
        if py.get(key) != rs.get(key):
            out.append(f"{key}: app={str(py.get(key))[:120]} rust={str(rs.get(key))[:120]}")
    pt, rt = py.get("tracks") or [], rs.get("tracks") or []
    if pt != rt:
        by_py = {t["path"]: t for t in pt if isinstance(t, dict) and t.get("path")}
        by_rs = {t["path"]: t for t in rt if isinstance(t, dict) and t.get("path")}
        only_py, only_rs = sorted(set(by_py) - set(by_rs)), sorted(set(by_rs) - set(by_py))
        changed = sorted(p for p in set(by_py) & set(by_rs) if by_py[p] != by_rs[p])
        if only_py or only_rs or changed:
            out.append(f"tracks: app={len(pt)} rust={len(rt)} only_in_app={len(only_py)} only_in_rust={len(only_rs)} differing={len(changed)}")
            for label, group in (("only_in_app", only_py), ("only_in_rust", only_rs), ("differing", changed)):
                for p in group[:2]:
                    out.append(f"  {label}: {p}")
        else:
            out.append("tracks: same tracks, different order")
    return out[: limit + 6]


def announce(settings, log) -> str:
    """One line per launch: which scan engine is selected and whether the Rust scan helper is there. The comparison itself only runs after a
    FULL library scan, so a quiet log otherwise cannot tell 'no full scan yet' from 'the helper is missing'."""
    try:
        engine = effective_engine(settings)
        text = f"engine={engine}" + (f" available={int(find_helper() is not None)}" if engine == "shadow" else "")
        log(f"[SCAN-SHADOW] {text}")
        return text
    except Exception:  # noqa: BLE001
        return ""


def run_shadow(result: dict, request: dict, log) -> str:
    """Run the helper and log the verdict. Returns the verdict word (for tests)."""
    def say(text):
        try:
            log(f"[SCAN-SHADOW] {text}")
        except Exception:
            pass

    try:
        if not isinstance(result, dict) or not result.get("ok"):
            say("skipped: the app's own scan did not finish")
            return "skipped"
        helper = find_helper()
        if helper is None:
            say("skipped: the Rust scan helper was not found")
            return "skipped"
        scratch = Path(tempfile.mkdtemp(prefix="singws-scan-shadow-"))
        try:
            request = dict(request, index_db=str(scratch / "index.db"), now=0)
            proc = subprocess.run(
                [str(helper)], input=json.dumps(request), capture_output=True, text=True, timeout=TIMEOUT_S,
                preexec_fn=lambda: os.nice(10),
            )
        finally:
            for child in scratch.glob("*"):
                try:
                    child.unlink()
                except OSError:
                    pass
            try:
                scratch.rmdir()
            except OSError:
                pass
        if proc.returncode != 0:
            say(f"skipped: helper exit status {proc.returncode}")
            return "skipped"
        reply = json.loads(proc.stdout)
        if not reply.get("ok"):
            say(f"skipped: {str(reply.get('error'))[:200]}")
            return "skipped"
        rs = reply["result"]
        diffs = compare_results(result, rs)
        if not diffs:
            say(f"clean: {len(rs.get('tracks') or [])} tracks identical; index rows={rs.get('index_rows')}")
            return "clean"
        say(f"MISMATCH: {len(diffs)} difference(s) (the disk may have changed between the two scans)")
        for line in diffs:
            say(f"  {line}")
        return "mismatch"
    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app
        say(f"skipped: {type(exc).__name__}: {str(exc)[:160]}")
        return "skipped"


def start(result: dict, roots, quick_mode, old_tracks, settings_snapshot, log) -> threading.Thread | None:
    """Called by the app's scan worker once its result exists. Returns the daemon thread (None when the shadow is not enabled)."""
    global _warned_rust
    try:
        engine = normalize_engine((settings_snapshot or {}).get("scan_engine", "python"))
        if engine == "rust" and not _warned_rust:
            _warned_rust = True
            log("[SCAN-SHADOW] scan_engine=rust is not wired; the app's own scan is used")
        if effective_engine(settings_snapshot) != "shadow":
            return None
        # The comparison deep-copies the whole result and the old track list through JSON and starts a helper that reread the library (one show
        # saw the app keep +300 MB and a 2 GB helper, with 5 s GUI stalls, from a 133k-track scan on 2026-11-12). That is fine for a small
        # library and too costly for a real one, so a big library is not compared. 0 disables the limit.
        try:
            limit = int((settings_snapshot or {}).get("scan_shadow_max_tracks", DEFAULT_SHADOW_MAX_TRACKS) or 0)
        except (TypeError, ValueError):
            limit = DEFAULT_SHADOW_MAX_TRACKS
        n_tracks = len((result or {}).get("tracks") or [])
        if limit > 0 and n_tracks > limit:
            log(f"[SCAN-SHADOW] skipped: {n_tracks:,} tracks is over scan_shadow_max_tracks={limit:,}")
            return None
        # Detach everything the thread reads from what the UI thread is about to mutate.
        snapshot = json.loads(json.dumps(result, default=str))
        request = build_request(roots, quick_mode, json.loads(json.dumps(list(old_tracks or []), default=str)), settings_snapshot)
        thread = threading.Thread(target=run_shadow, args=(snapshot, request, log), name="scan-shadow", daemon=True)
        thread.start()
        return thread
    except Exception:  # noqa: BLE001
        return None
