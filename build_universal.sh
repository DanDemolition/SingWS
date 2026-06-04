#!/bin/bash
set -e
cd /Users/daniel/Documents/SingWS
VER="0.2.18.1"; GST="/Library/Frameworks/GStreamer.framework/Versions/1.0"
echo ">>> UNIVERSAL build start $(date)"
rm -rf build dist
export GI_TYPELIB_PATH="${GST}/lib/girepository-1.0"
export DYLD_FALLBACK_LIBRARY_PATH="${GST}/lib"
export PKG_CONFIG_PATH="${GST}/lib/pkgconfig"
.venv-universal/bin/pyinstaller --noconfirm "SingWS-0.2.18.0-universal.spec"
echo ">>> universal app arch:"; file dist/SingWS.app/Contents/MacOS/SingWS | sed 's/^/    /'
lipo -info dist/SingWS.app/Contents/MacOS/SingWS 2>/dev/null | sed 's/^/    /' || true
rm -f "SingWS-${VER}-universal-installer.dmg"
.venv/bin/dmgbuild -s dmg_settings.py "SingWS-${VER}" "SingWS-${VER}-universal-installer.dmg"
echo ">>> UNIVERSAL COMPLETE $(date): $(ls -lh SingWS-${VER}-universal-installer.dmg | awk '{print $5}')"
