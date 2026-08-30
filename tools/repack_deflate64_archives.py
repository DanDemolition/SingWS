#!/usr/bin/env python3
"""Repack validated Deflate64 karaoke archives as portable Deflate ZIPs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


UNSUPPORTED_REASON = "That compression method is not supported"


def selected_archives(cache_path: Path) -> list[Path]:
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    if not isinstance(cache, dict):
        raise ValueError("loudness cache root is not an object")
    return [
        Path(path) for path, entry in cache.items()
        if isinstance(entry, dict)
        and entry.get("failed") is True
        and entry.get("reason") == UNSUPPORTED_REASON
    ]


def run_7zz(seven_zip: str, *args: str) -> None:
    result = subprocess.run(
        [seven_zip, *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "7-Zip failed").strip()
        raise RuntimeError(detail)


def _is_macos_metadata(name: str) -> bool:
    parts = Path(name).parts
    return "__MACOSX" in parts or any(part.startswith("._") for part in parts)


def _member_snapshot(root: Path, *, strip_macos_metadata: bool = False) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat().st_size
        for path in root.rglob("*") if path.is_file()
        and not (strip_macos_metadata and _is_macos_metadata(path.relative_to(root).as_posix()))
    }


def repack_one(
    archive: Path,
    *,
    library_root: Path,
    backup_root: Path,
    seven_zip: str,
    temp_root: Path | None = None,
    strip_macos_metadata: bool = False,
) -> Path:
    archive = archive.resolve()
    library_root = library_root.resolve()
    try:
        relative = archive.relative_to(library_root)
    except ValueError as exc:
        raise ValueError(f"archive is outside library root: {archive}") from exc
    if not archive.is_file():
        raise FileNotFoundError(archive)

    run_7zz(seven_zip, "t", "-bso0", "-bsp0", str(archive))
    with tempfile.TemporaryDirectory(prefix="singws-deflate64-", dir=temp_root) as td:
        extracted = Path(td) / "contents"
        extracted.mkdir()
        run_7zz(seven_zip, "x", "-y", f"-o{extracted}", str(archive))
        expected = _member_snapshot(extracted, strip_macos_metadata=strip_macos_metadata)
        if not expected:
            raise RuntimeError(f"archive extracted no files: {archive}")

        replacement = Path(td) / archive.name
        with zipfile.ZipFile(
            replacement, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as output:
            for name in sorted(expected):
                output.write(extracted / Path(name), arcname=name)
        with zipfile.ZipFile(replacement, "r") as check:
            if check.testzip() is not None:
                raise RuntimeError(f"Python integrity check failed: {archive}")
            rebuilt = {
                info.filename: info.file_size
                for info in check.infolist() if not info.is_dir()
            }
            if rebuilt != expected:
                raise RuntimeError(f"replacement member mismatch: {archive}")
            if any(info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                   for info in check.infolist()):
                raise RuntimeError(f"replacement still uses unsupported compression: {archive}")
        run_7zz(seven_zip, "t", "-bso0", "-bsp0", str(replacement))

        backup = backup_root / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            raise FileExistsError(backup)
        shutil.copy2(archive, backup)
        os.replace(replacement, archive)
        try:
            with zipfile.ZipFile(archive, "r") as final:
                if final.testzip() is not None:
                    raise RuntimeError("final Python integrity check failed")
            run_7zz(seven_zip, "t", "-bso0", "-bsp0", str(archive))
        except Exception:
            shutil.copy2(backup, archive)
            raise
        return backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=Path.home() / "SingWS" / "loudness.json")
    parser.add_argument("--library-root", type=Path, required=True)
    parser.add_argument("--seven-zip", default=shutil.which("7zz") or "")
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--archive", action="append", type=Path)
    parser.add_argument("--strip-macos-metadata", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.seven_zip:
        raise SystemExit("7zz was not found")

    archives = args.archive or selected_archives(args.cache)
    total_bytes = sum(path.stat().st_size for path in archives if path.is_file())
    print(f"selected={len(archives)} bytes={total_bytes}")
    if not args.apply:
        return 0
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = args.backup_root or (
        args.cache.parent / "cache-backups" / f"deflate64-repack-{stamp}"
    )
    backup_root.mkdir(parents=True, exist_ok=False)
    for index, archive in enumerate(archives, 1):
        repack_one(
            archive,
            library_root=args.library_root,
            backup_root=backup_root,
            seven_zip=args.seven_zip,
            strip_macos_metadata=args.strip_macos_metadata,
        )
        if index == 1 or index % 25 == 0 or index == len(archives):
            print(f"repacked={index}/{len(archives)}")
    print(f"backup={backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
