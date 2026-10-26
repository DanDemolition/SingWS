# Stage E: the karaoke display window (design, 2026-10-26)

Status: NOT BUILT, on purpose. The TV rotation window is built (`TvWindow.qml`). This document says why the karaoke display window is different and what the safe path to it is.

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
