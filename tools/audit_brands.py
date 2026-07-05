#!/usr/bin/env python3
"""Library brand audit (OpenKJ port, step 5).

Runs okj_fileinfo.brand_of() over every karaoke file under the given
directory and reports the brand distribution plus unrecognized song IDs, so
BRAND_PATTERNS can be tuned against the real library.

Usage:
    .venv/bin/python tools/audit_brands.py /path/to/karaoke/library [--limit N]

Runtime brand selection in SingWS still uses DISC_BRAND_ALIASES in
0.2.18.1.py; this tool only audits the parser.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from okj_fileinfo import brand_of  # noqa: E402

KARAOKE_EXTS = {".zip", ".cdg", ".mp4", ".mp3"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("library", help="karaoke library root directory")
    ap.add_argument("--limit", type=int, default=40, help="max unrecognized examples to print")
    args = ap.parse_args()

    root = Path(args.library).expanduser()
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2

    counts = Counter()
    unrecognized = []
    total = 0
    for path in root.rglob("*"):
        if path.suffix.lower() not in KARAOKE_EXTS or not path.is_file():
            continue
        if path.suffix.lower() == ".mp3" and path.with_suffix(".cdg").exists():
            continue  # counted via the .cdg side of the pair
        total += 1
        stem = path.stem
        # song_id convention: leading disc id token of the filename
        song_id = stem.split(" - ")[0].strip() if " - " in stem else stem.split()[0] if stem.split() else stem
        brand = brand_of(song_id)
        counts[brand or "UNRECOGNIZED"] += 1
        if brand is None and len(unrecognized) < args.limit:
            unrecognized.append((song_id, path.name))

    print(f"scanned {total} karaoke files under {root}\n")
    for brand, n in counts.most_common():
        print(f"  {brand:14s} {n:6d}  ({100.0 * n / max(1, total):.1f}%)")
    if unrecognized:
        print(f"\nfirst {len(unrecognized)} unrecognized song IDs:")
        for sid, name in unrecognized:
            print(f"  {sid!r:24s} {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
