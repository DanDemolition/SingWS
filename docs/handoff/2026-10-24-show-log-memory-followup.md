# Show-log native-memory follow-up — 2026-10-24

## When to do this

Defer this work until the current Rust build/migration work is finished. Do not
mix memory experiments into that build or change playback settings before the
Rust checkpoint is complete.

## Evidence from the 2026-10-23/24 shows

Logs examined:

- `singws_last_show_2026-10-24_01-36-15.log`: SingWS 1.0.1.4, 49 songs
  including two KaraFun songs. RSS rose from about 930 MB after launch to
  2.52 GB near shutdown.
- `singws_last_show_2026-10-24_01-34-07.log`: SingWS 1.0.1.3, 45 local songs.
  RSS rose from about 475 MB to 972 MB.

The comparison is not controlled. The higher-memory run had Qt Quick audience
and host-preview transition surfaces enabled and used enhanced Rust BGM audio
with normalization/EQ. The lower-memory run had Quick surfaces disabled and
used Simple Audio Mode.

This does not look like a Python/UI-object leak. During the 1.0.1.4 run,
Python allocated blocks, widget count, timers, threads, and file descriptors
were nearly flat. RSS occasionally fell and its growth slowed later in the
show. The largest identifiable steps were:

- about 328 MB across the first local song;
- about 126 MB across the first KaraFun ScreenCaptureKit session;
- about 78 MB across the second KaraFun ScreenCaptureKit session.

The installed 1.0.1.4 Objective-C mpv bridge was confirmed to use a 256 MiB
demuxer cache, one-second audio buffer, and ten seconds of readahead. The first
song's step is consistent with native decoder/cache/GPU initialization. The
KaraFun steps are consistent with ScreenCaptureKit and frame-surface high-water
allocations, but neither is proved to be the retained owner. There were no
SingWS native crash reports during the show window.

## Deferred work

1. Add lightweight, opt-in native-memory telemetry around the existing
   `[MEMORY-SONG]` records. At minimum record macOS physical footprint,
   resident size, compressed memory, and whether the mpv, Qt Quick, and
   ScreenCaptureKit paths are active. Keep it off by default if collection is
   materially more expensive than the current counters.
2. Run a controlled rehearsal with the same profile, display geometry, and
   song sequence twice:
   - `quick_gpu_surfaces` enabled;
   - `quick_gpu_surfaces` disabled.
3. Include at least one local CDG, one local MP4, and two KaraFun captures in
   each run. Record memory before first playback, after every song, after each
   KaraFun capture stops, and after several idle minutes.
4. Compare physical footprint as well as RSS. Confirm that capture shutdown
   releases the active stream and clears both audience and preview `QImage`
   frames. Determine whether the remaining allocation is reusable high-water
   memory or grows again with every capture.
5. Only after the A/B result identifies an owner, make the smallest isolated
   change. Possible targets to evaluate, not assumed fixes, are the 256 MiB mpv
   cache, Qt Quick surface lifetime, or ScreenCaptureKit buffer-pool cleanup.
   Any cache reduction must pass real CDG/MP4 playback without audio gaps.

## Acceptance gate

- Automated tests run with a scratch `SINGWS_HOME`.
- The candidate is built, installed under the normal SingWS identity, launched,
  and visually checked on the real two-display layout.
- Local CDG/MP4 and KaraFun playback show no black frame, ticker/QR stacking
  regression, audio gap, delayed handoff, or end-of-song regression.
- A rehearsal of comparable length shows bounded memory or a clearly explained
  and repeatable native high-water plateau.

