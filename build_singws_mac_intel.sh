#!/bin/bash
# Build only the Intel SingWS app and styled installer DMG.
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="SingWS"
ENTRY="0.2.18.1.py"
SPEC="SingWS-x86_64.spec"
PYTHON=".venv-universal/bin/python"
DMGBUILD=".venv/bin/dmgbuild"

if [[ "$(uname -m)" != "x86_64" ]]; then
    echo "This dedicated build must run natively on an Intel Mac."
    exit 1
fi

for required in \
    "$ENTRY" "$SPEC" "$PYTHON" "$DMGBUILD" \
    mpv_playback.py mpv_karaoke_transport.py MoltenVK_icd.json \
    SingWS.entitlements dmg_settings.py tools/verify_macos_arch.py; do
    [[ -e "$required" ]] || { echo "Missing required file: $required"; exit 1; }
done

for command in hdiutil codesign file otool shasum; do
    command -v "$command" >/dev/null || { echo "Missing command: $command"; exit 1; }
done

for required in \
    /usr/local/bin/mpv \
    /usr/local/lib/libmpv.2.dylib \
    /usr/local/bin/ffmpeg \
    /usr/local/lib/libMoltenVK.dylib; do
    [[ -e "$required" ]] || { echo "Missing Intel playback dependency: $required"; exit 1; }
    file "$required" | grep -q "x86_64" || {
        echo "Playback dependency is not Intel x86_64: $required"
        exit 1
    }
done

"$PYTHON" tools/verify_macos_arch.py --runtime --require x86_64
"$PYTHON" -c "import mpv; import PyQt6; import signalsmith_audio_native"

APP_VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' "$ENTRY" | head -1)"
[[ -n "$APP_VERSION" ]] || { echo "APP_VERSION is missing from $ENTRY"; exit 1; }
DMG_NAME="SingWS-${APP_VERSION}-x86_64-installer.dmg"

echo "Building $APP_NAME $APP_VERSION for Intel..."
.venv/bin/python tools/make_dmg_assets.py --style-only

rm -rf build dist
"$PYTHON" -m PyInstaller --noconfirm "$SPEC"

APP_PATH="dist/$APP_NAME.app"
[[ -d "$APP_PATH" ]] || { echo "Build failed: $APP_PATH was not created"; exit 1; }
"$PYTHON" tools/verify_macos_arch.py --bundle "$APP_PATH" --require x86_64

for bundled in \
    "$APP_PATH/Contents/Frameworks/mpv" \
    "$APP_PATH/Contents/Frameworks/libmpv.2.dylib" \
    "$APP_PATH/Contents/Frameworks/libMoltenVK.dylib" \
    "$APP_PATH/Contents/Resources/vulkan/icd.d/MoltenVK_icd.json"; do
    [[ -e "$bundled" ]] || { echo "Bundled mpv dependency is missing: $bundled"; exit 1; }
done

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

codesign --force --deep --sign - \
    --entitlements SingWS.entitlements "$STAGING/dist/$APP_NAME.app"
codesign --verify --deep --strict "$STAGING/dist/$APP_NAME.app"

rm -f "$DMG_NAME"
SINGWS_DMG_APP_ROOT="$STAGING" "$DMGBUILD" \
    -s dmg_settings.py "SingWS-${APP_VERSION}" "$DMG_NAME"
hdiutil verify "$DMG_NAME"

echo "Intel build complete:"
echo "  App: $(pwd)/$APP_PATH"
echo "  DMG: $(pwd)/$DMG_NAME"
shasum -a 256 "$APP_PATH/Contents/MacOS/$APP_NAME" "$DMG_NAME"
