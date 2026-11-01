#!/bin/bash
# Build the singws-netslice helper (network slices (sync probe, ...)) for both Mac architectures into native/singws_netslice/<arch>/ (macOS 12.3 minimum). The installer specs
# bundle the matching one; without it the net_slice_shadow setting is silently skipped.
#   ./rust/build_relaymsg.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_netslice"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-netslice --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/singws-netslice" "$OUT/$arch/singws-netslice"
    chmod 755 "$OUT/$arch/singws-netslice"
    codesign --force --sign "$IDENTITY" --identifier com.singws.netslice "$OUT/$arch/singws-netslice"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/singws-netslice" | tail -1
    echo "built $OUT/$arch/singws-netslice ($(lipo -archs "$OUT/$arch/singws-netslice"))"
done
