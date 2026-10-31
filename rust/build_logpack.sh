#!/bin/bash
# Build the singws-logpack helper for both Mac architectures into native/singws_logpack/<arch>/ (macOS 12.3 minimum). The installer specs
# bundle the matching one; without it the logpack_shadow setting is silently skipped.
#   ./rust/build_logpack.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_logpack"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-logpack --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/singws-logpack" "$OUT/$arch/singws-logpack"
    chmod 755 "$OUT/$arch/singws-logpack"
    codesign --force --sign "$IDENTITY" --identifier com.singws.logpack "$OUT/$arch/singws-logpack"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/singws-logpack" | tail -1
    echo "built $OUT/$arch/singws-logpack ($(lipo -archs "$OUT/$arch/singws-logpack"))"
done
