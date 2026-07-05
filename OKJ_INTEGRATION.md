# OpenKJ → singws Migration Kit

This folder contains Python ports of OpenKJ's (2.1.39-unstable) performance-critical
subsystems, ready to be integrated into **singws** (a Python karaoke app on macOS).
These instructions are written for Claude Code to execute the integration against
the actual singws codebase.

## Why this migration

singws currently suffers from:
- High CPU load during CDG rendering (likely fixed-fps rendering + per-frame
  RGB conversion in Python)
- Severe system slowdown during live key (pitch) and speed (tempo) changes
  (likely in-process Python DSP)
- A hardcoded ~500ms lyrics/audio offset compensating for pipeline latency

The fix: adopt OpenKJ's architecture — GStreamer does all audio DSP in native
code, CDG frames are emitted only when pixels actually change, and A/V sync is
clock-driven instead of offset-driven.

## Modules in this kit

| File | Replaces in singws | Depends on |
|---|---|---|
| `okj_audio_backend.py` | audio player, key/pitch changer, tempo/speed, EQ, fades, silence *sensing*, hang detection | PyGObject + GStreamer (`brew install gstreamer pygobject3`) |
| `okj_cdg.py` | CDG decoder/renderer | numpy |
| `okj_fileinfo.py` | filename → artist/title/song_id parsing; brand priority selection | stdlib only |
| `okj_ticker.py` | scrolling ticker (rules/tips/rotation) | Pillow (+ optional PySide6/PyQt5) |
| `gst_bootstrap.py` | — (new: runtime env setup for bundled GStreamer) | stdlib only |
| `wildstyle.spec` | — (new: PyInstaller bundling of GStreamer into the .app) | PyInstaller |
| `build_app.sh` | — (new: build + self-containment verification) | — |

All modules are self-contained and have no imports between each other, with one
optional data hand-off: `CdgReader.position_of_final_frame_ms()` feeds
`AudioBackend.cdg_final_frame_ms`.

## Integration order — DO NOT reorder

Each step must leave singws fully working before starting the next. Run the
app and test after every step.

### Step 1 — Audio backend, playback only
- Replace singws's audio player with `AudioBackend` using ONLY:
  `load()`, `play()`, `pause()`, `stop()`, `seek_ms()`, `position_ms()`,
  `duration_ms()`, `set_volume()`.
- Do NOT wire key/tempo/EQ yet. Leave singws's existing CDG renderer alone.
- The GLib main loop must be running for bus messages and timers. If singws
  is Qt-based, GLib and Qt loops need bridging: simplest is running
  `GLib.MainContext.default().iteration(False)` from a 10–20ms QTimer, or
  drive playback in a thread with its own GLib.MainLoop. Choose based on
  singws's existing structure.
- **Test gate:** plain playback of mp3, zip-extracted mp3, and mp4 works;
  pause/seek/volume work; existing lyrics display still functions.

### Step 2 — Key, tempo, EQ
- Wire existing singws UI controls to `set_key_change(semitones)`,
  `set_tempo(percent)`, `set_eq_level(band, db)`, `set_eq_bypass(bool)`.
- DELETE singws's old DSP code paths (this is the CPU win). Search for and
  remove any librosa/pydub/scipy/numpy per-buffer audio processing.
- EQ band centers for slider labels: 29, 59, 119, 237, 474, 947, 1889,
  3770, 7523, 15011 Hz. Gain range −24..+12 dB.
- **Test gate:** key change is instant and doesn't interrupt audio; tempo
  change works during playback; EQ bypass toggles without a gap; CPU during
  a key-changed song is dramatically lower than before.

### Step 3 — CDG renderer
- Replace singws's CDG decoder with `CdgReader`.
- Presentation must be driven by `AudioBackend.position_ms()` (the pipeline
  clock), NOT wall-clock timers: display the frame whose
  [position, position+duration) range covers the current audio position.
- Render loop pattern:
  ```python
  reader = CdgReader(path)
  # on a display timer (or vsync callback):
  pos = backend.position_ms()
  while reader.current_frame_position_ms() + reader.current_frame_duration_ms() <= pos:
      if not reader.move_to_next_frame():
          break
  blit(reader.current_frame_rgb())   # only if frame index changed
  ```
- Only blit when the frame actually changed (compare pkt index / keep a dirty
  flag) — this preserves the change-driven CPU savings.
- **REMOVE the 500ms offset.** Set it to 0. Test with a hard visual cue on a
  downbeat. If a small constant residual remains, expose it as a ±ms setting,
  but expect not to need it.
- On seeks: call `reader.seek_ms(target)` alongside `backend.seek_ms(target)`.
- **Test gate:** lyrics sync with offset=0; backward/forward seeks land
  in sync instantly; CPU during CDG playback is near-zero while lyrics are
  static.

### Step 4 — Silence sensing, CDG end gate, watchdog, fader
- Keep singws's EXISTING silence/overlap logic (it is better than OpenKJ's —
  it overlaps break music instead of leaving dead air). Feed it from
  `backend.on_rms` / `backend.is_silent(threshold)` instead of whatever
  audio tap it used before.
- ADD the CDG end gate to that logic: set
  `backend.cdg_final_frame_ms = reader.position_of_final_frame_ms()` after
  load (value becomes valid once the file has been fully scanned or played;
  it returns -1 until EOF has been reached — call after a full pre-scan
  or check lazily), and require `backend.cdg_lyrics_finished(pos)` before
  treating a CDG track as over. This prevents cutting quiet outros with
  lyrics still on screen.
- Wire `backend.on_playback_hung` to singws's backup-music trigger.
- Replace singws's fade/crossfade implementation with `fade_in()` /
  `fade_out(4.0)` (cubic, dedicated fader element, doesn't touch user volume).
- **Test gate:** break music overlap behavior matches or beats current
  singws behavior; a simulated stall (suspend the process feeding audio)
  triggers the hung callback within ~5s; fades sound smooth start-to-finish.

### Step 5 — Fileinfo/brand + ticker (leaf modules)
- Point `PatternResolver.from_openkj_db()` at
  `~/Library/Application Support/OpenKJ/openkj.sqlite` (read-only) OR
  construct `PatternResolver({path: KaraokeFilePattern(...)})` from singws's
  own library config if it has one.
- Use `brand_of()` / `brand_priority()` / `pick_preferred()` for version
  auto-selection. Brand priority order (already encoded): KV → ZOOM → CC →
  KARAFUN → PT → SBI → SC → SF → WSK → ME.
- AUDIT: run `brand_of()` over all song IDs in the library; review Nones and
  misclassifications; tune `BRAND_PATTERNS` regexes at the top of
  `okj_fileinfo.py`.
- Replace the ticker with `TickerWidget` (if singws is Qt) or `TickerStrip`
  (any other toolkit: call `advance()` on a frame timer, blit
  `current_crop()`).
- **Test gate:** known filenames parse correctly; brand auto-pick chooses KV
  over SC/SF duplicates; ticker scrolls smoothly with an invisible wrap seam.

### Step 6 — Bundling (LAST, only after everything runs from source)
- Ensure singws's entry point starts with:
  ```python
  import gst_bootstrap
  gst_bootstrap.setup()   # BEFORE any `import gi`
  ```
- Edit `wildstyle.spec`: set APP_NAME / ENTRY_SCRIPT / BUNDLE_ID for singws.
- Run `./build_app.sh`. It fails the build if any bundled dylib still
  references /opt/homebrew (non-relocatable).
- Trim/extend PLUGIN_ALLOWLIST in the spec to the formats singws actually
  plays.
- **Test gate:** built .app runs on a fresh user account (no Homebrew in
  PATH), plays all library formats, key/tempo/EQ work.

## Known integration decisions to make on-site
1. **GUI toolkit**: determines GLib/event-loop bridging (step 1) and ticker
   flavor (step 5).
2. **CDG display path**: either blit `current_frame_rgb()` to singws's
   existing surface (simplest, do this first) or later feed
   `current_frame_indexed()` into a GStreamer appsrc with RGB8P caps for the
   full OpenKJ zero-copy path.
3. **Zip (MP3+G) handling**: GStreamer can't read inside zips. If singws
   plays zips, extract with `zipfile` to a temp dir before `load()`.

## Hard rules
- Never reintroduce per-sample/per-buffer audio processing in Python.
- Never render CDG on a fixed-fps loop; presentation is position-driven and
  change-driven.
- The 500ms offset must end this migration at 0 (or an exposed user setting).
- singws's silence/overlap DECISION logic is kept; only its SENSING is
  replaced. Do not port OpenKJ's 2-second-wait cutoff behavior.
