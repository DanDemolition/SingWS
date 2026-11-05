# Stage E: the song-end background-music hand-off in Rust (`singws-songend`)

Status 2026-11-02: offline, proof only. Nothing in the app calls this crate; the Python methods of `0.2.18.1.py` stay the only thing that
starts the background music at the end of a karaoke song. The crate is the host-side decision layer as a pure state machine with an
injectable clock (the caller passes the event time), proven identical to the REAL Python code on scripted and seeded-random scenarios.
Rust drives nothing; no Python behaviour, setting or timing was changed.

Why: the 2026-11-01 Intel show had occasional dead air at normal song ends and always at KaraFun ends. This document says exactly which
rule produces which start time, so tuning can be argued from the rules instead of from a log read by eye.

## What is ported

Real Python methods driven by the harness (`tools/songend_scenarios.py`), all in `0.2.18.1.py`:

| area | methods |
|---|---|
| tick (every 500 ms) | `update_time_left` (BGM pre-start with `bgm_fallback_prestart_sec`, fallback EOS detector, stalled-position detector), `_effective_karaoke_duration_ns` |
| audio end | `_arm_audio_end_floor` (library-cache / cache / worker scan), `_apply_audio_end_floor` (8 s cap) |
| visual end | `_arm_visual_end_floor`, `_apply_visual_end_floor`, `_apply_visual_end_values` |
| end detector | `_setup_end_silence_state`, `_reset_end_silence_state`, `_maybe_trim_end_silence` (verified-tail branch AND the legacy meter/dwell/sector branch), `_prefire_bgm_at_verified_audio_end` (overlap start, `[CROSSFADE] BG fade started at verified audio end`) |
| media end | `_handle_media_end_safe`, `_schedule_media_end_cleanup`, `_run_media_end_cleanup`, `_cancel_pending_media_end_cleanup`, `_finish_media_end_cleanup`, `_auto_play_next_if_generation` |
| music start | `_schedule_bg_resume`, `_on_bg_resume_timer`, `_start_bg_with_fade[_safe]` (`[BG-HANDOFF] fade_in started reason=...`), `_verify_bg_resume_started` (retry / recovery) |
| KaraFun | the hand-off line of `_finish_external_karafun_playback`: `_schedule_bg_resume(120, reason="external_karafun_complete")` (and `..._return`) |

NOT driven, on purpose: the rest of `_finish_external_karafun_playback` and the KaraFun completion monitor (queue/history/AppleScript work; the
monitor's DETECTION time is an input of the model, `D` in the table below), `stop_playback` (replaced by a stand-in that does the parts that touch
this state, in the real order: `_cancel_pending_media_end_cleanup("stop_playback")`, `karaoke_playing=False`, `_reset_end_silence_state()`, transport
dropped), the player (`bg_music`; its own policy is `singws-bgm-policy`), the soundboard deferral branch of `_start_bg_with_fade` (the harness has no
soundboard strip), the transport shadow / `transport_drive_media_end` drive (a setting that is off), `_should_start_intro_loop` (retired feature, always
False in the app; the harness can force it), UI refreshes (`_update_karaoke_*`, `update_bg_button_state`, idle background, video surfaces, now-singing).
These are recorded as `host:*` commands so their ORDER against the music start is checked.

## Protocol

`tools/songend_scenarios.py` builds a bare `KaraokeApp` (`__new__`, Qt base initialised, no widgets) and runs the real methods with: a recording
fake `bg_music`, a fake transport (position model on a virtual clock, level meter, `engine_at_end`, CDG gate/generation/sectors), a virtual
`time.monotonic`, a fake `QTimer` (single-shots and the app's three `QTimer` instances `bg_resume`, `handoff_guard`, `media_end_cleanup` become events on a
heap) and a fake `threading.Thread` (scan workers run at spawn, their UI hand-back is delivered `deliver_ms` later and is itself an event). The 500 ms
tick is driven by the harness (skippable: a skipped tick is a GUI stall). Per event it records the INPUT the Rust side gets (event + a snapshot of
everything readable: settings, transport times/level/gate, bg state, flags, scan results) and the OUTPUT: every command in order (`bg:*`, `host:*`,
`timer` single-shots, `timer_start/stop`, `spawn`, `deliver_later`, whitelisted `log` lines), the return value and ~40 state fields.
`tools/songend_parity.py` replays the inputs through `singws-songend` and demands identical commands, identical return values and identical state
after EVERY event, with strict types (`1000` is not `1000.0`; log lines are compared as text, so the `[CROSSFADE]`/`[BG-HANDOFF]` markers an operator
greps are part of the proof).

## Rules and the start time they produce

`elapsed`/`remaining` are transport (picture clock) seconds; `dur` is `max(engine duration, scanned audio duration)` (`_effective_karaoke_duration_ns`).
`A` = verified audio end = `audio_duration - min(trailing_silence, 8.0)` (the 8 s cap is deliberate, 2026-10-29).
`O` = `bgm_overlap_before_audio_end_sec` (default 2.0; operator 3.0), `H` = `end_silence_trim_threshold_sec` (default 2.5 s, floor 0.5; operator 1.0),
`L` = clamp(`bgm_fallback_prestart_sec`, 2..8) (default 5.0, was a fixed 3.2). "Crossfade" = `seamless_transitions_enabled` AND
`karaoke_bgm_crossfade_enabled` (**default False**; the operator's profile has it True). All "bg idle" conditions: playlist non-empty, `bg_music.is_playing`
False, `bg_enabled` and `bg_autoplay_on_idle` True.

| # | situation (inputs) | what starts the music | command time (transport s) | fade | volume source |
|---|---|---|---|---|---|
| 1 | audio end `A` verified, crossfade, `O>0`, `karaoke_trim_verified_tail`, bg idle, `karaoke_auto_advance` False | `_prefire_bgm_at_verified_audio_end` from the tick, window `A-O <= elapsed < A` | first tick in the window (`A-O` .. `A-O+0.5`) | 3000 ms, `fade_in(None, 3000, allow=True)` | UI slider / saved setting (target None) |
| 2 | as 1 but `O=0`, or no tick landed in the window (GUI stall >= O) | same function, from the verified-tail branch once `elapsed >= A + H` (and meter quiet if there is one) | `A+H` (+<=0.5) | 3000 ms | same |
| 3 | `A` verified but crossfade OFF | nothing fires at `A`. If the visual end is verified: the detector ends the song at `max(A+H, visual_end)` (CDG also needs `cdg_lyrics_finished`), reason `verified_audio_tail` -> no overlap -> cleanup -> `_schedule_bg_resume(60)` | `max(A+H, V)` + 60 ms | 1000 ms (`karaoke_end`) | slider / `bg_volume` |
| 4 | `A` unknown (scan pending or failed, no duration, trim disabled), crossfade, bg idle | the tick's pre-start: `remaining <= L` and `remaining > 0.15 s` | `dur - L` (first tick with `dur-pos <= L`) | 3000 ms | None |
| 5 | physical EOS (engine `ended`, or tick fallback `near_end` twice with no live audio, or stalled position ~10 s) with crossfade, bg idle, nothing prefired | `_handle_media_end_safe` overlap path: `_start_bg_with_fade_safe("media_end_overlap")` at once, teardown 3200 ms later | EOS | 3000 ms, `allow=True` | slider / `bg_volume` |
| 6 | EOS, bg already playing (a pre-start or verified-tail fade ran) | nothing new; `[BG-HANDOFF] overlap already active ...` arms a 1.5 s verification (retry once, then bounded recovery) | - | - | - |
| 7 | EOS with crossfade OFF, or an early-end reason, or `karaoke_auto_advance` | cleanup first (stop_playback, idle background, ...), then `_schedule_bg_resume(60 if end-silence early end else 120)` | EOS + cleanup + 60/120 ms | 1000 ms (`karaoke_end`) | slider / `bg_volume` |
| 8 | KaraFun song complete | `_schedule_bg_resume(120, "external_karafun_complete")`; there is NO clock, so no pre-start/overlap exists | completion detected + 120 ms | 1000 ms | slider / `bg_volume` |

Common gate for every path through `_start_bg_with_fade`: a fresh launch with no BGM Play pressed and no karaoke song played this session blocks the
resume (`[BG-HANDOFF] resume blocked`); once a karaoke song has played (`_karaoke_played_this_session`, set at song start, also KaraFun) it opens.
Where a path starts via `bg_music.fade_in` directly (rows 1, 2, 4) that gate does not apply.

How long until it is HEARD: the engine slides from the fade-in floor (`bgm_fade_in_floor_db`, default -60, operator -40). Documented: a -40 dB floor is
-25 dB under the final level at 1.0 s of a 3 s fade (docs/RUST_BGM_ENGINE_CONTRACT.md). The 1000 ms fades of rows 3, 7, 8 are therefore the quick ones,
but they begin after everything else.

### Computed start times for the 2026-11-01 show shape

The Intel show log is not on this Mac (only `~/SingWS/logs/singws_2026-11-01.log` of the dev Mac, a 15-minute session with two scanned songs), so the
per-song tails of the 5 normal ends and 3 KaraFun ends could not be read. The model is therefore run over the plausible parameter range with the
operator's own settings (`tools/songend_timing_report.py`, real Python and Rust agree on each row). Settings: crossfade on, trim on, hold 1.0 s,
overlap 3.0 s, floor -40 dB, file end 240 s. `rel` = command time minus the last audible audio; negative is an overlap, positive is dead air
BEFORE the command (add the fade audibility on top).

#### Normal song end, audio end NOT verified (scan failed/pending): fallback pre-start

File end 240.0 s. `rel` = music command time minus the last audible audio (negative = music begins before the audio ends).

| case | command at (s) | rel (s) | fade |
|---|---|---|---|
| 1.0.2.8 fixed 3.2 s, dead tail 0 s | 237.0 | -3.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 1.0.2.8 fixed 3.2 s, dead tail 3 s | 237.0 | +0.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 1.0.2.8 fixed 3.2 s, dead tail 5 s | 237.0 | +2.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 1.0.2.8 fixed 3.2 s, dead tail 8 s | 237.0 | +5.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 1.0.2.8 fixed 3.2 s, dead tail 12 s | 237.0 | +9.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 1.0.2.9 default 5.0 s, dead tail 0 s | 235.0 | -5.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 1.0.2.9 default 5.0 s, dead tail 3 s | 235.0 | -2.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 1.0.2.9 default 5.0 s, dead tail 5 s | 235.0 | +0.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 1.0.2.9 default 5.0 s, dead tail 8 s | 235.0 | +3.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 1.0.2.9 default 5.0 s, dead tail 12 s | 235.0 | +7.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 8.0 s (clamp max), dead tail 0 s | 232.0 | -8.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 8.0 s (clamp max), dead tail 3 s | 232.0 | -5.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 8.0 s (clamp max), dead tail 5 s | 232.0 | -3.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 8.0 s (clamp max), dead tail 8 s | 232.0 | +0.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| 8.0 s (clamp max), dead tail 12 s | 232.0 | +4.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |

#### Normal song end, audio end verified (cached), tail cleared; for comparison

| case | command at (s) | rel (s) | fade |
|---|---|---|---|
| verified, overlap 3.0 s, dead tail 3 s | 234.0 | -3.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| verified, overlap 3.0 s, dead tail 5 s | 232.0 | -3.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |
| verified, overlap 3.0 s, dead tail 8 s | 229.0 | -3.0 | 3000 ms; audible +1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade) |

#### KaraFun end (no transport clock, no pre-start): music starts only when the completion is detected

`D` = how long after the last audible audio the monitor completes the song (not in the app's log shape here: parameter).

| case | command at (s) | rel (s) | fade |
|---|---|---|---|
| KaraFun, completion detected D=1 s after the audio | 11.1 | +1.1 | 1000 ms; audible ~+0.33 s (the same curve scaled to 1 s: estimate) |
| KaraFun, completion detected D=3 s after the audio | 13.1 | +3.1 | 1000 ms; audible ~+0.33 s (the same curve scaled to 1 s: estimate) |
| KaraFun, completion detected D=6 s after the audio | 16.1 | +6.1 | 1000 ms; audible ~+0.33 s (the same curve scaled to 1 s: estimate) |
| KaraFun, completion detected D=10 s after the audio | 20.1 | +10.1 | 1000 ms; audible ~+0.33 s (the same curve scaled to 1 s: estimate) |


Reading the table: with the audio end unverified (row 4) the start is `file_end - L` whatever the real tail is. At the old fixed 3.2 s the music command
came 5 s after the last note on a disc with an 8 s dead tail (`+5.0` plus the fade) and 9 s after on a 12 s tail; at 5.0 it is +3.0 / +7.0; it only
equals the last audible audio when the tail equals `L`. A verified end (row 1) is -3.0 by construction. For KaraFun the start is simply detection lag
plus 120 ms plus the 1 s fade: every second the completion monitor needs is a second of silence, and there is no way to start earlier because no
clock exists; the only levers are the detection time, the 120 ms, the 1000 ms fade length and the floor.

## Surprises in the Python (reported, not changed)

1. **Default profile has no overlap at all.** `karaoke_bgm_crossfade_enabled` defaults to False, so a fresh install takes rows 7/8 for every song end
   (EOS, then cleanup, then 120 ms, then a 1 s fade). Only profiles that switched it on (the operator's) get rows 1, 2, 4, 5.
2. **The tick pre-start (row 4) is keyed to the file end, not to `A`.** The verified overlap (row 1) is the only path keyed off `A`. With `O=0`
   and crossfade on, a song whose audio ends 12 s before the file ends starts the music at `A+H` (row 2) or at `dur-L`, whichever the tick reaches first
   (`A+H` wins when `H < tail - L`).
3. **The 8 s credit cap makes the verified end later than the audio on long tails.** Tail 17.9 s -> `A = dur - 8`, music command at `dur-8-O`, i.e.
   `17.9-8-O` s after the last audible audio (deliberate since 2026-10-29: protects against scans that mistake a quiet outro for silence).
4. **`_handle_media_end_safe` clears the pre-start flag** (`_reset_end_silence_state`), so the overlap decision relies only on `bg_music.is_playing`.
   If a pre-start `fade_in` was queued (startup preload in progress) and `is_playing` is still False at EOS, a second fade is requested.
5. **`stop_playback` cancels the pending media-end cleanup from inside the cleanup itself** (`_cancel_pending_media_end_cleanup("stop_playback")`: bumps the
   generation, stops both timers, clears `handoff_active`). The 6 s duplicate-EOS guard is therefore already cleared while the rest of the cleanup runs; it
   has no effect inside one synchronous call, but the guard no longer covers the remainder of the cleanup.
6. **A guard timer can outlive its purpose:** if the overlap cleanup is skipped (`_stop_in_progress` at fire time: `[END] media-end cleanup skipped`),
   nothing finishes the song; only the 6 s `handoff_guard` fires and re-opens the door for the next EOS (scenario `cleanup_skipped_stop_in_progress_guard_fires`).
7. **`_reset_end_silence_state` does not reset** `_karaoke_audio_duration_s`, `_karaoke_duration_floor_logged`, `_eos_guard`, `_last_pos_ns`, `_stalled_pos_ticks`.
   The first two are re-initialised by `_arm_audio_end_floor`; the others carry over to the next song's first ticks.
8. **Volume source differs by path:** rows 1/2/4 call `fade_in(None, ...)` (UI slider if the manager is open, else saved `bg_volume`); rows 5-8 pass an explicit
   `bg_volume` setting (or the visible slider) through `_start_bg_with_fade`. Normally the same number.
9. **A late tick skips the overlap window silently.** The row-1 window is `A-O <= elapsed < A` and is only tested when a tick lands in it; a GUI stall longer
   than `O` moves the start to row 2 (`A+H`). With the operator's `O=3`, `H=1` that is +3.0 s later. (Scenario `late_ticks_over_overlap_window`.)
10. **`[END-AUDIO] holding` is logged every 5 s per song** and is the largest log producer in the model corpus; the `no level tap` line is logged once per song
    on mpv, where the level is always None, so the legacy "silence accumulates" branch runs on time past the verified end and the meter never vetoes.
11. **`update_time_left` in the idle (`times=None`) branch** only resets the position trackers; a KaraFun song (no transport) therefore never reaches any
    BGM decision from the tick, which is why KaraFun has exactly one start path (row 8).

## Coverage

Parity: 109 scripted + 400 seeded-random scenarios (500+ events each on average), identical commands, return values and state after every event.
Mutation (2026-11-05, three passes): 507 mutants after the audio-runs-to-end rule and ~70 more scenarios (omitted settings keys, non-numeric setting values, exact boundaries, single-failing-operand cases); 372 killed, 80 survive (was 88 of 491). 27 of those are documented equivalent (3 `max(0)`, 2 `runs_to_end` flags on cache paths, 22 defaults the replay contract makes unreachable: snapshot keys always emitted, record fields always present, a JSON number always converting to f64); 53 stay open GAPs (about 32 exact ties, 10 unobserved returns, 5 connectives, ~6 defaults). The non-numeric-value scenarios found a real gap: the Rust model rejected a numeric STRING such as "3.5" in a numeric setting where Python's float() accepts it (fixed: py_float_from_text). The earlier text below is the 491-mutant history. (see `tools/mutants_songend.py`). They are open gaps, not proofs of equivalence, except the 3 labelled equivalent. Mutation: `tools/mutate_songend.py` generates mutants mechanically on every code line of `Machine::event` onward
(comparison operators, `&&`/`||`, negation, numeric constants, `true`/`false`, `min`/`max`, `+`/`-`), builds each in a detached copy of the crate, and
requires the parity corpus to change; survivors are listed with a reason in `tools/mutants_songend.py`.

Not covered / honest limits:
* The real mpv transport clock (position steps, `engine_at_end`, the CDG gate) is a model; the Rust machine takes those as inputs. What is proven is the
  DECISION logic, not that the inputs are what the app sees on a given disc.
* `_arm_audio_end_floor` worker scheduling: the scan runs at spawn and its answer arrives `deliver_ms` later; real thread timing is not modelled.
* The player (`bg_music.fade_in`) is a recording stand-in (starts or refuses by scenario); its own behaviour is `singws-bgm-policy`.
* Exceptions inside the Python methods (`try/except` paths such as a failing `fade_in`) are only modelled where a scenario can produce them (fade refused).
* KaraFun detection (the monitor) is not modelled; only the hand-off call.
* The soundboard-active deferral in `_start_bg_with_fade`, the transport-shadow drive, and `_verify_bg_resume_started` calls made while `soundboard` state is dirty.
* No real show log: the timing table is a parameter sweep with the operator's settings, not a replay of the 2026-11-01 log.

## Running it

```
cd rust && CARGO_TARGET_DIR=~/.cache/singws-rust-target-songend cargo build --release -p singws-songend
cd .. && SINGWS_HOME=$(mktemp -d) .venv/bin/python tools/songend_parity.py --seeds 600     # exit 0 only if IDENTICAL
SINGWS_HOME=$(mktemp -d) .venv/bin/python tools/mutate_songend.py                          # mutation (about an hour)
SINGWS_HOME=$(mktemp -d) .venv/bin/python tools/songend_timing_report.py                   # the start-time tables
```
(`.venv/bin/python` = any Python with PyQt6, e.g. the main checkout's `.venv`.)
