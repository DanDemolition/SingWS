#!/bin/bash
# Build only the Intel SingWS app and styled installer DMG.
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="SingWS"
ENTRY="0.2.18.1.py"
SPEC="SingWS-x86_64.spec"
PYTHON="${SINGWS_BUILD_PYTHON:-.venv-universal/bin/python}"

if [[ "$(uname -m)" != "x86_64" ]]; then
    echo "Run in an x86_64 process (on Intel, or with arch -x86_64 /bin/bash)."
    exit 1
fi

for required in \
    "$ENTRY" "$SPEC" "$PYTHON" \
    mpv_karaoke_transport.py MoltenVK_icd.json constraints-macos12.txt \
    SingWS.entitlements dmg_settings.py tools/verify_macos_arch.py \
    tools/verify_macos_min_version.py; do
    [[ -e "$required" ]] || { echo "Missing required file: $required"; exit 1; }
done

for command in hdiutil codesign file otool shasum; do
    command -v "$command" >/dev/null || { echo "Missing command: $command"; exit 1; }
done

: "${SINGWS_MPV_FRAMEWORKS:=$(pwd)/native_dual_view/Frameworks}"
export SINGWS_MPV_FRAMEWORKS
: "${SINGWS_MPV_BRIDGE:=$(pwd)/native/mpv_bridge/libsingws_mpv_bridge.dylib}"
export SINGWS_MPV_BRIDGE
STACK_INPUTS=(
    mpv_playback_iina.py
    "$SINGWS_MPV_BRIDGE"
    "$SINGWS_MPV_FRAMEWORKS/singws_libmpv.2.dylib"
)
for required in "${STACK_INPUTS[@]}"; do
    [[ -e "$required" ]] || { echo "Missing Intel playback dependency: $required"; exit 1; }
    if file "$required" | grep -q "Mach-O" && ! file "$required" | grep -q "x86_64"; then
        echo "Playback dependency is not Intel x86_64: $required"
        exit 1
    fi
done

"$PYTHON" tools/verify_macos_arch.py --runtime --require x86_64
"$PYTHON" -c "import PyQt6"

# This build is intended to cover macOS 12 and above, retiring the separate
# legacy edition. PyQt6/Qt6 6.10+ raise the floor to macOS 13 while carrying a
# "macosx_10_14" wheel tag, so the tag cannot be trusted -- check the installed
# versions against the verified pin set instead.
"$PYTHON" - <<'PYPINS'
import re, sys
from importlib.metadata import PackageNotFoundError, version
wanted = {}
for line in open("constraints-macos12.txt", encoding="utf-8"):
    line = line.split("#", 1)[0].strip()
    if not line:
        continue
    name, _, pin = line.partition("==")
    wanted[name.strip()] = pin.strip()
bad = []
for name, pin in wanted.items():
    try:
        found = version(name)
    except PackageNotFoundError:
        bad.append(f"{name}: not installed (need {pin})")
        continue
    if found != pin:
        bad.append(f"{name}: {found} installed, macOS 12 build needs {pin}")
if bad:
    print("Dependency versions break macOS 12 support:")
    for line in bad:
        print(f"  {line}")
    sys.exit("Install the pinned set: pip install -c constraints-macos12.txt "
             + " ".join(wanted))
print(f"macOS 12 dependency pins verified: "
      + ", ".join(f"{n}=={v}" for n, v in sorted(wanted.items())))
PYPINS

APP_VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' "$ENTRY" | head -1)"
[[ -n "$APP_VERSION" ]] || { echo "APP_VERSION is missing from $ENTRY"; exit 1; }
DMG_NAME="SingWS-${APP_VERSION}-x86_64-installer.dmg"

echo "Building $APP_NAME $APP_VERSION for Intel..."
"$PYTHON" tools/make_dmg_assets.py --style-only

rm -rf build dist
"$PYTHON" -m PyInstaller --noconfirm "$SPEC"

APP_PATH="dist/$APP_NAME.app"
[[ -d "$APP_PATH" ]] || { echo "Build failed: $APP_PATH was not created"; exit 1; }
"$PYTHON" tools/verify_macos_arch.py --bundle "$APP_PATH" --require x86_64

REQUIRED_BUNDLED=(
    "$APP_PATH/Contents/Frameworks/singws_libmpv.2.dylib"
    "$APP_PATH/Contents/Frameworks/libsingws_mpv_bridge.dylib"
)
LIBMPV_NAMES=(libsingws_mpv_bridge.dylib singws_libmpv.2.dylib)
for bundled in "${REQUIRED_BUNDLED[@]}"; do
    [[ -e "$bundled" ]] || { echo "Bundled mpv dependency is missing: $bundled"; exit 1; }
done

# Prove the permanent bundled media core loads before this bundle goes any
# further; there is no alternate karaoke engine to mask a broken runtime.
"$PYTHON" - "$APP_PATH" "${LIBMPV_NAMES[@]}" <<'PYCHECK'
import ctypes, pathlib, sys
frameworks = pathlib.Path(sys.argv[1]) / "Contents" / "Frameworks"
for name in sys.argv[2:]:
    target = frameworks / name
    if not target.exists():
        sys.exit(f"Bundled {name} is missing")
    try:
        ctypes.CDLL(str(target))
    except OSError as exc:
        sys.exit(f"Bundled {name} cannot be loaded:\n{exc}")
print(f"Bundled media core loads cleanly: {', '.join(sys.argv[2:])}")
PYCHECK

# The whole point of the pin set: nothing in the shipped bundle may require a
# newer macOS than 12.0, or this build cannot replace the legacy edition.
# Checks the real Mach-O load commands, not wheel tags or filenames.
"$PYTHON" tools/verify_macos_min_version.py "$APP_PATH" --arch x86_64 --maximum 12.0

# Stage the bundle BEFORE signing, and sign the staged copy.
#
# This project lives under ~/Documents, which is file-provider (iCloud)
# managed: every file carries com.apple.FinderInfo and com.apple.fileprovider
# xattrs, and codesign refuses them with "resource fork, Finder information, or
# similar detritus not allowed". They cannot be stripped in place -- xattr -cr
# runs, and the file provider puts them straight back. A
# `ditto --norsrc --noextattr` copy outside that tree has none of them.
# build_all.sh always signed such a copy, which is why it never hit this.
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/singws-intel-dmg.XXXXXX")"
cleanup() {
    rm -rf "$STAGING"
}
trap cleanup EXIT
mkdir -p "$STAGING/dist"
ditto --norsrc --noextattr "$APP_PATH" "$STAGING/dist/$APP_NAME.app"

# PyInstaller recreates compatibility symlinks after Analysis, including links
# for Qt's FFmpeg libraries even though that unused plugin and its libraries
# were filtered out. Remove only those now-dangling aliases before codesign and
# dmgbuild traverse the staged bundle.
for link_dir in Contents/Frameworks Contents/Resources; do
    for library in libavcodec.61.dylib libavformat.61.dylib libavutil.59.dylib \
                   libswresample.5.dylib libswscale.8.dylib; do
        link="$STAGING/dist/$APP_NAME.app/$link_dir/$library"
        [[ ! -L "$link" ]] || rm "$link"
    done
done
if find -L "$STAGING/dist/$APP_NAME.app" -type l -print -quit | grep -q .; then
    echo "Staged app contains a dangling symlink"
    find -L "$STAGING/dist/$APP_NAME.app" -type l -print
    exit 1
fi

codesign --force --deep --sign - \
    --entitlements SingWS.entitlements "$STAGING/dist/$APP_NAME.app"
codesign --verify --deep --strict "$STAGING/dist/$APP_NAME.app"

rm -f "$DMG_NAME"
SINGWS_DMG_APP_ROOT="$STAGING" "$PYTHON" -m dmgbuild \
    -s dmg_settings.py "SingWS-${APP_VERSION}" "$DMG_NAME"
hdiutil verify "$DMG_NAME"

echo "Intel build complete:"
echo "  App: $(pwd)/$APP_PATH"
echo "  DMG: $(pwd)/$DMG_NAME"
shasum -a 256 "$APP_PATH/Contents/MacOS/$APP_NAME" "$DMG_NAME"
