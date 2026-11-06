#!/bin/bash
# Build the singws-settingsmig helper (startup settings migrations) for both Mac architectures into native/singws_settingsmig/<arch>/ (macOS 12.3 minimum). The installer specs
# bundle the matching one; without it the settings_migrate_shadow setting is silently skipped.
#   ./rust/build_settingsmig.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_settingsmig"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-settingsmig --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/singws-settingsmig" "$OUT/$arch/singws-settingsmig"
    chmod 755 "$OUT/$arch/singws-settingsmig"
    codesign --force --sign "$IDENTITY" --identifier com.singws.settingsmig "$OUT/$arch/singws-settingsmig"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/singws-settingsmig" | tail -1
    echo "built $OUT/$arch/singws-settingsmig ($(lipo -archs "$OUT/$arch/singws-settingsmig"))"
done
