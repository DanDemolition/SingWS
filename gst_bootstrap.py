"""
gst_bootstrap.py
----------------
Runtime environment setup for a bundled GStreamer, for Python apps.

This is the Python equivalent of OpenKJ's MAC_OVERRIDE_GST block in
src/main.cpp: before GStreamer initializes, point it at the plugins,
plugin scanner, and typelibs that live INSIDE the app bundle instead of
/opt/homebrew or /Library/Frameworks.

IMPORT THIS FIRST — before `import gi` — in your app's entry point:

    import gst_bootstrap   # must be first
    gst_bootstrap.setup()

    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

When running unbundled (dev mode, straight from source with Homebrew),
setup() detects that and does nothing, so the same entry point works in
both environments — same trick as OpenKJ ("Not needed on brew installs").
"""

import os
import sys
from pathlib import Path


def _bundle_root() -> Path | None:
    """Return the bundle's resource root if we're running frozen, else None."""
    # PyInstaller onedir/onefile
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS)
        return Path(sys.executable).resolve().parent
    return None


def setup() -> bool:
    """Configure GST_* env vars for the bundled GStreamer.

    Returns True if bundled mode was activated, False if running against
    a system (Homebrew) GStreamer install.
    """
    root = _bundle_root()
    if root is None:
        return False  # dev mode: use Homebrew's gstreamer as installed

    plugin_dir = root / "gst-plugins"
    scanner = root / "gst-bin" / "gst-plugin-scanner"
    typelib_dir = root / "gi_typelibs"

    env = os.environ

    # Plugins: ONLY look inside the bundle. GST_PLUGIN_SYSTEM_PATH (not
    # GST_PLUGIN_PATH) replaces the default search path entirely, so a
    # user's stray Homebrew plugins can never be picked up by accident.
    env["GST_PLUGIN_SYSTEM_PATH"] = str(plugin_dir)

    # The scanner is a helper binary gstreamer spawns to load plugins
    # out-of-process during registry building.
    if scanner.exists():
        env["GST_PLUGIN_SCANNER"] = str(scanner)

    # Registry cache must live somewhere writable (the .app is not).
    cache = Path.home() / "Library" / "Caches" / "WildStyle" / "gstreamer-registry.bin"
    cache.parent.mkdir(parents=True, exist_ok=True)
    env["GST_REGISTRY"] = str(cache)

    # GObject introspection typelibs (Gst-1.0.typelib etc.)
    if typelib_dir.exists():
        env["GI_TYPELIB_PATH"] = str(typelib_dir)

    # Belt & suspenders for dylib resolution of the scanner helper.
    env["DYLD_FALLBACK_LIBRARY_PATH"] = str(root) + ":" + env.get(
        "DYLD_FALLBACK_LIBRARY_PATH", "")

    return True


if __name__ == "__main__":
    bundled = setup()
    print("bundled mode:", bundled)
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    print("GStreamer:", Gst.version_string())
    for name in ("scaletempo", "pitch", "equalizer-10bands", "autoaudiosink",
                 "uridecodebin", "appsrc", "volume", "audiopanorama"):
        ok = Gst.ElementFactory.find(name) is not None
        print(f"  {name:20s} {'OK' if ok else 'MISSING'}")
