#!/bin/bash
# make_karafun_helpers.sh <SingWS.app>: Chromium, run inside an app bundle, starts its page, graphics and other processes from the standard helper bundles
# "<name> Helper.app", "<name> Helper (Renderer).app", "(GPU)", "(Plugin)", "(Alerts)" next to each other in Contents/Frameworks (not from the path the
# program asks for: without them the page process cannot start and KaraFun shows a black screen). Each is a copy of singws-karafun-display, hidden from the
# Dock (LSUIElement), with Chromium's graphics libraries beside it (links to the ones in Contents/Frameworks). Run it on the staged app BEFORE signing.
set -euo pipefail
app="$1"
frameworks="$app/Contents/Frameworks"
program="$frameworks/singws-karafun-display"
[[ -f "$program" ]] || { echo "no singws-karafun-display in $frameworks"; exit 1; }
version=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$app/Contents/Info.plist")
base="SingWS KaraFun Helper"
for variant in "" " (Renderer)" " (GPU)" " (Plugin)" " (Alerts)"; do
    name="$base$variant"
    bundle="$frameworks/$name.app"
    id="com.singws.karafun.helper$(echo "$variant" | tr -d ' ()' | tr 'A-Z' 'a-z' | sed 's/^./.&/')"
    rm -rf "$bundle"
    mkdir -p "$bundle/Contents/MacOS"
    cp "$program" "$bundle/Contents/MacOS/$name"
    for lib in libEGL.dylib libGLESv2.dylib libvk_swiftshader.dylib vk_swiftshader_icd.json; do
        ln -s "../../../$lib" "$bundle/Contents/MacOS/$lib"
    done
    cat > "$bundle/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>$name</string>
<key>CFBundleIdentifier</key><string>$id</string>
<key>CFBundleName</key><string>$name</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>$version</string>
<key>CFBundleVersion</key><string>$version</string>
<key>LSUIElement</key><true/>
</dict></plist>
PLIST
done
echo "made the KaraFun helper bundles in $frameworks"
