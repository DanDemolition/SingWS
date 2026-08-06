# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
import platform
import subprocess
import sys

project_root = Path(SPECPATH)
machine = platform.machine().lower()
brew_root = Path("/opt/homebrew") if machine in {"arm64", "aarch64"} else Path("/usr/local")

# GStreamer has been removed from SingWS. This Intel/universal build no longer
# copies GStreamer (framework plugins, typelibs, scanner, core dylibs) —
# that framework was the single largest thing in the bundle (~315 MiB). `gi`
# is in `excludes` below so PyInstaller cannot pull GStreamer back in.

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

# mpv's macOS gpu-next windows use Vulkan through MoltenVK. Keep the current
# FFmpeg/Signalsmith path in the bundle as fallback, while adding every native
# runtime required by the experimental mpv engine on an Intel Mac.
mpv_binary = brew_root / "bin" / "mpv"
moltenvk = brew_root / "lib" / "libMoltenVK.dylib"
moltenvk_icd = project_root / "MoltenVK_icd.json"
for required in (mpv_binary, moltenvk, moltenvk_icd):
    if not required.exists():
        raise SystemExit(f"Required mpv bundle input is missing: {required}")
binaries.extend((
    (str(mpv_binary), "."),
    (str(moltenvk), "."),
))
# libmpv plus its full FFmpeg 8 dependency closure. Collecting only libmpv
# leaves @rpath/libavcodec.62.dylib unresolvable in the bundle, which is what
# made shipped builds fall back to the FFmpeg engine on every song.
sys.path.insert(0, str(project_root / "tools"))
from mpv_bundle_deps import libmpv_binaries  # noqa: E402

binaries.extend(libmpv_binaries(brew_root))
extra_datas.append((str(moltenvk_icd), "vulkan/icd.d"))

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
# The copies in bin/ are universal launchers. Their arm64 slices use Homebrew
# codec dylibs, while their x86_64 slices do not. PyInstaller analyzes the
# native arm64 slice during this cross-build, so its dependency scan can add
# ARM-only Homebrew codec libraries that the Intel slices never reference.
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
        'mpv_playback',
        'mpv_karaoke_transport',
        'mpv',
        'bass_background_engine',
        'song_index',
        # 10-band graphic EQ added this session — pulls in numpy + scipy.
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
    # Keep GStreamer out of the graph entirely (no plugins/scanner/typelibs).
    excludes=['gi', 'gi.repository'],
    noarchive=False,
    optimize=0,
)

# Fail loudly if GStreamer ever sneaks back into the frozen graph.
_gst_binaries = [item for item in a.binaries if 'gst' in str(item[0]).lower() or 'gstreamer' in str(item[0]).lower()]
if _gst_binaries:
    raise SystemExit(f"GStreamer artifacts unexpectedly present in build: {_gst_binaries[:5]}")


def _keep_intel_binary(item):
    """Exclude ARM-only dependencies discovered from universal tools.

    This used to only consider sources under /opt/homebrew/, which misses the
    case that actually happens on an Intel host: bin/ffmpeg is universal and
    the ARM-only codec dylibs it pulls in live beside it in the project's own
    bin/ directory, not in Homebrew. Those slipped through and the bundle
    failed verify_macos_arch.py --require x86_64 with 15 arm64 files.

    Judge by architecture, not by path -- anything carrying an x86_64 slice is
    kept, so universal binaries are never dropped.
    """
    source = Path(str(item[1]))
    if not source.exists():
        return True
    result = subprocess.run(
        ["lipo", "-archs", str(source)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0 or "x86_64" in result.stdout.split():
        return True
    print(f"[intel-build] excluding unreachable ARM-only dependency: {source}")
    return False


a.binaries = [item for item in a.binaries if _keep_intel_binary(item)]

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
    # Produce a universal2 executable so the .app runs on both Apple
    # Silicon and Intel Macs.  Requires every bundled native dependency
    # (Python, numpy, scipy, PyQt6, signalsmith_audio_native, BASS, ffmpeg,
    # GStreamer) to also be universal2.
    target_arch='x86_64',
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
        'CFBundleShortVersionString': '0.4.3.5',
        'CFBundleVersion': '0.4.3.5',
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
