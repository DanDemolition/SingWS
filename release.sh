#!/bin/bash
# One-command release for SingWS.
#
#   ./release.sh          # auto-increment patch (0.2.18.1 -> 0.2.18.2)
#   ./release.sh 0.3.0    # release a specific version
#
# Steps: run tests -> bump version (APP_VERSION + spec CFBundle) -> build all
# three DMGs (arm64, Intel, universal) -> regenerate docs/release.json with real
# size+sha256 -> commit + tag, push tag -> draft GitHub release, upload + verify
# the DMGs, publish -> push main. Auto-update clients pick the release up from
# docs/release.json on GitHub Pages, so main is pushed LAST: the manifest must
# never go live before the installers it points at are downloadable.
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
echo ">>> [1/7] running test suite"
SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS=1 $PY -m pytest test_*.py -q

# 2) Bump (or set) the version. This writes APP_VERSION + the spec CFBundle
#    strings so the built app and DMG names use the new version.
if [ -n "$EXPLICIT_VERSION" ]; then
  NEW_VER="$($PY tools/release_version.py --set "$EXPLICIT_VERSION")"
else
  NEW_VER="$($PY tools/release_version.py --bump)"
fi
TAG="v$NEW_VER"
echo ">>> [2/7] version: $CUR_VER -> $NEW_VER (tag $TAG)"

if git rev-parse "$TAG" >/dev/null 2>&1 || gh release view "$TAG" >/dev/null 2>&1; then
  echo "!! $TAG already exists — bump to a new version or delete the old release/tag first."
  echo "   (reverting the version bump)"
  git checkout -- 0.2.18.1.py SingWS-universal.spec SingWS-x86_64.spec SingWS-arm64.spec 2>/dev/null || true
  exit 1
fi

# 3) Build all three flavors (each script reads VER from APP_VERSION).
echo ">>> [3/7] building arm64 + Intel"
./build_all.sh
echo ">>> [3/7] building universal"
./build_universal.sh

DMG_ARM="SingWS-$NEW_VER-arm64-installer.dmg"
DMG_X86="SingWS-$NEW_VER-x86_64-installer.dmg"
DMG_UNI="SingWS-$NEW_VER-universal-installer.dmg"
for d in "$DMG_ARM" "$DMG_X86" "$DMG_UNI"; do
  [ -f "$d" ] || { echo "!! expected DMG not found: $d"; exit 1; }
done

# 4) Regenerate the auto-update manifest from the freshly built DMGs.
echo ">>> [4/7] writing docs/release.json"
$PY tools/write_manifest.py "$NEW_VER"

# 5) Commit the version bump + manifest and tag, but push ONLY the tag.
#    docs/release.json must not reach main (= GitHub Pages) until the DMGs are
#    actually downloadable, or auto-update clients get offered 404s.
echo ">>> [5/7] commit + tag (pushing tag only; main is pushed last)"
git add 0.2.18.1.py SingWS-universal.spec SingWS-x86_64.spec SingWS-arm64.spec docs/release.json
git commit -m "Release $TAG"
git tag "$TAG"
git push origin "$TAG"

# 6) Create the release as a draft, upload the DMGs (retrying flaky uploads),
#    verify every asset landed at full size, then publish. Until this step
#    finishes, nothing is public and clients are unaffected.
echo ">>> [6/7] creating GitHub release $TAG (draft) + uploading DMGs"
gh release create "$TAG" \
  --draft \
  --title "SingWS $NEW_VER" \
  --notes "Automated release $TAG."

for d in "$DMG_ARM" "$DMG_X86" "$DMG_UNI"; do
  uploaded=""
  for attempt in 1 2 3; do
    if gh release upload "$TAG" "$d" --clobber; then uploaded=1; break; fi
    echo "   upload of $d failed (attempt $attempt/3), retrying in 10s..."
    sleep 10
  done
  if [ -z "$uploaded" ]; then
    echo "!! could not upload $d after 3 attempts."
    echo "   The release is still an UNPUBLISHED DRAFT and main was not pushed, so"
    echo "   auto-update clients are unaffected. To finish by hand:"
    echo "     gh release upload $TAG $DMG_ARM $DMG_X86 $DMG_UNI --clobber"
    echo "     gh release edit $TAG --draft=false --latest"
    echo "     git push origin main"
    exit 1
  fi
done

for d in "$DMG_ARM" "$DMG_X86" "$DMG_UNI"; do
  local_size="$(stat -f%z "$d")"
  remote_size="$(gh release view "$TAG" --json assets --jq ".assets[] | select(.name == \"$d\") | .size")"
  if [ "$local_size" != "$remote_size" ]; then
    echo "!! uploaded asset $d is ${remote_size:-missing} bytes, expected $local_size."
    echo "   Re-upload it (gh release upload $TAG $d --clobber), then publish and"
    echo "   push main as printed above. The draft release is not public yet."
    exit 1
  fi
done

gh release edit "$TAG" --draft=false --latest

# 7) Only now go live: push main so GitHub Pages serves the new manifest, and
#    confirm an advertised download URL actually resolves.
echo ">>> [7/7] pushing main (manifest goes live)"
git push origin main || {
  echo "!! push to main failed. The release IS published, but clients still see"
  echo "   the previous manifest. Fix and re-run: git push origin main"
  exit 1
}

http_code="$(curl -sIL -o /dev/null -w '%{http_code}' \
  "https://github.com/DanDemolition/SingWS/releases/download/$TAG/$DMG_ARM")"
if [ "$http_code" != "200" ]; then
  echo "!! warning: download check for $DMG_ARM returned HTTP $http_code —"
  echo "   verify the release assets manually before trusting auto-update."
fi

echo "========================================"
echo " RELEASED SingWS $NEW_VER"
echo "   https://github.com/DanDemolition/SingWS/releases/tag/$TAG"
echo "   Auto-update clients see it via docs/release.json (GitHub Pages)."
echo "========================================"
