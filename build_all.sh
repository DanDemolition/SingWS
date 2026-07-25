#!/bin/bash
# Sequential build: arm64 (dev) + x86_64 (Intel test machine), each into its own DMG.
set -euo pipefail
cd "$(dirname "$0")"
# The build venvs link against the system Python 3.14 framework. If that install
# is missing, fall back to the local copy under ~/.singws-python314 so a plain
# ./build_all.sh still works. An explicit SINGWS_DYLD_FRAMEWORK_PATH always wins.
if [[ -z "${SINGWS_DYLD_FRAMEWORK_PATH:-}" \
      && ! -f /Library/Frameworks/Python.framework/Versions/3.14/Python \
      && -f "$HOME/.singws-python314/Python.framework/Versions/3.14/Python" ]]; then
  SINGWS_DYLD_FRAMEWORK_PATH="$HOME/.singws-python314"
  # PyInstaller resolves the Python shared library by basename via
  # DYLD_LIBRARY_PATH, so point it at the directory holding the `Python` dylib
  # or Analysis fails with "Python shared library was not found".
  : "${SINGWS_DYLD_LIBRARY_PATH:=$HOME/.singws-python314/Python.framework/Versions/3.14}"
fi
if [[ -n "${SINGWS_DYLD_FRAMEWORK_PATH:-}" ]]; then
  export DYLD_FRAMEWORK_PATH="$SINGWS_DYLD_FRAMEWORK_PATH"
fi
if [[ -n "${SINGWS_DYLD_LIBRARY_PATH:-}" ]]; then
  export DYLD_LIBRARY_PATH="$SINGWS_DYLD_LIBRARY_PATH"
fi
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-${TMPDIR:-/tmp}/singws-pyinstaller}"
# Version is the single source of truth in APP_VERSION (entry script).
VER="$(grep -E '^APP_VERSION' 0.2.18.1.py | sed -E 's/.*"([^"]+)".*/\1/')"
UNIVERSAL_PY=".venv-universal/bin/python"

stage_bundle() {
  local flavor="$1"
  local stage="${TMPDIR:-/tmp}/singws-${flavor}-dmg-$$"
  rm -rf "$stage"
  mkdir -p "$stage/dist"
  ditto --norsrc --noextattr dist/SingWS.app "$stage/dist/SingWS.app"

  if [[ "$flavor" == "x86_64" ]]; then
    local frameworks="$stage/dist/SingWS.app/Contents/Frameworks"
    local resources="$stage/dist/SingWS.app/Contents/Resources"
    local binary name
    while IFS= read -r binary; do
      [[ "$(lipo -archs "$binary" 2>/dev/null || true)" == "arm64" ]] || continue
      name="${binary##*/}"
      rm -f "$binary" "$resources/$name"
    done < <(find "$frameworks" -maxdepth 1 -type f)
  fi

  codesign --force --deep --sign - \
    --entitlements SingWS.entitlements "$stage/dist/SingWS.app"
  codesign --verify --deep --strict "$stage/dist/SingWS.app"
  printf '%s\n' "$stage"
}

# Intel is cross-built by native PyInstaller from a genuinely universal Python
# runtime. Running PyInstaller itself under Rosetta breaks the ARM-only
# pkg-config helper; using the regular ARM-only .venv cannot collect Intel
# Python extensions. Refuse either mistake before changing build artifacts.
"${UNIVERSAL_PY}" tools/verify_macos_arch.py \
  --runtime --require arm64 --require x86_64

echo "========================================"
echo " BUILD START $(date)"
echo "========================================"

# Re-apply the DMG helper's custom icon + hidden extension (filesystem metadata
# that git doesn't track) so every DMG ships the styled "Open Me First" helper.
.venv/bin/python tools/make_dmg_assets.py --style-only

# ---- arm64 ----
echo ">>> [1/4] arm64 PyInstaller"
rm -rf build dist
.venv/bin/pyinstaller --noconfirm "SingWS-arm64.spec"
.venv/bin/python tools/verify_macos_arch.py \
  --bundle dist/SingWS.app --require arm64
echo ">>> arm64 app arch:"; file dist/SingWS.app/Contents/MacOS/SingWS | sed 's/^/    /'
echo ">>> [2/4] arm64 dmgbuild"
rm -f "SingWS-${VER}-arm64-installer.dmg"
ARM_STAGE="$(stage_bundle arm64)"
SINGWS_DMG_APP_ROOT="$ARM_STAGE" .venv/bin/dmgbuild \
  -s dmg_settings.py "SingWS-${VER}" "SingWS-${VER}-arm64-installer.dmg"
rm -rf "$ARM_STAGE"
echo ">>> arm64 DMG done: $(ls -lh SingWS-${VER}-arm64-installer.dmg | awk '{print $5}')"

# ---- x86_64 (Intel) ----
echo ">>> [3/4] x86_64 PyInstaller (universal venv)"
rm -rf build dist
"${UNIVERSAL_PY}" -m PyInstaller --noconfirm "SingWS-x86_64.spec"
X86_STAGE="$(stage_bundle x86_64)"
"${UNIVERSAL_PY}" tools/verify_macos_arch.py \
  --bundle "$X86_STAGE/dist/SingWS.app" --require x86_64
echo ">>> x86_64 app arch:"; file dist/SingWS.app/Contents/MacOS/SingWS | sed 's/^/    /'
echo ">>> [4/4] x86_64 dmgbuild"
rm -f "SingWS-${VER}-x86_64-installer.dmg"
SINGWS_DMG_APP_ROOT="$X86_STAGE" .venv/bin/dmgbuild \
  -s dmg_settings.py "SingWS-${VER}" "SingWS-${VER}-x86_64-installer.dmg"
rm -rf "$X86_STAGE"
echo ">>> x86_64 DMG done: $(ls -lh SingWS-${VER}-x86_64-installer.dmg | awk '{print $5}')"

echo "========================================"
echo " BUILD COMPLETE $(date)"
ls -lh SingWS-${VER}-*-installer.dmg | sed 's/^/    /'
echo "========================================"
