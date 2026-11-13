#!/bin/bash
# Build the singws-karafun-display program (KaraFun's web player in Chromium/CEF, drawn on the graphics chip) for both Mac architectures into
# native/singws_karafun_display/<arch>/ (program + the three graphics libraries Chromium loads from beside it). CEF 150 supports macOS 12.0 and newer
# (CEF 156 needs 13). It needs CEF's development files for each architecture, downloaded once into native/cef150-<arch>/ (git-ignored):
#   https://cef-builds.spotifycdn.com/cef_binary_150.0.14%2Bg7c1aa68%2Bchromium-150.0.7871.129_macosx64_minimal.tar.bz2     (Intel, sha1 c0591785...)
#   https://cef-builds.spotifycdn.com/cef_binary_150.0.14%2Bg7c1aa68%2Bchromium-150.0.7871.129_macosarm64_minimal.tar.bz2   (Apple Silicon, sha1 47baa745...)
# unpacked in place (tar -xjf). This script writes the archive.json marker the cef crate's build step wants.
#   ./rust/build_karafun_display.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target-kfdisplay}"
IDENTITY="${SINGWS_CODESIGN_IDENTITY:-SingWS Local Code Signing}"
ROOT="$(cd .. && pwd)"
OUT="$ROOT/native/singws_karafun_display"
CEFV="150.0.14+g7c1aa68+chromium-150.0.7871.129"

for pair in "arm64:aarch64-apple-darwin:macosarm64:cef150-arm64:47baa745213c938861861f0f3f65c6dd03e7254e" "x86_64:x86_64-apple-darwin:macosx64:cef150-x64:c0591785a04404e2301cb1e216b6508231445dde"; do
    IFS=: read -r arch triple plat dir sha <<<"$pair"
    sdk="$ROOT/native/$dir/cef_binary_${CEFV}_${plat}_minimal"
    [[ -d "$sdk/Release" ]] || { echo "CEF files for $arch are missing: $sdk (see the top of this script)"; exit 1; }
    printf '{\n  "type": "minimal",\n  "name": "%s",\n  "sha1": "%s"\n}' "cef_binary_${CEFV}_${plat}_minimal.tar.bz2" "$sha" > "$sdk/archive.json"
    CEF_PATH="$sdk" cargo build --release -p singws-karafun-display --target "$triple"
    mkdir -p "$OUT/$arch"
    cp "$CARGO_TARGET_DIR/$triple/release/singws-karafun-display" "$OUT/$arch/singws-karafun-display"
    for lib in libEGL.dylib libGLESv2.dylib libvk_swiftshader.dylib vk_swiftshader_icd.json; do
        cp "$sdk/Release/Chromium Embedded Framework.framework/Libraries/$lib" "$OUT/$arch/$lib"
    done
    chmod 755 "$OUT/$arch/singws-karafun-display"
    for f in "$OUT/$arch"/*.dylib "$OUT/$arch/singws-karafun-display"; do
        codesign --force --sign "$IDENTITY" "$f"
    done
    python3 ../tools/verify_macos_min_version.py --arch "$arch" --maximum 12.3 "$OUT/$arch/singws-karafun-display" | tail -1
    echo "built $OUT/$arch/singws-karafun-display ($(lipo -archs "$OUT/$arch/singws-karafun-display"))"
done
