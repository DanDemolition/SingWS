"""Exact-content duplicate review for MP3+G ZIP karaoke libraries."""

from __future__ import annotations

from collections import defaultdict
import hashlib
from pathlib import Path
import shutil
import time
import zipfile
import zlib


def _member(zf: zipfile.ZipFile, suffix: str):
    rows = [row for row in zf.infolist() if not row.is_dir() and row.filename.lower().endswith(suffix)]
    return rows[0] if len(rows) == 1 else None


def _member_sha256(path: str, member_name: str) -> str:
    digest = hashlib.sha256()
    with zipfile.ZipFile(path) as archive, archive.open(member_name) as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _keeper_key(path: str):
    name = Path(path).stem.casefold()
    duplicate_hint = int(any(token in name for token in (" copy", "duplicate", " dup", "(1)", "_1")))
    return duplicate_hint, len(Path(path).name), len(path), path.casefold()


def audit_zip_duplicates(paths, *, progress=None, cancel_check=None):
    """Return verified duplicate groups without changing any files.

    CRC/size narrows the catalog cheaply; SHA-256 is calculated only for those
    candidates. Removal eligibility requires identical MP3 and CDG content.
    """
    unique_paths = sorted({str(path) for path in paths if str(path).lower().endswith(".zip")})
    candidates = defaultdict(list)
    unreadable = 0
    total = len(unique_paths)
    for index, path in enumerate(unique_paths, 1):
        if cancel_check is not None and cancel_check():
            return {"groups": [], "scanned": index - 1, "unreadable": unreadable, "cancelled": True}
        try:
            with zipfile.ZipFile(path) as archive:
                audio = _member(archive, ".mp3")
                cdg = _member(archive, ".cdg")
                if audio is None:
                    unreadable += 1
                    continue
                candidates[(audio.CRC, audio.file_size)].append({
                    "path": path,
                    "audio_member": audio.filename,
                    "cdg_member": cdg.filename if cdg is not None else "",
                    "cdg_crc": cdg.CRC if cdg is not None else None,
                    "cdg_size": cdg.file_size if cdg is not None else None,
                })
        except (OSError, zipfile.BadZipFile, RuntimeError):
            unreadable += 1
        if progress is not None and (index == 1 or index == total or index % 1000 == 0):
            progress(index, total, Path(path).name)

    audio_groups = defaultdict(list)
    verify_rows = [row for rows in candidates.values() if len(rows) > 1 for row in rows]
    verify_total = len(verify_rows)
    for verify_index, row in enumerate(verify_rows, 1):
        if cancel_check is not None and cancel_check():
            return {"groups": [], "scanned": total, "unreadable": unreadable, "cancelled": True}
        try:
            audio_digest = _member_sha256(row["path"], row["audio_member"])
        except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error):
            unreadable += 1
            continue
        audio_groups[audio_digest].append(row)
        if progress is not None and (
            verify_index == 1 or verify_index == verify_total or verify_index % 100 == 0
        ):
            progress(
                total + verify_index, total + verify_total,
                f"Verifying exact content: {Path(row['path']).name}",
            )

    groups = []
    for audio_digest, rows in sorted(audio_groups.items()):
        if len(rows) < 2:
            continue
        cdg_groups = defaultdict(list)
        for row in rows:
            if row["cdg_member"]:
                try:
                    cdg_digest = _member_sha256(row["path"], row["cdg_member"])
                except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error):
                    unreadable += 1
                    cdg_digest = ""
            else:
                cdg_digest = ""
            cdg_groups[cdg_digest].append(row)

        for cdg_digest, same_package in cdg_groups.items():
            if cdg_digest and len(same_package) > 1:
                ordered = sorted((row["path"] for row in same_package), key=_keeper_key)
                groups.append({
                    "kind": "identical_audio_cdg", "eligible": True,
                    "audio_sha256": audio_digest, "cdg_sha256": cdg_digest,
                    "keeper": ordered[0], "paths": ordered,
                })
        if len(cdg_groups) > 1:
            ordered = sorted((row["path"] for row in rows), key=_keeper_key)
            groups.append({
                "kind": "identical_audio_different_cdg", "eligible": False,
                "audio_sha256": audio_digest, "cdg_sha256": "",
                "keeper": "", "paths": ordered,
            })

    groups.sort(key=lambda group: (not group["eligible"], group["paths"][0].casefold()))
    return {"groups": groups, "scanned": total, "unreadable": unreadable, "cancelled": False}


def move_to_recovery(paths, recovery_root: str | Path):
    """Move explicitly selected archives into a recoverable timestamped folder."""
    sources = [Path(raw) for raw in paths]
    missing = [str(source) for source in sources if not source.is_file()]
    if missing:
        raise FileNotFoundError(f"selected archive is missing: {missing[0]}")
    destination = Path(recovery_root) / time.strftime("duplicates-%Y%m%d-%H%M%S")
    destination.mkdir(parents=True, exist_ok=False)
    moved = []
    try:
        for source in sources:
            target = destination / source.name
            counter = 2
            while target.exists():
                target = destination / f"{source.stem}-{counter}{source.suffix}"
                counter += 1
            shutil.move(str(source), str(target))
            moved.append({"source": str(source), "destination": str(target)})
    except Exception:
        for row in reversed(moved):
            try:
                shutil.move(row["destination"], row["source"])
            except Exception:
                pass
        raise
    return {"folder": str(destination), "moved": moved}
