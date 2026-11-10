#!/bin/bash
# Build the singws-karafun-web helper (KaraFun search script builder) for both Mac architectures into native/singws_karafun_web/<arch>/ (macOS 12.3 minimum). The installer specs
# bundle the matching one; without it the karafun_web_player setting cannot be used.
#   ./rust/build_karafun_web.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
OUT="$(cd .. && pwd)/native/singws_karafun_web"

for pair in "arm64:aarch64-apple-darwin" "x86_64:x86_64-apple-darwin"; do
    arch="${pair%%:*}"; triple="${pair##*:}"
    cargo build --release -p singws-karafun-web --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/singws-karafun-web" "$OUT/$arch/singws-karafun-web"
    chmod 755 "$OUT/$arch/singws-karafun-web"
    codesign --force --sign "$IDENTITY" --identifier com.singws.karafun-web "$OUT/$arch/singws-karafun-web"
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/singws-karafun-web" | tail -1
    echo "built $OUT/$arch/singws-karafun-web ($(lipo -archs "$OUT/$arch/singws-karafun-web"))"
done
