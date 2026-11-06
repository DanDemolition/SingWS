"""Settings-migration shadow (Stage E): at launch, the app's one-off settings migrations (the ``*_migrated`` flags and their value rewrites in
``KaraokeApp.__init__``) run exactly as before. Right after them a separate low-priority process (Rust ``singws-settingsmig``) runs the SAME migrations on a copy of
the settings as they were BEFORE the block, and a DIFFERENCE from what Python produced is logged.

SHADOW ONLY. Python's settings are always the ones used and saved; nothing here writes settings, calls the app or changes what the migration block did.
Setting ``settings_migrate_shadow`` (default True). Every failure is swallowed; a missing helper binary is skipped without a log line.

Only the keys the migrations read or write are sent to the helper (never the venue API key, PINs, tokens or paths), and the log never contains values:
  ``MISMATCH keys: <key names that differ>``         the same keys hold different values (or one side lacks the key)
  ``MISMATCH order: <key names>``                     same values, different key order
  ``MISMATCH changed: app=<bool> rust=<bool>``        the "something changed, save the file" flag differs
  ``skipped: <reason>``                               the helper failed, declined (a value it will not guess) or answered nonsense
Log lines carry the tag SETTINGS-MIGRATE-SHADOW. One ``enabled available=0/1`` line per launch and one ``first check clean`` once.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

HELPER_NAME = "singws-settingsmig"
TIMEOUT_S = 20.0
TAG = "SETTINGS-MIGRATE-SHADOW"

# every key the migration block reads or writes (see the block in 0.2.18.1.py and rust/crates/singws-settingsmig)
KEYS = (
    "performance_mode", "safe_mode", "mp4_max_height",
    "transport_shadow", "transport_shadow_default_on_migrated",
    "rust_evidence_defaults_migrated", "waitlist_snapshot_default_on_migrated", "waitlist_snapshot",
    "queue_shadow", "plan_shadow", "settings_shadow", "store_shadow", "wire_shadow", "publish_live_state",
    "transport_drive_media_end", "transport_drive_watchdog", "start_shadow", "plan_shadow_live", "shell_commands",
    "performance_debug_enabled", "performance_debug_default_migrated",
    "stall_event_attribution", "show_safe_stall_diagnostics_migrated",
    "stall_stack_capture", "stall_stack_capture_safe_migrated", "stall_stack_capture_off_migrated",
    "ffmpeg_cdg_timing_migrated", "ffmpeg_cdg_750_baseline_migrated", "cdg_timing_engine_split_migrated",
    "mpv_cdg_minus_50_baseline_migrated", "mpv_mp4_100_baseline_migrated", "mpv_mp4_zero_baseline_migrated",
    "cdg_timing_offset_ms", "video_timing_offset_ms", "cdg_timing_offset_mpv_ms", "mp4_timing_offset_mpv_ms",
    "end_silence_trim_threshold_sec", "end_silence_threshold_2_5_migrated",
    "karafun_transparent_handoff", "karafun_windowed_reveal", "karafun_transparent_rehearsal_reset",
)
_seen: set[str] = set()
_clean_noted = False


def enabled(settings) -> bool:
    return bool((settings or {}).get("settings_migrate_shadow", True))


def find_helper() -> Path | None:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    candidates: list[Path] = []
    override = os.environ.get("SINGWS_SETTINGSMIG_BIN")
    if override:
        candidates.append(Path(override))
    for root in (getattr(sys, "_MEIPASS", None),
                 Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
                 Path(__file__).resolve().parent):
        if root:
            root = Path(root)
            candidates += [root / HELPER_NAME, root / "Frameworks" / HELPER_NAME, root / "native" / "singws_settingsmig" / arch / HELPER_NAME]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def subset(settings) -> dict:
    """The migration-relevant keys of ``settings``, in the dict's own order."""
    keys = set(KEYS)
    return {k: v for k, v in (settings or {}).items() if k in keys}


def run_helper(helper: Path, settings: dict) -> dict:
    proc = subprocess.run([str(helper)], input=json.dumps({"settings": settings}).encode("utf-8"), capture_output=True, timeout=TIMEOUT_S,
                          preexec_fn=lambda: os.nice(10))
    if proc.returncode != 0:
        raise RuntimeError(f"helper exit status {proc.returncode}")
    return json.loads(proc.stdout)


def _same(a, b) -> bool:
    return json.dumps(a, ensure_ascii=True) == json.dumps(b, ensure_ascii=True)


def compare(py_after: dict, py_changed, rs: dict, compare_changed: bool = True) -> list[str]:
    """Differences (key NAMES only, never values) between the app's migrated settings and the helper's."""
    out: list[str] = []
    a, b = subset(py_after), rs.get("settings") or {}
    differ = sorted(k for k in set(a) | set(b) if k not in a or k not in b or not _same(a[k], b[k]))
    if differ:
        out.append("keys: " + ", ".join(differ))
    elif list(a) != list(b):
        out.append("order: " + ", ".join(k for k in a if k not in b or list(a).index(k) != list(b).index(k)))
    if compare_changed and bool(py_changed) != bool(rs.get("changed")):
        out.append(f"changed: app={bool(py_changed)} rust={bool(rs.get('changed'))}")
    return out


def announce(settings, log) -> str:
    try:
        text = f"enabled available={int(find_helper() is not None)}" if enabled(settings) else "off (settings_migrate_shadow=false)"
        log(f"[{TAG}] {text}")
        return text
    except Exception:  # noqa: BLE001
        return ""


def run_shadow(before: dict, py_after: dict, py_changed, compare_changed: bool, log, helper: Path | None = None) -> str:
    """One comparison. Returns ``clean``, ``mismatch`` or ``skipped`` (for tests)."""
    global _clean_noted

    def say(text):
        try:
            log(f"[{TAG}] {text}")
        except Exception:
            pass

    try:
        helper = helper or find_helper()
        if helper is None:
            return "skipped"
        rs = run_helper(helper, before)
        if "decline" in rs:
            say(f"skipped: {str(rs['decline'])[:120]}")
            return "skipped"
        diffs = compare(py_after, py_changed, rs, compare_changed)
        if not diffs:
            if not _clean_noted:
                _clean_noted = True
                say("first check clean (the Rust migrations equal the app's)")
            return "clean"
        fresh = [d for d in diffs if d not in _seen]
        _seen.update(diffs)
        for d in fresh:
            say(f"MISMATCH {d}")
        return "mismatch"
    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app
        say(f"skipped: {type(exc).__name__}: {str(exc)[:160]}")
        return "skipped"


def start(settings, log, *, before: dict, after: dict, changed, compare_changed: bool = True) -> threading.Thread | None:
    """Called by the app right after its migration block. ``before``: the settings as they were when the block began; ``after``: as the block left them
    (before the DEFAULTS fill); ``changed``: the block's own changed flag. Returns the daemon thread (None when off or on any failure)."""
    try:
        if not enabled(settings) or find_helper() is None:
            return None
        thread = threading.Thread(target=run_shadow, args=(subset(before), subset(after), bool(changed), bool(compare_changed), log), name="settings-migrate-shadow",
                                  daemon=True)
        thread.start()
        return thread
    except Exception:  # noqa: BLE001
        return None
