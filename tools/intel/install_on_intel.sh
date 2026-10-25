#!/bin/bash
# Run ON THE INTEL MAC:  bash install_on_intel.sh /path/to/SingWS-<ver>-x86_64-installer.dmg
# Verifies the DMG against the .sha256 file beside it, backs up the current app and settings, installs, verifies, and keeps the old app for rollback.
# Nothing is deleted. Quit SingWS first. Override APPS_DIR / SINGWS_HOME only for testing.
set -euo pipefail
DMG="${1:?usage: install_on_intel.sh /path/to/SingWS-x86_64-installer.dmg}"
APPS="${APPS_DIR:-/Applications}"; HOME_DIR="${SINGWS_HOME:-$HOME/SingWS}"; TS=$(date +%Y%m%d-%H%M%S)
[[ -f "$DMG" ]] || { echo "No such file: $DMG"; exit 1; }
[[ "$(uname -m)" == "x86_64" || -n "${ALLOW_ANY_ARCH:-}" ]] || { echo "This is not an Intel Mac (uname -m = $(uname -m)). Stopping."; exit 1; }
if pgrep -f "SingWS.app/Contents/MacOS/SingWS" >/dev/null && [[ -z "${ALLOW_RUNNING:-}" ]]; then echo "SingWS is running. Quit it first."; exit 1; fi

echo "== 1. Checking the file =="
SUMFILE="${DMG%.dmg}.sha256"; [[ -f "$SUMFILE" ]] || SUMFILE="$DMG.sha256"
[[ -f "$SUMFILE" ]] || { echo "Missing $(basename "$SUMFILE") next to the DMG. Copy both files."; exit 1; }
WANT=$(cut -d' ' -f1 "$SUMFILE"); GOT=$(shasum -a 256 "$DMG" | cut -d' ' -f1)
[[ "$WANT" == "$GOT" ]] || { echo "HASH MISMATCH (wanted ${WANT:0:16}, got ${GOT:0:16}). The copy is damaged. Stopping."; exit 1; }
echo "hash ok ${GOT:0:16}"; hdiutil verify -quiet "$DMG" && echo "dmg ok"

echo "== 2. Backups =="
if [[ -d "$APPS/SingWS.app" ]]; then
  OLDVER=$(defaults read "$APPS/SingWS.app/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo unknown)
  ditto -c -k --sequesterRsrc --keepParent "$APPS/SingWS.app" "$HOME/SingWS-app-backup-$OLDVER-$TS.zip" && echo "app backed up: ~/SingWS-app-backup-$OLDVER-$TS.zip"
fi
[[ -f "$HOME_DIR/settings.json" ]] && cp -p "$HOME_DIR/settings.json" "$HOME_DIR/settings.json.bak-$TS-before-install" && echo "settings backed up (settings.json.bak-$TS-before-install)"

echo "== 3. Installing =="
M=$(mktemp -d); trap 'hdiutil detach -quiet "$M" 2>/dev/null || true' EXIT
hdiutil attach -readonly -nobrowse -mountpoint "$M" "$DMG" >/dev/null
rm -rf "$APPS/SingWS.app.incoming"; ditto "$M/SingWS.app" "$APPS/SingWS.app.incoming"
codesign --verify --deep --strict "$APPS/SingWS.app.incoming" && echo "signature ok"
ARCHS=$(lipo -archs "$APPS/SingWS.app.incoming/Contents/MacOS/SingWS"); echo "architectures: $ARCHS"
[[ "$ARCHS" == *x86_64* ]] || { echo "Not an Intel build. Stopping (nothing swapped)."; rm -rf "$APPS/SingWS.app.incoming"; exit 1; }
NEWVER=$(defaults read "$APPS/SingWS.app.incoming/Contents/Info" CFBundleShortVersionString)
if [[ -d "$APPS/SingWS.app" ]]; then mv "$APPS/SingWS.app" "$APPS/SingWS-$OLDVER-before-$NEWVER-$TS.app"; fi
mv "$APPS/SingWS.app.incoming" "$APPS/SingWS.app"
echo "installed SingWS $NEWVER; the previous app is kept as $APPS/SingWS-$OLDVER-before-$NEWVER-$TS.app"
echo
echo "Next: open SingWS from Applications ONCE (right-click > Open the first time), let it reach the main screen, then run:  bash check_launch.sh"
echo "To go back: quit SingWS, delete $APPS/SingWS.app and rename the kept app to SingWS.app (settings backup is above)."
