#!/bin/bash
# Build the singws-queue-state helper (the queue-state machine, Stage B) for both Mac architectures into native/singws_queue_state/<arch>/ (macOS 12.3 minimum). The installer specs
# bundle the matching one; without it the queue_state_shadow setting is silently skipped.
#   ./rust/build_queue_state.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_queue_state"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-queue-state --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/singws-queue-state" "$OUT/$arch/singws-queue-state"
    chmod 755 "$OUT/$arch/singws-queue-state"
    codesign --force --sign "$IDENTITY" --identifier com.singws.queuestate "$OUT/$arch/singws-queue-state"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/singws-queue-state" | tail -1
    echo "built $OUT/$arch/singws-queue-state ($(lipo -archs "$OUT/$arch/singws-queue-state"))"
done
