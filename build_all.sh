#!/bin/bash
# Build the native macOS installer for the Mac running this command.
# mpv/libmpv/MoltenVK are architecture-specific Homebrew runtimes, so Intel
# and Apple Silicon installers must be produced on their matching hardware.
set -euo pipefail

cd "$(dirname "$0")"

case "$(uname -m)" in
  arm64)
    echo ">>> Native Apple Silicon release build"
    exec ./build_singws_mac_arm64.sh
    ;;
  x86_64)
    echo ">>> Native Intel release build"
    exec ./build_singws_mac_intel.sh
    ;;
  *)
    echo "Unsupported macOS architecture: $(uname -m)"
    exit 1
    ;;
esac
