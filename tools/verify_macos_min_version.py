#!/usr/bin/env python3
"""Reject Mach-O files whose selected slice requires a newer macOS."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

MACHO_MAGICS = {
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",  # universal
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",  # universal 64
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",  # Mach-O 32
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",  # Mach-O 64
}


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in str(value).split(".") if part.isdigit())


def minimum_macos(path: Path, arch: str) -> str | None:
    try:
        with path.open("rb") as stream:
            if stream.read(4) not in MACHO_MAGICS:
                return None
    except OSError:
        return None
    proc = subprocess.run(
        ["otool", "-arch", arch, "-l", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        return None
    lines = proc.stdout.splitlines()
    for index, line in enumerate(lines):
        if "LC_BUILD_VERSION" in line:
            for candidate in lines[index + 1:index + 8]:
                match = re.search(r"\bminos\s+([0-9.]+)", candidate)
                if match:
                    return match.group(1)
        if "LC_VERSION_MIN_MACOSX" in line:
            for candidate in lines[index + 1:index + 6]:
                match = re.search(r"\bversion\s+([0-9.]+)", candidate)
                if match:
                    return match.group(1)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--arch", default="x86_64")
    parser.add_argument("--maximum", default="12.0")
    args = parser.parse_args()
    root = args.path.resolve()
    paths = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
    checked = 0
    failures = []
    for path in paths:
        detected = minimum_macos(path, args.arch)
        if detected is None:
            continue
        checked += 1
        if version_tuple(detected) > version_tuple(args.maximum):
            failures.append((detected, path))
    if failures:
        for detected, path in failures:
            print(f"requires macOS {detected}: {path}")
        raise SystemExit(
            f"{len(failures)} Mach-O file(s) exceed macOS {args.maximum} "
            f"for {args.arch}"
        )
    print(f"verified {checked} Mach-O file(s): {args.arch} minimum <= macOS {args.maximum}")


if __name__ == "__main__":
    main()
