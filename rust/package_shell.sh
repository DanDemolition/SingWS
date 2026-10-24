#!/bin/zsh
# Bundle the universal Qt Quick shell into a self-contained, locally signed "SingWS Shell.app".
#   Needs the Qt kit from build_shell.sh plus:  aqt install-qt -O ~/Qt mac desktop 6.12.0 clang_64 --archives qttools
# Usage: package_shell.sh OUTDIR   (runs build_shell.sh first)
set -e
cd "$(dirname "$0")"
OUT="${1:?usage: package_shell.sh OUTDIR}"
export QT_ROOT="${QT_ROOT:-$HOME/Qt/6.12.0/macos}"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target-qt}"
./build_shell.sh
A="$OUT/SingWS Shell.app"; rm -rf "$A"; mkdir -p "$A/Contents/"{MacOS,Resources}
cp "$CARGO_TARGET_DIR/universal/singws-shell" "$A/Contents/MacOS/SingWS Shell"
cat > "$A/Contents/Info.plist" <<'P'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>SingWS Shell</string>
<key>CFBundleDisplayName</key><string>SingWS Shell</string>
<key>CFBundleIdentifier</key><string>com.singws.shell</string>
<key>CFBundleExecutable</key><string>SingWS Shell</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>0.1.0</string>
<key>CFBundleShortVersionString</key><string>0.1.0</string>
<key>LSMinimumSystemVersion</key><string>12.3</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
P
# macdeployqt's SQL-plugin errors (missing ODBC/Postgres libs) are harmless; we drop those drivers.
"$QT_ROOT/bin/macdeployqt" "$A" -qmldir=crates/singws-shell/qml >/dev/null 2>&1 || true
rm -rf "$A/Contents/PlugIns/sqldrivers"
codesign --force --deep --sign "${SIGN_IDENTITY:-SingWS Local Code Signing}" "$A"
codesign --verify --deep --strict "$A"
echo "bundle: $A  $(lipo -archs "$A/Contents/MacOS/SingWS Shell")  $(du -sh "$A" | cut -f1)"
