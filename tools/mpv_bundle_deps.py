"""Collect every native library the bundled libmpv actually needs.

Shipping libmpv alone is not enough. Homebrew's libmpv links against the
FFmpeg 8 dylibs (libavcodec.62 / libavutil.60 / ...), libplacebo, libass and
around forty more, all through absolute Homebrew paths. PyInstaller relocates
libmpv into Contents/Frameworks and rewrites those references to @rpath, so
anything it did not also collect becomes unresolvable at runtime:

    dlopen(.../Frameworks/libmpv.dylib, 0x0006):
        Library not loaded: @rpath/libavcodec.62.dylib

python-mpv reports that as "Most likely this dynlib/dll was not found when the
application was frozen", SingWS logs 'experimental engine failed; falling back
to FFmpeg', and the app silently plays every song on the fallback engine —
which on Intel is capped to 720p. Shipped builds did exactly this.

Resolving the closure here, from the spec, makes the set deterministic instead
of a side effect of whatever PyInstaller happened to walk.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Libraries under these prefixes belong to macOS and must never be bundled.
SYSTEM_PREFIXES = ("/usr/lib/", "/System/")

# libmpv must be linked against FFmpeg 8. A mixed bundle (an FFmpeg 7
# libavcodec.61 next to an FFmpeg 8 libmpv) loads nothing at all.
REQUIRED_FFMPEG_SONAMES = {
    "libavcodec": "62",
    "libavformat": "62",
    "libavutil": "60",
    "libavfilter": "11",
    "libswresample": "6",
    "libswscale": "9",
}


def _linked_names(path: str):
    """Every non-system install name recorded in `path`."""
    result = subprocess.run(
        ["otool", "-L", path], capture_output=True, text=True, check=True
    )
    for line in result.stdout.splitlines()[1:]:
        name = line.strip().split(" (")[0]
        if name and not name.startswith(SYSTEM_PREFIXES):
            yield name


def _resolve(name: str, search_paths) -> str | None:
    if name.startswith(("@rpath/", "@loader_path/", "@executable_path/")):
        base = os.path.basename(name)
        for directory in search_paths:
            candidate = Path(directory) / base
            if candidate.exists():
                return str(candidate)
        return None
    return name if os.path.exists(name) else None


def libmpv_dependency_closure(brew_root, *, strict: bool = True) -> list[str]:
    """Absolute paths of libmpv and everything it loads, deepest first.

    `strict` rejects an unresolvable dependency or a non-FFmpeg-8 libmpv, so a
    broken bundle fails the build instead of failing silently on a user's Mac.
    """
    brew_root = Path(brew_root)
    root = brew_root / "lib" / "libmpv.2.dylib"
    if not root.exists():
        raise SystemExit(f"libmpv is missing: {root} (brew install mpv)")
    search_paths = [
        brew_root / "lib",
        brew_root / "opt" / "ffmpeg" / "lib",
    ]

    # Keyed by realpath so a library reached twice is walked once, but the
    # value keeps the path AS REFERENCED — libmpv asks for
    # @rpath/libavcodec.62.dylib, so the bundle needs that exact basename, not
    # the libavcodec.62.28.102.dylib the symlink points at.
    collected: dict[str, str] = {}
    unresolved: list[str] = []
    queue = [str(root)]
    while queue:
        current = queue.pop()
        real = os.path.realpath(current)
        if real in collected:
            continue
        collected[real] = current
        for name in _linked_names(current):
            resolved = _resolve(name, search_paths)
            if resolved:
                queue.append(resolved)
            elif name not in unresolved:
                unresolved.append(f"{name} (needed by {os.path.basename(current)})")

    if unresolved and strict:
        raise SystemExit(
            "libmpv dependencies could not be resolved:\n  "
            + "\n  ".join(unresolved)
        )

    referenced = sorted(collected.values())
    _verify_ffmpeg_8(referenced, strict=strict)
    return referenced


def _verify_ffmpeg_8(collected, *, strict: bool) -> None:
    present = {}
    for path in collected:
        base = os.path.basename(path)
        stem = base.split(".", 1)[0]
        if stem in REQUIRED_FFMPEG_SONAMES:
            present[stem] = base
    wrong = [
        f"{base} (expected {stem}.{REQUIRED_FFMPEG_SONAMES[stem]}.dylib)"
        for stem, base in present.items()
        if not base.startswith(f"{stem}.{REQUIRED_FFMPEG_SONAMES[stem]}.")
    ]
    missing = sorted(set(REQUIRED_FFMPEG_SONAMES) - set(present))
    if (wrong or missing) and strict:
        raise SystemExit(
            "libmpv is not linked against FFmpeg 8 "
            f"(wrong: {wrong or 'none'}; missing: {missing or 'none'}). "
            "Run: brew upgrade ffmpeg mpv"
        )


def libmpv_binaries(brew_root) -> list[tuple[str, str]]:
    """PyInstaller `binaries` entries: libmpv plus its whole closure.

    The library is also shipped under the plain `libmpv.dylib` name: python-mpv
    finds it with ctypes.util.find_library('mpv'), which resolves to that name,
    and PyInstaller's ctypes hook then looks for exactly that basename inside
    the bundle. Both names must be the SAME library — a stale `libmpv.dylib`
    beside a newer `libmpv.2.dylib` is how the shipped build ended up with one
    copy linked to FFmpeg 7 and one to FFmpeg 8.
    """
    closure = libmpv_dependency_closure(brew_root)
    entries = [(path, ".") for path in closure]
    plain = Path(brew_root) / "lib" / "libmpv.dylib"
    if not plain.exists():
        raise SystemExit(
            f"{plain} is missing; python-mpv resolves libmpv under that name"
        )
    entries.append((str(plain), "."))
    return entries
