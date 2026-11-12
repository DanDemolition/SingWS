#!/bin/bash
# Make lighter H.264 copies of the background video loops (the Photo-JPEG .mov files cost a lot of CPU to decode).
# The originals are never touched; the copies go to a separate folder you then point Settings > Background Video at.
# Runs at the lowest priority with one encoder thread so it adds almost no load; do not run it during a show.
#
#   tools/convert_bg_loops.sh "/path/to/VJ Loops"                  # writes "/path/to/VJ Loops H264"
#   tools/convert_bg_loops.sh "/path/to/VJ Loops" "/path/to/out"   # explicit output folder
#   tools/convert_bg_loops.sh --dry-run "/path/to/VJ Loops"        # list what it would do
#
# Safe to re-run: a finished copy is skipped; a half-written one is rebuilt. Needs ffmpeg (brew install ffmpeg).
set -euo pipefail

DRY=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY=1; shift; fi
SRC="${1:-}"
if [[ -z "$SRC" || ! -d "$SRC" ]]; then
    echo "usage: $0 [--dry-run] <folder of loops> [output folder]" >&2
    exit 2
fi
SRC="${SRC%/}"
OUT="${2:-$SRC H264}"

FFMPEG="$(command -v ffmpeg || true)"
for c in /opt/homebrew/bin/ffmpeg /usr/local/bin/ffmpeg; do
    [[ -z "$FFMPEG" && -x "$c" ]] && FFMPEG="$c"
done
if [[ -z "$FFMPEG" ]]; then
    echo "ffmpeg was not found. Install it first: brew install ffmpeg" >&2
    exit 3
fi
FFPROBE="$(dirname "$FFMPEG")/ffprobe"

[[ "$DRY" == 1 ]] || mkdir -p "$OUT"
made=0; skipped=0; failed=0
# Files with these extensions, one level deep and below (spaces in names are fine).
while IFS= read -r -d '' f; do
    rel="${f#"$SRC"/}"
    dest="$OUT/${rel%.*}.mp4"
    if [[ -s "$dest" && "$dest" -nt "$f" ]]; then
        skipped=$((skipped + 1)); continue
    fi
    if [[ "$DRY" == 1 ]]; then echo "would convert: $rel"; continue; fi
    mkdir -p "$(dirname "$dest")"
    part="$dest.partial.mp4"
    echo "converting: $rel"
    # nice -n 20 + one thread: slow, but it takes almost nothing from anything else.
    # Same size and frame rate as the source; yuv420p so every player takes it; a keyframe every 30 frames so loops restart cleanly;
    # the sound (these loops are muted anyway) is kept as AAC because the player uses an audio stream as its clock when one exists.
    if nice -n 20 "$FFMPEG" -nostdin -v error -y -i "$f" \
        -map 0:v:0 -map '0:a:0?' \
        -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -threads 1 -g 30 -movflags +faststart \
        -c:a aac -b:a 128k \
        "$part"; then
        mv "$part" "$dest"
        made=$((made + 1))
    else
        rm -f "$part"
        echo "FAILED: $rel" >&2
        failed=$((failed + 1))
    fi
done < <(find "$SRC" -type f \( -iname '*.mov' -o -iname '*.mp4' -o -iname '*.m4v' -o -iname '*.avi' -o -iname '*.mkv' \) ! -name '._*' -print0 | sort -z)

echo "done: $made converted, $skipped already done, $failed failed. Output: $OUT"
if [[ "$DRY" == 0 && "$made" -gt 0 ]]; then
    echo "Originals were not changed. Point Settings > Background Video at: $OUT"
fi
[[ "$failed" == 0 ]]
