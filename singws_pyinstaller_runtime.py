"""Runtime path setup for frozen SingWS builds.

PyInstaller's GStreamer hook collects the libraries and plugins into the app
bundle, but macOS launches a .app with a sparse environment. Point GI/GStreamer
at the bundled resources before the main script imports gi.repository.
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


def _configure_frozen_gstreamer_paths() -> None:
    if not getattr(sys, "frozen", False):
        return

    contents_dir = Path(sys.executable).resolve().parents[1]
    resources_dir = contents_dir / "Resources"
    frameworks_dir = contents_dir / "Frameworks"

    _prepend_env_path("GI_TYPELIB_PATH", resources_dir / "gi_typelibs")
    _prepend_env_path("XDG_DATA_DIRS", resources_dir / "share")

    gst_plugins_dir = frameworks_dir / "gst_plugins"
    if gst_plugins_dir.exists():
        os.environ.setdefault("GST_PLUGIN_SYSTEM_PATH_1_0", str(gst_plugins_dir))
        os.environ.setdefault("GST_PLUGIN_PATH_1_0", str(gst_plugins_dir))
        os.environ.setdefault("GST_PLUGIN_PATH", str(gst_plugins_dir))

    scanner_candidates = (
        resources_dir / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner",
        resources_dir / "libexec" / "gstreamer-1__dot__0" / "gst-plugin-scanner",
        frameworks_dir / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner",
        frameworks_dir / "libexec" / "gstreamer-1__dot__0" / "gst-plugin-scanner",
    )
    for scanner in scanner_candidates:
        if scanner.exists():
            os.environ.setdefault("GST_PLUGIN_SCANNER", str(scanner))
            break

    # ffmpeg and ffprobe land in Contents/Frameworks/ when PyInstaller
    # reorganises the bundle.  Add it to PATH so shutil.which() finds them.
    _prepend_env_path("PATH", frameworks_dir)

    # CRITICAL: make sure DYLD can find the bundled GStreamer/GLib dylibs
    # before the typelib loader does its first dlopen().  This must happen
    # in a runtime hook (i.e. before the main script imports gi) because
    # the main script's _setup_gstreamer_runtime_paths() runs too late for
    # some macOS versions to honour the env var update.
    if frameworks_dir.exists():
        _prepend_env_path("DYLD_FALLBACK_LIBRARY_PATH", frameworks_dir)
    if resources_dir.exists():
        _prepend_env_path("DYLD_FALLBACK_LIBRARY_PATH", resources_dir)


_configure_frozen_gstreamer_paths()


def _patch_pygobject_glibunix_deprecation() -> None:
    """Avoid a PyGObject/GLib 2.88 frozen-app assertion on macOS.

    In frozen builds, PyGObject can try to mark GLib.unix_signal_add_full as a
    deprecated override even when that compatibility symbol is not present in
    the loaded GLib typelib. That raises an AssertionError during
    ``from gi.repository import Gst`` before SingWS can start. The symbol is
    obsolete and not used by SingWS, so ignore only that deprecation marker.
    """

    if not getattr(sys, "frozen", False):
        return

    try:
        import gi.overrides
    except Exception:
        return

    original_deprecated_attr = gi.overrides.deprecated_attr

    def safe_deprecated_attr(namespace: str, attr: str, replacement: str) -> None:
        if namespace == "GLib" and attr == "unix_signal_add_full":
            return
        original_deprecated_attr(namespace, attr, replacement)

    gi.overrides.deprecated_attr = safe_deprecated_attr


_patch_pygobject_glibunix_deprecation()
