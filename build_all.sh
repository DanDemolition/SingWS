#!/bin/bash
# Sequential build: arm64 (dev) + x86_64 (Intel test machine), each into its own DMG.
set -e
cd /Users/daniel/Documents/SingWS
# Version is the single source of truth in APP_VERSION (entry script).
VER="$(grep -E '^APP_VERSION' 0.2.18.1.py | sed -E 's/.*"([^"]+)".*/\1/')"
GST="/Library/Frameworks/GStreamer.framework/Versions/1.0"

echo "========================================"
echo " BUILD START $(date)"
echo "========================================"

# Re-apply the DMG helper's custom icon + hidden extension (filesystem metadata
# that git doesn't track) so every DMG ships the styled "Open Me First" helper.
.venv/bin/python tools/make_dmg_assets.py --style-only

# ---- arm64 ----
echo ">>> [1/4] arm64 PyInstaller"
rm -rf build dist
.venv/bin/pyinstaller --noconfirm "SingWS-arm64.spec"
echo ">>> arm64 app arch:"; file dist/SingWS.app/Contents/MacOS/SingWS | sed 's/^/    /'
echo ">>> [2/4] arm64 dmgbuild"
rm -f "SingWS-${VER}-arm64-installer.dmg"
.venv/bin/dmgbuild -s dmg_settings.py "SingWS-${VER}" "SingWS-${VER}-arm64-installer.dmg"
echo ">>> arm64 DMG done: $(ls -lh SingWS-${VER}-arm64-installer.dmg | awk '{print $5}')"

# ---- x86_64 (Intel) ----
echo ">>> [3/4] x86_64 PyInstaller (universal venv + GStreamer env)"
rm -rf build dist
export GI_TYPELIB_PATH="${GST}/lib/girepository-1.0"
export DYLD_FALLBACK_LIBRARY_PATH="${GST}/lib"
export PKG_CONFIG_PATH="${GST}/lib/pkgconfig"
.venv-universal/bin/pyinstaller --noconfirm "SingWS-x86_64.spec"
echo ">>> x86_64 app arch:"; file dist/SingWS.app/Contents/MacOS/SingWS | sed 's/^/    /'
echo ">>> [4/4] x86_64 dmgbuild"
rm -f "SingWS-${VER}-x86_64-installer.dmg"
.venv/bin/dmgbuild -s dmg_settings.py "SingWS-${VER}" "SingWS-${VER}-x86_64-installer.dmg"
echo ">>> x86_64 DMG done: $(ls -lh SingWS-${VER}-x86_64-installer.dmg | awk '{print $5}')"

echo "========================================"
echo " BUILD COMPLETE $(date)"
ls -lh SingWS-${VER}-*-installer.dmg | sed 's/^/    /'
echo "========================================"
