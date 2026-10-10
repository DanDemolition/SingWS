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
| Offline renderer `singws-bgm-render` (two real files -> WAV + gap/peak/RMS report) | `rust/crates/singws-bgm/src/render_main.rs` | done, run on real tracks |
| Streaming decode on a worker thread into a ring buffer | - | milestone 3 |
| Sound card output (cpal), named device, hot-plug, rate change | - | milestone 3 |
| 10-band EQ and master compressor inside the engine | - | milestone 4 |
| C ABI / Python adapter with the same method names as the BASS engine | - | milestone 5 |

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
5. **M6 - rehearsal**: 4 h soak here, the Intel Mac, two real shows. Only then may the default change.

## How to run the offline renderer

    export PATH="$HOME/.cargo/bin:$PATH" CARGO_TARGET_DIR=$HOME/.cache/singws-rust-target
    cd rust && cargo build --release -p singws-bgm
    $CARGO_TARGET_DIR/release/singws-bgm-render --out mix.wav --xfade-ms 5000 --lead 10 --tail 6 A.mp3 B.mp3

It prints the decoded lengths, the peak, the gap frames, and RMS per second so a level dip at the fade can be read without listening.
First real-file run (2026-10-10, two local BGM tracks, 44.1 kHz MP3s): 0 gap frames, peak -0.91 dBFS, the level step at the fade is
the two tracks' own loudness difference (about 8 dB, which the app's normalization gains remove).
