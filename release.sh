#!/bin/bash
# One-command release for SingWS.
#
#   ./release.sh          # auto-increment patch (0.2.18.1 -> 0.2.18.2)
#   ./release.sh 0.3.0    # release a specific version
#
# Steps: run tests -> bump version (APP_VERSION + spec CFBundle) -> build all
# three DMGs (arm64, Intel, universal) -> regenerate docs/release.json with real
# size+sha256 -> commit + tag -> push -> create the GitHub release with the DMGs.
# Auto-update clients pick it up from docs/release.json on GitHub Pages.
#
# Plain ./build_all.sh and ./build_universal.sh remain non-publishing test builds.
set -euo pipefail
cd /Users/daniel/Documents/SingWS

PY=".venv/bin/python"
EXPLICIT_VERSION="${1:-}"

echo "========================================"
echo " SingWS RELEASE  $(date)"
echo "========================================"

# Fail early if the release tools aren't where we expect.
[ -x "$PY" ] || { echo "!! $PY not found"; exit 1; }
command -v gh >/dev/null || { echo "!! gh (GitHub CLI) not found"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "!! gh not authenticated (run: gh auth login)"; exit 1; }

CUR_VER="$($PY tools/release_version.py --current)"
echo ">>> current version: $CUR_VER"

# 1) Tests first — never ship a version that fails the suite. (Runs on current
#    code; version-independent, so do it before the bump.)
echo ">>> [1/6] running test suite"
SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS=1 $PY -m pytest test_*.py -q

# 2) Bump (or set) the version. This writes APP_VERSION + the spec CFBundle
#    strings so the built app and DMG names use the new version.
if [ -n "$EXPLICIT_VERSION" ]; then
  NEW_VER="$($PY tools/release_version.py --set "$EXPLICIT_VERSION")"
else
  NEW_VER="$($PY tools/release_version.py --bump)"
fi
TAG="v$NEW_VER"
echo ">>> [2/6] version: $CUR_VER -> $NEW_VER (tag $TAG)"

if git rev-parse "$TAG" >/dev/null 2>&1 || gh release view "$TAG" >/dev/null 2>&1; then
  echo "!! $TAG already exists — bump to a new version or delete the old release/tag first."
  echo "   (reverting the version bump)"
  git checkout -- 0.2.18.1.py SingWS-universal.spec SingWS-x86_64.spec SingWS-arm64.spec 2>/dev/null || true
  exit 1
fi

# 3) Build all three flavors (each script reads VER from APP_VERSION).
echo ">>> [3/6] building arm64 + Intel"
./build_all.sh
echo ">>> [3/6] building universal"
./build_universal.sh

DMG_ARM="SingWS-$NEW_VER-arm64-installer.dmg"
DMG_X86="SingWS-$NEW_VER-x86_64-installer.dmg"
DMG_UNI="SingWS-$NEW_VER-universal-installer.dmg"
for d in "$DMG_ARM" "$DMG_X86" "$DMG_UNI"; do
  [ -f "$d" ] || { echo "!! expected DMG not found: $d"; exit 1; }
done

# 4) Regenerate the auto-update manifest from the freshly built DMGs.
echo ">>> [4/6] writing docs/release.json"
$PY tools/write_manifest.py "$NEW_VER"

# 5) Commit the version bump + manifest, tag, and push.
echo ">>> [5/6] commit + tag + push"
git add 0.2.18.1.py SingWS-universal.spec SingWS-x86_64.spec SingWS-arm64.spec docs/release.json
git commit -m "Release $TAG"
git tag "$TAG"
git push origin main
git push origin "$TAG"

# 6) Create the GitHub release with the DMGs (tag already pushed above).
echo ">>> [6/6] creating GitHub release $TAG"
gh release create "$TAG" \
  "$DMG_ARM" "$DMG_X86" "$DMG_UNI" \
  --title "SingWS $NEW_VER" \
  --notes "Automated release $TAG." \
  --latest

echo "========================================"
echo " RELEASED SingWS $NEW_VER"
echo "   https://github.com/DanDemolition/SingWS/releases/tag/$TAG"
echo "   Auto-update clients see it via docs/release.json (GitHub Pages)."
echo "========================================"
