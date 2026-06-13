# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import platform

project_root = Path("/Users/daniel/Documents/SingWS")
# Universal-build mode: prefer the universal GStreamer.framework (x86_64 +
# arm64) installed at /Library/Frameworks over the single-arch Homebrew
# install at /opt/homebrew.  Homebrew remains the fallback so the spec
# still works on dev machines that don't have the framework installed.
gst_framework_root = Path("/Library/Frameworks/GStreamer.framework/Versions/1.0")
gst_framework_scanner = gst_framework_root / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner"
machine = platform.machine().lower()
brew_root = Path("/opt/homebrew") if machine in {"arm64", "aarch64"} else Path("/usr/local")
gst_scanner = brew_root / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner"
glibunix_typelib_candidates = (
    gst_framework_root / "lib" / "girepository-1.0" / "GLibUnix-2.0.typelib",
    brew_root / "lib" / "girepository-1.0" / "GLibUnix-2.0.typelib",
    Path("/usr/local/lib/girepository-1.0/GLibUnix-2.0.typelib"),
)

extra_datas = []
binaries = []
if gst_framework_scanner.exists():
    extra_datas.append((str(gst_framework_scanner), "libexec/gstreamer-1.0"))
elif gst_scanner.exists():
    extra_datas.append((str(gst_scanner), "libexec/gstreamer-1.0"))

for typelib in glibunix_typelib_candidates:
    if typelib.exists():
        extra_datas.append((str(typelib), "gi_typelibs"))
        break

for helper in (
    "python_karaoke_transport.py",
    "bass_background_engine.py",
    "song_index.py",
    "singws_eq.py",
    "singws_master_audio.py",
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

# When building against GStreamer.framework, the PyGObject hook can't
# auto-discover the plugin tree by globbing Homebrew's prefix.  Add
# every .dylib under the framework's gstreamer-1.0 plugin folder so the
# universal2 plugins all end up bundled.
gst_framework_lib = gst_framework_root / "lib"
gst_framework_plugins = gst_framework_lib / "gstreamer-1.0"
if gst_framework_plugins.exists():
    for plug in gst_framework_plugins.glob("*.dylib"):
        binaries.append((str(plug), "gst_plugins"))
    # GLib typelibs needed by gi.repository.Gst at runtime
    typelib_dir = gst_framework_lib / "girepository-1.0"
    if typelib_dir.exists():
        for tl in typelib_dir.glob("*.typelib"):
            extra_datas.append((str(tl), "gi_typelibs"))
    # Bundle the framework's core shared libs so gst-plugin-scanner can
    # load them inside the .app.
    for core_dylib in gst_framework_lib.glob("lib*.dylib"):
        # Skip the plugin dir we already enumerated and any per-arch
        # symlinks.  Real shared libraries are picked up here.
        if not core_dylib.is_file() or core_dylib.is_symlink():
            continue
        binaries.append((str(core_dylib), "."))

# Bundle ffmpeg and ffprobe so the app works without a Homebrew install.
# ``bin/ffmpeg`` is a universal binary (lipo'd arm64 + x86_64), so we
# prefer it over Homebrew's single-arch copy.  ffprobe is currently only
# available from Homebrew (single-arch arm64); if a universal ffprobe is
# ever placed in ``bin/`` it'll be picked up first.
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
    ['/Users/daniel/Documents/SingWS/0.2.18.1.py'],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=extra_datas,
    hiddenimports=[
        'signalsmith_audio_native',
        'mutagen',
        'python_karaoke_transport',
        'bass_background_engine',
        'song_index',
        # 10-band graphic EQ added this session — pulls in numpy + scipy.
        'singws_eq',
        # Master "mix bus" processing (gate/comp/limiter/EQ) — numpy + scipy.
        'singws_master_audio',
        'numpy',
        'scipy',
        'scipy.signal',
        'scipy.signal._sosfilt',
        'scipy.signal._signaltools',
        # WebSocket request relay (wss://wskar.com/relay)
        'PyQt6.QtWebSockets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / 'singws_pyinstaller_runtime.py')],
    excludes=[
        # The bundled GStreamer.framework ships an older glib whose typelib set
        # predates the GLibUnix/GioUnix split (glib 2.80). PyGObject references
        # them, so PyInstaller's gi hook logs a "Typelib not found" GError while
        # querying them. The app never had these typelibs and PyGObject degrades
        # gracefully without them, so exclude them to keep the build log clean.
        # (The arm64 spec must NOT exclude these — homebrew glib provides them.)
        'gi.repository.GLibUnix',
        'gi.repository.GioUnix',
    ],
    noarchive=False,
    optimize=0,
)

excluded_optional_gst_plugins = {
    "libgstanalyticsoverlay.dylib",
    "libgstgtk.dylib",
    "libgstgtk4.dylib",
    "libgstpango.dylib",
    "libgstrsclosedcaption.dylib",
    "libgstrsonvif.dylib",
    "libgstrsvg.dylib",
    "libgstttmlsubs.dylib",
}
a.binaries = [
    item for item in a.binaries
    if not (
        str(item[0]).startswith("gst_plugins/")
        and Path(str(item[0])).name in excluded_optional_gst_plugins
    )
]

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
    target_arch='universal2',
    codesign_identity=None,
    entitlements_file=None,
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
        'CFBundleShortVersionString': '0.3.0.2',
        'CFBundleVersion': '0.3.0.2',
        'NSHighResolutionCapable': True,
    },
)
