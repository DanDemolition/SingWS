# GStreamer Removal Audit (2026-07-19 → 2026-07-20)

## Decision

**REMOVED (2026-07-20, branch `chore/remove-gstreamer`).** After milestones
1–7 migrated every GStreamer-only feature and flipped the default engine to
FFmpeg/Qt (shipped 0.4.1.10 → 0.4.2.3), GStreamer was fully deleted: the
`GstKaraokeTransport` engine, `okj_audio_backend`, `gst_bootstrap`, the native
SoundTouch plugin, the `cdg_native` appsrc wrapper, all runtime `gi`/`Gst`
usage in the host, and every packaging hook (specs, build scripts, runtime
hook, `gi` now excluded).

Validation on a clean arm64 build: **no GStreamer files bundled, no Mach-O
binary links to system/dev-machine GStreamer, zero GStreamer build warnings,
and the frozen app launches FFmpeg-only.** The arm64 installer dropped from
~204 MB to ~113 MB. Full suite green (634 tests + subtests) including the
strengthened `test_no_gstreamer_guard.py`.

**Residual risk (the removal's stated approval bar, step 7):** paired
show-length validation on real Apple Silicon AND Intel hardware has not yet
been performed on the FFmpeg-only build — that is the branch's one open item
before it ships. The original 2026-07-19 findings below are retained for
history.

### Historical (2026-07-19): why removal was blocked then

At audit time GStreamer was still the preferred live karaoke transport and the
only implementation for several host features; the sections below documented
that state and the migration path that has since been completed.

## Current engine selection

- Live karaoke: since the default flip (milestone 7), the FFmpeg/QAudioSink
  `PythonKaraokeTransport` is the default engine. `GstKaraokeTransport` is
  selected when the host pins `karaoke_engine` to `gstreamer` (or `auto`, the
  old GStreamer-preferred behavior), and remains the escape hatch until
  removal.
- Background music: BASS/BASSmix is primary. GStreamer remains the recovery
  backend if BASS initialization fails.
- CDG rendering: both transports have a native Python/Qt CDG decoder, but the
  released selection still routes normal CDG playback through GStreamer for
  audio, timing, pitch, tempo, seeking, and the playback watchdog.

The application also currently treats failure to import or initialize core
GStreamer elements at startup as fatal outside the explicit test mode.

## Supported features that still depend on GStreamer

| Feature | Current dependency / user-visible impact if removed |
| --- | --- |
| Normal live karaoke | Preferred engine for audio-only, MP3+CDG, and MP4 playback. Removing it changes every live song to the less-proven fallback. |
| Key and tempo | GStreamer uses the native SoundTouch `pitch` element and `scaletempo` with rate-preserving seeks. The fallback has Signalsmith support, but it has not passed equivalent Intel/Apple Silicon show testing. |
| Audio output selection | Device enumeration and pinned sinks use `Gst.DeviceMonitor` and `Gst.Device.create_element`. The fallback `QAudioSink` currently uses the system default and is not wired to the selected SingWS device. |
| Soundboard | Migrated in the second removal milestone: pads now use independent preloaded BASS streams and no longer require GStreamer. |
| Decorative MP4 behind CDG lyrics | Migrated in the fifth removal milestone: the worker now decodes through `FfmpegVideoReader` (rawvideo + VideoToolbox probe) with wall-clock pacing; no GStreamer involvement remains. |
| Background-music recovery | Migrated in the third removal milestone: BASS failure now falls back to the FFmpeg/Qt background engine (same deck/crossfade/meter API); GStreamer BGM is only reached if both engines fail to construct. |
| Lead-silence analysis | Migrated in the fourth removal milestone: the scan is now an FFmpeg/numpy RMS pass in `phrase_detect.detect_lead_silence`; no GStreamer involvement remains. |
| Non-default output routing for BGM/soundboard | The selected output identity is discovered through GStreamer even when BASS performs normal background playback. |
| MP4 end handling and hung recovery | Current production coverage and diagnostics exercise the GStreamer audio-master EOF/last-frame path and its stalled-playback watchdog. |

## Formats and metadata

- The FFmpeg fallback can decode the common audio/video inputs used by SingWS,
  and both transports can render native CDG data.
- ZIP extraction occurs before transport selection, so ZIP itself is not a
  unique GStreamer capability.
- Library metadata and duration discovery primarily use fast MP3 parsing,
  `ffprobe`, cached metadata, and application records; no unique GStreamer
  metadata requirement was found.
- This format overlap is not sufficient to approve removal because output
  routing, soundboard playback, recovery, timing, and live cross-architecture
  evidence are still missing from the fallback.

## Runtime evidence

Recent application logs under `~/SingWS/logs` were inspected. The July 13-15
logs contain 18 successful `[GST-KARAOKE] started` events (all CDG) and zero
`construct failed, falling back` events. Representative diagnostics identify
the real MP3 decoder (`mpg123audiodec`), native CDG/Qt renderer, SoundTouch
pitch element, and `scaletempo` tempo path. This proves normal real-world
playback invokes GStreamer.

The old log attachments previously copied into Downloads were no longer
present, so the canonical application log directory was used instead.

## Packaging and size evidence

The released 0.4.1.9 artifacts are approximately:

- Apple Silicon DMG: 208 MiB on disk (`204 MB` manifest label)
- Intel DMG: 272 MiB
- Universal DMG: 464 MiB on disk (`455 MB` manifest label)
- Universal app bundle: 1.2 GiB uncompressed
- Universal `Contents/Frameworks/gst_plugins`: about 315 MiB uncompressed

That plugin directory is the largest single directory in the Universal bundle.
Additional GStreamer core libraries, typelibs, scanner, GLib dependencies, and
resources are packaged elsewhere, so 315 MiB is a lower bound rather than a
claim about the final removable size. An absent-GStreamer release comparison
was intentionally not produced because the functional stop conditions above
failed; such an artifact would not be a supported SingWS build.

## Build and source surface audited

- Runtime setup and initialization in `0.2.18.1.py`, `gst_bootstrap.py`, and
  `singws_pyinstaller_runtime.py`
- Karaoke and audio paths in `gst_karaoke_transport.py`,
  `python_karaoke_transport.py`, `okj_audio_backend.py`, and `cdg_native.py`
- Apple Silicon, Intel, and Universal PyInstaller specifications
- `build_all.sh`, `build_universal.sh`, `release.sh`, and the custom
  `native/gst-soundtouch` plugin/build script
- GStreamer-specific and no-GStreamer unit tests, documentation, and recent
  runtime logs

## Migration path to safe removal

1. **Completed:** the `karaoke_engine` setting (auto | gstreamer | ffmpeg,
   Settings → Audio → Playback) pins the live engine, and every song start
   logs a `[KARAOKE-ENGINE]` line recording the engine that actually played
   it (plus `_last_karaoke_engine` on the host).
2. **Wiring completed** (first milestone): the transport resolves the
   persisted output selection to a matching `QAudioDevice`. Cross-architecture
   routing proof still comes from the paired show tests in step 7.
3. **Completed:** replace soundboard `playbin` pads with independent BASS
   streams on the selected BASS/CoreAudio output.
4. **Completed:** the CDG decorative-video pipeline now uses the existing
   FFmpeg video reader (see migration progress below).
5. **Completed:** lead-silence analysis now uses FFmpeg/numpy PCM analysis
   (see migration progress below).
6. **Completed:** BASS failure now falls back to a Qt/FFmpeg background engine
   rather than GStreamer (see migration progress below).
7. Run paired show-length tests for CDG, MP4, audio-only, pause/resume, seek,
   skip, automatic advancement, key/tempo, output switching, EOF, corrupt
   media, and decoder failure on both architectures.
8. Build in clean macOS environments with GStreamer discovery blocked, inspect
   all Mach-O dependencies, and only then remove specs, hooks, scanners,
   plugins, typelibs, licenses, native plugin code, and documentation.

### Migration progress

The first routing milestone is now implemented: when
`PythonKaraokeTransport` is selected, SingWS resolves the current persisted
GStreamer output selection to a matching Qt `QAudioDevice` by normalized
display name and constructs `QAudioSink` with that device. Missing or ambiguous
matches fall back to Qt's current system default without blocking playback.
Production engine priority is unchanged.

The second milestone is also implemented: soundboard pads now borrow the
already initialized BASS output owned by the background engine. Pads preload a
native stream, restart it without rebuilding on repeated hits, and remain
independent so several pads can overlap each other, background music, and the
karaoke transport. Output changes release pad handles before BASS changes its
CoreAudio device and resume active pads at their prior positions afterward.
WAV, AIFF, MP3, OGG, and FLAC load directly. The bundled BASS runtime does not
open AAC/M4A (error 41), so those formats are decoded once by the already
bundled FFmpeg into a content-keyed PCM cache during preload; playback never
performs that conversion or runs FFmpeg on the realtime path.
There is no Python callback in this audio path. A native muted smoke test
confirmed the BGM mixer and two distinct pad streams can all remain active at
the same time on the bundled universal BASS runtime. On the Apple Silicon test
machine, preloading the bundled MP3 fixture took 0.186 ms and 250 muted native
restarts took 16.206 ms total while allocating exactly one stream. These are
developer smoke measurements, not a substitute for show-length listening on
both architectures.

The third milestone (BASS failure recovery) is implemented: when
`BassBackgroundEngine` construction raises, `BackgroundMusicPlayer` now falls
back to `ffmpeg_background_engine.FfmpegBackgroundEngine` — one ffmpeg decode
process per deck feeding a Python mixer behind a pull-mode `QAudioSink` on the
selected (or default) output. It mirrors the BASS engine's public API: decks
with per-deck normalize gains, load/play/pause/stop, master volume with
slides, start/complete/cancel crossfade, get_times/seek/source_ended, RMS
meter, and the same `configure_stream`/`process_f32_array` EQ and master-chain
DSP contract, so every existing `_bass_ready()` call site works unchanged. The
GStreamer BGM pipeline is now reached only if both engines fail to construct.
Diagnostics report the active backend (`backend_name`) instead of assuming
BASS. Covered by `test_ffmpeg_background_engine.py` (mix-level tests that run
without audio hardware, plus host selection tests for the BASS-fails and
both-fail paths) and a muted real-QAudioSink smoke run that confirmed
real-time pull, clock advance, and a live meter on the Apple Silicon test
machine.

The seventh milestone (default flip) is implemented: `karaoke_engine` now
defaults to `ffmpeg`, so live karaoke runs on the FFmpeg/Qt transport for
hosts who never touched the setting, while GStreamer stays fully bundled and
selectable (`gstreamer`, or `auto` for the old GStreamer-preferred behavior)
as the escape hatch. Removal (step 8) should follow only after real shows on
both architectures confirm the default. A muted real-library smoke on the
default engine verified clock advance, duration probing, and a clean decode.

The sixth milestone (host-selectable engine) is implemented: a new
`karaoke_engine` setting (default `auto`) with a combo in Settings → Audio →
Playback lets the host pin GStreamer or the FFmpeg/Qt transport for the next
song. Selection is resolved by `_select_karaoke_transport_cls` (aliases
ffmpeg/python/qt and gstreamer/gst accepted; unavailable or unknown values
fall back to auto), and each song start records the engine that actually
played it via a `[KARAOKE-ENGINE] engine=… pref=… mode=… file=…` log line and
`_last_karaoke_engine`, so show logs distinguish preference from outcome when
a construction fallback occurs. Covered by
`test_karaoke_engine_selection.py`.

The fifth milestone (decorative background video) is implemented:
`_LyricsBackgroundVideoWorker` no longer builds a GStreamer
`uridecodebin`/appsink pipeline. It drives the karaoke transport's
`FfmpegVideoReader` (ffprobe geometry probe, VideoToolbox hwaccel probe,
rawvideo rgb24 decode with a bounded frame queue, now with a decode-fps cap
parameter) and paces delivery against a wall clock that anchors on the first
decoded frame, so probe latency never fast-forwards a clip. The shuffle bag,
newest-frame-only backpressure, worker-thread isolation, stats logging, and
player API are unchanged, and the host's start gate no longer requires
GStreamer. Verified by the extended `test_bg_video_lyrics.py` (source-level
no-GStreamer guard plus a functional decode-and-loop test over a generated
MP4 that crosses EOF into the next clip) and a real-file smoke on the host's
VJ Loops folder: a 1280x720 `.mov` delivered 236 frames at a paced 30.1 fps
with one drop and a healthy 24-frame queue.

The fourth milestone (lead-silence analysis) is implemented:
`detect_lead_silence` in the host now delegates to
`phrase_detect.detect_lead_silence`, an FFmpeg decode of the first 30 s into
100 ms windows with per-channel RMS in dBFS and the same threshold, minimum,
and 10 s clamp semantics as the old `decodebin`/`level`/`fakesink` pipeline.
An old-vs-new parity run across sampled real library MP3s agreed within 0.1 s
on every file except one borderline 0.5 s lead-in, where the new scan is the
correct one (the level element timestamped windows by their start and
under-measured the run past the `min_silence` gate). Covered by
`DetectLeadSilenceTests` in `test_phrase_detect.py`, and the no-GStreamer
guard now exercises the scan functionally with `gi` blocked.

`test_no_gstreamer_guard.py` now verifies in a fresh process that the host can
be imported in explicit no-GStreamer test mode while `gi` is actively blocked,
and that generated audio is decoded through the FFmpeg/Signalsmith fallback.
