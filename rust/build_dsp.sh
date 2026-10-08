#!/bin/bash
# Build libsingws_dsp_ffi.dylib for both Mac architectures into native/singws_dsp/<arch>/ (macOS 12.3 minimum).
# The installer specs pick up the matching one; without it the app silently keeps the Python master processor.
#   ./rust/build_dsp.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
# Keep build output out of the iCloud-synced Documents folder.
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_dsp"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-dsp-ffi --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/libsingws_dsp_ffi.dylib" "$OUT/$arch/libsingws_dsp_ffi.dylib"
    install_name_tool -id @rpath/libsingws_dsp_ffi.dylib "$OUT/$arch/libsingws_dsp_ffi.dylib"
    codesign --force --sign "$IDENTITY" "$OUT/$arch/libsingws_dsp_ffi.dylib"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/libsingws_dsp_ffi.dylib" | tail -1
    echo "built $OUT/$arch/libsingws_dsp_ffi.dylib ($(lipo -archs "$OUT/$arch/libsingws_dsp_ffi.dylib"))"
done
