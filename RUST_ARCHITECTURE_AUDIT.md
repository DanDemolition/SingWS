# SingWS architecture audit (Phase 1, read-only)

Audit date: 2026-10-08. Tree: `main` at `7cfb504` ("Refresh v1.0.1.0 release artifacts"), `APP_VERSION = "1.0.1.0"`.
Nothing in the repository was modified to produce this document; it is the only file added by the audit
(together with `RUST_MIGRATION_PLAN.md` and `RUST_PROTOTYPE_DESIGN.md`).

Labels used throughout:
**[VERIFIED]** read in the current source or measured on this Mac (Apple Silicon, arm64, macOS 27) in this audit;
**[REPORTED]** taken from `HANDOFF.md` or earlier session notes, i.e. observed on the venue Intel Mac but not re-measured here;
**[SUSPECTED]** a reasoned hypothesis, not demonstrated;
**[OPEN]** an unresolved question.

---

## 0. Headline conclusions

1. **The realtime audio path is already native.** Karaoke audio, CDG graphics and video are decoded, mixed and output
   inside libmpv, driven by an Objective-C++ bridge (`native/mpv_bridge/bridge.mm`, 1,809 lines). Background music (BGM)
   runs in BASS (`bass_background_engine.py`, loaded through ctypes). Python does not sit in the karaoke sample path. [VERIFIED]
2. **The documented DSP direction does not match the shipped code.** `AGENTS.md` and `README.md` say key/tempo is Signalsmith Stretch.
   In the shipped app, key and tempo are done by mpv's `rubberband` filter (R3) composed into mpv's `af` chain
   (`native/mpv_bridge/bridge.mm` `applyAudioFilters`, ~line 935-960). The Signalsmith C++ module exists
   (`native/signalsmith_audio_native.cpp`, a prebuilt `.so`) but is **excluded** from the PyInstaller specs
   (`SingWS-arm64.spec` `excludes=[... 'signalsmith_audio_native']`) and **no application module imports it**. [VERIFIED]
3. **The measured reliability problems are GUI-thread stalls, not audio dropouts.** The venue Intel Mac logs ~79 GUI freezes/hour
   (median ~173 ms; ~49 song-end freezes of ~439 ms in the 2026-10-04 show) while capture and playback keep running. [REPORTED]
   No confirmed audio underrun/xrun has been found in the notes or logs I have access to. [OPEN: logs were not re-read in this audit]
4. **Rust is not demonstrated to fix any currently measured fault.** Its strongest justifications are (a) removing Python/GIL from places that
   can run on an audio thread, (b) removing a proprietary dependency (BASS) if the project's own "no proprietary SDK" rule is to be honoured,
   (c) making sequencing/decision logic that is spread over a 60,415-line file unit-testable, and (d) faster, leaner library analysis.
   None of these is "audio engine rewrite". See the migration plan.

---

## 1. Application architecture, dependencies, entry points

[VERIFIED] unless noted.

| Item | Finding |
| --- | --- |
| Entry point | `0.2.18.1.py` (60,415 lines, 68 classes). The filename is frozen; the real version is `APP_VERSION` at line 26. |
| Total Python | ~94,553 lines across `*.py` at the repo root, of which 100 files are `test_*.py`. |
| Language/runtime | Python 3.14 (build venvs), PyQt6 6.9.x, numpy 2.5.3, scipy 1.18.1 (pins in `constraints-macos12.txt`). |
| Native code in repo | `native/mpv_bridge/bridge.mm` (1,809 lines ObjC++), `native/karafun_capture/capture.swift` (304 lines, ScreenCaptureKit), `native/signalsmith_audio_native.cpp` (178 lines, unused, see 0.2). |
| Vendored | `vendor/bass` (proprietary BASS, BASSmix, BASSFLAC dylibs), `vendor/signalsmith-stretch`, `vendor/signalsmith-linear`, `vendor/pybind11`, `vendor/mpv-iina-Frameworks`; libmpv runtime built via `native/mpv_runtime/`. |
| Packaging | PyInstaller, one spec per arch (`SingWS-arm64.spec`, `SingWS-x86_64.spec`), `build_singws_mac_arm64.sh`, `build_singws_mac_intel.sh`, `release.sh`. Intel is built here under Rosetta (`.venv-build-intel-rosetta`); arm64 natively. Minimum macOS 12.3 (`tools/verify_macos_min_version.py`). |
| Modules around the monolith | `mpv_karaoke_transport.py` (281), `mpv_playback_iina.py` (510), `mpv_audio_filters.py` (308), `libmpv_media_jobs.py` (957), `libmpv_background_engine.py` (644), `bass_background_engine.py` (1,110), `bass_soundboard_engine.py` (231), `singws_master_audio.py` (431), `singws_eq.py` (248), `transition_analysis.py` (877), `song_index.py` (850), `phrase_detect.py` (679), `phrase_markers.py` (573), `okj_fileinfo.py` (314). |
| Server | `SingWS-Server/` (separate private repo, checked out inside the app tree): PHP 8.3 + SQLite on one Ubuntu host (wskar.com). 52 files in `api/v1`, ~151 `.php`/`.inc` files outside `tenants/`, 23 PHP test scripts in `tools/`. |

**Dev-machine fact [VERIFIED]:** `uname -m` is `arm64`. `AGENTS.md` still says the dev Mac is Intel and cannot run arm64 binaries; that section is
stale. The venue Mac is Intel (6 physical cores, 32 GB) [REPORTED]. This matters for the prototype: aarch64 runs natively here, x86_64 only under Rosetta.

**Toolchain [VERIFIED]:** `cargo`, `rustc`, `rustup` and `maturin` are **not installed** on this Mac.

---

## 2. Audio playback, decoding, buffering, mixing, output

### 2.1 Karaoke path (mpv-owned)

* `mpv_karaoke_transport.py::MpvKaraokeTransport` is the host-facing API (`start`, `stop`, `seek`, `pause`, `resume`, `position_seconds`,
  `set_modifiers(tempo_ratio, semitones)`, `set_volume`, `set_video_offset_ms`, `fade_out`). It wraps `MpvPlaybackPlugin` in
  `mpv_playback_iina.py`, which calls the bridge's C ABI through ctypes (`singws_bridge_*`, ~35 exported functions, see
  `bridge.mm` lines 1720-1809: create/destroy, load/play/pause/stop/seek, position/duration/at_end, set_tempo/set_key/set_volume/set_device,
  set_dsp_chain, set_audio_delay, background load/stop, rotation/sidefill/spotlight hosts, grab_frame, scan_silence).
* One in-process libmpv core renders each frame once into a shared GL texture presented by two native `NSView`s (output + preview)
  (`native/mpv_bridge/README.md`). Control calls run on a serial queue (`com.singws.mpv.control`, `bridge.mm` ~576); rendering and event
  draining are dispatched to the main queue (~1175-1249). A GPU fence (`glFenceSync`) orders the master context's render against the consumer
  contexts (~1462-1471).
* Buffering: `audio-buffer=1.0`, `demuxer-readahead-secs=10`, `demuxer-max-bytes=256MiB`, `cache=yes`, `video-sync=audio`, `hwdec=auto-safe`
  (`bridge.mm` ~738-752). The source comment records that mpv's core lock can be held >400 ms during file open, hence the 1.0 s margin.
* Output device: `audio-device` property set from `singws_bridge_set_device` (`bridge.mm` ~915); device list read from `audio-device-list` (~152).
* DSP: loudness normalise -> 10-band EQ -> "master bus" are expressed as an mpv `af` string (`mpv_audio_filters.py`) and composed with the key/tempo
  filter in `applyAudioFilters`. The master bus is **approximate**: lavfi `acompressor`/`agate`/`alimiter`/`aexciter` are different implementations
  from the NumPy `MasterAudioProcessor`; `mpv_audio_filters.chain_fidelity_notes()` reports which stages are exact (volume and the EQ biquads are exact).

### 2.2 Background music (BASS)

* `BassBackgroundEngine` (`bass_background_engine.py`) drives BASS + BASSmix through ctypes: a stereo-float mixer (`BASS_Mixer_StreamCreate`), decks as
  mixer channels, equal-power crossfade envelopes on BASS's own audio clock (`BASS_Mixer_ChannelSetEnvelope`), preloaded paused decks.
* **Python on BASS's audio thread:** when the EQ is non-flat or the master processor is active, the engine registers ctypes `DSPPROC` callbacks
  (`_dsp_proc`, `bass_background_engine.py` ~559-590 and ~611-630) that call into NumPy/SciPy (`proc.process_f32_array`). Each callback must take the GIL and
  can allocate. They swallow exceptions ("Audio thread: swallow exceptions so we never crash BASS"). [VERIFIED]
  Whether these callbacks are active in a show depends on settings: `master_audio_enabled` defaults to `False` (`0.2.18.1.py:3873`) and `_eq_should_attach`
  requires a non-flat EQ. [VERIFIED defaults; OPEN: what the venue's real `settings.json` has]
* Fallback: `libmpv_background_engine.py` (`QAudioSink` pull-mode feeder mixing decoded PCM) when BASS fails to initialise; same public API and DSP hook contract.
* Soundboard clips: `bass_soundboard_engine.py` (BASS).

### 2.3 Two audio stacks, no shared mixer or clock [VERIFIED structurally]

Karaoke audio leaves through mpv's audio output; BGM and soundboard leave through BASS on a (possibly different) device. Sequencing (fade BGM down when a singer
starts, bring it back near song end) is decided in Python on the GUI thread (QTimers) in `0.2.18.1.py` (e.g. `_handle_media_end_safe`, the `[END-AUDIO]` logic,
`karaoke_trim_verified_tail`), not by an engine that owns both streams. [OPEN: sample-accurate alignment between the two is not provided and was never required so far.]

### 2.4 Analysis path

`libmpv_media_jobs.py` runs libmpv offline "jobs" (`OfflineMpvJob`) for EBU R128 loudness, silence boundaries and visual tail metrics. Library-scale scans use
recyclable isolated helper processes (`IsolatedLoudnessSession`, `run_isolated_analysis_worker`) because in-process libmpv leaked memory (REPORTED history:
~1 MB/track). Turbo mode runs four helpers; measured 1.68 -> 2.16 tracks/s going from three to four helpers on the six-core Intel Mac [REPORTED].

---

## 3. Time, clocks and sync

* The audible clock for MP3+G is mpv's `audio-pts`, falling back to `time-pos` (`bridge.mm` ~871-880), because CDG packets update sparsely. [VERIFIED]
  Note this is the *decoder/pts* position; `audio-buffer=1.0` means audible time lags it by the output latency. The CDG visual offset is applied by mapping it onto
  `audio-delay` (`mpv_playback_iina.py`, `bridge.mm` ~982-997). [VERIFIED] The "decoder position is not audible time" rule in `AGENTS.md` is therefore
  only approximately honoured; the calibrated offset absorbs the difference for CDG. [OPEN: no measurement of true audible latency per device]
* The host polls the engine from a 50 ms `QTimer` on the GUI thread (`MpvKaraokeTransport._timer`, `setInterval(50)`), reading `positionMs()`, `isPlaying()`,
  `visualsReady()`, `atEnd()` over ctypes, and emits `started`/`ended`/`visual_stalled`. [VERIFIED] Positions cross the boundary as integer milliseconds.
* The bridge deliberately returns "not ready" while `_loading` because "mpv can hold its core lock for several hundred milliseconds" and the GUI polls would otherwise block (`bridge.mm` ~866-870). [VERIFIED]

---

## 4. CDG, MP4/video

* CDG is **not** decoded by SingWS code: libmpv (libavcodec's CDG decoder) renders the graphics; MP3 audio is attached as an external audio track and is the clock
  (`mpv_karaoke_transport.py` ~75-76: `source = video_path if mode in {"cdg","mp4"} else audio_path`). A CDG graphics-stall detector (`visual_stalled`) is report-only. [VERIFIED]
* MP4 uses the same core. Background loops use a second libmpv instance (`singws_bridge_load_background`, `_backgroundMpv`, `hwdec=no` pinned by `test_bg_video_lyrics.py`).
* KaraFun streaming songs are not played by SingWS at all: KaraFun is automated via System Events AppleScript and its preview pane is captured with ScreenCaptureKit
  (`native/karafun_capture/capture.swift`). [VERIFIED]

---

## 5. PyQt6 UI, signals, threading, event loops

Counts in `0.2.18.1.py` [VERIFIED by grep]: 52 `QThread` references, 80 `threading.Thread`, 76 `QTimer(`, 113 `singleShot`, 46 `pyqtSignal`, 3 `ThreadPoolExecutor`,
3 `subprocess.Popen`, 10 `Lock()`. Worker QThreads include `SongSearchThread` (line 891), `IndexBuildThread` (1050), `GitHubUpdateWorker` (1130), `SongbookUploadThread` (1306).
Qt Quick/QML "render-thread" surfaces exist for the rotation rail, now-singing card, show-screen VFX and ticker (`RenderThread*` classes). Native child surfaces (mpv views, Quick
surfaces) stack by creation order on macOS (see `AGENTS.md` rule 6), which constrains any UI/native refactor.

Single-process model: UI, queue/rotation logic, server sync, analysis scheduling and engine polling all share one Python interpreter and its GIL.
The venue profile shows GUI stalls with no Python-level cause (stack capture is deliberately off; time ends in Qt painting). [REPORTED]

---

## 6. Rotation, requests, waitlists, playback state

* Request lifecycle, rotation rules and tombstones live in `0.2.18.1.py` and are covered by `test_remote_request_tombstones.py` (2,232 lines), `test_rotation_identity.py`,
  `test_queue_sync_authority.py`, `test_request_relay.py`. The rules in `AGENTS.md` ("host actions are authoritative", permanent request IDs, never resurrect host-removed songs) are
  implemented there. [VERIFIED to exist; behaviour not re-audited here]
* "Playback state" is not one object: it is spread across the transport's flags, `_handle_media_end_safe`, KaraFun monitor state (`fallback_remaining`, `ended_reported`,
  `expected_end_reached`) and BGM scheduler state. [VERIFIED by reading code; SUSPECTED as the main source of ordering bugs, e.g. KaraFun false-completion and skip-ahead end bugs fixed 2026-08-31 and 2026-10-05]

---

## 7. Server and desktop communication

* Desktop <-> server: HTTPS JSON endpoints (`api/v1/*.php`, venue API key header) plus a WebSocket relay (`wss://wskar.com/relay`) used by `RelayRequestWorker` (line 11484, QWebSocket,
  GUI-thread, async) and `HostControlRelayWorker` (line 11668). [VERIFIED]
* Singer phones use the PHP web app (`index.php`, `singer_session_ui.js`, `submitreq.php`), the DAW show-screen preview uses `api/show-screen/snapshot`.
* Host actions are authoritative, per `AGENTS.md`. Server tests: 23 PHP scripts run through `tools/run_room_chat_tests.py` and `run_history_regressions.py`.

---

## 8. Persistence

* App: JSON files under `~/SingWS` (`settings.json`, `queue.json`, `singer_history.json`, `singer_preferences.json`, `deferred_remote_adds.json`, loudness and transition caches with append-only JSONL checkpoints);
  SQLite for the song index (`song_index.py`, tables `meta`, `songs`, ~134k rows), phrase markers (`phrase_markers.py`, tables `markers`, `song_meta`) and `okj_fileinfo.py`.
* Server: per-tenant SQLite databases under `tenants/<user>/` (history, queue state, notifications, chat). Schemas are additive-only by policy.
* Nothing in this audit proposes schema changes. [Required by the brief]

---

## 9. Intel / Apple Silicon

* Both architectures are built from the same Python source with different specs and a per-arch bridge and mpv runtime. The mpv bridge must be rebuilt per arch
  (`build_bridge.sh --arch`); `native/mpv_bridge/libsingws_mpv_bridge.dylib` is arm64 after an arm64 build, so an Intel build needs an explicitly supplied x86_64 bridge. [VERIFIED 2026-10-05]
* Intel has a pattern of 120-440 ms GUI stalls that Apple Silicon does not reproduce [REPORTED]; memory on the Intel Mac grows ~350-470 MB/h with no plateau (cause unknown) [REPORTED].
* No arm64 binary can be tested on the Intel venue Mac and vice-versa without the matching machine; Intel here runs only under Rosetta. [VERIFIED]

---

## 10. Tests, diagnostics, logging, packaging verification

* 100 `test_*.py` files; the current full run was 1,309 passing in the last session (scratch `SINGWS_HOME`). Native tests need `.venv-universal`, which is missing on this Mac. [REPORTED/VERIFIED]
* Source-guard style tests pin many UI/native behaviours by asserting on source text; none can detect rendering, stacking or realtime audio defects (see `AGENTS.md` rules 3-4). [VERIFIED]
* Logging: async `QueueListener` log; `[PERF-DIAG]` slow-step probes via `@_perf_timed` (41 references); bridge logs routed into the app log (`singws_bridge_set_log_callback`).
  Native crashes are only in `~/Library/Logs/DiagnosticReports` (`AGENTS.md` rule 8). Stack capture and event-filter diagnostics are off by design.
* `tools/singws_perf_harness.py` samples process metrics during live tests; `tools/headless_stability_check.py` and `tools/show_cycle_simulation.py` exist.
* Packaging verification is scripted (`tools/verify_macos_arch.py`, `verify_macos_min_version.py`, hdiutil, codesign), but there is **no automated audio-quality or A/V-sync regression test**. [VERIFIED]

---

## 11. Confirmed vs suspected problems

### Confirmed in the source [VERIFIED]
1. **Docs/code mismatch on key/tempo** (Signalsmith claimed, rubberband shipped, Signalsmith module unused and excluded from builds).
2. **Python numpy/scipy code is registered as BASS DSP callbacks** (audio-thread, GIL, allocation) whenever BGM EQ is non-flat or master audio is enabled.
3. **Two independent audio engines/clocks** with sequencing done by GUI-thread timers.
4. **Karaoke master-bus DSP in the shipped path is an approximation** (lavfi filters) of the Python reference.
5. **Single 60 kLOC file** holds queue logic, UI, scheduling, KaraFun automation, sync; playback "state" has no single owner.
6. **BASS is a proprietary dependency** (`vendor/bass/*`), in tension with `AGENTS.md` ("do not introduce paid/proprietary SDK dependencies"). `vendor/bass/VERSIONS.md` states that the distribution is covered by "the appropriate BASS license". [OPEN: confirm the licence type actually held and its terms for a distributed paid product]
7. **Stale `AGENTS.md` machine facts** (dev Mac is Intel; Signalsmith preferred; test commands).

### Reported from the venue, not re-measured [REPORTED]
8. GUI freezes on Intel (~79/h, song end ~439 ms, song start ~174 ms, other ~144 ms); song-end hitch sources partly timed by `ui_songend_*` probes; ~200 ms before cleanup untimed.
9. Intel memory growth ~350-470 MB/h, cause unknown.

### Suspected, not demonstrated [SUSPECTED]
10. Audio dropouts from Python DSP callbacks under GIL pressure (needs underrun counters to prove; not logged today).
11. `audio-buffer=1.0` and the control-queue design suffice for the mpv path, but there is no instrumentation of underruns, so margin is unknown.
12. GUI stalls on Intel originate in Qt painting/Quick sync (native sampling on arm64 showed ~23% widget repaint, ~10% Quick sync, but the stall itself did not reproduce there).

### Not a problem the evidence supports
* "Python is too slow for the audio path": the sample path is already C/ObjC++. A Rust rewrite of it would not remove the measured stalls.
* "CDG decoding is slow/wrong": handled by libavcodec; calibration confirmed on screen 2026-08-18.

---

## 12. Open questions for the operator (decisions that change the plan)

1. Is Signalsmith Stretch still the intended key/tempo engine, or is mpv/rubberband R3 now accepted? (The shipped code comment says rubberband was chosen as the available phase vocoder.)
2. Which BASS licence is held, and does it cover the paid-show distribution model long term? Is removing BASS a goal?
3. Are the BGM EQ and master processor used in real shows? (decides whether Python-on-audio-thread is a live risk or dormant)
4. Is any real audio dropout being heard at the venue, and on which output device? (none is documented)
5. Is Windows/Linux a real target within the next year? (changes how much to invest in portable abstractions now)
