# SingWS CDG Rendering Audit - 2026-05-31

## Findings

1. Fullscreen CDG paints were re-scaling the 300x216 CDG image during every
   paint event. A single lyric frame can be painted more than once by Qt, so
   scaling inside `paintEvent()` amplified CPU cost and made frame pacing worse.

2. Both audience and preview widgets copied every incoming `QImage`. CDG frames
   are already immutable/shared at that point, so the copies were avoidable.

3. CDG scroll packets used nested Python per-pixel loops across the full bitmap.
   Scroll-heavy discs could spike the UI/render path.

4. A prior pass already reduced unnecessary redraws by emitting CDG frames only
   when the decoded bitmap generation changes.

## Implemented

- Added `cdg_quality_mode` setting:
  - `standard`: fast scaling, lower CPU.
  - `high`: smooth scaling and smooth pixmap paint hints.
- Moved CDG scaling out of `paintEvent()` into a cached scaled pixmap.
- Invalidates the scaled cache only when frame, widget size, stretch mode, or
  quality mode changes.
- Avoids copying incoming CDG `QImage` objects in audience and preview widgets.
- Optimized CDG scroll packets with row-slice copies instead of per-pixel loops.
- Added `[CDG-RENDER]` diagnostics every 300 scaled audience frames with average
  scale cost.

## Benchmarks

Synthetic CDG/QImage scaling benchmark, 300 frames:

- 1280x720 Standard: 0.7962 ms/frame
- 1280x720 High: 0.8034 ms/frame
- 1920x1080 Standard: 1.7717 ms/frame
- 1920x1080 High: 1.6017 ms/frame

The main runtime win is cache placement: scaling now happens once per new CDG
frame/size instead of on every repaint.

Synthetic scroll-packet bitmap benchmark:

- old nested pixel loop: 5.1117 ms/scroll packet
- new row-slice copy: 0.0537 ms/scroll packet
- improvement: about 95x faster for scroll packets

Decoder stress check:

- 9000 synthetic scroll packets to 30.0 s: 614.12 ms total.

## Remaining Live Verification

Needs a representative CDG+MP3 file to measure real frame emission cadence,
paint frequency, CPU/RAM, and audio/video sync during playback in both quality
modes.
