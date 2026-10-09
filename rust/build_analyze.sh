#!/bin/bash
# Build the singws-analyze helper for both Mac architectures into native/singws_analyze/<arch>/ (macOS 12.3 minimum).
# The installer specs bundle the matching one; without it the app keeps using libmpv for analysis (analysis_engine only
# matters when the operator sets it to "shadow" or "rust").
#   ./rust/build_analyze.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_analyze"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-analysis --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/singws-analyze" "$OUT/$arch/singws-analyze"
    chmod 755 "$OUT/$arch/singws-analyze"
    codesign --force --sign "$IDENTITY" "$OUT/$arch/singws-analyze"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/singws-analyze" | tail -1
    echo "built $OUT/$arch/singws-analyze ($(lipo -archs "$OUT/$arch/singws-analyze"))"
done
