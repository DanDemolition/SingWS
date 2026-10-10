# Rust BGM engine contract (roadmap item 1, milestone 1)

Status 2026-10-10. This is the written contract the Rust background-music engine must meet before it can replace BASS
(`bass_background_engine.py`, class `BassBackgroundEngine`, ~1,300 lines). BASS stays the shipped engine; the Rust engine will sit
behind a `bgm_engine` setting that defaults to `"bass"` until it has been rehearsed (see "Gates" below).

## Where the code is

| Piece | File | State |
|---|---|---|
| Device-free engine core: two decks, crossfade envelopes, volume slides, normalization, meter, effects chain | `rust/crates/singws-bgm/src/engine.rs` | done, 14 tests |
| Mixer used to prove equal-power maths | `rust/crates/singws-bgm/src/lib.rs` | done, 6 tests |
| File decoder (symphonia) + 44.1/48 kHz resampler (rubato `Fft`) + in-memory deck | `rust/crates/singws-bgm/src/decode.rs` | done, 6 tests |
| A/B harness against BASS: `tools/bgm_ab_compare.py` (milestone 2, see results below) | tools | done |
| Offline renderer `singws-bgm-render` (two real files -> WAV + gap/peak/RMS report) | `rust/crates/singws-bgm/src/render_main.rs` | done, run on real tracks |
| Streaming decode on a worker thread into a lock-free ring (`stream.rs`, `ring.rs`, `resample.rs`) | rust/crates/singws-bgm | done (M3) |
| Sound card output (cpal): named device with fallback, rate negotiation, callback timing, latency, error flags (`device.rs`) | rust/crates/singws-bgm | done (M3) |
| 10-band EQ (`eq.rs`) and the full master chain (`fx.rs`, the `singws-dsp` crate used directly) as engine effects, meter | rust/crates/singws-bgm | done (M4) |
| Real-time wrapper: lock-free command queue in, state snapshot + retired-deck outbox out (`rt.rs`), `BgmSystem` (`system.rs`) | rust/crates/singws-bgm | done (M5) |
| C ABI (`singws-bgm-ffi`) and the Python adapter `rust_bgm_engine.py` (same method names as the BASS engine) behind the `bgm_engine` setting | repo root | done (M5) |

## Operations the app uses (Python surface of the BASS engine)

All of these exist as methods on `Engine` in `engine.rs` unless marked "later".

| BASS engine method | Meaning | Rust |
|---|---|---|
| `load(path, paused, volume)` | replace everything with one track, paused or playing, optional master volume | `Engine::load` (decode happens before; takes a `DeckSource`) |
| `play()` / `pause()` / `stop()` | transport; stop resets everything | same names |
| `is_playing()` / `is_paused()` | state | same names |
| `set_master_volume(v)` | 0..1 clamp | same name |
| `slide_master_volume(v, ms)` | smooth fade of the master, lands exactly, monotonic | `slide_master_volume` |
| `set_normalize_gain` / `set_primary_normalize_gain` / `set_secondary_normalize_gain` | per-deck loudness normalization, clamp 0.05..4 | same names |
| `preload_secondary(path, norm)` | next track held paused and silent: it consumes nothing until the crossfade starts | `preload_secondary` |
| `invalidate_secondary_preload()` | drop the preloaded track (playlist changed) unless a fade is running | same name |
| `start_crossfade(path, ms, norm, start_seconds)` | two 65-node envelopes (out = cos, in = sin, linear between nodes) started in the SAME block; length clamped to what the incoming file has left (min 50 ms); incoming starts `start_seconds` in | `start_crossfade(ms, start_seconds)` (the path is given at preload) |
| `crossfade_finished()` | envelope complete AND the incoming track has been heard that far | same name |
| `crossfade_lead_seconds()` | queued audio ahead of the audible clock: envelopes are scheduled this early | **not needed** in Rust (no queue between decode and clock: the position IS the audible position); return 0 in the adapter |
| `complete_crossfade()` / `cancel_crossfade()` | promote the incoming deck / abandon it | same names |
| `get_times()` | (audible position, duration) of the primary, seconds | same name |
| `seek(seconds)` | reposition the primary; clamps to 1 ms before the end | same name |
| `source_ended()` | primary ended or within 20 ms of its end | same name |
| `meter_level()` | RMS over 50 ms of the audible deck (incoming while crossfading), x4, clamped 0..1 | same name |
| `set_eq(eq)` | ten BASS DX8 PARAMEQ bands, 12-semitone bandwidth, +/-12 dB | later (milestone 4): an `Effect` |
| `set_master_compressor(params)` | BASS DX8 compressor on the output | later (milestone 4); note the app mostly uses the master processor below |
| `set_master_processor(proc)` | the full master chain (gate, tilt EQ, exciter, compressor, limiter, clip) | later (milestone 4): the existing `singws-dsp` crate plugs in as an `Effect`, no FFI hop |
| `set_audio_diagnostics(on)` / `audio_diagnostics_snapshot()` | the once-a-minute `[AUDIO-DIAG]` counters | later: same counters from the callback |
| `prepare_primary` / `install_prepared_primary` / `discard_prepared_primary` / `install_prepared_secondary` | slow file open off the UI thread, graph change on the UI thread | replaced by `decode_file` on a worker + `load`/`preload_secondary` |
| `close()` | free the device | later (device layer) |
| `output_name` (constructor) | named output device; changing it re-opens the device | later (milestone 3) |

## Behavioural rules carried over (each is a test in `engine.rs`)

* Stereo f32 at one fixed engine rate (48 kHz). Files at other rates are resampled once, at load, with a fixed-ratio FFT resampler.
  Mono is duplicated to both sides; more than two channels keep the front pair.
* The crossfade envelope owns the fade; normalization is a separate constant gain per deck and is never mixed into the envelope.
* Times and `source_ended` follow the AUDIBLE position (frames actually mixed), not the decoder.
* The preloaded deck consumes nothing and is silent until `start_crossfade`.
* Level through an equal-power fade of uncorrelated material stays within 0.5 dB.
* No silent frames when the incoming track covers the outgoing track's end (the gap meter stays 0); a gap is counted when nothing is left.
* `mix` never allocates, locks or does I/O (tested with huge blocks).

## Known differences from BASS (decided, not accidents)

* Whole-file decode before playing (offline path). A 4-minute track is ~92 MB of f32 at 48 kHz. The live engine will stream through a
  ring buffer (milestone 3) with identical per-frame output; memory per deck then stays small.
* No `crossfade_lead_seconds` (see table).
* Damaged files: symphonia skips undecodable packets and counts them (`Decoded::skipped_packets`); a file with more than 100
  consecutive bad packets is rejected, a format change mid-file drops the odd packets. BASS behaviour on damaged MP3s was never specified.

## Gates (nothing below changes until each is met)

1. **M2 - A/B against BASS** on real tracks: gap under 5 ms, crossfade level swing under 0.5 dB, same position/duration readouts.
2. **M3 - device**: named output, hot-plug, sample-rate change, latency measured on the real audio interface.
3. **M4 - parity**: EQ bands, master chain, compressor, meter, normalization match BASS on the same input (tools: `tools/bgm_eq_check.py`,
   `tools/master_dsp_stage_check.py`).
4. **M5 - wiring** behind `bgm_engine` (default `"bass"`; BASS kept in the build, automatic fallback if the Rust engine fails to start).
5. **M6 - rehearsal**: 4 h soak here, the Intel Mac, one real show (reduced from two by the operator on 2026-10-10). The default was flipped to rust before this completed, also by the operator's decision.

## How to run the offline renderer

    export PATH="$HOME/.cargo/bin:$PATH" CARGO_TARGET_DIR=$HOME/.cache/singws-rust-target
    cd rust && cargo build --release -p singws-bgm
    $CARGO_TARGET_DIR/release/singws-bgm-render --out mix.wav --xfade-ms 5000 --lead 10 --tail 6 A.mp3 B.mp3

It prints the decoded lengths, the peak, the gap frames, and RMS per second so a level dip at the fade can be read without listening.
First real-file run (2026-10-10, two local BGM tracks, 44.1 kHz MP3s): 0 gap frames, peak -0.91 dBFS, the level step at the fade is
the two tracks' own loudness difference (about 8 dB, which the app's normalization gains remove).

## Milestone 2 results - A/B against the BASS engine (2026-10-10)

`tools/bgm_ab_compare.py A.mp3 B.mp3` renders the identical scenario (play A, crossfade into B starting on an exact frame, 5 s fade)
through BASS's decode-only mixer and through the Rust engine, aligns them, and compares. Run on 6 real pairs from the BGM
playlist (all 44.1 kHz MP3, which is all this Mac has; no 48 kHz or non-MP3 file was available):

| Target (plan) | Result |
|---|---|
| gap < 5 ms | 0 silent frames in both engines on every pair |
| crossfade level within 0.5 dB | worst 100 ms disagreement inside the fade 0.00-0.42 dB |
| position readout | 0.02 ms apart at the fade start; duration within 13 ms on a 2-minute track |
| sample alignment | 0 frames on every pair after the two fixes below |
| audible-band match (added) | below 10 kHz the difference is 56-58 dB under the signal; overall SNR 26-52 dB |

Where the engines still differ: only above ~10 kHz. BASS's mixer resampler and rubato's FFT resampler have different filters
(difference energy sits at 16-20 kHz and above the 22.05 kHz Nyquist of the source). Not audible in practice; rubato is the
higher-quality of the two.

Two real defects the harness found, both fixed:

1. **MP3 start offset (25.06 ms).** MP3s with no LAME encoder-delay header (symphonia reports delay 0): BASS still drops the standard
   1105 start samples (576 + 529), symphonia drops none. `decode.rs` now drops 1105 too (`MP3_DEFAULT_DELAY_FRAMES`). MP3s with a
   LAME header already agreed to the sample.
2. **Click on start/seek.** BASSmix fades a channel in linearly over ~5 ms at start and after every reposition (gain at frame i is
   (i + 0.5) / 240 at 48 kHz). The engine had no ramp, so a seek mid-song would click. `Engine` now applies it
   (`RAMP_IN_S`, `set_ramp_in_seconds`; test `a_deck_ramps_in_over_five_ms_...`). Peak single-sample difference vs BASS fell
   from 0.87 to 0.03-0.26.

Harness caveats: BASS ignores the master volume on a decode-only mixer, so the harness compares at volume 1.0; the BASS mixer
sits in a decode-only graph, so its output device behaviour (latency, hot-plug) is milestone 3, not measured here.

## Milestone 3 results - streaming decode and the sound card (2026-10-10)

New dependency (approved by the operator): **cpal 0.18** (Apache-2.0, runtime). Tools: `singws-bgm-streamcheck FILES` (streaming deck vs
whole-file decode), `singws-bgm-device list|probe` (the engine over a real output; silent unless `--audible`).

**Streaming deck** (`stream.rs`): worker thread decodes -> stereo -> streaming rubato resampler -> 2 s lock-free ring; the audio callback only pops.
* Streaming resampler output is sample-identical to the whole-clip one for any chunking (test).
* All 69 tracks in the local BGM playlist: full read through the streaming deck is bit-identical to `decode_file` (worst difference 0.0) and
  seeks at 7 positions per file agree to ~5e-7; 0 underruns.
* `read` returns short only at the true end; an underrun is silence with the position still advancing (counted).
* Seek is NON-blocking: `seek` returns at once, the deck reports `poll_pending` until the worker has restarted (about 1 s before the
  target so the resampler settles) and 30 ms of the new position is buffered; the engine plays silence for a pending deck WITHOUT
  advancing its position or marking it ended (`pending_frames`, test). While an incoming deck is pending the crossfade waits.
* Two bugs found by the tests/real files and fixed: a race that threw away new-position audio (worker now waits for a "ring flushed"
  signal), and a start offset on MP3s with an encoder-delay tag (the decoded buffer begins `trim_start` frames into its packet).

**Sound card** (`device.rs`), MacBook Pro Speakers, 48 kHz stereo f32, engine + 2 streaming decks + crossfade, silent:
| buffer | callback mean / max | driver latency | missed blocks |
|---|---|---|---|
| 512 frames (10.7 ms, default) | 17-22 us / 87-95 us | 13.7 ms | 0 |
| 256 | 7 / 27 us | 8.3 ms | 0 |
| 128 | 2 / 10 us | 5.7 ms | 0 |
| 64 | 1 / 8 us | 4.3 ms | 0 |
A 44.1 kHz stream (decks resampled to 44.1 kHz) also runs clean. The callback uses under 1% of its time budget at every size.

**Open weakness (decided, for M5):** the engine is shared as `Arc<Mutex<Engine>>` and the callback only `try_lock`s it. With a control thread
taking the lock every 2 ms plus a seek each second for 60 s: 1-3 of ~5,650 blocks were lost (a click each); with the same traffic but no seeks,
0 lost over 42,000 calls (two runs). The losses are not clustered in the 100 ms after a seek, so the cause is not yet understood (suspects: the
control thread being pre-empted while holding the lock while decoder threads run hot after a seek). The app's real traffic is orders of
magnitude lower, but the proper fix is a lock-free command queue from the control side to the audio thread, which the M5 Python adapter
will want anyway. `singws-bgm-device probe --hammer` is the regression check.

**Not covered by this milestone:** an external USB interface (only the built-in speakers exist on this Mac), unplugging a device mid-play (the
error flag path exists but was never exercised), changing the device's sample rate mid-play, the Intel Mac.

## Milestone 4 results - EQ, master chain, meter (2026-10-10)

* **Graphic EQ** (`eq.rs`): ten peaking biquads at 31.5 Hz .. 16 kHz, bandwidth one octave, +/-12 dB, controlled through an `EqHandle`.
  The BASS DX8 PARAMEQ response was MEASURED (`tools/bgm_eq_response.py`; one band at +12 dB, 40 frequencies) and found to equal the RBJ
  peaking formula with BW = 1.0 octave to within 0.12 dB. Against the real BASS engine with the operator's own curve and a harsh
  alternating +/-12 dB curve (neighbouring bands overlapping), the Rust EQ is within 0.29 dB at all 40 frequencies (tolerance 0.5).
  Disabled or flat = bit-exact pass-through; gain changes apply at the next block without a click (filter state kept).
* **Master chain** (`fx.rs`): `singws-dsp`'s `MasterProcessor` as an engine effect, no FFI hop, parameters by name through a `MasterHandle`
  (typos rejected). With the engine substituted for the library, `tools/master_dsp_stage_check.py` passes unchanged: compressor, limiter
  (-1.00 dBFS), output clip, tilt EQ, your tilt, exciter, gate, live slider change, and agreement with the Python reference (2.98e-08).
  `tools/bgm_rust_engine_check.py` runs both parts (EQ vs BASS, chain through the engine).
* **Meter**: the engine's meter (RMS over 50 ms x4, audible deck, clamp 0..1) matches BASS's `meter_level` to within 0.0005 where it is not
  pegged at 1.0 (most loud music pegs at 1.0 in both engines).
* Effect order in the engine: EQ first, master chain last, as in the BASS mixer.
* Not ported on purpose: `set_master_compressor` (BASS's separate DX8 compressor). The app never calls it; only old tests do.
* Not covered: the EQ/master chain were checked at 48 kHz (EQ) and 44.1 kHz (chain tool); a rate change mid-play would call
  `MasterProcessor::configure_stream`, which the engine does not do yet (the engine rate is fixed per output; M5 rebuilds the engine on a
  device change).

## Milestone 5 results - wiring behind a setting (2026-10-10)

* **Setting `bgm_engine`** = `"bass"` (default) | `"rust"`; read at launch (`BackgroundMusicPlayer._bgm_engine_preference`, which reads the settings file
  itself because the player is built before the host's settings exist). `"rust"` tries `RustBackgroundEngine`; ANY failure (library missing, wrong
  ABI, output cannot be opened) logs `[BG-RUST] ... using BASS` and continues with BASS, then the existing libmpv fallback. If a saved output
  name is not found the system default is used and logged. A shipped (frozen) app loads ONLY the library bundled inside it.
* **Lock-free design** (`rt.rs`): the audio thread owns the engine. Control -> audio: a bounded SPSC command queue (256; producers serialised by a
  mutex the audio thread never touches). Audio -> control: a `Snapshot` of atomics (position, duration, meter, flags, counters) refreshed every
  block, and an outbox that hands retired decks back so their buffers are freed on the control thread. This removes the milestone-3 weakness:
  `singws-bgm-device probe --hammer` (a control thread every 2 ms plus a seek each second) over two 60-second runs and 21,000+ control
  iterations each: 0 xruns, 0 dropped commands, 0 gap frames, callback max 94-96 us.
* **C ABI** (`singws-bgm-ffi`, `libsingws_bgm_ffi.dylib`, ABI 1): create/destroy, deck_open (worker thread), the transport and crossfade
  commands, snapshot reads, EQ and master-chain setters (master parameter names checked against the app's list), stats, flush, headless
  create + render for tests. Every call catches panics. `rust/build_bgm.sh` builds arm64 + x86_64 (macOS 12.3 minimum) into
  `native/singws_bgm/<arch>/`; both specs bundle it if present.
* **Adapter** (`rust_bgm_engine.py`): the methods and attributes the app really uses (load/play/pause/stop, volume + slide, primary/secondary
  normalisation, prepare/install/discard, preload, start/complete/cancel crossfade, seek, times, ended, meter, set_eq, set_master_processor
  (mirrors the processor's parameters; the app re-applies after every change), diagnostics, close, `primary`/`secondary`, `bass.BASS_ChannelPlay`
  shim). State the app reads right after a call is made fresh: after calls that change the track the adapter waits (a few ms) for the audio thread
  to confirm, so a stale "ended" from the previous track cannot leak into the new one (test).
* **Evidence:** 21 Python tests drive the REAL engine headless and measure the audio (playback level, seek, crossfade promotion, volume slide,
  normalisation x2 and the x4 clamp, EQ +12 dB and detach, limiter at -6 and -12 dB following parameter changes, meter, diagnostics shape, close),
  plus app-selection tests (default bass, rust when available, any failure -> BASS, bass never touches the Rust module). Full suite 1,455 pass.
  With the real sound card (silent) the adapter loads a track in 20 ms, positions advance in step with real time, a 1.5 s crossfade finishes in
  1.51 s, the `[AUDIO-DIAG]` line prints (callback mean 269 us incl. EQ + master chain). In the PACKAGED arm64 app on a scratch profile: default ->
  `BASS background engine ready`; `bgm_engine=rust` -> `Rust background engine ready`; `bgm_engine=rust` with the library removed -> the
  fallback line and BASS.

**Not done / not verified (all for M6):** actual background-music playback through the app's own `BackgroundMusicPlayer` with the Rust engine
(playlist advance, scheduled crossfades, fade in/out, volume recovery) has not been run; the Intel installer was not rebuilt and the Intel library
never ran on Intel hardware; a USB interface; unplugging the output mid-play (`device_lost` is exposed and counted, the app does not yet react to
it, and neither does it for BASS); the app logs `[AUDIO-DIAG] master-dsp[rust-engine]`, so grep for that.

**Default flipped (2026-10-10, operator decision):** `bgm_engine` now defaults to `"rust"` (before the M6 rehearsal; the operator chose this knowing M6 was
outstanding). `"bass"` opts out. A start-up failure still falls back to BASS automatically; a running-but-misbehaving Rust engine does not.

**M6 acceptance (operator, 2026-10-10):** one real show, preferably on the Intel venue Mac, instead of two. Listening checks on the Apple Silicon Mac passed
(crossfades, volume, fade in/out, EQ and master chain, pause/resume/skip).
