#!/usr/bin/env python3
"""Single source of truth for the SingWS version.

The canonical version lives in ``APP_VERSION`` in the entry script. This helper
reads it, computes the next patch version (or accepts an explicit one), and
writes it back — keeping the PyInstaller specs' CFBundle version strings in sync.

Usage:
    python tools/release_version.py --current      # print current version
    python tools/release_version.py --bump         # patch++ , write, print new
    python tools/release_version.py --set 0.3.0    # set explicit, write, print
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Entry script keeps its (frozen) versioned filename; the real version is the
# APP_VERSION constant inside it, not the filename.
ENTRY = ROOT / "0.2.18.1.py"
SPECS = [
    ROOT / "SingWS-universal.spec",
    ROOT / "SingWS-x86_64.spec",
    ROOT / "SingWS-arm64.spec",
]

APP_RE = re.compile(r'^(APP_VERSION\s*=\s*)"([^"]*)"', re.M)


def read_version(entry: Path = ENTRY) -> str:
    m = APP_RE.search(entry.read_text())
    if not m:
        raise SystemExit(f"APP_VERSION not found in {entry}")
    return m.group(2)


def bump_patch(version: str) -> str:
    """Increment the last numeric dotted component: 0.2.18.1 -> 0.2.18.2."""
    parts = version.split(".")
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].isdigit():
            parts[i] = str(int(parts[i]) + 1)
            return ".".join(parts)
    raise SystemExit(f"Cannot auto-increment non-numeric version {version!r}")


def _sub_spec_versions(text: str, new: str) -> str:
    text = re.sub(r"('CFBundleShortVersionString':\s*)'[^']*'",
                  lambda m: m.group(1) + f"'{new}'", text)
    text = re.sub(r"('CFBundleVersion':\s*)'[^']*'",
                  lambda m: m.group(1) + f"'{new}'", text)
    return text


def write_version(new: str, entry: Path = ENTRY, specs=SPECS) -> None:
    new = new.lstrip("vV").strip()
    if not re.fullmatch(r"[0-9][0-9A-Za-z.\-]*", new):
        raise SystemExit(f"Refusing to write implausible version {new!r}")
    txt = entry.read_text()
    txt2, n = APP_RE.subn(lambda m: f'{m.group(1)}"{new}"', txt, count=1)
    if n != 1:
        raise SystemExit("Failed to update APP_VERSION")
    entry.write_text(txt2)
    for spec in specs:
        if spec.exists():
            spec.write_text(_sub_spec_versions(spec.read_text(), new))


def main() -> None:
    ap = argparse.ArgumentParser(description="Read/bump the SingWS version.")
    ap.add_argument("--current", action="store_true", help="print current version")
    ap.add_argument("--bump", action="store_true", help="auto-increment the patch component")
    ap.add_argument("--set", metavar="VERSION", help="set an explicit version")
    args = ap.parse_args()

    current = read_version()
    if args.current:
        print(current)
        return
    if args.set:
        new = args.set.lstrip("vV")
    elif args.bump:
        new = bump_patch(current)
    else:
        raise SystemExit("specify one of --current / --bump / --set X")
    write_version(new)
    print(new)


if __name__ == "__main__":
    main()
