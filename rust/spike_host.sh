#!/bin/zsh
# Stage E "no-Python host" spike (docs/design/stage-e-no-python-gap.md, Step 3). A THROWAWAY bundle "SingWS Spike <arch>.app", bundle id com.singws.spike,
# its own SINGWS_HOME; it never touches /Applications/SingWS.app, /Applications/SingWS Shell.app or ~/SingWS. It launches the Rust shell in --selfcheck
# mode through LaunchServices (so TCC sees the bundle, not this terminal), which loads the video bridge + BGM engine in-process, hands two Qt Quick
# windows to the bridge, plays ~5 s through the BGM engine headless, and writes a report.
#   Usage: spike_host.sh [--arch arm64|x86_64|both] [--out DIR] [--no-build] [--play SONG.cdg]
#   --play: after the selfcheck, also run the SECOND spike (`singws-shell --play-demo SONG.cdg SONG.mp3`, see docs/design/stage-e-no-python-host-spike-2.md):
#   a real CDG+MP3 song through the Rust video bridge and the Rust karaoke voice, HEADLESS audio (silent), ~45 s; the song files are only read.
#   Needs: the Qt kit (build_shell.sh), native/mpv_runtime/artifacts/<arch>/Frameworks, ffmpeg (test media), the local signing identity.
#   Opens two small windows for ~10 s. Opens NO permission prompt by design (no capture, no AX, headless audio); if macOS shows one, do not click it
#   and tell whoever runs this.
set -e
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
ARCHS=(); OUT=""; BUILD=1; PLAY=""
while [[ $# -gt 0 ]]; do case "$1" in
  --arch) [[ "$2" == both ]] && ARCHS=(arm64 x86_64) || ARCHS=("$2"); shift 2;;
  --out) OUT="$2"; shift 2;;
  --no-build) BUILD=0; shift;;
  --play) PLAY="$2"; shift 2;;
  *) echo "unknown argument $1" >&2; exit 2;; esac; done
[[ ${#ARCHS[@]} -gt 0 ]] || ARCHS=("$(uname -m)")
[[ -n "$OUT" ]] || OUT="$(mktemp -d "${TMPDIR:-/tmp}/singws-spike.XXXXXX")"
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
export QT_ROOT="${QT_ROOT:-$HOME/Qt/6.12.0/macos}"
export QMAKE="$QT_ROOT/bin/qmake6" PATH="$HOME/.cargo/bin:$QT_ROOT/bin:$PATH" MACOSX_DEPLOYMENT_TARGET=12.3
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/singws-rust-target-spike}"
IDENTITY="${SIGN_IDENTITY:-SingWS Local Code Signing}"
[[ -x "$QMAKE" ]] || { echo "Qt kit not found at $QT_ROOT" >&2; exit 2; }
command -v ffmpeg >/dev/null || { echo "ffmpeg is needed to make the test media" >&2; exit 2; }

# --- test media (generated; no library path involved) ---
ffmpeg -loglevel error -y -f lavfi -i "testsrc=duration=12:size=640x360:rate=25" -pix_fmt yuv420p -c:v libx264 -an "$OUT/test.mp4"
if ffmpeg -loglevel error -y -f lavfi -i "sine=frequency=440:duration=8" -c:a libmp3lame -b:a 128k "$OUT/tone.mp3" 2>/dev/null; then AUDIO="$OUT/tone.mp3"
else ffmpeg -loglevel error -y -f lavfi -i "sine=frequency=440:duration=8" "$OUT/tone.wav"; AUDIO="$OUT/tone.wav"; fi

REPORT="$OUT/report.md"
{ echo "# SingWS no-Python host spike report"; echo; echo "- date: $(date -u +%FT%TZ)"
  echo "- host: $(sysctl -n machdep.cpu.brand_string), uname -m = $(uname -m), macOS $(sw_vers -productVersion)"
  echo "- Rosetta runs x86_64 here: $(arch -x86_64 /usr/bin/true 2>/dev/null && echo yes || echo no)"
  echo "- output folder: $OUT"; } > "$REPORT"

for ARCH in $ARCHS; do
  case $ARCH in arm64) T=aarch64-apple-darwin;; x86_64) T=x86_64-apple-darwin;; *) echo "bad arch $ARCH" >&2; exit 2;; esac
  [[ $BUILD == 1 ]] && cargo build --release -p singws-shell --target $T
  A="$OUT/SingWS Spike $ARCH.app"; rm -rf "$A"; mkdir -p "$A/Contents/"{MacOS,Resources,Frameworks}
  cp "$CARGO_TARGET_DIR/$T/release/singws-shell" "$A/Contents/MacOS/SingWS Spike"
  cat > "$A/Contents/Info.plist" <<'P'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>SingWS Spike</string>
<key>CFBundleDisplayName</key><string>SingWS Spike</string>
<key>CFBundleIdentifier</key><string>com.singws.spike</string>
<key>CFBundleExecutable</key><string>SingWS Spike</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>0.0.1</string>
<key>CFBundleShortVersionString</key><string>0.0.1</string>
<key>LSMinimumSystemVersion</key><string>12.3</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
P
  # libmpv + its dependencies (the bridge finds it through ../Frameworks; no SINGWS_LIBMPV override is used)
  # copy ONLY dylibs: a loose text file (SHA256SUMS) in Contents/Frameworks fails strict signing ("main executable failed strict validation");
  # the Objective-C bridge is not used by the Rust host
  for D in "$ROOT/native/mpv_runtime/artifacts/$ARCH/Frameworks/"*.dylib; do
    [[ "$(basename "$D")" == libsingws_mpv_bridge.dylib ]] || cp -p "$D" "$A/Contents/Frameworks/"
  done
  [[ -z "$(find "$A/Contents/Frameworks" -type f ! -name '*.dylib')" ]] || { echo "Contents/Frameworks holds a non-dylib file" >&2; exit 3; }
  QML_COPY="$(mktemp -d)/qml"; cp -R crates/singws-shell/qml "$QML_COPY"   # qmlimportscanner skips dot-folder paths such as .claude worktrees
  "$QT_ROOT/bin/macdeployqt" "$A" -qmldir="$QML_COPY" >/dev/null 2>&1 || true
  [[ -d "$A/Contents/Resources/qml/QtQuick/Controls" ]] || { echo "macdeployqt bundled no QML imports" >&2; exit 3; }
  rm -rf "$A/Contents/PlugIns/sqldrivers"
  codesign --force --deep --sign "$IDENTITY" "$A"; codesign --verify --deep --strict "$A"

  [[ $BUILD == 1 ]] && cargo build --release -p singws-mac --bin singws-mac-probe --target $T
  PROBE="$(arch -$ARCH "$CARGO_TARGET_DIR/$T/release/singws-mac-probe" --json 2>&1 | tr '\n' ' ' | cut -c1-600)"
  H="$OUT/home-$ARCH"; rm -rf "$H"; mkdir -p "$H"; J="$OUT/selfcheck-$ARCH.json"; rm -f "$J"
  RC=0
  open -n -W --arch $ARCH --env SINGWS_HOME="$H" --stdout "$OUT/stdout-$ARCH.txt" --stderr "$OUT/stderr-$ARCH.txt" "$A" \
    --args --selfcheck --selfcheck-report "$J" --selfcheck-video "$OUT/test.mp4" --selfcheck-audio "$AUDIO" || RC=$?

  if [[ -n "$PLAY" ]]; then
    MP3="${PLAY%.cdg}.mp3"; PJ="$OUT/playdemo-$ARCH.json"; rm -f "$PJ" "$OUT/frame-$ARCH.rgba"
    [[ -f "$PLAY" && -f "$MP3" ]] || { echo "--play needs SONG.cdg and SONG.mp3 side by side" >&2; exit 2; }
    PRC=0
    open -n -W --arch $ARCH --env SINGWS_HOME="$H" --stdout "$OUT/play-stdout-$ARCH.txt" --stderr "$OUT/play-stderr-$ARCH.txt" "$A" \
      --args --play-demo "$PLAY" "$MP3" --play-demo-report "$PJ" --play-demo-frame "$OUT/frame-$ARCH.rgba" || PRC=$?
    echo; echo "play-demo $ARCH exit $PRC, report $([[ -f $PJ ]] && echo "$PJ" || echo MISSING)" | tee -a "$REPORT"
  fi

  # --- identity / TCC evidence ---
  E="$A/Contents/MacOS/SingWS Spike"
  DR1="$(codesign -d -r- "$A" 2>&1 | grep designated || true)"
  # a rebuild: change the bundle (new version string -> new cdhash) on a COPY, sign it with the same identity and ad hoc, compare designated requirements
  C="$OUT/rebuild-copy.app"; rm -rf "$C"; cp -R "$A" "$C"
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion 0.0.2" "$C/Contents/Info.plist"
  codesign --force --deep --sign "$IDENTITY" "$C" 2>/dev/null || true
  DR2="$(codesign -d -r- "$C" 2>&1 | grep designated || true)"      # rebuilt bundle, same identity
  codesign --force --deep --sign - "$C" 2>/dev/null || true
  DR3="$(codesign -d -r- "$C" 2>&1 | grep designated || true)"      # rebuilt bundle, ad hoc
  rm -rf "$C"
  TCC="$(sqlite3 "file:$HOME/Library/Application Support/com.apple.TCC/TCC.db?mode=ro" "select service,auth_value,auth_reason from access where client='com.singws.spike'" 2>&1 | head -5)"
  {
    echo; echo "## $ARCH"
    echo "- bundle: \`$A\` ($(lipo -archs "$E"), $(du -sh "$A" | cut -f1)), bundle id com.singws.spike, SINGWS_HOME=$H"
    echo "- signing: $(codesign -dvv "$A" 2>&1 | grep -E '^(Authority|Signature|TeamIdentifier)' | tr '\n' ';')"
    echo "- launch via LaunchServices (\`open -n -W --arch $ARCH\`) exit code: $RC; report file: $([[ -f $J ]] && echo present || echo MISSING)"
    echo "- designated requirement, local identity: \`$DR1\`"
    echo "- designated requirement after the binary changed, same identity: \`$DR2\` -> $([[ "$DR1" == "$DR2" ]] && echo 'IDENTICAL (a TCC grant keyed to it survives a rebuild)' || echo 'DIFFERENT')"
    echo "- designated requirement, ad hoc signing: \`$DR3\` (cdhash-based: changes every build)"
    echo "- TCC.db rows for com.singws.spike (read-only query): ${TCC:-none}"
    echo "- stdout/stderr: $OUT/stdout-$ARCH.txt, $OUT/stderr-$ARCH.txt"
    echo; echo "### checks"
    if [[ -f $J ]]; then python3 -I - "$J" <<'PY'
import json,sys
for c in json.load(open(sys.argv[1]))["checks"]:
    tag = 'PEND' if str(c['detail'].get('status','')).startswith('pending') else ('OK  ' if c['ok'] else 'FAIL')
    print(f"- {tag} {c['name']}: {json.dumps(c['detail'])}")
PY
    else echo "- FAIL no report written (the app did not finish; see stderr)"; fi
    echo "- same shim run as a plain process from this terminal (\`singws-mac-probe --json\`, responsible process = the terminal/IDE that ran this script): \`$PROBE\`"
  } >> "$REPORT"
done
echo "report: $REPORT"; cat "$REPORT"
