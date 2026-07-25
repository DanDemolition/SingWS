# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
import platform
import sys

project_root = Path(SPECPATH)
machine = platform.machine().lower()
brew_root = Path("/opt/homebrew") if machine in {"arm64", "aarch64"} else Path("/usr/local")

# GStreamer has been removed from SingWS. Nothing here bundles gstreamer
# plugins, the plugin scanner, gi typelibs, or a plugin registry — `gi` is in
# `excludes` below so PyInstaller cannot pull GStreamer back in transitively.

extra_datas = []
binaries = []

for helper in (
    "python_karaoke_transport.py",
    "bass_background_engine.py",
    "song_index.py",
    "singws_eq.py",
    "singws_master_audio.py",
    "mac_keep_awake.py",
):
    helper_path = project_root / helper
    if helper_path.exists():
        extra_datas.append((str(helper_path), "."))

for bass_lib in (Path("vendor/bass") / name for name in (
    "libbass.dylib",
    "libbassmix.dylib",
    "libbassflac.dylib",
)):
    if bass_lib.exists():
        binaries.append((str(bass_lib), "vendor/bass"))

for openssl_lib in ("libssl.3.dylib", "libcrypto.3.dylib"):
    candidates = (
        Path(sys.base_prefix) / "lib" / openssl_lib,
        brew_root / "opt" / "openssl@3" / "lib" / openssl_lib,
        Path("/opt/homebrew/opt/openssl@3/lib") / openssl_lib,
        Path("/usr/local/opt/openssl@3/lib") / openssl_lib,
        Path("/usr/local/lib") / openssl_lib,
    )
    for candidate in candidates:
        if candidate.exists():
            binaries.append((str(candidate), "."))
            break

# PyInstaller 6.21's Qt dependency validator does not resolve the framework-
# qualified @rpath names used by Qt 6.11 plugins, so it silently excludes even
# the mandatory Cocoa platform plugin. Bundle the runtime plugin groups
# explicitly at the path used by PyInstaller's PyQt6 runtime hook.
qt_plugins_root = (
    Path(sys.prefix)
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
    / "PyQt6"
    / "Qt6"
    / "plugins"
)
qt_plugin_groups = (
    "generic",
    "iconengines",
    "imageformats",
    "multimedia",
    "networkinformation",
    "platforms",
    "styles",
    "tls",
)
for plugin_group in qt_plugin_groups:
    plugin_dir = qt_plugins_root / plugin_group
    plugin_files = sorted(plugin_dir.glob("*.dylib"))
    if not plugin_files:
        raise SystemExit(f"Required Qt plugin group is missing: {plugin_dir}")
    for plugin_file in plugin_files:
        binaries.append(
            (str(plugin_file), f"PyQt6/Qt6/plugins/{plugin_group}")
        )

# Bundle ffmpeg and ffprobe so the app works without a Homebrew install.
# Prefer the checked universal launchers so cross-builds do not pick up the
# build host's architecture-specific Homebrew binaries.
# The runtime hook adds Frameworks/ to PATH so _ffmpeg_path() finds them
# via shutil.which() in the frozen app.
for ff_binary in ("ffmpeg", "ffprobe"):
    candidates = (
        project_root / "bin" / ff_binary,
        brew_root / "bin" / ff_binary,
        Path("/usr/local/bin") / ff_binary,
    )
    for candidate in candidates:
        if candidate.exists():
            binaries.append((str(candidate), "."))
            break

a = Analysis(
    [str(project_root / '0.2.18.1.py')],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=extra_datas,
    hiddenimports=[
        'signalsmith_audio_native',
        'mutagen',
        'python_karaoke_transport',
        'bass_background_engine',
        'song_index',
        'singws_eq',
        'singws_master_audio',
        'mac_keep_awake',
        'numpy',
        'scipy',
        'scipy.signal',
        'scipy.signal._sosfilt',
        'scipy.signal._signaltools',
        'ssl',
        '_ssl',
        '_hashlib',
        'certifi',
        'urllib3.util.ssl_',
        # WebSocket request relay (wss://wskar.com/relay)
        'PyQt6.QtWebSockets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / 'singws_pyinstaller_runtime.py')],
    # Keep GStreamer out of the graph entirely: no plugins, scanner, typelibs,
    # or GLib gir get pulled in, and a stray transitive `import gi` cannot
    # resurrect ~315 MiB of frameworks.
    excludes=['gi', 'gi.repository'],
    noarchive=False,
    optimize=0,
)

# Fail loudly if GStreamer ever sneaks back into the frozen graph.
_gst_binaries = [item for item in a.binaries if 'gst' in str(item[0]).lower() or 'gstreamer' in str(item[0]).lower()]
if _gst_binaries:
    raise SystemExit(f"GStreamer artifacts unexpectedly present in build: {_gst_binaries[:5]}")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SingWS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file=str(project_root / 'SingWS.entitlements'),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SingWS',
)
# Bundle is named SingWS.app so it lands in /Applications as just "SingWS".
# Version metadata is preserved in Info.plist via info_plist below.
app = BUNDLE(
    coll,
    name='SingWS.app',
    icon=str(project_root / 'SingWS.icns'),
    bundle_identifier='com.singws.app',
    info_plist={
        'CFBundleName': 'SingWS',
        'CFBundleDisplayName': 'SingWS',
        'CFBundleShortVersionString': '0.4.2.7',
        'CFBundleVersion': '0.4.2.7',
        'NSHighResolutionCapable': True,
        'NSAppleEventsUsageDescription': (
            "SingWS uses System Events to find, queue, and control songs in the KaraFun application."
        ),
        'NSLocationWhenInUseUsageDescription': (
            "SingWS uses this Mac's location to set venue coordinates for request signups."
        ),
        'NSLocationUsageDescription': (
            "SingWS uses this Mac's location to set venue coordinates for request signups."
        ),
    },
)
