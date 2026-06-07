#!/usr/bin/env python3
"""Regenerate docs/release.json (the auto-update manifest) from built DMGs.

The desktop updater reads this file from GitHub Pages, compares ``version``
against APP_VERSION, and verifies the download against the per-arch ``sha256``.
So the manifest MUST reflect the actual built DMGs — this computes real sizes
and hashes rather than hand-editing.

Usage:
    python tools/write_manifest.py <version> [dmg_dir]
"""
from __future__ import annotations

import datetime
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "DanDemolition/SingWS"

ARCHES = [
    ("mac_arm64", "Apple Silicon Mac", "arm64"),
    ("mac_x86_64", "Intel Mac", "x86_64"),
    ("mac_universal", "Universal Mac", "universal"),
]


def human_size(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.0f} MB"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(version: str, dmg_dir: Path = ROOT, *, repo: str = REPO,
                   release_date: str | None = None) -> dict:
    version = version.lstrip("vV").strip()
    downloads = {}
    for key, label, arch in ARCHES:
        filename = f"SingWS-{version}-{arch}-installer.dmg"
        path = dmg_dir / filename
        if not path.exists():
            raise SystemExit(f"missing DMG for manifest: {path}")
        downloads[key] = {
            "label": label,
            "filename": filename,
            "url": f"https://github.com/{repo}/releases/latest/download/{filename}",
            "size": human_size(path.stat().st_size),
            "sha256": sha256(path),
        }
    return {
        "name": "SingWS",
        "version": version,
        "release_date": release_date or datetime.date.today().isoformat(),
        "repository": repo,
        "release_url": f"https://github.com/{repo}/releases/latest",
        "downloads": downloads,
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: write_manifest.py <version> [dmg_dir]")
    version = sys.argv[1]
    dmg_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT
    manifest = build_manifest(version, dmg_dir)
    out = ROOT / "docs" / "release.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {out} (version {manifest['version']})")


if __name__ == "__main__":
    main()
