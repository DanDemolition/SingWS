# SingWS CDG Rendering Audit

## 2026-07-10 — Intermittent line-start artifacts (root cause + fix)

Symptom: some tracks briefly showed garbage at the start of a new lyric line
(random blocks, partial characters, pieces of the previous line).

Confirmed root cause: the direct-QImage presentation path (`_CdgAdapter`)
stepped `CdgFileReader.move_to_next_frame()`, an iterator built for the
appsrc pull model that decodes ahead to the NEXT visible change and stamps
frames with a one-display-tick (16.7 ms) duration. Presented directly, the
current frame "expired" 16 ms into any idle gap between lyric lines, so the
adapter fetched the next change frame — future packets — and displayed the
upcoming line's first packet batch (partial tiles, wipes, palette swaps)
early. Measured on a real rip (CC - Billy Joel - Pressure): 687 of 5275
published frames were >33 ms early, worst 10.9 s ahead of playback.

Fix: new `CdgFileReader.advance_to_position_ms()` applies exactly the packets
due at or before the playback position (never past it) and re-snapshots only
when the image changed; the adapter now uses it, so a future or partial state
can never be published. Also fixed while auditing: border preset painted the
bottom border band one row early (wiping visible row 203), and `seek()` now
compares backward seeks against packets actually applied and re-renders the
snapshot at the seek target (previously the pre-seek image lingered until the
next visible change).

Diagnostics (default off): `SINGWS_CDG_DEBUG=1` logs a rate-limited publish /
seek-rebuild summary; `SINGWS_CDG_SNAPSHOT_DIR=<dir>` saves each published
native frame as PNG. Regression tests: `CdgNoLookaheadTests` /
`CdgSurfaceCommandTests` in `test_gst_transport_port.py`.

# Earlier audit - 2026-05-31

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

- Kept CDG on the standard fast scaling path.
- Moved CDG scaling out of `paintEvent()` into a cached scaled pixmap.
- Invalidates the scaled cache only when frame, widget size, stretch mode, or
  CDG display mode changes.
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
