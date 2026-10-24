#!/bin/zsh
# Build the Rust Qt Quick shell (singws-shell) for arm64 and x86_64 against the official universal Qt kit, then lipo them together.
#   Qt kit:   aqt install-qt -O ~/Qt mac desktop 6.12.0 clang_64 --archives qtbase qtdeclarative qtsvg -m qtshadertools
#   (aqtinstall from PyPI; Homebrew no longer supports Intel Macs, and its Qt is arm64 only.)
# Output: $CARGO_TARGET_DIR/universal/singws-shell. The binary finds Qt through an rpath into $QT_ROOT/lib; bundling and signing is a later step.
set -e
cd "$(dirname "$0")"
export QT_ROOT="${QT_ROOT:-$HOME/Qt/6.12.0/macos}"
export QMAKE="$QT_ROOT/bin/qmake6" PATH="$QT_ROOT/bin:$PATH" MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target-qt}"
[[ -x "$QMAKE" ]] || { echo "Qt kit not found at $QT_ROOT" >&2; exit 2; }
for t in aarch64-apple-darwin x86_64-apple-darwin; do
  cargo build --release -p singws-shell --target $t
done
mkdir -p "$CARGO_TARGET_DIR/universal"
lipo -create -output "$CARGO_TARGET_DIR/universal/singws-shell" \
  "$CARGO_TARGET_DIR/aarch64-apple-darwin/release/singws-shell" "$CARGO_TARGET_DIR/x86_64-apple-darwin/release/singws-shell"
echo "universal: $(lipo -archs "$CARGO_TARGET_DIR/universal/singws-shell")  $(shasum -a 256 "$CARGO_TARGET_DIR/universal/singws-shell" | cut -c1-16)"
