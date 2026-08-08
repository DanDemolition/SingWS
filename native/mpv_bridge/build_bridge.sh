#!/bin/bash
# Build libsingws_mpv_bridge.dylib -- the in-process libmpv core behind
# mpv_playback_iina.py.
#
# The IINA-derived media libraries are NOT in this repo; point FRAMEWORKS at
# the directory holding singws_libmpv.2.dylib and friends.
#
#   ./build_bridge.sh [--arch x86_64|arm64|universal] [--frameworks DIR]
#
# Deployment target is pinned to 12.0 so the produced dylib can never be the
# reason a build stops running on a legacy Intel Mac. Verify afterwards with:
#   tools/verify_macos_min_version.py <out> --arch x86_64 --maximum 12.0
set -euo pipefail
cd "$(dirname "$0")"

ARCH="x86_64"
FRAMEWORKS="${SINGWS_MPV_FRAMEWORKS:-$HOME/Downloads/native_dual_view/Frameworks}"
OUT="libsingws_mpv_bridge.dylib"
DEPLOYMENT_TARGET="12.0"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch) ARCH="$2"; shift 2 ;;
        --frameworks) FRAMEWORKS="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

[[ -d "$FRAMEWORKS" ]] || { echo "frameworks directory not found: $FRAMEWORKS" >&2; exit 1; }
MPV_LIB="$FRAMEWORKS/singws_libmpv.2.dylib"
[[ -f "$MPV_LIB" ]] || { echo "singws_libmpv.2.dylib not found in $FRAMEWORKS" >&2; exit 1; }

ARCH_FLAGS=()
case "$ARCH" in
    universal) ARCH_FLAGS=(-arch x86_64 -arch arm64) ;;
    *)         ARCH_FLAGS=(-arch "$ARCH") ;;
esac

# libmpv is loaded from the app bundle's Frameworks directory at runtime, so
# link against the install name rather than baking in this build path.
clang++ -dynamiclib -std=c++17 -fobjc-arc -O2 \
    "${ARCH_FLAGS[@]}" \
    -mmacosx-version-min="$DEPLOYMENT_TARGET" \
    -I include \
    -framework Cocoa -framework OpenGL -framework QuartzCore \
    -Wl,-rpath,@loader_path -Wl,-rpath,@loader_path/Frameworks \
    "$MPV_LIB" \
    -install_name "@rpath/$OUT" \
    -o "$OUT" \
    bridge.mm

echo "built $OUT ($ARCH, macOS $DEPLOYMENT_TARGET+)"
otool -L "$OUT" | sed -n '2,4p'
