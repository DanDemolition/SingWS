#!/bin/zsh
set -euo pipefail
here=${0:A:h}
sdk=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk
arch=${1:-arm64}
extra_flags=()
# Apple Silicon CLT omits the x86_64 Swift compatibility archives. The
# ScreenCaptureKit bridge only targets macOS 12.3+, so those shims are unneeded.
if [[ "$arch" == x86_64 ]]; then
  extra_flags+=(-runtime-compatibility-version none)
fi
CLANG_MODULE_CACHE_PATH=${TMPDIR:-/private/tmp}/singws-clang-cache swiftc \
  -parse-as-library -swift-version 5 -emit-library \
  "${extra_flags[@]}" \
  -target "${arch}-apple-macos12.3" -sdk "$sdk" \
  "$here/capture.swift" -o "$here/libsingws_karafun_capture-${arch}.dylib" \
  -framework ScreenCaptureKit
cp "$here/libsingws_karafun_capture-${arch}.dylib" \
  "$here/libsingws_karafun_capture.dylib"
