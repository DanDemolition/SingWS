# Transition analysis architecture

## Upgrade and rollback contract

Transition metadata lives in `~/SingWS/transition-analysis.json`. It is
deliberately separate from `loudness.json`: adding or revising transition and
visual analysis never marks existing LUFS/peak results unanalyzed and never
forces a full library rescan. Records are keyed by canonical playback path and
validated against file mtime and size. Invalid, corrupt, stale, or unknown-
version records are ignored per file and playback falls back to physical EOS.

The **Seamless transitions** setting is the emergency master switch. Turning it
off preserves all cached data and does not interrupt the current song; it
disables optional early completion and adaptive decisions so normal physical
end-of-file behavior remains authoritative.

Cache loading is asynchronous. The playback/UI path never parses the JSON file
and treats a cache that is still loading exactly like missing metadata.

MP4 thumbnail decoding is exposed only through the offline/backfill adapter.
Live playback may consume a validated cached MP4 visual endpoint, but it never
starts video analysis. A missing or uncertain endpoint therefore means normal
container EOS. Visual backfill merges into an existing record and preserves
its audio envelope, LUFS, peak, and boundary fields.

The native BASS background engine exposes a silent secondary-deck preload.
Prepared decks carry path, mtime, and size identity. Crossfade start reuses the
deck only when all three still match; otherwise it frees the stale deck and
opens the requested current-next file through the normal fallback path. A
preload exception therefore cannot prevent ordinary BGM playback.

## Scanner integration

Full loudness scans now run EBU R128, peak measurement, and a 100 ms RMS
envelope through one libmpv/libavfilter decode. Tracks with valid full LUFS but
missing transition fields are included; their accepted LUFS/peak values are
preserved while only the new boundaries are backfilled. Fast/sample scans do
not claim full-track boundaries.

The audio settings screen also provides **Analyze Missing Transition Data**.
It works even when normalization is disabled, enumerates only missing/stale
audio or visual records, pauses between files whenever karaoke is active, and
persists successful results incrementally. CDG packet analysis and MP4 tail
thumbnail analysis run in this worker, never on the Qt or live playback path.
Cache saves are debounced during large passes and flushed at completion or
cancellation so an interrupted backfill resumes from prior results.

Status: architecture audit and implementation plan only. No live transition
behavior is enabled by this document.

## Safety invariant

Automatic karaoke completion and audible BGM start require affirmative evidence
that both meaningful audio and meaningful lyric/visual content have finished.
Missing, stale, low-confidence, or unsupported analysis must fall back to normal
decoder/container end. Audio silence alone never authorizes a karaoke handoff.

Manual Stop and Skip remain explicit host actions and are outside this automatic
completion rule.

## Existing implementation

### Analysis and cache

- `0.2.18.1.py` owns a persistent `loudness.json` cache keyed by resolved audio
  path and invalidated by file size and integer mtime.
- Full analysis uses libmpv's `ebur128` filter through `libmpv_media_jobs.py` and
  stores integrated LUFS plus peak dB. Fast mode samples five sections.
- Analysis is serialized, cancellable, background-threaded, and debounced when
  saving. The library scan already pauses or throttles according to live-show
  policy.
- `mpv_playback_iina.scan_silence()` calls the bundled native mpv bridge. It
  returns lead silence, last meaningful audio/content timestamp, and duration.
  `phrase_detect.py` provides the existing conservative silence algorithms.
- Karaoke trailing-silence results also exist in an in-memory cache, separate
  from `loudness.json`. They are scanned on demand and are not a versioned,
  persistent transition record.

### Karaoke playback and completion

- `MpvKaraokeTransport` is the sole local karaoke transport for CDG+audio and
  MP4. The native bridge owns decoding, audio/video timing, output, key/tempo,
  and the audible clock.
- CDG uses a graphics file plus external MP3/WAV audio in one mpv core. MP4 uses
  one audiovisual source. End-of-stream is currently mpv `eof-reached`.
- The host has optional early-tail logic in `_maybe_trim_end_silence()`. It
  requires a scanned audio endpoint and otherwise falls back safely to EOS.
- For CDG, that logic observes remaining sectors and graphics generation. It
  also tries to call `karaoke_transport.cdg_lyrics_finished()`, but the current
  `MpvKaraokeTransport` does not implement that method. Consequently the
  documented “hard lyrics floor” is not active on the native path.
- For MP4, the current early-tail gate has no verified visual-end metadata. Once
  the audio endpoint is passed, being near container end can authorize early
  completion. This is not strong enough for lyric-safe early termination.
- The optional `karaoke_bgm_crossfade_enabled` path can pre-start BGM during the
  final 3.2 seconds based on remaining duration alone. It is off by default but
  is incompatible with the invariant and must not be part of seamless mode.

### Background music

- `BackgroundMusicPlayer` uses `BassBackgroundEngine` first and
  `LibmpvBackgroundEngine` only as recovery.
- Both engines already expose two source decks. A BGM crossfade opens the next
  file on the secondary deck, starts it at zero, and performs native gain
  slides. BASS uses logarithmic slides.
- The current adjacent-track crossfade is fixed at five seconds. Generation
  counters reject stale fade and crossfade completion callbacks.
- The current track can be opened paused before BGM resumes after karaoke.
  Crossfade deck B is created only when the crossfade begins; there is no stable
  prepared-next-deck identity contract yet.
- BGM start is suppressed while `karaoke_playing` unless a caller explicitly
  bypasses the guard. This bypass is currently used by optional overlap paths.

### UI and state

- Qt/Python owns queue selection, host actions, transition policy, and display
  state. The native mpv transport is the clock and media authority.
- The next karaoke path can be identified, but `_preload_next_up_cdg()` no
  longer decodes/preloads packets. There is no prepared native karaoke core.
- Queue mutations require stable request identity and preload invalidation;
  paths, singer metadata, or row positions alone are not sufficient identity.

## Smallest safe design

### Versioned transition record

Add a format-independent `TransitionAnalysis` record with separately preserved
fields:

- schema/algorithm version, path, size, mtime, media kind, duration
- integrated LUFS and peak dB (reusing existing results)
- 100 ms quantized audio envelope, audio start, audio end, fade start/confidence
- visual start, visual end, visual confidence, visual analysis method
- effective karaoke end and safety margin
- explicit `safe_for_early_completion` boolean plus reason

The serialized envelope should be compact (quantized dB values or run-length
segments), bounded in size, and optional. Existing loudness entries remain
readable during migration. Playback must never wait for this record.

### Conservative boundary policy

Pure policy code should calculate boundaries without Qt or playback objects.
For karaoke, early completion is allowed only when:

1. the record matches the current file signature and analysis version;
2. audio end is verified with conservative hysteresis and tail context;
3. visual end is verified for the actual visual source;
4. playhead is beyond `max(audio_end, visual_end) + safety_margin`; and
5. the current playback generation still matches.

Any failed condition means normal EOS. A static final frame is not automatically
dead; uncertain static content remains through EOS.

### CDG visual analysis

Analyze CDG commands offline and track timestamps of framebuffer-changing
operations after filtering redundant/no-op packets. Preserve palette changes,
tile writes, scrolls, clears, and other visible mutations. The first version
must use a conservative last-visible-mutation boundary plus a static-screen
hold policy; it must not infer lyric semantics it cannot prove. Runtime exposes
the analyzed boundary through an explicit transport method rather than relying
on remaining packet count.

### MP4 visual analysis

Sample decoded video frames near the beginning and tail during offline analysis,
using small luminance thumbnails and frame-difference scores. Static black/blank
tails may be marked dead only at high confidence. Static title cards or final
lyrics remain ambiguous and therefore fall back to container end. No OCR and no
live full-frame analysis are required.

### Transition controller

Introduce one Python policy/controller with generation tokens and observable
states for BGM, karaoke preparation/playback, visual finishing, and handoff.
Only this controller may request an automatic BGM start during karaoke teardown.
The native transport remains the time source; the controller does not invent a
wall-clock position.

## Incremental milestones

1. Add pure transition record/cache and audio-boundary policy tests. Reuse the
   existing decode pass where possible. No playback behavior changes.
2. Add offline CDG visual analysis and fixtures. Implement an explicit,
   conservative CDG completion API. Keep early completion disabled.
3. Add offline MP4 tail analysis and fixtures. Uncertain results must be unsafe.
4. Wire analyzed dead-intro/dead-tail behavior for BGM behind an opt-in flag,
   then add adaptive BGM crossfade selection without changing karaoke.
5. Add stable next-BGM preparation with playlist generation/path validation.
6. Add karaoke transition controller and lyric-safe karaoke-to-BGM handoff.
   Remove duration-only automatic overlap from seamless mode.
7. Add native karaoke preparation only after identity invalidation and
   A/V-clock behavior are proven. Keep current load-on-Play as fallback.
8. Run race, corpus, installed-app, Intel, and Apple Silicon verification before
   enabling defaults.

## Required verification gates

- Pure unit tests for envelope thresholds, hysteresis, cache versioning and
  invalidation, effective end, unknown metadata, and stale generations.
- CDG fixtures covering late lyrics, static final lyrics, clears, redundant
  packets, blank tails, silent breaks, and instrumental endings.
- MP4 fixtures covering lyrics after audio, static final lyrics, black tails,
  title cards, and quiet/fading audio.
- Scratch `SINGWS_HOME` for every test. No test may touch live show data.
- No app launch, installation, output-device change, media playback, or live
  corpus test during a show.
- Installed x86_64 launch and visual observation after the show. Apple Silicon
  build and on-device verification are separate release gates.

## Current blockers and assumptions

- Meaningful lyric semantics cannot be proved from arbitrary static CDG/MP4
  frames without OCR. The safe first implementation keeps ambiguous static
  screens until EOS.
- The installed app is version 0.4.5.9, matching the current source tag, but it
  has not been launched or byte-for-byte compared during this audit because a
  show is active.
- The working tree was clean at audit time. This document is the only change in
  this milestone.
