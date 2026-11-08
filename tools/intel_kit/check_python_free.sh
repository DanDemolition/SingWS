#!/bin/bash
# Run ON THE MAC YOU WANT TO TEST (the Intel venue Mac, with the TV connected and KaraFun running) BEFORE or AFTER the show, with SingWS quit:
#   ./check_python_free.sh [/path/to/a/karaoke-song.zip]
# It launches the Rust "SingWS Shell" app itself (so the permission checks are the SHELL's own, not Terminal's) and runs the Python-free readiness test:
#   * the machine (Intel or Apple Silicon, Rosetta or native, graphics, attached displays), every bundled helper, the shell's Accessibility grant, KaraFun's UI
#   * with a song zip (any MP3+G): the one-process host spike, the song played through the Rust picture + Rust voice, and the audience display on the TV with screenshots
#     and one yes/no question on screen.
# It changes nothing in SingWS (no settings, queue or history) and plays no sound (the music engine runs headless). Results: ~/SingWS/readiness/<time>/readiness.txt and .json;
# after_show.sh adds the newest one to the zip you send back.
set -uo pipefail
APP="${SINGWS_SHELL_APP:-/Applications/SingWS Shell.app}"
SONG="${1:-}"
[[ -d "$APP" ]] || { echo "SingWS Shell.app not found at: $APP"; echo "Copy SingWS Shell.app to /Applications (or set SINGWS_SHELL_APP) and run this again."; exit 2; }
if pgrep -x "SingWS" >/dev/null 2>&1; then echo "SingWS is running: quit it first (this test opens its own windows)."; exit 2; fi
# The song must be an MP3+G zip (one .cdg and one .mp3 inside): check it BEFORE the long run so a wrong path or an MP4 song is explained here, not as a bare "could not be unpacked".
if [[ -n "$SONG" ]]; then
  [[ -f "$SONG" ]] || { echo "Song zip not found: $SONG"; echo "Check the path (drag the file into the Terminal window to paste it, with its quotes) and run again."; exit 2; }
  LIST=$(unzip -Z1 "$SONG" 2>/dev/null) || { echo "That file is not a readable zip: $SONG"; exit 2; }
  echo "$LIST" | grep -qi '\.cdg$' || { echo "That zip has no .cdg file inside, so it is not an MP3+G karaoke song: $SONG"; echo "Pick a CDG song, for example one from KARAOKE LIBRARY/CC/."; exit 2; }
  echo "$LIST" | grep -qi '\.mp3$' || { echo "That zip has no .mp3 file inside: $SONG"; exit 2; }
fi
OUT="${SINGWS_HOME:-$HOME/SingWS}/readiness/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$OUT"
echo "Shell: $APP"; lipo -archs "$APP/Contents/MacOS/SingWS Shell" 2>/dev/null | sed 's/^/Shell architectures: /'
echo "Running (a song takes about as long as the song itself)... windows will open and close; answer the question when it appears."
if [[ -n "$SONG" ]]; then
  open -W -n -a "$APP" --args --readiness-run "$OUT" --song "$SONG"
else
  open -W -n -a "$APP" --args --readiness-run "$OUT"
fi
if [[ -f "$OUT/readiness.txt" ]]; then
  echo; cat "$OUT/readiness.txt"; echo; echo "Saved: $OUT"
  grep -q '^\[fail\]' "$OUT/readiness.txt" && { echo "RESULT: something FAILED (lines starting with [fail])."; exit 1; }
  grep -q '^\[warn\]' "$OUT/readiness.txt" && echo "RESULT: no failures, but some steps could not be tested (lines starting with [warn])." || echo "RESULT: every check passed."
else
  echo "The shell wrote no report: it may have crashed (look in ~/Library/Logs/DiagnosticReports for singws-shell*.ips)."; exit 2
fi
