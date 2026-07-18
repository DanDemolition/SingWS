#!/bin/bash
set -euo pipefail
cd /Users/daniel/Documents/SingWS
# Version is the single source of truth in APP_VERSION (entry script).
VER="$(grep -E '^APP_VERSION' 0.2.18.1.py | sed -E 's/.*"([^"]+)".*/\1/')"
GST="/Library/Frameworks/GStreamer.framework/Versions/1.0"
UNIVERSAL_PY=".venv-universal/bin/python"
"${UNIVERSAL_PY}" tools/verify_macos_arch.py \
  --runtime --require arm64 --require x86_64
echo ">>> UNIVERSAL build start $(date) (v${VER})"
# Re-apply the DMG helper's custom icon + hidden extension (git can't track it).
.venv/bin/python tools/make_dmg_assets.py --style-only
rm -rf build dist
export GI_TYPELIB_PATH="${GST}/lib/girepository-1.0"
export XDG_DATA_DIRS="${GST}/share:${XDG_DATA_DIRS:-}"
export DYLD_FALLBACK_LIBRARY_PATH="${GST}/lib"
export PKG_CONFIG_PATH="${GST}/lib/pkgconfig"
"${UNIVERSAL_PY}" -m PyInstaller --noconfirm "SingWS-universal.spec"
# A universal bundle may legitimately contain architecture-specific helper
# dependencies used by only one slice. Verify the launcher itself here; the
# universal Python/native runtime was already verified above.
"${UNIVERSAL_PY}" tools/verify_macos_arch.py \
  --path dist/SingWS.app/Contents/MacOS/SingWS --require arm64 --require x86_64
echo ">>> universal app arch:"; file dist/SingWS.app/Contents/MacOS/SingWS | sed 's/^/    /'
lipo -info dist/SingWS.app/Contents/MacOS/SingWS 2>/dev/null | sed 's/^/    /' || true
rm -f "SingWS-${VER}-universal-installer.dmg"
.venv/bin/dmgbuild -s dmg_settings.py "SingWS-${VER}" "SingWS-${VER}-universal-installer.dmg"
echo ">>> UNIVERSAL COMPLETE $(date): $(ls -lh SingWS-${VER}-universal-installer.dmg | awk '{print $5}')"
