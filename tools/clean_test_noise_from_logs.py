#!/usr/bin/env python3
"""Remove unit-test noise from the live show logs, conservatively and reversibly.

Before test isolation (tests/_test_profile.py) existed, running the unit tests wrote into the live `~/SingWS/logs`. The noise is
easy to recognise ONLY when a line carries a request id: real request ids are epoch milliseconds (>= 1e12), the tests use tiny ids
such as 123. This tool removes exactly the lines whose every `request_id=` / `kept_request_id=` value is a positive number below
1e12. It deliberately leaves everything it cannot attribute with certainty (name-only lines such as ROTATION-SLOT, QUEUE-STATE,
SURFACE, ...), lines without an id, and lines that mix a real id with a small one.

Safety: dry-run by default. `--apply` first writes a gzip archive of every log it will touch (and refuses to run if that archive
cannot be written), rewrites each file atomically (temp file + rename, mode and mtime preserved), and re-reads the result to check
that kept + removed == original line count and that no id-bearing real line was lost. Restore with:
    tar -xzf <archive> -C <logs dir parent>

Usage:  python3 tools/clean_test_noise_from_logs.py [--logs DIR] [--archive-dir DIR] [--apply]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tarfile
import tempfile
import time
from pathlib import Path

REAL_ID_MIN = 10**12
_IDS = re.compile(r"\b(?:request_id|kept_request_id)=(\d+)")


def is_test_noise(line: str) -> bool:
    """True when the line carries request ids and every one of them is a unit-test id (0 < id < 1e12)."""
    ids = _IDS.findall(line)
    return bool(ids) and all(0 < int(value) < REAL_ID_MIN for value in ids)


def scan(path: Path):
    kept = removed = real_id_lines = 0
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        for line in handle:
            if is_test_noise(line):
                removed += 1
            else:
                kept += 1
                if any(int(v) >= REAL_ID_MIN for v in _IDS.findall(line)):
                    real_id_lines += 1
    return kept, removed, real_id_lines


def rewrite(path: Path):
    """Atomically replace `path` without its test-noise lines; returns (kept, removed)."""
    stat = path.stat()
    kept = removed = 0
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as out, \
                open(path, encoding="utf-8", errors="surrogateescape", newline="") as src:
            for line in src:
                if is_test_noise(line):
                    removed += 1
                else:
                    kept += 1
                    out.write(line)
        os.chmod(tmp, stat.st_mode & 0o7777)
        os.utime(tmp, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return kept, removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs", default=os.path.expanduser("~/SingWS/logs"))
    parser.add_argument("--archive-dir", default=None, help="default: <logs dir>/../log-archive")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    logs = Path(args.logs)
    files = sorted(logs.glob("singws_*.log"))
    plan = []
    for path in files:
        kept, removed, real = scan(path)
        if removed:
            plan.append((path, kept, removed, real))
    total_removed = sum(item[2] for item in plan)
    total_lines = sum(item[1] + item[2] for item in plan)
    print(f"{len(files)} log files; {len(plan)} contain test-noise lines; {total_removed} lines would be removed "
          f"({total_removed} of {total_lines} lines in those files)")
    for path, kept, removed, real in plan[:5]:
        print(f"  {path.name}: remove {removed}, keep {kept} ({real} with real ids)")
    if not args.apply:
        print("dry run only; pass --apply to archive and rewrite")
        return 0
    if not plan:
        return 0

    archive_dir = Path(args.archive_dir) if args.archive_dir else logs.parent / "log-archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / f"logs-before-test-noise-cleanup-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path, *_ in plan:
            tar.add(path, arcname=f"{logs.name}/{path.name}")
    with tarfile.open(archive) as tar:  # prove the archive reads back before touching anything
        names = {m.name for m in tar.getmembers()}
    missing = [p.name for p, *_ in plan if f"{logs.name}/{p.name}" not in names]
    if missing:
        print(f"archive is incomplete ({missing[:3]}...); nothing was changed", file=sys.stderr)
        return 1
    print(f"archived {len(plan)} files -> {archive} ({archive.stat().st_size / 1e6:.1f} MB)")

    bad = []
    for path, kept, removed, real in plan:
        k, r = rewrite(path)
        after_kept, after_removed, after_real = scan(path)
        if (k, r) != (kept, removed) or after_removed != 0 or after_kept != kept or after_real != real:
            bad.append(path.name)
    if bad:
        print(f"VERIFICATION FAILED for {bad}; restore from {archive}", file=sys.stderr)
        return 1
    print(f"removed {total_removed} test-noise lines from {len(plan)} files; every kept line and every real-id line verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
