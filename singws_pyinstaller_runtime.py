"""Runtime path setup for frozen SingWS builds.

macOS launches a .app with a sparse environment. The bundled ffmpeg/ffprobe
land in Contents/Frameworks/, so add that directory to PATH before the app
resolves them with shutil.which().

GStreamer has been removed from SingWS, so there is no GI/GStreamer runtime
configuration here anymore — no registry, typelib path, plugin scanner, or
DYLD framework paths.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepend_env_path(name: str, path: Path) -> None:
    if not path.exists():
        return
    value = str(path)
    current = os.environ.get(name)
    if not current:
        os.environ[name] = value
        return
    parts = current.split(os.pathsep)
    if value not in parts:
        os.environ[name] = value + os.pathsep + current


def _configure_frozen_paths() -> None:
    if not getattr(sys, "frozen", False):
        return
    contents_dir = Path(sys.executable).resolve().parents[1]
    frameworks_dir = contents_dir / "Frameworks"
    # ffmpeg and ffprobe land in Contents/Frameworks/ when PyInstaller
    # reorganises the bundle. Add it to PATH so shutil.which() finds them.
    _prepend_env_path("PATH", frameworks_dir)


_configure_frozen_paths()
