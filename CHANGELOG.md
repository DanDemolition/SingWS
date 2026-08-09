## 0.4.3.9

0.4.3.8 was built and run locally but never tagged or released, so everything
written up here ships as 0.4.3.9.

### Added
- **A second widescreen side fill that is visible on every disc: "Widescreen
  side fill (blurred image)".** The existing fill paints the disc's own
  background colour, which is black on most of this library — correct, and
  indistinguishable from "Fit". The new mode fills the bars with a heavily
  blurred, mirrored copy of the lyrics picture instead, dimmed to 88% so it
  reads as ambience rather than a second picture. Implemented on all three
  render paths: the in-process mpv bridge (shader), the follower mpv backend
  (a `boxblur` + `overlay` lavfi graph), and the FFmpeg/Qt painter (a 24x18
  downscale smooth-upscaled to the widget). The old mode is unchanged and is
  now labelled "Widescreen side fill (background color)".

### Fixed
- **The side fill's pillarbox measurement never once ran, on either of the two
  logged shows.** 0.4.3.7 started measuring libmpv's pillarbox from
  `dwidth`/`dheight` at `FILE_LOADED` so the fill would seam at the true CDG
  edge — but mpv publishes those on video reconfigure, which for a CDG opened
  at `demuxer-lavf-probescore=1` lands *after* `FILE_LOADED`. Not one
  `[bridge] picture … span=` line exists in the 2026-08-08 or 2026-08-09 logs;
  the fill silently ran on the nominal fallback the whole time. The measurement
  now also runs on `MPV_EVENT_VIDEO_RECONFIG`, requests a redraw when the span
  actually changes, and logs the failure instead of returning in silence.
- **Show-screen images were letterboxed inside the video area.** Idle
  background art and the lyrics background now stretch to fill the whole area
  above the ticker rather than sitting centred inside black bars.
- **The show window restored onto a screen that was no longer attached.** The
  saved `karaoke_window_pos` was applied verbatim — the 2026-08-09 04:10 launch
  restored it at x=1680 with a single 1680x1050 display, i.e. entirely off the
  desktop, unreachable and with no screen for macOS to composite it against.
  The rotation window has always had this guard; the show window now does too:
  a rect that touches no attached screen is re-centred on the primary at its
  saved size, and the move is logged. A genuinely multi-monitor placement is
  untouched.

### Known issues
- **The `QPaintDevice::devicePixelRatio()` crash is NOT fixed, and 0.4.3.8's
  attribution of it was wrong.** Both 0.4.3.8 crashes (2026-08-09 04:11:44,
  hiding the rotation window 0.3s earlier; and 13:05:54, 7s into launch) are the
  same null dereference in `QWidgetRepaintManager::flush` →
  `QBackingStore::flush` → the platform backing store's paint device. The 04:11
  session had **no native surfaces of any kind**: the log shows Qt Quick
  resolved off (`[SHOW-VFX] quick surfaces … resolved=off`, legacy painter
  ticker, native-widget rotation renderer) and mpv's core is created lazily on
  first play, which had not happened. So neither the window containers nor
  `MpvVideoHost` can be the cause — `WA_DontCreateNativeAncestors` was worth
  setting on its own merits, but it did not address this crash. What both
  crashes share is a top-level backing store being flushed with no paint
  device; the off-screen show window above is a suspected contributor, not a
  proven cause.
- **The mpv video host turned the whole show window native, which is very
  likely the long-running crash.** Qt promotes every *ancestor* of a
  `WA_NativeWindow` widget to native unless `WA_DontCreateNativeAncestors` is
  set, and `MpvVideoHost` set only the former — so creating it silently
  promoted `video_area` and the entire `VideoWindow`. That is exactly the
  nested native backing stores the video-window code already names as "the
  common factor in the Intel macOS `QPaintDevice::devicePixelRatio()` crashes",
  and why it is careful never to call `video_area.winId()`; the host promoted
  them through the back door anyway. **13 of the 15 packaged-app crash reports
  on this machine are that one null dereference inside `QBackingStore::flush`**,
  spanning v0.4.3.4 through v0.4.3.7. The promotion is permanent for the
  session, which is why one of them happened while the app sat idle between
  singers with nothing playing. `WA_DontCreateNativeAncestors` is now set: the
  host keeps its own NSView, the ancestors stay ordinary painted widgets.
- **Every Qt Quick window container had the same omission.** All four
  `createWindowContainer()` sites (ticker, show VFX, rotation) promoted their
  ancestors too. `_rotation_quick_surfaces_supported`'s own docstring describes
  the consequence — "Qt's Cocoa backing-store path can dereference a released
  paint device when a top-level widget containing multiple
  createWindowContainer() children is hidden" — which is the crash that got Qt
  Quick banned on Intel macOS in the first place. It was the missing attribute,
  not Quick. All four now set `WA_DontCreateNativeAncestors`.

### Changed
- **The animated ticker and show transitions can be turned back on.** Qt Quick
  child surfaces were disabled outright on Intel macOS on 2026-08-01 ("Fix
  Intel crashes and playback stalls"). The attribution looks wrong: across 126
  crash reports on that machine **Qt Quick and the scene graph appear in the
  faulting thread zero times**, and the crash it was meant to stop went on
  happening with the gate in place — including both crashes during the
  2026-08-08 show. New `quick_gpu_surfaces` setting ("auto" | "on" | "off",
  default "auto" — unchanged behaviour) with a Settings → Display control, plus
  `SINGWS_QUICK_SURFACES=1/0` for a one-off test. Applied at launch, before the
  video window is built, so it never changes backend mid-show.
- **The animated ticker settings offered an effect that cannot run on Intel
  Macs.** `set_effects_enabled` exists only on `RenderThreadTicker`, and Qt
  Quick child surfaces are refused on Intel macOS by
  `_native_quick_child_surfaces_supported` — that is the Cocoa backing-store
  flush path behind the video-window crashes. So an Intel Mac always gets the
  legacy painter ticker, which has no lighting effects at all, and both
  "Animated ticker lighting and accents" checkboxes saved a setting that was
  then silently dropped — indistinguishable from the animated ticker being
  broken. Both checkboxes are now disabled where the live backend cannot honour
  them, and say why. The scroll itself is unaffected and still runs. `[TICKER]
  init` also reports `effects=available|unavailable`, so a show log can tell
  "effects turned off" from "effects impossible here".
- **The previous singer's video played over the next singer's audio.** The
  bridge reuses one libmpv core and one shared GL texture across songs, and
  `loadfile` is asynchronous: mpv keeps rendering the outgoing file into that
  texture until the replacement takes over. Those frames were marked as "we have
  a frame", which both painted them and satisfied `visualReady` — the flag the
  host gates its surface reveal on — so the old song's picture was shown against
  the new song's audio. Logged on 2026-08-09: Nikki's *Since U Been Gone*
  (01:06:39) still on screen for FrankieRod's *Slow An' Easy* (01:10:59), with
  the correct `.cdg` path handed to mpv both times. Frames rendered while a load
  is pending are no longer counted, so the surface stays black across the switch
  and reveals on the new song's first real picture. This also closes a window
  the asynchronous load path had widened.
- **Songs whose MP3+G graphics stream is shorter than its audio were force-
  stopped mid-vocal.** MP3+G is two files: mpv loads the `.cdg` as the media and
  the `.mp3` as an external audio track, and its `duration` property reports the
  *graphics* stream. Plenty of discs run the graphics short. On 2026-08-08,
  CC's Starman carried **234.25s of graphics against 256.76s of audio** and
  Livin' On A Prayer **248.25s against 262.36s** — both had the background music
  faded in over the top and were then stopped with 22s and 14s of vocal still to
  sing. Every end-of-song rule keys off `duration`, so all of them fired early.
  Three of 44 songs that night were cut; the other 41 had graphics and audio
  within a second of each other and were fine, which is why it looked random.
  The trailing-silence prescan already probes the mp3 and knows its true length,
  so the duration used for end-of-song and crossfade decisions is now floored by
  it. Never shortened — this may only ever let a song run to its real end.
- **The "never force-end a song that is still producing sound" protection was
  dead on the mpv engine.** It gates on an audio level tap mpv does not have, so
  it read "no metering" as "silent" and the fallback EOS detector fired
  unopposed — the second half of the cut-offs above. It now asks mpv's own
  `eof-reached` first, which needs no metering; a first-hand "not at end" now
  blocks the fallback. The FFmpeg path is unchanged (it answers `None`).
- **Early fades and the crossfade under a song's silent tail never ran on mpv,
  leaving long silences between karaoke and background music.** Same dead level
  tap: `_maybe_trim_end_silence` returned early on every tick of every song, so
  the `[END-SILENCE] near end but no level data yet` line in the log was the
  feature declining to work rather than a transient, and tracks sat through
  their whole trailing silence (up to 13s on some discs) before the background
  music came back. The meter was never the authority — the file prescan is, and
  the meter only ever delays past it — so with no meter the handoff now runs on
  the prescan alone and counts time past the verified audio endpoint. This
  cannot cut a song short: the tail handoff only starts the background
  crossfade, and karaoke still runs to its real EOS.
- **MP4 video timing could not be corrected on the mpv engine, from anywhere.**
  `mp4_timing_offset_ms` has been in the defaults all along but only the
  painter path ever read it, and the mpv start path applied a visual offset
  only when the mode was `cdg` — so an MP4 whose picture led or lagged the
  vocal had no control at all. The mpv path now applies it, it is keyed per
  engine (`mp4_timing_offset_mpv_ms`) for the same reason CDG is — mpv
  composites through its own GL surface and the painter path blits into a
  widget, so their display latencies differ — and Settings → Display gained an
  "MP4 Video Timing Offset" card next to the CDG one, applying live on the
  currently playing song. No baseline is applied: unlike CDG, neither engine has
  a systematic decoder offset to compensate, so this is operator trim only.
- **The mpv master bus ran a decibel hot into a cancelled ceiling, and its
  compressor started 3 dB early.** Three knob values were passed to lavfi in
  SingWS's units rather than lavfi's. Measured against the NumPy chain the
  FFmpeg engine actually plays, on the same audio:
  - `alimiter`'s **auto level defaults to ON** and divides the output by the
    limit, normalizing straight back to 0 dBFS. A -1 dB ceiling produced exactly
    +1.00 dB of blanket gain at every level below it, and output peaks sat above
    the ceiling the setting exists to enforce. Now `level=disabled`.
  - `acompressor` detects on a smoothed RMS where `MasterAudioProcessor` uses a
    smoothed mean-of-|x|, which reads ~3 dB lower for the same programme. The
    threshold now carries that offset; without it the compressor applied ~1.4 dB
    of extra gain reduction at normal levels.
  - `acompressor`/`agate` express the knee as a **linear factor**, not dB —
    the transition spans `threshold/sqrt(k)` to `threshold*sqrt(k)`. Passing
    `comp_knee_db=6` straight through made a 15.6 dB knee.

  With the three corrected, the compressor's static transfer curve matches
  MasterAudioProcessor within **0.02 dB from -40 to -3 dBFS** (it was out by up
  to 1.45 dB) and the limiter matches exactly below its ceiling. On programme
  material the loudness gap against the FFmpeg engine halves, from -1.9 LUFS to
  -1.0 LUFS averaged over three songs. The gate and exciter were already within
  measurement noise and are unchanged.

  **Residual, deliberately not chased:** roughly 1 LUFS remains and it is
  material-dependent (0 to -1.7 LU across the songs tested), because the two
  compressors' detectors differ dynamically — lavfi's is an asymmetric
  mean-square follower, the NumPy one a symmetric rectified follower. Tuning the
  threshold further closes the average but only by overfitting to particular
  tracks, so the derived calibration was kept instead.
- **Tempo changes switched time-stretching algorithm class.** mpv applies
  `speed` with whichever filter in the chain can stretch time; with no
  rubberband present (i.e. any tempo change at key 0) that was `scaletempo2`, a
  time-domain WSOLA, where the FFmpeg engine used Signalsmith Stretch, a phase
  vocoder. Rubberband is now inserted for a tempo change as well as a key
  change, with `pitch-scale=1` when the key is unchanged, so the stretch runs on
  rubberband's R3 engine. Verified against the shipped libmpv (mpv 0.38.0): R3
  `finer` and formant preservation are already its defaults, pitch is preserved
  to 0.0 cents, and the burst-onset timeline is *more* accurate than
  scaletempo2 (±4 ms vs ±18 ms at 0.92x/1.08x), so the CDG visual offset is
  unaffected.

  **Not fixed, because it cannot be:** key change still uses librubberband where
  the FFmpeg engine used Signalsmith Stretch. Those are different algorithms;
  the shipped mpv is already on rubberband's best settings, so no configuration
  change closes that gap.
- **KaraFun's audio output could never be verified, so every song ran
  unrouted.** The AppleScript looked for a control labelled "audio output" or
  "output device". KaraFun has neither in its window: the 2026-08-08 show logged
  the full candidate list and the only audio control present was
  `[AXButton: … button Audio Settings …]`, which opens a panel that holds the
  picker. All three KaraFun songs that night proceeded on the "could not read
  the output control" warning path. The search now recognises that button as the
  way in, presses it, re-scans for the target device (a panel animates in and
  populates later than a popup menu, so one fixed delay was never going to be
  enough), and dismisses the panel afterwards so it cannot sit over the audience
  screen during the fullscreen handoff. A real picker still wins when one is on
  screen. Script timeout 10s → 25s to cover the re-scans.
- **Widescreen side fill was seamed ~6 CDG columns inside the picture, and
  changing the CDG display mode did nothing until the next song.** The shader
  hardcoded libmpv's pillarbox at x=.125/.875, i.e. 4:3 — but CDG is 300x216
  (DAR 1.389), so the real edges are .109/.891 and the fill painted over live
  image on both sides. The bridge now measures the pillarbox from `dwidth`/
  `dheight` at `FILE_LOADED` and passes it to the shader, which also fixes the
  preview crop that was cropping and squeezing by the same amount. Separately,
  `on_cdg_display_mode_changed` saved the setting without pushing it to the live
  mpv plugin, so the mode only took effect at the next song load; it now applies
  immediately and logs what it pushed. **Note:** side fill draws the CDG's own
  background/border colour by design, and 12 of 14 sampled discs in this library
  have a pure black background — on those it is correctly a no-op.
- **The GUI thread stalled at nearly every song change on the mpv engine.**
  Across the 2026-08-08 show, 40% of the seconds containing a `TRANSITION` /
  `PLAYNEXT` / `KARAOKE-ENGINE` / `MPV-NATIVE` line also contained a `[STALL]`,
  against a 7% base rate, and 163 of the 274 stalls (29.7s total) had a Python
  stack of only `app.exec()` — the block was inside C++. The bridge was doing a
  synchronous `stop`, then `pause` / `demuxer-lavf-probescore` / `audio-files`
  property writes, on Qt's thread, and mpv can hold its core lock for several
  hundred milliseconds while an external MP3 and a CDG open. All mpv commands
  and property writes now run on a serial control queue, so the ordering the
  load path depends on (stop completes before the replacement is configured)
  still holds while the GUI thread never waits on the core. The first load of a
  session stays inline because creating the core touches the thread-affine
  master GL context. `SINGWS_MPV_SYNC_LOAD=1` restores the old synchronous
  behaviour.
- **Stall attribution never recorded anything.** The filter that names which Qt
  event was in flight when the GUI thread blocked lived on the tooltip gate, and
  its install failure was swallowed by a bare `except` — not one `[STALL]` line
  in the whole 2026-08-08 show carried a `last_event=`, which is why the largest
  stall bucket was unattributable. The watchdog now owns the filter, keeps a
  strong reference to it, logs `event attribution live` the first time it
  records, and reports the reason when it cannot install.

## 0.4.3.7

### Fixed
- **CDG was stretched in the preview window on the mpv engine.** The bridge
  cropped libmpv's 4:3 CDG region out of the shared 16:9 texture and then drew
  it edge-to-edge, on the assumption that the preview host is a fixed 300x216
  (25:18) panel. It is a freely resizable window whose only child fills it, so
  the picture was stretched by whatever the mismatch happened to be — 4% at the
  default size, 33% once resized to 16:9. It now letterboxes the cropped region
  against the host's measured aspect.
- **Between-singer animations stopped appearing on the mpv engine.** Qt
  composites a `WA_NativeWindow` child above everything its parent widget
  paints, and the mpv host is one. The Intel painter transition is drawn in
  `VideoAreaWidget.paintEvent`, so revealing the mpv surface at
  `transport.started` painted straight over it. The surface is now suppressed
  for the overlay's lifetime (reason-counted, so the output and preview windows
  cannot restore it out from under each other) and the readiness-gated reveal
  yields to a running overlay. **Known gap:** the next-up card and the request
  QR share this root cause but are meant to sit *over* live video, so hiding the
  surface is the wrong fix for them — they need a native overlay sibling and are
  still invisible on the mpv engine.
- **CDG lyrics ran ~750 ms out on the mpv engine, and calibrating one engine
  silently de-calibrated the other.** `FFMPEG_CDG_BASE_OFFSET_MS` compensates
  SingWS's own CDG decoder; the in-process backend decodes inside mpv off real
  timestamps and needs a different baseline entirely, but was handed FFmpeg's.
  Both engines also shared one fine-tuning key. Baselines and fine tuning are
  now per engine (`cdg_timing_offset_mpv_ms`), with a migration that
  re-expresses any saved value against the engine it was calibrated on. That
  migration is pinned to a `600` literal on purpose: it reconstructs an offset
  dialled in while that was the live baseline, so reading the constant would
  re-interpret saved settings whenever it changes.
- **The FFmpeg CDG baseline is now 750 ms**, finishing a change
  `ffmpeg_cdg_750_baseline_migrated` already assumed had happened — it zeroes a
  saved +150 fine on the basis that the baseline carries it, but the constant
  was never raised, so installs calibrated to +750 quietly ran 150 ms early.
- **Choppy video on the mpv engine.** `glFinish()` and an unconditional present
  to both the output and preview views ran once per frame on the Qt GUI thread.
  Now `glFlush()`, and off-screen views are skipped — `drawRect:` repaints them
  from the retained shared texture when they come back on screen.
- **"Stretch to fill" silently did nothing** on the in-process backend, which
  always preserves aspect. Backends now report whether they support it and the
  option is offered only where it works; a saved `stretch` that cannot render
  says so in the log instead of quietly falling back to `fit`.

### Changed
- **The native bridge's diagnostics now reach `singws_*.log`.** All 25 of them
  went to `stderr`, which a bundled `.app` discards — so an output window that
  went blank left no evidence at all. They route through a host-installed
  callback, and `presentView:` reports a per-view state naming why a view is not
  drawing (`window-transition`, `view-hidden`, `no-window`,
  `window-not-visible`, `no-frame (black)`).

## Unreleased

### Added
- **A single Intel build that runs on macOS 12+ (opt-in, `SINGWS_MEDIA_STACK=iina`).**
  Aimed at retiring the separate legacy edition. Two independent floors had to
  come down, and neither was where it looked:
  - **The Python stack was never the hard limit.** numpy/scipy publish several
    macOS wheels per version, and `pip install` on a modern Mac silently picks
    the `macosx_14_0` variant — so a correctly *version*-pinned build still
    shipped 189 binaries requiring macOS 14. The fix is a platform-pinned
    wheelhouse, documented in `constraints-macos12.txt`. numpy 2.5.1, scipy
    1.18.0 and Python 3.14 all stay current; **only Qt gives ground** (PyQt6
    6.9.1 / Qt6 6.9.2, because 6.10+ requires macOS 13 while still carrying a
    `macosx_10_14` wheel tag).
  - **The media stack was the real blocker.** Homebrew's libmpv/FFmpeg closure
    targets macOS 14/15. `SingWS-x86_64.spec` can now bundle the IINA-derived
    stack (all dylibs ≤ 10.15) plus `libsingws_mpv_bridge.dylib` instead. This
    also required dropping the out-of-process `mpv` binary and MoltenVK (the
    bridge runs libmpv in process), excluding the `mpv` Python module (whose
    PyInstaller ctypes hook silently re-bundled Homebrew's whole closure), and
    preferring python.org's OpenSSL over Homebrew's macOS-15 build.
  - `build_singws_mac_intel.sh` gained two gates: a pre-build dependency-pin
    check and a post-build sweep of **every Mach-O in the finished bundle**
    against macOS 12.0. Wheel tags and filenames are not trusted — only load
    commands. Result: `verified 873 Mach-O file(s): x86_64 minimum <= macOS 12.0`.
- **`_load_mpv_playback_backend()`** picks whichever mpv backend a build ships:
  `mpv_playback_iina` (in-process libmpv via the native bridge) when its bridge
  dylib sits beside it, otherwise `mpv_playback` (out-of-process Homebrew mpv).
  The call site previously hard-coded `from mpv_playback import ...`, so the
  IINA build would have raised ImportError and dropped every song onto the
  FFmpeg engine with its entire bundled media stack unused. The bridge-presence
  check keeps a source checkout — where both modules exist — on the backend it
  can actually run.

- **The SingWS audio chain now runs on mpv's own audio engine.** Previously,
  choosing mpv for audio silently bypassed loudness normalization, the 10-band
  graphic EQ and the whole master bus: `mpv_playback` drives mpv as an
  out-of-process binary over JSON IPC, so samples never reach Python and the
  NumPy processors in `singws_eq` / `singws_master_audio` have no sample path to
  sit in. New `mpv_audio_filters.py` expresses the same chain as an mpv `af`
  filter chain, applied in mpv, so the audible clock stays with mpv and its
  fast seek and tempo/key are preserved. Filter order mirrors the signal path
  exactly: key → normalize → graphic EQ → gate → tilt EQ → exciter → compressor
  → limiter. Tempo stays out of the chain (mpv's `speed` property), matching
  how the Python transport keeps tempo out of its DSP. The chain is built from
  the *same* engine-param dict that drives `MasterAudioProcessor`
  (`_compute_master_audio_params`), so the friendly knobs keep one source of
  truth, and it is rebuilt live on EQ-slider, master-setting and Simple Audio
  Mode changes as well as at song start.
  - **Normalization and the graphic EQ are exact ports.** Normalization is a
    single `volume=<db>dB`. The EQ is the same bank of RBJ peaking biquads at
    the same frequencies and Q, and is verified against `singws_eq.GraphicEQ` by
    an impulse-response test: **max deviation 0.018 dB across 20 Hz–20 kHz**,
    far below audibility. The test fails above 0.1 dB.
  - **The master bus is an approximation, by design.** `acompressor` / `agate` /
    `alimiter` / `aexciter` are different implementations from
    `MasterAudioProcessor`'s own soft-knee compressor, downward expander, peak
    limiter and exciter. Knob values carry over unchanged (a deliberate choice),
    so settings land in the same ballpark but will not sound identical.
    `chain_fidelity_notes()` names the approximate stages and they are reported
    in the `[MPV-AUDIO]` log line. `gain_reduction_db()` metering is not
    available on this path.
  - Gate/threshold unit conversions are handled where lavfi differs from the
    SingWS params (`agate` takes linear 0..1, not dB; `aexciter` clamps `freq`
    to 2–12 kHz), and the limiter/clip-guard pair collapses to the single
    binding ceiling rather than two limiter passes.

- **Request QR on the rotation window.** The Show Rotation window now carries a
  QR card at the bottom of the shell with a call to action (default
  **"JOIN THE QUEUE!"**, editable in Settings → Network) and the line "Scan with
  your phone camera to request a song". It uses the same URL as the header and
  show-screen QR (`header_qr_url`, falling back to the network tenant page) and
  the same `requests_accepting` gate, so it never advertises a request page that
  is closed. It has its own enable setting (`rotation_request_qr_enabled`,
  default on) separate from `show_request_qr`, because the rotation window is
  usually on a room-facing second display where hosts want the QR even when the
  lyrics output stays clean. `_refresh_show_screen_qr()` now drives both cards,
  so every existing trigger (accepting change, venue switch, settings toggle,
  surface rebuild) refreshes them together; the card is also seeded when the
  rotation window is first built, since that happens lazily. Both keys are
  venue-scoped.

- **WebSocket request relay (v0.3.0.x)** — on wskar.com the app now connects to
  `wss://wskar.com/relay` and fetches new requests the moment the server pushes
  a `requests_available` notification, instead of polling `get_requests.php`
  every couple of seconds.
  - Requests are fetched from `get_requests_v2.php` and **acknowledged after
    they are successfully queued** (lease + ack model), so a request is never
    lost or double-queued across fetches, reconnects, or app restarts; a fetch
    on connect recovers anything submitted while the app was offline.
  - Auto-selects WebSocket mode when the base URL host is `wskar.com` /
    `www.wskar.com` with a tenant + API key configured (override with the
    `request_transport` setting: `auto` / `websocket` / `polling`). Other hosts
    (e.g. beta) and older setups keep the existing polling transport.
  - Reconnects 5 s after a drop with a single socket and timer; host
    remote-controls polling stays active in relay mode (the relay does not push
    host commands). Request polling stays off the whole time.
- **Phrase-Aligned Song Start** — start a song at a musical phrase (4/8/16 bars
  in, or a hand-placed marker) instead of only at the file start, to skip long
  intros and land on a verse/chorus.
  - Right-click a queued song → **Phrase Start** submenu (Beginning / 4 / 8 /
    16 Bars / Custom…). BPM-derived offsets use embedded ID3 BPM when present.
  - **Phrase Start dialog** with a rendered waveform, vertical labeled marker
    lines (4 Bar / 8 Bar / 16 Bar / Custom / Suggested), a position slider, and
    click-to-preview, plus **▶ Preview / ⏸ Pause / ⏹ Stop** controls (preview
    no longer plays on with no way to stop it; closing the dialog stops it too).
  - **Automatic BPM detection** from the audio (pure numpy/scipy tempo
    estimation — no tags or typing needed), cached per song. The 4/8/16-bar
    starts and the Custom dialog now fill in tempo on their own; the dialog also
    **pre-selects a suggested start** so it's near plug-and-play.
  - **Suggested** intro-skip point from a lightweight energy/onset heuristic
    (numpy/scipy, snapped to the nearest bar).
  - **Section detection** — structural boundaries (self-similarity matrix +
    Foote novelty, pure numpy/scipy, no new dependencies) shown as labeled
    waveform markers for the transitions (Intro→Verse, Verse→Chorus,
    Chorus→Verse, Instrumental→Vocal). Boundaries are reliable; section labels
    are best-effort **estimates** the host can rename/correct.
  - Markers persist per song and are **reused automatically**; **JSON
    export/import** for backup; **cloud backup/restore per tenant** (pushes on
    change, pulls on startup) so markers survive a reinstall / new machine.
  - Playback reuses the existing gap/overlap-free transport, so lyrics, CDG,
    MP4 video and audio stay in sync after the jump, and key/tempo changes are
    preserved.
- **Rotation Lock** (Rotation mode only) — a **Lock** button in the queue
  controls. While locked, new singers are woven into the **next** rotation
  instead of cutting into the current one; existing singers adding another song
  keep their slot. The lock auto-clears when the next rotation starts, and when
  switching Queue Mode back to Classic. State is saved in settings (survives
  restart). Covers manual host adds, remote/web requests, and Singer History
  adds. Classic mode is unchanged.
- **Host song-limit override** — the operator can add a 3rd+ song for a singer
  past the per-singer cap; public/web/Singer-History requests stay capped.
- **Intro Loop** (between-songs filler, opt-in) — when a song ends, the next
  queued song's intro auto-loops a chosen number of bars (4/8/16) starting at
  its phrase point (reuses the auto-BPM / phrase markers) instead of background
  music; hitting **Play/Next** releases the loop and that same track continues
  past the loop and plays through. Settings toggle + bar selector; a `LOOPING`
  badge shows while held. Default OFF — Classic playback is unchanged.
  - **Beat-aligned & accurate**: loops now snap to a detected **beat grid**
    (start on a downbeat, span exactly N bars) using improved, octave-hardened
    tempo + beat-phase analysis (pure numpy/scipy) — so they sit on the groove
    instead of drifting. Bar-derived phrase starts are beat-aligned too.
  - **Analyze Library** (Library Tools) — batch-detect tempo + beat grid for
    every song and cache it (incremental, resumable, cancellable progress), so
    loops/starts are accurate and instant. "Re-analyze All" forces a full pass.

### Changed
- **mpv stays opt-in; FFmpeg/SignalSmith remains the default engine.** An
  earlier revision of this changelog made mpv the default, force-migrated saved
  settings onto it, and removed the engine checkbox. That was wrong and is
  reverted: `MpvKaraokeTransport.set_video_offset_ms()` is a no-op, so the
  calibrated **+750ms CDG baseline never reaches mpv's renderer** and the
  Display tab's fine tuning does nothing while mpv is on — CDG lyrics run
  badly out of time with no way to correct them. (Under `mpv-video` the offset
  lands on the audio-only Python transport, which renders nothing, so the
  result is the same.) The chooser is back in Settings → Audio → Playback, now
  labelled with that limitation, and both checkboxes read the saved engine by
  testing for their own value. A CDG song started on mpv with a non-zero
  offset now logs a `[VIDEO-OFFSET] WARNING` naming the cause and the fix.
  MP4 playback is unaffected. Making mpv the default again requires plumbing
  the offset into `MpvPlaybackPlugin._sync_loop`, where `delta = master - t`
  would become `delta = (master + offset) - t`, and calibrating it live.

### Fixed
- **KaraFun automation sent nothing to KaraFun all night (2026-08-06).** Every
  attempt died in `_ensure_karafun_audio_output()` with
  `ERROR|KaraFun Audio output button was not found` — five attempts, zero
  successes, and the check has never once passed since it was added (Aug 1-5
  logs show 0 route verifications and 0 automation failures; Aug 6 shows 5 of
  each). It runs *before* the song is searched or played, so nothing ever
  reached KaraFun. Three causes, all fixed:
  - The window loop assigned `mainWindow` on **every** non-"Dual Renderer"
    match, so the **last** window won — any popover, sheet or utility window
    KaraFun had open became the search target and the route button was
    genuinely absent from it. (The sibling renderer script guards this with
    `if mainWindow is missing value`.) The search now walks **all** non-renderer
    windows instead of committing to one.
  - The element filter only accepted `AXButton`, skipping the `AXPopUpButton` /
    `AXMenuButton` a route picker usually is. It now accepts all three and also
    matches an "output device" label.
  - The failure message named only what was missing, making every failure
    undiagnosable. It now reports the button-like elements it *did* find
    (`candidates=[role:label]…`).
- **An unreadable route control no longer kills the song.** The gate exists to
  stop audio escaping to AirPlay/a TV — so a *wrong or unavailable* device still
  hard-blocks. But failing to *read* the control is not evidence of an unsafe
  route, and treating it as one cost a full show. It now logs a loud
  `[KARAFUN-AUDIO] WARNING` and proceeds. Set `karafun_audio_route_strict: true`
  to restore blocking.

- **mpv appeared to "not save" and needed re-enabling after every restart.** It
  was saving correctly the whole time — two separate things reported it wrong:
  - The Settings checkbox was rendered with
    `settings["karaoke_engine"] == "mpv"`, but ticking mpv *and* "Keep the
    SingWS audio engine" saves `"mpv-video"`. So the box came back unticked on
    every reopen even though mpv was on, and re-ticking just rewrote the same
    value — the endless cycle.
  - The startup banner logged a hardcoded
    `- Karaoke engine: FFmpeg/Qt (GStreamer removed)` regardless of the real
    engine, so logs from mpv sessions "confirmed" the false revert. It now
    reports the configured engine via `_configured_karaoke_engine_label()`,
    honouring `SINGWS_KARAOKE_ENGINE` and the legacy Intel build.

- **Analyze Library progress window** — opens on top of the main window (was
  hidden behind it); clicking Analyze again while a pass runs resurfaces the
  existing window instead of spawning duplicates; a corrupt/locked file now
  times out (~60s) and is skipped instead of hanging the whole batch.
- **Rotation Lock logic** (ported from the 0.3.1.0 build) — locked newcomers
  are woven in **behind** returning singers (no longer cut ahead); the lock
  only engages while the marked next-rotation singer isn't already next (button
  disabled with a tooltip otherwise; no safe gap → newcomers append like
  unlocked rotation); a stale saved lock in that state clears itself.

### Removed
- **Video-quality settings for slower machines.** mpv plays smoothly at full
  resolution, so the settings that traded picture quality for frame rate are
  gone:
  - `mp4_max_height` (the Settings → Display "MP4 Video Quality" 720p/1080p/
    Native chooser), its `_effective_mp4_max_height()` helper, and the hard
    Intel-Mac clamp that overrode a Native/1080p choice back to 720p. The
    FFmpeg fallback transport now defaults to `max_video_height = 0` (native);
    its measured-headroom downshift for sources that genuinely cannot keep up
    is unchanged. Stale `mp4_max_height` keys are dropped from settings files
    on load.
  - The Intel-Mac show-effects backoff, which silently forced rotation and
    ticker effects off regardless of the host's own `rotation_vfx_enabled` /
    `ticker_vfx_enabled` toggles. Those toggles now decide on every machine.
  - Background-video quality settings (`bg_video_quality`,
    `bg_video_auto_transcode_720p`) are **unchanged** — they govern the
    decorative layer behind transparent CDG lyrics, not karaoke playback.
- **GStreamer** — fully removed. The FFmpeg/Qt engine (`PythonKaraokeTransport`)
  is now the sole live karaoke engine for CDG, MP4, and audio-only playback,
  and BASS (with an FFmpeg/Qt recovery engine) drives background music and the
  soundboard. Deleted the GStreamer transport, OpenKJ audio backend, native
  SoundTouch plugin, the appsrc CDG wrapper, and all GStreamer bundling from
  the PyInstaller specs and build scripts. The arm64 installer drops from
  ~204 MB to ~113 MB; the frozen app no longer links to or ships any GStreamer
  framework, plugins, typelibs, or scanner. The `karaoke_engine` setting still
  exists; a stale `gstreamer`/`auto` value maps to FFmpeg.

### Notes
- Pairs with the SingWS-Server marker-sync endpoints for cross-machine markers.
- Tests: `test_phrase_markers.py`, `test_phrase_detect.py`,
  `test_rotation_lock.py`, plus the existing rotation/regression suites.
  Run with `SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS=1 .venv/bin/python -m pytest`.
