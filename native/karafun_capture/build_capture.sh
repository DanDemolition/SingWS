#!/bin/zsh
set -euo pipefail
here=${0:A:h}
sdk=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk
arch=${1:-arm64}
CLANG_MODULE_CACHE_PATH=${TMPDIR:-/private/tmp}/singws-clang-cache swiftc \
  -parse-as-library -swift-version 5 -emit-library \
  -target "${arch}-apple-macos12.3" -sdk "$sdk" \
  "$here/capture.swift" -o "$here/libsingws_karafun_capture-${arch}.dylib" \
  -framework ScreenCaptureKit
cp "$here/libsingws_karafun_capture-${arch}.dylib" \
  "$here/libsingws_karafun_capture.dylib"
