#!/bin/bash
# KaraFun GPU test: run ON THE INTEL MAC (no show needed, SingWS closed). It starts the KaraFun display program by itself twice, once the way SingWS
# 1.0.3.7 does (the Mac's default graphics chip) and once the way 1.0.3.6 did (forcing the AMD chip), shows a small picture window for ~15 s each, and writes
# what it saw to a folder on the Desktop: frames per second, the browser's own graphics report, anything that stopped.
#   bash karafun_gpu_test.sh
# Needs Chromium already downloaded (open SingWS once, queue a KaraFun song, answer Yes to the download).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/singws-karafun-display"
[ -x "$BIN" ] || BIN="/Applications/SingWS.app/Contents/Frameworks/singws-karafun-display"
[ -x "$BIN" ] || { echo "singws-karafun-display not found"; exit 1; }
"$BIN" installed || { echo "Chromium is not downloaded yet: open SingWS, queue a KaraFun song, say Yes to the download, then run this again."; exit 1; }
OUT="$HOME/Desktop/karafun-gpu-test-$(date +%H%M%S)"
mkdir -p "$OUT"
{ echo "machine: $(uname -m) macOS $(sw_vers -productVersion)"; system_profiler SPDisplaysDataType 2>/dev/null | grep -E "Chipset Model|Resolution|Metal"; } > "$OUT/machine.txt"

run() {   # run <name> <env assignment or ->
    local name="$1" setting="$2"
    echo; echo "=== $name: starting (about 40 s). LOOK at the small window that appears in the top left of your screen."
    rm -f "$OUT/$name.fifo"; mkfifo "$OUT/$name.fifo"
    ( if [ "$setting" = "-" ]; then "$BIN" --profile "$OUT/profile-$name" < "$OUT/$name.fifo" > "$OUT/$name.out" 2> "$OUT/$name.err";
      else env "$setting" "$BIN" --profile "$OUT/profile-$name" < "$OUT/$name.fifo" > "$OUT/$name.out" 2> "$OUT/$name.err"; fi & )
    exec 9> "$OUT/$name.fifo"
    for i in $(seq 1 60); do sleep 1; grep -q '"type":"ready"' "$OUT/$name.out" 2>/dev/null && break; done
    echo "    page ready after ${i}s"
    echo '{"cmd":"screencast","on":true,"width":1280,"height":720}' >&9
    echo '{"cmd":"window","on":true,"x":60,"y":60,"w":640,"h":360}' >&9
    sleep 16
    echo '{"cmd":"stats"}' >&9; sleep 2
    echo '{"cmd":"window","on":false}' >&9; echo '{"cmd":"quit"}' >&9; sleep 4
    exec 9>&-
    echo "    frames:  $(grep -o '"frames":"[^"]*"' "$OUT/$name.out" | tail -1)"
    grep -q '"state":"no_frames"' "$OUT/$name.out" && echo "    !! NO FRAMES reached the window (black picture)"
    grep -q "page stopped" "$OUT/$name.err" && echo "    !! the page process stopped"
    grep '"type":"log"' "$OUT/$name.out" | grep diag | sed 's/.*"message":"//; s/","type":"log".*//; s/\\"/"/g' | sed 's/^/    /'
}

run default -
run amd-forced SINGWS_KARAFUN_GPU=high
cp "$HOME/SingWS/KaraFun-Chromium/chromium.log" "$OUT/" 2>/dev/null
echo; echo "Done. Everything is in: $OUT"
echo "Tell me for EACH run: did the small window show the KaraFun picture or stay black? Then send me that folder (zip it)."
