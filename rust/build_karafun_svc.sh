#!/bin/bash
# Build the singws-karafun-svc program (Rust driver for the KaraFun song and service machines) for both Mac architectures into native/singws_karafun_svc/<arch>/ (macOS 12.3 minimum). The installer specs
# bundle the matching one; without it karafun_web_engine=rust falls back to the Python host.
#   ./rust/build_karafun_svc.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_karafun_svc"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-karafun-svc --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/singws-karafun-svc" "$OUT/$arch/singws-karafun-svc"
    chmod 755 "$OUT/$arch/singws-karafun-svc"
    codesign --force --sign "$IDENTITY" --identifier com.singws.karafun-svc "$OUT/$arch/singws-karafun-svc"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/singws-karafun-svc" | tail -1
    echo "built $OUT/$arch/singws-karafun-svc ($(lipo -archs "$OUT/$arch/singws-karafun-svc"))"
done
