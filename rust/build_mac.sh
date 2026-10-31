#!/bin/bash
# Build the singws-mac-probe helper for both Mac architectures into native/singws_mac/<arch>/ (macOS 12.3 minimum) and sign it with the local
# certificate under a FIXED identifier, so a privacy grant given to it is tied to the certificate and not to one build's code hash.
# The installer specs bundle the matching one; without it the platform_shadow setting only logs "skipped".
#   ./rust/build_mac.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_mac"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-mac --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/singws-mac-probe" "$OUT/$arch/singws-mac-probe"
    chmod 755 "$OUT/$arch/singws-mac-probe"
    codesign --force --sign "$IDENTITY" --identifier com.singws.mac-probe "$OUT/$arch/singws-mac-probe"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/singws-mac-probe" | tail -1
    echo "built $OUT/$arch/singws-mac-probe ($(lipo -archs "$OUT/$arch/singws-mac-probe"))"
done
