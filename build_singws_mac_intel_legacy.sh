#!/bin/bash
# Build the permanent Intel edition for macOS 12 Monterey and macOS 13 Ventura.
set -euo pipefail

cd "$(dirname "$0")"
if [[ -d "$HOME/Library/Frameworks/Python.framework/Versions/3.12" ]]; then
    export DYLD_FRAMEWORK_PATH="$HOME/Library/Frameworks${DYLD_FRAMEWORK_PATH:+:$DYLD_FRAMEWORK_PATH}"
    export DYLD_LIBRARY_PATH="$HOME/Library/Frameworks/Python.framework/Versions/3.12${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
fi
APP_NAME="SingWS"
ENTRY="0.2.18.1.py"
SPEC="SingWS-intel-legacy.spec"
PYTHON=".venv-intel-legacy/bin/python"
DMGBUILD=".venv-intel-legacy/bin/dmgbuild"

[[ "$(uname -m)" == "x86_64" ]] || { echo "Run this build on an Intel Mac."; exit 1; }
for required in "$ENTRY" "$SPEC" "$PYTHON" "$DMGBUILD" \
    bin/ffmpeg bin/ffprobe signalsmith_audio_native.cpython-312-darwin.so \
    SingWS.entitlements dmg_settings.py tools/verify_macos_arch.py \
    tools/verify_macos_min_version.py; do
    [[ -e "$required" ]] || { echo "Missing legacy build input: $required"; exit 1; }
done

"$PYTHON" -c "import PyQt6, numpy, scipy"
APP_VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' "$ENTRY" | head -1)"
DMG_NAME="SingWS-${APP_VERSION}-intel-legacy-installer.dmg"

"$PYTHON" tools/make_dmg_assets.py --style-only
rm -rf build-intel-legacy build-intel-legacy-input dist-intel-legacy
mkdir -p build-intel-legacy-input
lipo bin/ffmpeg -thin x86_64 -output build-intel-legacy-input/ffmpeg
lipo bin/ffprobe -thin x86_64 -output build-intel-legacy-input/ffprobe
chmod +x build-intel-legacy-input/ffmpeg build-intel-legacy-input/ffprobe
"$PYTHON" -m PyInstaller --noconfirm --clean \
    --workpath build-intel-legacy --distpath dist-intel-legacy "$SPEC"

APP_PATH="dist-intel-legacy/$APP_NAME.app"
"$PYTHON" tools/verify_macos_arch.py --bundle "$APP_PATH" --require x86_64
"$PYTHON" tools/verify_macos_min_version.py "$APP_PATH" \
    --arch x86_64 --maximum 12.0

SIGNING_STAGE="$(mktemp -d "${TMPDIR:-/tmp}/singws-intel-sign.XXXXXX")"
ditto --norsrc --noextattr "$APP_PATH" "$SIGNING_STAGE/$APP_NAME.app"
rm -rf "$APP_PATH"
mv "$SIGNING_STAGE/$APP_NAME.app" "$APP_PATH"
rmdir "$SIGNING_STAGE"
codesign --force --deep --sign - --entitlements SingWS.entitlements "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"

STAGING="$(mktemp -d "${TMPDIR:-/tmp}/singws-intel-legacy.XXXXXX")"
cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT
mkdir -p "$STAGING/dist"
ditto --norsrc --noextattr "$APP_PATH" "$STAGING/dist/$APP_NAME.app"
rm -f "$DMG_NAME"
SINGWS_DMG_APP_ROOT="$STAGING" "$DMGBUILD" \
    -s dmg_settings.py "SingWS-${APP_VERSION}" "$DMG_NAME"
hdiutil verify "$DMG_NAME"
shasum -a 256 "$APP_PATH/Contents/MacOS/$APP_NAME" "$DMG_NAME"
echo "Legacy Intel DMG ready: $(pwd)/$DMG_NAME"
