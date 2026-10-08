# SingWS Rust migration plan (Phases 2-4)

Companion to `RUST_ARCHITECTURE_AUDIT.md` (read it first; its labels **[VERIFIED] / [REPORTED] / [SUSPECTED] / [OPEN]** are reused here).
Status: **proposal for review. No implementation has started and none will start without approval.**

Guiding principle: migrate only where there is a measurable reliability or performance gain, or a dependency/maintainability reason that the project
itself has stated. Do not translate Python into Rust for consistency.

---

## 1. What the audit says about where Rust helps

| Area | Today | Does Rust measurably help? |
| --- | --- | --- |
| Karaoke decode, CDG, video, output | libmpv + ObjC++ bridge (already native) | **No.** Keep libmpv. A rewrite loses libavcodec CDG/MP4 decoding, GL texture sharing and years of calibration. |
| Key/tempo | mpv `rubberband` R3 in the `af` chain | **Not by itself.** Algorithm choice (R3 vs Signalsmith) is independent of the host language. |
| BGM mixing/crossfades | BASS via ctypes, optional Python DSP callbacks on the audio thread | **Yes, conditionally**: removing Python/GIL from the audio thread; optionally removing proprietary BASS. |
| Library analysis (loudness, silence, boundaries) | libmpv jobs in recyclable helper processes (`libmpv_media_jobs.py`) | **Yes, plausible**: no libmpv per track, no leak history, lower memory/CPU, easier parallelism. Measurable. |
| Transport/sequencing decisions | scattered across a 60 kLOC file, GUI-thread timers | **Yes for testability**, not for speed: a pure state machine can be exhaustively tested and replayed from logs. |
| GUI stalls on Intel | Qt painting / Quick sync, cause not isolated | **No evidence.** Needs native sampling on the Intel Mac first (Stage 0). |
| Server, singer rotation, request rules | PHP/SQLite + Python | **No.** Keep. Rules are well covered by tests; a migration risks the live show. |

---

## 2. Target architecture (Phase 2)

### 2.1 Shape

```
Python / PyQt6 (unchanged owner of UI, rotation, requests, server sync, settings)
        |   ctypes C ABI (existing pattern)         |  PyO3 (non-realtime only)
        v                                           v
  libsingws_audio (Rust cdylib, C ABI)        singws_analysis (Rust, PyO3 module or CLI)
   - Stage 2: DSP block processors              - Stage 1: loudness / silence / boundaries
   - Stage 4: BGM engine (decks, mixer,         - golden-tested against existing caches
     fades, device output) [gated]
        |
        v
  libmpv + bridge.mm (UNCHANGED): karaoke audio, CDG, MP4, GL presentation
```

Rust never owns UI, queue rules or the web services. The karaoke path stays in libmpv.

### 2.2 Proposed crates (a Cargo workspace under `rust/`, nothing else in the tree touched)

| Crate | Purpose | Stage |
| --- | --- | --- |
| `singws-core-types` | shared sample-format, time (frames/ns), error and ABI-version types | 1 |
| `singws-analysis` | EBU R128 integrated loudness + true peak, silence boundaries, envelope, decode of MP3/FLAC/WAV and MP3 inside MP3+G ZIPs | 1 |
| `singws-dsp` | biquad EQ, soft-knee compressor, expander, limiter, exciter (ports of `singws_eq.py` / `singws_master_audio.py`), `no_std`-friendly, allocation-free `process()` | 2 |
| `singws-transport` | pure playback-state machine and event/command types (no I/O) | 3 |
| `singws-bgm` | decks, mixer, equal-power fades, scheduling on a sample clock, `cpal`/CoreAudio output | 4 (gated) |
| `singws-ffi` | the stable `extern "C"` surface and a generated header; versioned | 2+ |
| `singws-py` | PyO3 bindings for the non-realtime crates | 1+ |

### 2.3 Key decisions and evaluations requested in the brief

**Signalsmith Stretch: keep via C++ interop or replace?** The shipped app does not use it today (audit 0.2), so there is nothing to "preserve". Options:
(a) stay on mpv/rubberband R3 for karaoke, which already works and is operator-accepted; (b) if Signalsmith is wanted, add it **behind a C++ wrapper called from Rust (`cc`/`cxx`)**,
never port it: it is MIT-licensed C++ header code with years of tuning, and a port is high-risk, low-benefit. Recommendation: **do not touch key/tempo in the migration**; decide (a) vs (b) with the operator
(Open Question O1) and treat any switch as its own listening-test project. For the Stage 4 BGM engine, key/tempo is not needed (BGM plays at nominal speed).

**PyO3/maturin vs C ABI.** Use **both, by purpose**:
* Realtime-adjacent code (Stage 2 DSP callback, Stage 4 engine) is exposed as a plain `extern "C"` cdylib loaded with ctypes, exactly like `libsingws_mpv_bridge.dylib`. This avoids Python-ABI coupling
  (the app is on Python 3.14, a very new interpreter; extension ABI/free-threading support must be re-verified for every PyO3 upgrade [OPEN]) and lets the *audio thread call Rust directly*, with Python only in the control plane.
* Non-realtime code (Stage 1 analysis, Stage 3 state machine) can be a PyO3 module built with maturin (abi3 where possible) or simply a CLI, whichever the prototype shows is simpler to package with PyInstaller.

**Independent Rust playback process vs in-process extension.**
* *Fault isolation* is attractive: a crash in the engine would not take down the UI. But for **karaoke** the in-process design exists *because* libmpv renders once into a shared GL texture shown by two native views;
  moving the engine out of process forces cross-process texture sharing (IOSurface) and reintroduces the A/V sync problems of the retired follower/out-of-process stacks (`mpv_playback.py` was removed in 0.4.5.8). Not recommended.
* For **BGM** an out-of-process engine is feasible (no video, simple control surface) and would isolate BASS/Rust audio faults and GIL contention entirely. It costs IPC latency for fades (acceptable if fades are scheduled by sample time inside the engine),
  a supervisor/restart policy, and packaging a second executable. Recommendation: **design the Stage 4 engine so it can run in-process or out-of-process behind the same command protocol**; ship in-process first, evaluate out-of-process after real-show data.
* The existing analysis helpers are already out-of-process by design; Stage 1 keeps that property.

**Real-time rules for every Rust audio callback:** no locks that Python can hold, no allocation, no I/O, no logging (use a lock-free SPSC ring to a logger thread), no panics across FFI (`catch_unwind` + `panic = "abort"` policy decided per crate), bounded work per block, denormal flushing, fixed channel/layout set at configure time.

**Future capabilities** (mic monitoring, AI transitions/vocal effects): the Stage 4 engine's design includes an input stream and a per-block processing graph, but nothing is built for them until needed. Low-latency mic monitoring needs a measured round-trip budget on the venue hardware; AI-assisted effects should run off the audio thread (analysis produces *parameters/automation* the sample-clock scheduler applies).

### 2.4 Non-goals

Rewriting PyQt6 UI; replacing the PHP server or SQLite; changing database schemas; replacing libmpv, libavcodec or the CDG decoder; re-implementing KaraFun automation; changing the request lifecycle.

---

## 3. Migration requirements (Phase 3) and how each is held

| Requirement | Mechanism |
| --- | --- |
| Preserve karaoke/BGM behaviour, CDG/MP4, key/tempo, routing, rotation rules, web signup, DB compatibility, settings and history | Each stage ships behind a setting/flag, default **off**; old path remains the default until acceptance criteria pass on both Macs; no schema/file-format change; shadow-mode comparison where possible. |
| No blocking in real-time callbacks, no unpredictable allocation, avoid underruns | Rules in 2.3; per-block time budget asserted in tests and benchmarks; xrun counters surfaced to the log (Stage 0). |
| A/V sync, seeking, pause/resume, smooth karaoke<->BGM transitions | Only Stage 4 can affect the BGM side; karaoke remains libmpv. Transition acceptance = recorded-output test (Section 5). |
| UI cannot block audio | Stage 2/4 audio threads never take Python locks; control via lock-free command ring. |
| Intel + Apple Silicon | Targets `x86_64-apple-darwin` and `aarch64-apple-darwin`, `MACOSX_DEPLOYMENT_TARGET=12.3`, built per-arch like the bridge (Intel from the Rosetta build env) or lipo'd to universal; verified by `tools/verify_macos_arch.py` / `verify_macos_min_version.py`. |
| Portability | Platform specifics (CoreAudio device selection, Cocoa) isolated in one `platform` module per crate; `cpal` is the abstraction candidate for output. No macOS-only types in public APIs. |

---

## 4. Staged roadmap (Phase 4)

Every stage: Python app stays usable; old path remains; rollback = flip the flag and, if needed, delete the artifact (the app falls back automatically when the library is absent).

### Stage 0 - Instrument and baseline (no Rust)
* **Python modules:** `0.2.18.1.py` (log probes), `bass_background_engine.py`, `mpv_karaoke_transport.py`, `tools/singws_perf_harness.py`.
* **Work:** BASS/mpv underrun/xrun counters and callback-duration histograms into the log; audible-latency measurement per output device; a native `sample` recipe for the Intel stalls; record real settings (are EQ/master enabled?); capture a baseline of analysis throughput and memory on both Macs.
* **Benefit:** turns [SUSPECTED] items into numbers and decides whether Stages 2 and 4 are needed at all.
* **Risks:** instrumentation cost (AGENTS rule 9: keep opt-in, default off). **Regression risk:** low.
* **Tests:** unit tests for counters; before/after PERF-DIAG comparison.
* **Rollback:** setting off. **Acceptance:** a one-page baseline report with underrun count = known number, callback p99 duration, analysis tracks/s, RSS/h on both Macs.

### Stage 1 - `singws-analysis` (loudness, silence, boundaries) -- **recommended first prototype**
* **Replaces (as an alternative, not removal):** `libmpv_media_jobs.py` (`OfflineMpvJob`, `LoudnessSession`, `IsolatedLoudnessSession`, `run_isolated_analysis_worker`), parts of `transition_analysis.py` inputs.
* **Rust:** `singws-analysis` (+ `symphonia` for MP3/FLAC/WAV/AAC decode, `ebur128` crate for BS.1770, `zip` for MP3+G archives), `singws-py` or CLI.
* **Dependencies:** build-time Rust toolchain (rustup, stable); runtime: none beyond the compiled module. [new dependency, explained in the prototype design]
* **Benefit:** no libmpv instance per track, no helper-process churn, lower and flatter memory (the historical 1 MB/track leak class disappears), deterministic parallelism (rayon), faster Turbo scans.
* **Risks:** numerical parity with libmpv's `ebur128` filter (same algorithm family; must be demonstrated, not assumed); decoder differences (symphonia vs libavcodec) on odd MP3s ("Header missing" files seen before); ZIP quirks (Deflate64, `__MACOSX` junk).
* **Regression risk:** low: it is only used when the "analysis engine" setting selects it; results are written through the existing cache writers.
* **Tests:** golden corpus from the existing loudness/transition caches (measured by libmpv) + synthetic tones with known LUFS (EBU Tech 3341 style signals) + corrupt-file corpus; property tests for silence boundaries.
* **Rollback:** setting back to libmpv; cache entries carry an `engine` tag so either engine can be re-run.
* **Acceptance:** integrated loudness within +-0.1 LU and sample peak (what the cache stores) within +-0.1 dB of the libmpv result on >= 99% of the corpus (outliers explained); silence boundaries within +-50 ms; >= 2x tracks/s per core; RSS growth < 5 MB over 5,000 tracks; zero crashes on the corrupt corpus; builds and passes on arm64 and x86_64.

### Stage 2 - `singws-dsp` and a native BASS DSP callback (take Python off the audio thread)
* **Python modules:** `singws_eq.py`, `singws_master_audio.py`, `bass_background_engine.py` (`_attach_master_dsp`, `_attach_eq_dsp`, `_dsp_proc` ~559-630).
* **Rust:** `singws-dsp` (allocation-free biquad EQ, compressor/expander/limiter/exciter) + `singws-ffi` exporting a `DSPPROC`-compatible function pointer that ctypes hands to `BASS_ChannelSetDSP`; Python only configures parameters through an atomic parameter block.
* **Benefit:** removes GIL and numpy allocation from the BASS audio thread; deterministic per-block cost; same behaviour as the Python reference (it is a port, with the Python code kept as the reference oracle).
* **Risks:** filter-state continuity and parameter smoothing differences; coefficient parity (scipy vs hand-rolled); only valuable if the processors are actually enabled in shows (Stage 0 answers this).
* **Tests:** bit-tolerance comparison against `MasterAudioProcessor`/`singws_eq` on recorded blocks (+-1e-5 relative); denormal and silence behaviour; real-time budget test (block time < 10% of period).
* **Rollback:** setting selects Python processors; native library missing -> automatic fallback.
* **Acceptance:** matches the Python reference to tolerance on the test corpus; per-block p99 time well under budget on the Intel Mac; no Python frames on the audio thread (verified by a thread-state assertion/log); 8-hour soak without a counted underrun attributable to DSP.
* **Gate:** proceed only if Stage 0 shows the callbacks are in use or measurably cause underruns/GIL waits.

### Stage 3 - `singws-transport` pure state machine
* **Python modules:** the end-of-song, completion-monitor, BGM scheduling and fade-decision code in `0.2.18.1.py` (`_handle_media_end_safe`, KaraFun monitor, `[END-AUDIO]`, `karaoke_trim_verified_tail`) behind the existing signals.
* **Rust:** `singws-transport`: `State`, `Event`, `Command`, `step(state, event, now) -> (state, [commands])`, no I/O, no clocks (time passed in). PyO3 binding.
* **Benefit:** testability (replay real show logs as event sequences), one owner for playback state, removes ordering bugs between the engines and timers. Not a speed gain.
* **Risks:** highest behavioural risk of any stage: this logic encodes years of show-found fixes (false completions, skip-ahead, session identity guards). **Do this last among low-risk stages, shadow-mode first** (run the machine alongside the Python code and log disagreements; do not act on it).
* **Tests:** table-driven transition tests lifted from existing source-guard tests; log-replay of the 2026-10-04 and 2026-10-03 shows; fuzzing of event orders.
* **Rollback:** disable shadow/active flag. **Acceptance:** 0 unexplained disagreements across >= 3 recorded shows in shadow mode before it may drive anything.

### Stage 4 - `singws-bgm` engine (gated; replaces BASS only if justified)
* **Python modules:** `bass_background_engine.py`, `bass_soundboard_engine.py`, `libmpv_background_engine.py` (same public API preserved so `BackgroundMusicPlayer` is unchanged).
* **Rust:** `singws-bgm` on `cpal` (CoreAudio) + `symphonia`; decks preloaded off-thread into ring buffers, mixer on the audio callback, fades as sample-accurate envelopes, device selection by name.
* **Benefit:** removes the proprietary BASS dependency (audit finding 6), one portable engine (Windows/Linux later), sample-clock scheduling, optional out-of-process mode, a base for mic monitoring.
* **Risks:** large; device/hot-plug behaviour, sample-rate conversion quality, gapless MP3 (encoder delay/padding) and FLAC edge cases, equal-power crossfade parity, latency on the venue's interface. 
* **Gate:** only if (a) the BASS licence is a problem (O2), or (b) Stage 0/2 show unfixable BASS-side issues, or (c) a cross-platform port is scheduled.
* **Tests:** recorded-output A/B (render N transitions offline with both engines and compare spectra, level, gap length); long soak; device-removal tests.
* **Rollback:** engine selector setting; BASS path stays in the build until two shows pass. **Acceptance:** zero gaps > 5 ms between consecutive tracks in a 4-hour soak; crossfade level error < 0.5 dB vs reference; no underruns on the Intel venue Mac over 3 shows.

### Stage 5 - Mic monitoring and AI-assisted transitions (future, unscheduled)
Requires Stage 4. Input stream + low-latency monitor path (budget set from measured device latency); AI features produce parameters offline, never run inference on the audio thread.

---

## 5. Cross-cutting test and release requirements

* All new tests run with a scratch `SINGWS_HOME`; Rust tests are `cargo test` + `criterion` benchmarks, run in CI-equivalent scripts under `tools/` alongside `run_tests.sh`.
* **Audio verification that tests cannot give by themselves (AGENTS rules 3-4):** every stage that touches playback requires launching the built app, a recorded-output comparison, and an on-screen/by-ear check at the venue before it is default-on. Tests passing is not acceptance.
* Packaging: the Rust artifact is a prebuilt, signed dylib per arch inside the bundle (same pattern as the bridge); `PyInstaller` specs gain one data/binary entry; fallback to the old path if the dylib fails to load (logged).
* Release gating: no stage ships default-on in a release without a rehearsal on the Intel venue Mac.
* Documentation: reconcile `AGENTS.md`/`README.md` with reality (Signalsmith vs rubberband, dev Mac architecture, test commands) **as part of Stage 0**, as a separate, reviewed change.

---

## 6. Sequencing recommendation

1. Stage 0 (no Rust) and the doc reconciliation.
2. Stage 1 prototype (`RUST_PROTOTYPE_DESIGN.md`) in isolation.
3. Decide Stage 2 from Stage 0 data.
4. Stage 3 in shadow mode.
5. Stage 4 only through its gate.

Total new build-time dependency before Stage 1 is accepted: the Rust stable toolchain (not installed on this Mac today). No runtime dependency is added by Stage 1.
