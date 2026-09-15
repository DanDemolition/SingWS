#!/bin/bash
# Build the native macOS installer. SingWS 2.0 is Apple Silicon (arm64),
# macOS 15+ only. Intel installers are built from the 1.x (0.4.7.x) checkout.
set -euo pipefail

cd "$(dirname "$0")"

case "$(uname -m)" in
  arm64)
    echo ">>> Native Apple Silicon release build"
    exec ./build_singws_mac_arm64.sh
    ;;
  *)
    echo "SingWS 2.0 builds on Apple Silicon only (found $(uname -m))."
    echo "Build Intel installers from the 1.x checkout."
    exit 1
    ;;
esac
