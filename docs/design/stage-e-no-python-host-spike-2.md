# Stage E, spike 2: a Rust host plays a real CDG+MP3 song, no Python (2026-11-01)

Tool: `rust/spike_host.sh --arch arm64|x86_64|both --out DIR --play "SONG.cdg"` (the `.mp3` must sit beside it), or the bundle's binary directly:
`singws-shell --play-demo SONG.cdg SONG.mp3 [--play-demo-report F.json] [--play-demo-frame F.rgba] [--play-demo-full] [--play-demo-seek-end SECONDS]`.
Code: `rust/crates/singws-shell/src/karaoke_proto.rs` (the transport protocol as a pure state machine, 13 unit tests against fakes),
`play_demo.rs` (the real wiring + analysis, 6 tests), `qml/PlayDemo.qml`. Throwaway bundle `SingWS Spike <arch>.app`, `SINGWS_HOME` scratch, audio HEADLESS
(no sound card is opened: nothing was audible). The song files were only read. No change to `play_next_file`, queue, rotation, chat, relay or settings code.

## Answer

**Feasible. A Rust process plays a real CDG+MP3 song the way the app does: picture (Rust video bridge + libmpv, CDG), sound (Rust karaoke voice, shadow audio ON so mpv
keeps only the picture and the clock), the voice slaved to the picture clock every ~30 ms, start protocol, seek, pause/resume, natural end, clean teardown.**
Measured on the real song `CC - Radiohead - Let Down` (300.56 s). It is NOT yet a transport: the port covers start/follow/seek/pause/resume/stop/end only (see the last list).

## Evidence (Apple M1 Max, macOS 27.2; x86_64 = Rosetta, not Intel hardware)

Scenario: play 8 s, seek to 60 s, play 4 s, pause 1.5 s, resume 3 s, seek to the last 30 s, play to the natural end, 3 s tail. Plus one full-length run (no seeks).

| | arm64 (scenario) | x86_64 under Rosetta (scenario) | arm64 full song, 300 s |
|---|---|---|---|
| picture presented (bridge `present_count`) | 3122 frames | 3110 | 21052 |
| presentation verdict | OK, bridge said nothing | OK | OK |
| sound-vs-picture error, steady (voice audible position minus mpv clock) | median 9.3 ms, p95 43.9 ms, max 76 ms (320 samples, 100 ms apart) | median 8.0, p95 46.1, max 65 | median 11.5, p95 55, max 222 (1979 samples) |
| same, every 10 ms tick | median 5.9, p95 45 | median 5.9, p95 43 | median 7.1, p95 48 |
| seek (pause both, move both, voice first, picture when the voice clock moves): call to Running | 351 / 337 ms | 348 / 337 ms | n/a |
| pause 1.5 s: picture / voice moved | 0 ms / 0 ms | 4 ms / 0 ms | n/a |
| natural end: `Ended` | 48.1 s | 48.1 s | 303.6 s |
| audible blocks after the voice's FINISHED flag / more than 250 ms after the end | 0 / 0 | 0 / 0 | 0 / 0 |
| position jumps BACKWARD while audible (earlier audio replayed) | 0 | 0 | 0 |
| last audible block (song position) | 298.24 s | 298.21 s | 298.35 s |
| engine underruns / gap frames / dropped commands | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| `[KARAOKE-RUST]` log | 1 line: kept its place near the end (below) | same | same |
| teardown (bridge destroy, voice stop, render thread join, keep-awake end), exit code | clean, 0 | clean, 0 | clean |

- Reference for "the last audible moment": the MP3 itself is 300.559 s long, its last sample above -80 dBFS is at 298.49 s (ffmpeg decode). The block analysis (RMS of 512-frame blocks
  above -80 dBFS) puts the voice's last audible block at 298.2 to 298.35 s, i.e. at the file's own last audible moment; nothing was audible after it, none after the
  FINISHED flag, none after the stop. The real-show symptom (a quarter-second clip of earlier audio at the end of CDG songs) did NOT occur here: no backward position jump, no audible block after the end, in three runs.
- The tool catches that symptom: `analyze_blocks` unit test "a quarter-second clip of earlier audio at the end is caught" feeds exactly that shape and fails it.
- The picture was looked at: `docs/design/stage-e-no-python-host-spike-2-frame.png` is the bridge's own frame readback (`grab_frame`, the CDG title card "Radiohead / [Let Down] / CC Karaoke"),
  not a screenshot of the window, so it proves the CDG decodes and renders into the bridge's texture; that the NSView itself showed it is evidenced by the bridge log
  (`output view presenting`, `preview view presenting`) and `present_count`, not by eyes. No screenshot was taken (it can raise a Screen Recording prompt).
- Not heard: nothing was played through a sound card, so "sounds right" and the sound card's own latency are NOT measured. In the real app `set_output_latency_seconds` tells the voice the device latency;
  the demo's engine is headless, so the error numbers above are voice-clock-vs-mpv-clock, not acoustic.

### Findings in the song's last seconds (arm64, x86_64, full run: identical)

1. **mpv's picture clock (`audio-pts`) falls back to 278.033 s for ~0.3 s at 300.2 s, 0.35 s before the end, then jumps to the end** (Intel/Rosetta the same). The voice must not follow that; it does not:
   `[KARAOKE-RUST] voice kept its place (close to the end of the song): song 300.22s, picture clock 278.03s, song length 300.6s`. That is the guard in the Rust voice (HEAD commit ea57c85), working through the C ABI
   from a Rust host. A host that slaved naively would have re-sought the voice 22 s back: this is the most likely mechanism of the real-show "clip of earlier audio". The real transport must keep slaving through `slave_clock` (not its own re-seek) near the end.
2. **A seek to within ~20 s of the end ends the picture at once.** Seeks to 60 s, 240.6 s, 270.6 s are fine (~340 ms to Running). Seeks to 280.6 s, 285.6 s, 288.6 s, 292.6 s: the bridge's position reads 300.503 s immediately, `at_end` is true
   within ~0.5 s and the song ends while the voice is at the seek target (12 s of the song lost). `singws_bridge_seek` sends `seek <s> absolute+exact`; cause (mpv/ffmpeg CDG demuxer with external audio) NOT determined. It is in the shared bridge, so the Python app very likely has it too
   (not reproduced there). Evidence: `--play-demo-seek-end 12|15|20` vs `30|60`. Left unfixed: not demanded by this task, and AGENTS rule 10.
3. The end-of-song signal is mpv's `at_end` (as in `MpvKaraokeTransport._poll`); the voice's own FINISHED flag came ~0.2 s later. The port stops the voice at `Ended` like Python does.

## The three traps of spike 1, and what was done

1. **Hidden/unshown view: the bridge silently drew nothing.** Fixed in the HOST, not the bridge: `play_demo.rs::view_problem` asks AppKit (window exists, `isVisible`, view not hidden, no hidden ancestor) and the QML polls it
   every 100 ms until true (gives up with a clear failure after 5 s, exit code 2, report written). After the song a `presentation` check requires `present_count > 30` and, if not, prints the bridge's own reason lines (`view-hidden`, `window-not-visible`, `no-window`, `NO PICTURE`)
   captured through `singws_bridge_set_log_callback`. **A fourth trap found on the way (this one cost a run):** `singws_bridge_create(output, 0, ...)` with ONE view runs the core headless (`vo=null`) and presents nothing, with no log line. The first run had present_count 0 for the whole song while position and `visual_ready` looked healthy.
   Additive fix in `singws-video/src/core.rs`: it now logs `[bridge] NO PICTURE: output_view=.. preview_view=..; both must be non-zero, running headless`. Behaviour unchanged (log only); the host passes both (output + preview windows, as the app does).
2. **libmpv beside the executable only:** already fixed on main before this task (`mpvapi::candidates()` tries `../Frameworks/singws_libmpv.2.dylib`, unit test exists). Re-verified: both bundles load libmpv with no `SINGWS_LIBMPV`.
3. **Stray text file breaks strict signing:** `spike_host.sh` now copies only `*.dylib` into `Contents/Frameworks` (skipping the Objective-C bridge) and aborts if any non-dylib is there. Both bundles strict-verify.

## New traps found in this spike

- **App Nap.** A first full-song run launched from a terminal lost the first ~60 s of clean play, then the process was throttled (timeline samples 10 to 30 s apart, picture at a fraction of wall time) and the voice re-synced ~60 times. The Python app holds an `NSProcessInfo` activity assertion for this (`mac_keep_awake.py`). The Rust host now calls `singws_mac::keep_awake_begin` (already in the repo, tested) at start; the same song then ran 300 s with zero resyncs. The host MUST hold it for the whole session (another session's full test suite was loading the machine at the time, which made it visible).
- **Both views are required** (above). **A view must be on screen** (above).
- Position source: the bridge reports `audio-pts` (the external MP3's clock) as the picture position for CDG, which is also why finding 1 shows up.

## What the real Rust transport must still do (not in this spike)

- Everything outside start/follow/seek/pause/resume/stop/end: key and tempo changes (`set_key`/`set_tempo` on both sides; mpv keeps only the tempo), volume and `slide_volume` for the end fade, normalization gain, karaoke EQ and master chain (`set_eq`/`set_master_params`), the CDG visual offset (`set_audio_delay`), loop (intro loops).
- The sound card: a real device engine (`singws_bgm_create` with a device name, hot-plug, `set_output_latency_seconds` from the engine's stats). This spike's engine is headless and paced by a thread; acoustic sync, the sound card's latency and underruns on real hardware are unmeasured.
- Fallbacks of `_drop_voice` beyond what is ported: the port drops to mpv audio (`set_shadow_audio(false)` + play) but the bridge's per-song restore ("setShadowAudio BEFORE the load") and the Objective-C bridge fallback are not.
- `visual_stalled` (CDG with healthy audio but a dead picture at 5 s), the song-end handoff (BGM fade-in start before the audio's end, lyric-aware end gates, `[END-AUDIO]`), the `Started` event wired to rotation/server state: the port emits `Started`/`Ended` only.
- The transport shadow/drive gates (`singws-transport` etc.) are not connected to this machine; the port is a second implementation of the same rules and should be unified with `singws-transport` before it drives anything.
- Seeking near the end (finding 2), and the end-of-song clock fall-back (finding 1) need a guard at the transport level, not only in the voice.
- A real host window (not a test pattern in a plain `Window`): the audience output, the preview and the stacking rules (AGENTS rule 6: native child surfaces stack by creation order) are untouched here.
- Not measured: real Intel hardware; render-thread mode (`render_thread` was false in these runs); Screen Recording / Automation grants; memory over a show.

## Verified vs not

Verified: `cargo test -p singws-shell --bins` (41 tests incl. 13 protocol + 6 demo), `cargo test -p singws-video` (7), both bundles built, strict-signed, launched through LaunchServices
(`open -n -W --arch`), the selfcheck of spike 1 still passes on both after the dylib-only copy change, the play-demo runs above, the full Python suite once at the end: 2489 tests, 2 failures, both reproduce alone and are NOT from this change (no Python file was touched): `test_logpack_shadow.RunShadow.test_clean_logs_nothing` and `test_rust_defaults_migration...test_after_the_marker_the_operator_choices_are_respected`, both from HEAD's commit f1ea0ef ("quiet shadows announce themselves" / one-time waitlist_snapshot migration) whose new log line / migration the older tests do not expect.
Not verified: sound through a device, Intel hardware, the window as seen by eye, real-show behaviour.

## Follow-up (2026-11-01): the seek-near-the-end problem, cause found, NOT fixed

**Cause (evidence, headless mpv, `python3 -I tools/cdg_seek_probe.py SONG.cdg SONG.mp3`):** it is the CDG itself plus mpv's hr-seek, not the bridge. In this file the graphics have two
quiet stretches: the last real screen is cleared by a burst of Memory Preset packets at 277.98 to 278.03 s, then 22 s of all-zero packets (73,029 of 90,167 packets in the file are all zeros), then a final Memory Preset burst at 300.50 to 300.55 s
(checked by reading the .cdg: command 1 packets at those times only). The decoder therefore produces NO video frames between 278.03 and 300.50 s. An `absolute+exact` (or plain `absolute`, or `hr-seek=yes/always`) seek to a target in that gap waits for the first video frame at or after the target, which is the one at 300.50:
targets 270, 277, 277.9 land on target; 278.1, 279, 285, 288.6 land at 300.503 (eof-reached follows within 1 s). It is the same with or without the external MP3 (`audio-file`), so it is the CDG video stream. `absolute+keyframes` and `hr-seek=no` + `absolute` go to the PRECEDING screen (277.98) instead.
The natural-end clock fall-back (finding 1 above, picture clock 278.033 s about 0.35 s before the end) is the same fact seen from the other side: when the video stream's last frame is reached, the clock source falls back to that frame's pts (278.033).
No mpv option tried helps (hr-seek=no/yes, hr-seek-framedrop=no, video-sync=audio, demuxer-lavf-o); a two-step seek (keyframe, then exact or relative) lands at 300.50 again; a seek away and back repeats it.

**Does the Python app have it? Yes.** `mpv_playback_iina.seekMedia` calls `singws_bridge_seek`; the Rust bridge (default) sends `seek <s> absolute+exact` (`core.rs::seek_ms`), and the Objective-C bridge sends the same command (`bridge.mm:952`), on the same libmpv. Only songs with a long graphics-free tail are affected (a seek landing inside it); a seek elsewhere is fine (~340 ms). Not reproduced in the running Python app (no GUI session), reproduced at the shared libmpv level and through the bridge in this spike.

**Why no fix was made:** the options all have a cost. (a) `absolute+keyframes` for CDG: the picture/clock would land at 277.98 while the voice is sought to the target, so the follower drags the voice back ~10 s (the clip-of-earlier-audio symptom). (b) Clamp the seek target in the transport to the last frame time: needs that time, which mpv does not report; it can be read from the CDG (last non-empty packet before a gap) in Rust (`singws-cdg` already decodes) or from `duration` minus the dead tail, and it changes where a seek lands (a decision for the operator). (c) Let the picture jump but keep the voice at the target and stop slaving the voice while the picture is in the dead zone: also needs the dead-zone bounds.
Recommended: (b) in the Rust transport, using `singws-cdg` to find the last frame before the final gap; in the Python app leave as is (rare: needs a seek into the last ~8% of such a song) unless the operator wants a test-first fix. A real-CDG test for it: seek the song above to 288.6 and assert the position stays below 280 s.


## Fix (2026-11-01, operator decisions: clamp to the last frame, in both the Rust transport and the Python app)

A seek whose target falls in a CDG's graphics-free tail now moves back to the last frame before the tail, for the picture and the voice alike (they are given the same clamped target, so the follower has nothing to correct).
- **Rule** (one rule, two twins): frames are the packets FFmpeg turns into frames; the tail is the last stretch of more than 1 s without frames that is followed only by a closing burst (<= 1 s). A song with no such tail, or a quiet stretch in the middle, is never touched. A target equal to either end of the gap is not moved.
- **Python:** `cdg_seek_guard.py`, used by `MpvKaraokeTransport.start` (start offset) and `.seek` (CDG mode only; the gap is read from the CDG file when the song starts; any read problem means no guard). Failing-before test: `test_cdg_seek_guard.TransportSeekTests` runs the real transport with the real Rust bridge on `CC - Radiohead - Let Down`: before the change a seek to 288.6 s put the picture at 300.56 s (reproduced in the Python transport, not only at libmpv level); now it stays below 280 s. Listed in both specs.
- **Rust:** `singws_cdg::{frame_times, terminal_gap, clamp_seek}` and `Protocol::seek_gap` (set by the play demo from the CDG file), unit tests plus a check against the real disc.
- **Not verified:** a live show (a human seeking in the running app); the shipped Python app has not been rebuilt. Landing 2 to 22 s earlier than asked when seeking into such a tail is the intended trade.
