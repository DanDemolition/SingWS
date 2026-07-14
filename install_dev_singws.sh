#!/bin/bash
# Install the local arm64 development build and make Accessibility recovery easy.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SOURCE_APP="$ROOT/dist/SingWS.app"
DEST_APP="/Applications/SingWS.app"
EXPECTED_BUNDLE_ID="com.singws.app"
BUILD=0
RESET_ACCESSIBILITY=0

usage() {
    cat <<'EOF'
Usage: ./install_dev_singws.sh [--build] [--reset-accessibility]

  --build                 Build dist/SingWS.app with SingWS-arm64.spec first.
  --reset-accessibility   Clear SingWS's stale Accessibility entry and open
                          the correct System Settings pane after installation.

The final Accessibility authorization must be clicked manually; macOS does not
allow a terminal script to grant that privacy permission.
EOF
}

while (($#)); do
    case "$1" in
        --build)
            BUILD=1
            ;;
        --reset-accessibility)
            RESET_ACCESSIBILITY=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

cd "$ROOT"

if ((BUILD)); then
    echo ">>> Building arm64 SingWS.app"
    .venv/bin/pyinstaller --noconfirm SingWS-arm64.spec
fi

if [[ ! -x "$SOURCE_APP/Contents/MacOS/SingWS" ]]; then
    echo "Missing build: $SOURCE_APP" >&2
    echo "Run with --build or build SingWS-arm64.spec first." >&2
    exit 1
fi

BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$SOURCE_APP/Contents/Info.plist")"
if [[ "$BUNDLE_ID" != "$EXPECTED_BUNDLE_ID" ]]; then
    echo "Refusing to install unexpected bundle id: $BUNDLE_ID" >&2
    exit 1
fi

echo ">>> Verifying source bundle"
codesign --verify --deep --strict "$SOURCE_APP"

echo ">>> Quitting installed SingWS"
pkill -f '/Applications/SingWS.app/Contents/MacOS/SingWS' 2>/dev/null || true
for _ in {1..20}; do
    if ! pgrep -f '/Applications/SingWS.app/Contents/MacOS/SingWS' >/dev/null; then
        break
    fi
    sleep 0.1
done

if pgrep -f '/Applications/SingWS.app/Contents/MacOS/SingWS' >/dev/null; then
    echo "SingWS did not quit; installation stopped." >&2
    exit 1
fi

if [[ "$DEST_APP" != "/Applications/SingWS.app" ]]; then
    echo "Refusing unsafe destination: $DEST_APP" >&2
    exit 1
fi

echo ">>> Replacing $DEST_APP"
rm -rf "$DEST_APP"
ditto "$SOURCE_APP" "$DEST_APP"

echo ">>> Verifying installed bundle"
codesign --verify --deep --strict "$DEST_APP"
INSTALLED_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$DEST_APP/Contents/Info.plist")"

if ((RESET_ACCESSIBILITY)); then
    echo ">>> Resetting stale Accessibility record for $BUNDLE_ID"
    tccutil reset Accessibility "$BUNDLE_ID"
    open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'
fi

echo ">>> Launching SingWS $INSTALLED_VERSION"
open -b "$BUNDLE_ID"

echo
echo "Installed and launched SingWS $INSTALLED_VERSION."
if ((RESET_ACCESSIBILITY)); then
    echo "In Accessibility settings, enable SingWS once if it is off, then reopen SingWS."
else
    echo "If automation is denied, rerun:"
    echo "  ./install_dev_singws.sh --reset-accessibility"
fi
