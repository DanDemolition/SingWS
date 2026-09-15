"""One-time import of SingWS 1.x user data into SingWS Pro (2.0).

SingWS Pro keeps its own data folder (~/SingWSPro) so 1.x and 2.0 can run on the
same Mac without touching each other's files. On first launch the operator is
offered a copy of ~/SingWS. The 1.x folder is only ever READ.

Pure module: no Qt, so it is unit-testable. The prompt lives in the app.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

DECISION_MARKER = ".legacy_import_decision"
SKIP_DIRS = {"logs", "__pycache__"}
SKIP_SUFFIXES = (".lock", ".tmp", ".download", ".flushing")


def needs_prompt(new_dir: Path, legacy_dir: Path, *, env=os.environ) -> bool:
    """True when a fresh SingWS Pro folder could import existing 1.x data."""
    if env.get("SINGWS_HOME"):
        return False  # tests / explicit data roots never import
    new_dir, legacy_dir = Path(new_dir), Path(legacy_dir)
    try:
        if new_dir.resolve() == legacy_dir.resolve():
            return False
    except OSError:
        return False
    if (new_dir / DECISION_MARKER).exists():
        return False
    if (new_dir / "settings.json").exists():
        return False
    return (legacy_dir / "settings.json").is_file()


def record_decision(new_dir: Path, decision: str) -> None:
    new_dir = Path(new_dir)
    new_dir.mkdir(parents=True, exist_ok=True)
    (new_dir / DECISION_MARKER).write_text(str(decision) + "\n", encoding="utf-8")


def _rewrite_paths(value, old_prefix: str, new_prefix: str):
    if isinstance(value, str):
        if value == old_prefix or value.startswith(old_prefix + os.sep):
            return new_prefix + value[len(old_prefix):]
        return value
    if isinstance(value, list):
        return [_rewrite_paths(v, old_prefix, new_prefix) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite_paths(v, old_prefix, new_prefix) for k, v in value.items()}
    return value


def import_legacy_data(legacy_dir: Path, new_dir: Path) -> int:
    """Copy 1.x data into the new folder. Never overwrites, never writes legacy.

    Returns the number of files copied. Paths inside settings.json that point
    into the 1.x folder are rewritten to the new folder.
    """
    legacy_dir, new_dir = Path(legacy_dir), Path(new_dir)
    if not legacy_dir.is_dir():
        raise FileNotFoundError(legacy_dir)
    copied = 0
    for root, dirs, files in os.walk(legacy_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(root, d))]
        rel = Path(root).relative_to(legacy_dir)
        for name in files:
            if name.endswith(SKIP_SUFFIXES) or name == DECISION_MARKER:
                continue
            src = Path(root) / name
            if src.is_symlink():
                continue
            dst = new_dir / rel / name
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
    settings = new_dir / "settings.json"
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8") or "{}")
            fixed = _rewrite_paths(data, str(legacy_dir), str(new_dir))
            if fixed != data:
                tmp = settings.with_suffix(".json.importing")
                tmp.write_text(json.dumps(fixed, indent=2), encoding="utf-8")
                os.replace(tmp, settings)
        except (ValueError, OSError):
            pass  # a corrupt copied settings file is handled by the app's loader
    record_decision(new_dir, "imported")
    return copied
