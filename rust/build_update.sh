#!/bin/bash
# Build the singws-update helper (the update rules, Stage E) for both Mac architectures into native/singws_update/<arch>/ (macOS 12.3 minimum). The installer specs
# bundle the matching one; without it the update_shadow setting is silently skipped.
#   ./rust/build_update.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_update"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-update --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/singws-update" "$OUT/$arch/singws-update"
    chmod 755 "$OUT/$arch/singws-update"
    codesign --force --sign "$IDENTITY" --identifier com.singws.update "$OUT/$arch/singws-update"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/singws-update" | tail -1
    echo "built $OUT/$arch/singws-update ($(lipo -archs "$OUT/$arch/singws-update"))"
done
