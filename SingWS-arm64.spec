# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
import platform

project_root = Path("/Users/daniel/Documents/SingWS")
build_gst_registry = project_root / "build" / "gst-registry.bin"
build_gst_registry.parent.mkdir(parents=True, exist_ok=True)
os.environ["GST_REGISTRY"] = str(build_gst_registry)
machine = platform.machine().lower()
brew_root = Path("/opt/homebrew") if machine in {"arm64", "aarch64"} else Path("/usr/local")
gst_scanner = brew_root / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner"
gst_framework_root = Path("/Library/Frameworks/GStreamer.framework/Versions/1.0")
gst_framework_scanner = gst_framework_root / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner"
glibunix_typelib_candidates = (
    brew_root / "lib" / "girepository-1.0" / "GLibUnix-2.0.typelib",
    Path("/opt/homebrew/lib/girepository-1.0/GLibUnix-2.0.typelib"),
    Path("/usr/local/lib/girepository-1.0/GLibUnix-2.0.typelib"),
)

extra_datas = []
binaries = []
if gst_scanner.exists():
    extra_datas.append((str(gst_scanner), "libexec/gstreamer-1.0"))
elif gst_framework_scanner.exists():
    extra_datas.append((str(gst_framework_scanner), "libexec/gstreamer-1.0"))

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

# Bundle ffmpeg and ffprobe so the app works without a Homebrew install.
# Homebrew (arm64) takes priority over bin/ because bin/ffmpeg is x86_64.
# The runtime hook adds Frameworks/ to PATH so _ffmpeg_path() finds them
# via shutil.which() in the frozen app.
for ff_binary in ("ffmpeg", "ffprobe"):
    candidates = (
        brew_root / "bin" / ff_binary,
        Path("/usr/local/bin") / ff_binary,
        project_root / "bin" / ff_binary,
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
        'singws_eq',
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
    excludes=[],
    noarchive=False,
    optimize=0,
)

excluded_optional_gst_plugins = {
    "libgstanalyticsoverlay.dylib",
    "libgstgtk.dylib",
    "libgstgtk4.dylib",
    "libgstpango.dylib",
    # Optional Python plugin loader. It depends on @rpath/Python3 and emits a
    # startup warning in packaged apps; SingWS does not use Python Gst plugins.
    "libgstpython.dylib",
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

# Self-built soundtouch plugin: provides the "pitch" element for realtime key
# changes (Homebrew's monolithic gstreamer formula ships without it; the
# GStreamer.framework used by the x86_64/universal builds already has it).
# Built by native/gst-soundtouch/build_gst_soundtouch.sh.
soundtouch_plugin = project_root / "native" / "gst-soundtouch" / "libgstsoundtouch.dylib"
if soundtouch_plugin.exists():
    a.binaries += [("gst_plugins/libgstsoundtouch.dylib", str(soundtouch_plugin), "BINARY")]
    soundtouch_lib = Path("/opt/homebrew/opt/sound-touch/lib/libSoundTouch.1.dylib")
    if soundtouch_lib.exists():
        a.binaries += [("libSoundTouch.1.dylib", str(soundtouch_lib), "BINARY")]
else:
    raise SystemExit(
        "native/gst-soundtouch/libgstsoundtouch.dylib missing — run "
        "native/gst-soundtouch/build_gst_soundtouch.sh first (needs: brew install sound-touch)"
    )

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
    target_arch=None,
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
        'CFBundleShortVersionString': '0.4.1.5',
        'CFBundleVersion': '0.4.1.5',
        'NSHighResolutionCapable': True,
        'NSLocationWhenInUseUsageDescription': (
            "SingWS uses this Mac's location to set venue coordinates for request signups."
        ),
        'NSLocationUsageDescription': (
            "SingWS uses this Mac's location to set venue coordinates for request signups."
        ),
    },
)
