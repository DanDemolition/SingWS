# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
import platform
import subprocess
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
    "mpv_playback.py",
    "mpv_karaoke_transport.py",
    "bass_background_engine.py",
    "song_index.py",
    "singws_eq.py",
    "singws_master_audio.py",
    "mac_keep_awake.py",
):
    helper_path = project_root / helper
    if helper_path.exists():
        extra_datas.append((str(helper_path), "."))

# The mpv/Metal runtime is an ARCHITECTURE-SPECIFIC Homebrew install, so it can
# only be bundled when this build runs on Apple Silicon. An Intel host can still
# cross-build this arm64 app -- both venvs are universal2 and PyInstaller targets
# arm64 fine -- it just cannot supply arm64 libmpv, because Homebrew publishes no
# arm64 bottle for mpv at all. Rather than fail, such a build ships without the
# mpv engine, exactly as every arm64 DMG has to date; the app falls back to the
# FFmpeg/Signalsmith engine, which is the default anyway.
mpv_binary = brew_root / "bin" / "mpv"
libmpv = brew_root / "lib" / "libmpv.2.dylib"
moltenvk = brew_root / "lib" / "libMoltenVK.dylib"
moltenvk_icd = project_root / "MoltenVK_icd.json"


def _has_arm64_slice(path):
    if not path.exists():
        return False
    result = subprocess.run(
        ["lipo", "-archs", str(path)], capture_output=True, text=True, check=False
    )
    return result.returncode == 0 and "arm64" in result.stdout.split()


mpv_available = all(
    _has_arm64_slice(p) for p in (mpv_binary, libmpv, moltenvk)
) and moltenvk_icd.exists()

if mpv_available:
    binaries.extend((
        (str(mpv_binary), "."),
        (str(moltenvk), "."),
    ))
    # libmpv plus its full FFmpeg 8 dependency closure — see tools/mpv_bundle_deps.py.
    sys.path.insert(0, str(project_root / "tools"))
    from mpv_bundle_deps import libmpv_binaries  # noqa: E402

    binaries.extend(libmpv_binaries(brew_root))
    extra_datas.append((str(moltenvk_icd), "vulkan/icd.d"))
    print("SingWS-arm64: bundling the mpv engine (native Apple Silicon build)")
else:
    print(
        "SingWS-arm64: no arm64 mpv runtime present — building WITHOUT the mpv "
        "engine (cross-build); the app uses the FFmpeg/Signalsmith engine"
    )

for bass_lib in (Path("vendor/bass") / name for name in (
    "libbass.dylib",
    "libbassmix.dylib",
    "libbassflac.dylib",
)):
    if bass_lib.exists():
        binaries.append((str(bass_lib), "vendor/bass"))

# libssl and libcrypto MUST come from one OpenSSL build. libmpv links
# Homebrew's, Python links the framework's, and the two share sonames, so
# whichever pair the bundle ends up with has to satisfy both. Taking the
# Homebrew pair does: it is the newer build, and an older consumer resolves
# against it. Mixing them does not — a Homebrew libssl beside the framework's
# libcrypto dies on a missing _CRYPTO_calloc, which is exactly what the
# post-build libmpv load test caught.
_already_bundled = {os.path.basename(source) for source, _ in binaries}
for openssl_lib in ("libssl.3.dylib", "libcrypto.3.dylib"):
    if openssl_lib in _already_bundled:
        continue  # libmpv's closure already supplied the matching pair
    # Only a candidate carrying an arm64 slice is usable here. On an Intel host
    # Homebrew's OpenSSL is x86_64-only, while the Python framework ships
    # universal2 -- so the framework wins a cross-build and Homebrew wins a
    # native Apple Silicon build, keeping the pair consistent either way.
    candidates = (
        brew_root / "opt" / "openssl@3" / "lib" / openssl_lib,
        Path("/opt/homebrew/opt/openssl@3/lib") / openssl_lib,
        Path(sys.base_prefix) / "lib" / openssl_lib,
        Path("/usr/local/opt/openssl@3/lib") / openssl_lib,
        Path("/usr/local/lib") / openssl_lib,
    )
    for candidate in candidates:
        if _has_arm64_slice(candidate):
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
        *(('mpv_playback', 'mpv_karaoke_transport', 'mpv') if mpv_available else ()),
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
    excludes=['gi', 'gi.repository'] + ([] if mpv_available else ['mpv']),
    noarchive=False,
    optimize=0,
)

# Fail loudly if GStreamer ever sneaks back into the frozen graph.
_gst_binaries = [item for item in a.binaries if 'gst' in str(item[0]).lower() or 'gstreamer' in str(item[0]).lower()]
if _gst_binaries:
    raise SystemExit(f"GStreamer artifacts unexpectedly present in build: {_gst_binaries[:5]}")


def _keep_arm64_binary(item):
    """Drop anything with no arm64 slice.

    PyInstaller's analysis resolves libraries against the HOST, so an Intel
    machine cross-building this app can pull x86_64-only dylibs into an arm64
    bundle -- python-mpv's ctypes lookup dragged in Homebrew's Intel libmpv and
    its whole closure, and verify_macos_arch.py then failed on 25 files. Judge
    by architecture rather than by path, so universal dependencies are kept.
    """
    source = Path(str(item[1]))
    if not source.exists():
        return True
    result = subprocess.run(
        ["lipo", "-archs", str(source)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return True
    return "arm64" in result.stdout.split()


_dropped = [item for item in a.binaries if not _keep_arm64_binary(item)]
if _dropped:
    print(
        f"SingWS-arm64: dropping {len(_dropped)} binaries with no arm64 slice: "
        + ", ".join(sorted({Path(str(i[0])).name for i in _dropped})[:8])
    )
    a.binaries = [item for item in a.binaries if _keep_arm64_binary(item)]

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
        'CFBundleShortVersionString': '0.4.4.0',
        'CFBundleVersion': '0.4.4.0',
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
