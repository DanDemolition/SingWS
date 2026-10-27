# Stage E: the karaoke display window (design, 2026-10-26)

Status: NOT BUILT as a display, on purpose. The first two testable pieces (a frame-exact CDG decoder and an isolated demo window) exist since 2026-10-26: see "First pieces: BUILT" at the end. Nothing here drives or replaces the live display. The TV rotation window is built (`TvWindow.qml`). This document says why the karaoke display window is different and what the safe path to it is.

## What the app's display window is
`VideoWindow` (0.2.18.1.py, about 5,900 lines of the file around it) is not a viewer. It is where the show is drawn: the video surface (mpv through the video bridge, setting `video_bridge`, default "rust"), the CDG graphics painter (`set_karaoke_frame`, scaled and near-black handling), the ticker and show-effects surfaces (Qt Quick layers that must be re-raised after geometry changes), the idle overlay, the output-window geometry and full-screen logic across monitors, and the render-thread option. The picture the audience sees is produced inside the Python process, driven by the playback engine that Python still owns (song start/stop, seek, key/tempo, KaraFun capture).

## Why a read-only shell window cannot be "the display"
To show the real video the shell would have to PLAY it (decode, render, stay in sync with the audio the app is playing). Two players for one song is wrong, and taking playback away from Python is the later, show-gated step (the transport drives: media end and KaraFun end must first be CLEAN over several shows; the 2026-10-25 show had media-end 37/37 and KaraFun-end 4 of 6 disagreeing, fixed but unproven). So building the window now would be either a copy with nothing in it or a second player.

## What is true today (the parts that make it possible later)
* Rust already renders video: `rust/crates/singws-video` (libmpv render API on OpenGL, scanner, audio-device handling) is what the app loads as its video bridge by default.
* Rust already plays karaoke audio (`karaoke_audio_engine: rust`, `rust_karaoke_voice.py`) and background music (`singws-bgm`).
* The tickers and effects are already Qt Quick, so they move into a Rust-owned Qt Quick window without a rewrite.
* The audience rotation screen is built in Qt Quick (`TvWindow.qml`) from the same parity-tested view model.

## Path (in order, each step only after the one before is proven in a real show)
1. Stage A/B drives: Rust owns the song lifecycle decisions (media end, KaraFun end, watchdog) with Python as fallback; then Python's rules are retired one at a time.
2. A Rust-owned playback orchestrator: queue to "play this" (library files, CDG pairs, MP3+G zips, KaraFun capture), using the Rust audio engine and video bridge.
3. The display window in the shell: one Qt Quick window with the video item, the CDG painter, ticker and effects layers, idle overlay and the monitor/fullscreen rules. At this point the Python window is switched off (setting-gated, with rollback), not before.

## What can be built before that (small, optional)
A read-only OUTPUT PREVIEW in the shell, the same picture the phone page shows (`/api/show-screen/snapshot.php`, JPEG frames the app uploads while the preview setting is on; needs the host session cookie the shell already obtains at login). It is a monitor, not a display: roughly a second of delay and compressed frames. Not built yet; ask if it is wanted.


## Output preview: BUILT (2026-10-26)
The optional read-only OUTPUT PREVIEW now exists in the shell's Controls tab ("Show karaoke output preview", off by default). `singws-control::preview` polls `/api/show-screen/snapshot.php` with its own host login (a second in-memory session cookie), every 0.4 s while a song plays, 1 s when paused, 1.5 s when idle, and stores the JPEG the app uploaded under `~/Library/Application Support/SingWS-Shell/preview/`. It is a monitor, not the display: about a second behind, no sound, and every poll counts as a viewer, so the app spends a little CPU capturing frames only while the box is ticked. Tests: 7 Rust unit tests (frame rules, cadence, JPEG check, status words, session calls against a stub) and two shell loopback scenarios against the real PHP endpoints (a frame is shown byte for byte, a watcher is registered only when switched on, an idle screen says so). The loopback writes the producer's files directly because the loopback PHP has no API-key database; the app's real upload is exercised only in a show.
Screenshot: docs/verification/2026-10-26-rust-shell-output-preview.png.


## First pieces: BUILT (2026-10-26, branch `claude/rust-cdg-display-af96b7`, offline and isolated from the app)

### 1. `rust/crates/singws-cdg`: the CDG decoder
A dependency-free Rust decoder for CD+G (24-byte packets, 300 packets a second): memory preset, border preset, tile block normal and XOR, scroll preset and copy, define transparent colour, load colour table low and high. `CdgDecoder::apply(packet)` mutates a 300x216 indexed frame plus palette; `to_rgba()` gives straight-alpha RGBA; `CdgPlayer::render_at(seconds)` is the picture in force at a time (state after packet `floor(t*300)`, backward seeks replay from a checkpoint taken every second). `singws-cdg-dump FILE.cdg [--at SECONDS ...]` writes raw RGBA frames for tests.

**What "frame-exact against the app" means here (a correction to the brief).** The app has no Python CDG pixel renderer. CDG is decoded by libmpv through FFmpeg's `cdgraphics` decoder (the app bundles FFmpeg 8.1.3; the reference used for the tests was Homebrew's 8.1.1, the decoder is unchanged between them as far as the results show). `transition_analysis.analyze_cdg_visual_bytes` only simulates tiles to find a blank tail, it does not render. `set_karaoke_frame` takes whatever QImage the video bridge produced. So FFmpeg itself is the reference, which is also the stricter test: it is exactly what the audience sees today. There is also no 6-pixel border cropping in the app: libmpv shows the whole 300x216 frame including the border colour, and so does the Rust decoder; the app's own display modes (`cdg_display_mode`: fit, sidefill, blur, stretch) and the near-black clean-up (`_clean_cdg_near_black`) are done afterwards on the QImage and are NOT ported yet.

**Result.** `tools/cdg_diff.py` feeds the same CDG bytes to `ffmpeg -f cdg` and to `singws-cdg-dump` and compares every frame byte for byte, then checks 33 time points per stream (`render_at(t)` against the FFmpeg frame in force at `t`, read from ffprobe's pts). On this Mac: 404 streams (4 hand-built ones that use every instruction, scroll in all 32 command combinations with fine offsets, repeat bytes, out-of-range tiles, non-CDG packets, unknown instructions, plus 400 seeded random-fuzz streams) = 164,504 frames, 0 differences. `python3 tools/cdg_diff.py --fuzz 400` reproduces it (needs ffmpeg and ffprobe; the dump binary comes from `cargo build --release -p singws-cdg`). `tools/mutate_cdg.py` mutates the decoder (80 mutants: 69 killed, 11 equivalent and documented in the script, 0 survivors) and requires the crate tests or the differential test to catch each change.

**FFmpeg behaviours the decoder reproduces (all found by the differential test, none guessed):** unset palette entries are transparent black (alpha 0), not opaque black; `DEFINE_TRANSPARENT` stores 16 six-bit values that become palette alpha `255 - 4*v` only when a palette is loaded afterwards; the memory and border presets act only when their repeat nibble is 0; the fine scroll offsets also move where later tiles land; in a scroll the horizontal offset moves the picture right and the vertical offset moves it up; a tile outside the picture (or a non-CDG packet) produces no frame; a scroll-copy strip does not follow the other axis' shift.

**One FFmpeg behaviour that is NOT reproduced, and a risk to look for in a show.** A scroll packet that moves nothing (offsets equal to the current ones, no scroll command) makes FFmpeg swap in a fresh frame buffer and return before filling it, so what is shown depends on recycled memory: all transparent black when the buffer is new, an older frame otherwise (the same stream gave both in our experiments). There is no reproducible reference for it, so the Rust decoder keeps the picture and counts the event (`CdgDecoder::noop_scrolls`), and `cdg_diff.py` neutralises such packets before comparing. If real discs send these, the audience may today see an occasional blank or stale frame from libmpv that the Rust display would not show. The shadow comparison below is how to find out how often a real disc does it.

### 2. The demo window in the shell
`KaraokeDisplay.qml` (a `Window`, like `TvWindow.qml`) is created by `Main.qml` but shown only with `--display-demo FILE.cdg`; with no argument nothing changes for the existing tabs. It plays the file in real time (looping) or holds a moment with `--display-at SECONDS`, fits the 300x216 picture into the window on black (the "fit" mode), and draws a ticker strip at the bottom. The picture reaches Qt Quick through an image provider: `cpp/cdg_provider.cpp` registers `image://cdg/<milliseconds>` on the engine and asks Rust (`src/cdg_demo.rs`, `singws_cdg_demo_frame`) for the pixels; the `CdgDemo` QObject loads the file and reports its length. The demo reads no show data, makes no network call and plays no sound. Options: `--display-ticker "TEXT"`, `--display-time MM:SS`, `--display-snapshot FILE.png` (grab the window and quit; the shell's existing `--snapshot` mechanism, run with `QT_QPA_PLATFORM=offscreen`).

The ticker is only the strip: `singws-uimodel` has no ticker rules (its rotation model feeds the TV rotation window), so the strip copies the geometry rules of the app's `QML_TICKER_RT_SOURCE` (black bar, green bold text scrolling at 78 px/s from the right edge to off the left, countdown pinned right with a fixed gap). Its content is a placeholder argument. The ribbon, glass pill, pulse and queue-change effects are not ported. Making the strip show the real ticker text means porting the app's ticker text rules into `singws-uimodel` with a parity test, the way the rotation and history views were done.

Build and run (Homebrew Qt; own target dir):
```
export PATH="/opt/homebrew/opt/qt/bin:$HOME/.cargo/bin:$PATH" QMAKE=/opt/homebrew/opt/qt/bin/qmake6 CARGO_TARGET_DIR=$HOME/.cache/singws-rust-target-cdg
cd rust && cargo build --release -p singws-shell
python3 ../tools/make_demo_cdg.py /tmp/demo.cdg      # a made-up karaoke screen, no show data
QT_QPA_PLATFORM=offscreen $CARGO_TARGET_DIR/release/singws-shell --display-demo /tmp/demo.cdg --display-at 4.5 --display-snapshot out.png
```
Screenshot of the result: docs/verification/2026-10-26-rust-cdg-display-demo.png.

### What remains, in order
1. **Display modes and clean-up on the picture.** Port `cdg_display_mode` (fit, sidefill, blur, stretch) and `_clean_cdg_near_black` (threshold, transparent black), each with a pixel comparison against the Python function on the same RGBA frame (those two are plain Python/Qt image code and CAN be compared directly).
2. **Video.** MP4 and background video through the existing `singws-video` crate (libmpv render API on OpenGL). That crate already owns the picture for the live app, so the display needs a Qt Quick item that shows its texture; the open question is the GL context sharing with Qt Quick's scene graph (the app today uses native views stacked by creation order, AGENTS rule 6, which a Qt Quick item would replace).
3. **Clock.** Replace the demo's own timer with the audible position from the Rust engine (`current_audible_time`), plus the CDG timing offset (`FFMPEG_CDG_BASE_OFFSET_MS` and the user's fine tuning) and key/tempo scaling, so lyrics stay on the voice.
4. **Effects and ticker.** Singer-start, next-up and outro effects (the QML in `QML_SHOW_SCREEN_VFX_SOURCE`, which is already QML and moves nearly as is) and the real ticker text and effects.
5. **Idle overlay and rotation screen** on the same window (`TvWindow.qml` content), then **monitor and full-screen rules**: which screen, hidden when the host window is minimised, restore after sleep, window level above KaraFun, the re-raise of native surfaces after geometry changes. These are the part tests cannot cover: they need the real TV and the on-screen checks of AGENTS rules 4 and 6.
6. Only then the switch-over, setting-gated with rollback, and only after the transport drives have had clean shows.

### How a shadow comparison with the live Python display would work
Same gate policy as the other shadows: log-only, Python wins, off the audio path.
* **CDG picture (decoder level).** At song start the app already has the CDG bytes (`play_cdg_mp3_dual`, `_play_cdg`). A shadow hook hands the same bytes to `singws-cdg` and, every second or so (not per frame), asks for the picture at the mpv clock position the app reads for the preview and compares it with `video_bridge`'s `grab_frame` (the bridge already exports a frame grab for the show-screen snapshot; the app already keeps one for the phone preview). Compare the RGBA pixels, ignoring only the 1-2 frames around a seek where mpv's clock and the grab can legitimately differ by a packet. Log `[CDG-SHADOW] matched=N mismatched=M noop_scrolls=K`, and on a mismatch the packet index and the pixel count. This is also how the no-op scroll risk above gets measured on real discs. Cost: one more decode of a file the app already holds, on a worker thread; keep it off by default behind a setting like the other shadows.
* **Display level (later).** Once the shell window draws video too, compare what the two windows show by screenshot (`screencapture -x -D`) of the same moment during a rehearsal, as a manual check, since two windows of one song cannot both own the audio clock. That is a rehearsal exercise, not an automated shadow.
* **Evidence for the gate.** `tools/shadow_report.py` gets a CDG-SHADOW row; CLEAN means at least one compared song, zero mismatches that are not at a seek, and the no-op scroll count reported separately.

Not done and not promised by this step: any change to `VideoWindow`, the video bridge, a setting, or a default.
