#!/usr/bin/env python3
"""Remove only ambiguous legacy failures poisoned by the 2026-08-29 scan."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil


POISON_REASON = "no measurable loudness"
CURRENT_FAILURE_VERSION = 2


def is_poisoned_entry(path: str, entry: object) -> bool:
    del path  # the failure schema/reason, not media type, identifies retryability
    return bool(
        isinstance(entry, dict)
        and entry.get("failed") is True
        and (
            entry.get("reason") == POISON_REASON
            or str(entry.get("reason") or "").startswith("Turbo helper failed:")
        )
        and int(entry.get("failure_version", 0)) < CURRENT_FAILURE_VERSION
    )


def valid_measurement_count(cache: dict) -> int:
    return sum(
        1 for entry in cache.values()
        if isinstance(entry, dict) and not entry.get("failed") and "i" in entry
    )


def repair(cache_path: Path, *, apply: bool) -> tuple[int, int, Path | None]:
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    if not isinstance(cache, dict):
        raise ValueError("loudness cache root is not an object")

    before_valid = valid_measurement_count(cache)
    poisoned = [key for key, entry in cache.items() if is_poisoned_entry(key, entry)]
    if not apply or not poisoned:
        return len(poisoned), before_valid, None

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = cache_path.parent / "cache-backups" / f"loudness-poison-repair-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_path = backup_dir / cache_path.name
    shutil.copy2(cache_path, backup_path)

    for key in poisoned:
        del cache[key]
    if valid_measurement_count(cache) != before_valid:
        raise RuntimeError("repair would change valid loudness measurements")

    temp_path = cache_path.with_suffix(cache_path.suffix + ".repairing")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(cache, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, cache_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return len(poisoned), before_valid, backup_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "cache",
        nargs="?",
        type=Path,
        default=Path.home() / "SingWS" / "loudness.json",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    removed, preserved, backup = repair(args.cache, apply=args.apply)
    action = "removed" if args.apply else "would_remove"
    print(f"{action}={removed} valid_measurements_preserved={preserved}")
    if backup is not None:
        print(f"backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
