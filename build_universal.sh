#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -n "${SINGWS_DYLD_FRAMEWORK_PATH:-}" ]]; then
  export DYLD_FRAMEWORK_PATH="$SINGWS_DYLD_FRAMEWORK_PATH"
fi
if [[ -n "${SINGWS_DYLD_LIBRARY_PATH:-}" ]]; then
  export DYLD_LIBRARY_PATH="$SINGWS_DYLD_LIBRARY_PATH"
fi
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-${TMPDIR:-/tmp}/singws-pyinstaller}"
# Version is the single source of truth in APP_VERSION (entry script).
VER="$(grep -E '^APP_VERSION' 0.2.18.1.py | sed -E 's/.*"([^"]+)".*/\1/')"
UNIVERSAL_PY=".venv-universal/bin/python"
"${UNIVERSAL_PY}" tools/verify_macos_arch.py \
  --runtime --require arm64 --require x86_64
echo ">>> UNIVERSAL build start $(date) (v${VER})"
# Re-apply the DMG helper's custom icon + hidden extension (git can't track it).
.venv/bin/python tools/make_dmg_assets.py --style-only
rm -rf build dist
"${UNIVERSAL_PY}" -m PyInstaller --noconfirm "SingWS-universal.spec"
# A universal bundle may legitimately contain architecture-specific helper
# dependencies used by only one slice. Verify the launcher itself here; the
# universal Python/native runtime was already verified above.
"${UNIVERSAL_PY}" tools/verify_macos_arch.py \
  --path dist/SingWS.app/Contents/MacOS/SingWS --require arm64 --require x86_64
echo ">>> universal app arch:"; file dist/SingWS.app/Contents/MacOS/SingWS | sed 's/^/    /'
lipo -info dist/SingWS.app/Contents/MacOS/SingWS 2>/dev/null | sed 's/^/    /' || true
rm -f "SingWS-${VER}-universal-installer.dmg"
UNIVERSAL_STAGE="${TMPDIR:-/tmp}/singws-universal-dmg-$$"
rm -rf "$UNIVERSAL_STAGE"
mkdir -p "$UNIVERSAL_STAGE/dist"
ditto --norsrc --noextattr dist/SingWS.app "$UNIVERSAL_STAGE/dist/SingWS.app"
codesign --force --deep --sign - --entitlements SingWS.entitlements \
  "$UNIVERSAL_STAGE/dist/SingWS.app"
codesign --verify --deep --strict "$UNIVERSAL_STAGE/dist/SingWS.app"
SINGWS_DMG_APP_ROOT="$UNIVERSAL_STAGE" .venv/bin/dmgbuild \
  -s dmg_settings.py "SingWS-${VER}" "SingWS-${VER}-universal-installer.dmg"
rm -rf "$UNIVERSAL_STAGE"
echo ">>> UNIVERSAL COMPLETE $(date): $(ls -lh SingWS-${VER}-universal-installer.dmg | awk '{print $5}')"
