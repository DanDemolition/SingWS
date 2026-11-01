#!/bin/bash
# Build the singws-scan-cli helper for both Mac architectures into native/singws_scan/<arch>/ (macOS 12.3 minimum). The installer specs
# bundle the matching one; without it the scan shadow reports 'helper not found' after a full scan.
#   ./rust/build_scan.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_scan"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-scan --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/singws-scan-cli" "$OUT/$arch/singws-scan-cli"
    chmod 755 "$OUT/$arch/singws-scan-cli"
    codesign --force --sign "$IDENTITY" --identifier com.singws.scan "$OUT/$arch/singws-scan-cli"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/singws-scan-cli" | tail -1
    echo "built $OUT/$arch/singws-scan-cli ($(lipo -archs "$OUT/$arch/singws-scan-cli"))"
done
