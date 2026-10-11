#!/bin/bash
# Build libsingws_video.dylib (the Rust video bridge, roadmap item 2) for both Mac architectures into native/singws_video/<arch>/ (macOS 12.3
# minimum). The installer specs bundle the matching one beside libsingws_mpv_bridge.dylib; the app only uses it when the `video_bridge`
# setting is "rust" (default "objc"), and falls back to the Objective-C bridge if it cannot be used.
#   ./rust/build_video.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
# Keep build output out of the iCloud-synced Documents folder.
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_video"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-video --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/libsingws_video.dylib" "$OUT/$arch/libsingws_video.dylib"
    install_name_tool -id @rpath/libsingws_video.dylib "$OUT/$arch/libsingws_video.dylib"
    codesign --force --sign "$IDENTITY" "$OUT/$arch/libsingws_video.dylib"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/libsingws_video.dylib" | tail -1
    echo "built $OUT/$arch/libsingws_video.dylib ($(lipo -archs "$OUT/$arch/libsingws_video.dylib"))"
done
