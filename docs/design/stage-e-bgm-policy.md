# Stage E: the background-music player POLICY in Rust (`singws-bgm-policy`)

Status 2026-11-02: offline, proof only. Nothing in the app calls this crate; the Python `BackgroundMusicPlayer` (0.2.18.1.py ~5073-6690)
stays the only thing that drives the Rust *engine*. The crate is the decision layer of that class as a pure state machine, with an
injectable clock, and it is proven identical to the real Python player on scripted and seeded-random scenarios.

## What is ported (and what is not)

Ported (all inside `BackgroundMusicPlayer`): the 500 ms tick (`_check_track_position`), next-track preload
(`_prepare_next_background_track` and its worker hand-back), start/complete of a crossfade, `next_track`/`previous_track`, `play`,
`pause`, `stop`, the Stop-button fade (`stop_with_fade`), `fade_out`/`fade_in` (the karaoke interrupt/resume entry points), `ensure_audible`,
`cancel_pending_fades`, `set_volume`, `set_enabled`, `set_stop_after_current`, startup preload (`preload_current_track_paused[_async]`),
volume-source rules (`_target_volume_from_ui`, `_sync_volume_from_ui_or_settings`, `initialize_startup_volume`), normalisation gain rules
(`_bg_norm_factor_for_path`, `_refresh_bg_normalize`), the unplayable-file bookkeeping (`_mark_bg_track_unplayable`, `_bg_track_is_unplayable`,
`_next_playable_bg_index`), the trailing-silence detector (`_check_bg_tail_silence`), and the `bgm_fade_in_floor_db` push at engine attach.
The two pure analysis rules the tick consults (`bgm_audio_window`, `select_bgm_crossfade_seconds`) are NOT re-implemented: the crate
depends on `singws-plan`, which another session already proved against Python.

Not ported, on purpose: `apply_audio_output_change`, `recover_failed_resume`, `shuffle_playlist`, artwork caching, the volume diagnostics,
the `_schedule_volume_state_probe` timers (they only write log lines; the harness drops them on both sides), the EQ/master/diag attach calls inside
`_attach_host_audio_chain`, and the libmpv-fallback path (`_bgm_ready()` false). The host-side halves of karaoke interrupt/resume
(`_start_bg_with_fade`, `_prefire_bgm_at_verified_audio_end`, the `bgm_overlap_before_audio_end_sec` window inside `_maybe_trim_end_silence`,
~43317-43490) are described below but NOT ported: they live in the host window, call the player only through `fade_in`/`fade_out`/`stop`
(which ARE covered), and need the karaoke end-silence machinery as input.

## Protocol (how the proof works)

`tools/bgm_policy_scenarios.py` runs the REAL class with: a recording fake engine with a tiny time model (position advances, a crossfade ends after
its length, `source_ended` at the duration), a fake host window, a virtual `time.monotonic`, a fake `QTimer` (single-shots become events on a heap;
the 500 ms position timer is driven by the harness) and a fake `threading.Thread` (the worker body runs at spawn, its UI hand-back is delivered
after `preload_latency_ms`). Real temporary files back every track, so the unplayable-signature logic runs against the real filesystem. Per event it
records (a) the INPUT the Rust side gets: event name/args plus a snapshot of everything readable (engine position/duration/ended/finished, lead, meter,
settings, live slider, karaoke flag, file stats, cached analysis records, cached loudness, scripted engine answers), and (b) the OUTPUT: every engine/host
command, every single-shot timer request, worker spawn, the return value and the player's state (index, flags, generations, volume, meter, silence
accumulator, unplayable set). `tools/bgm_policy_parity.py` replays the inputs through `singws-bgm-policy` and demands identical commands in identical
order with strict types (`5000` is not `5000.0`), identical return values and identical state after every event.

Snapshot rule: the policy never models the engine's internals. It tracks locally only what its own commands change inside one event (`stop` empties
the primary, `load` fills it) and reads everything else from the snapshot taken before the event.

## The rules

All times are virtual-clock milliseconds; "tick" is `_check_track_position`, run every 500 ms by `position_timer`.

### Volume
* Target volume = the live slider (`_host_bgm_live_volume`, clamped 0..1), else the manager slider, else the saved `bg_volume` (default 0.8, **not clamped**).
* `_sync_volume_from_ui_or_settings(reason)`: target clamped to 0..1, stored in `volume`, pushed to the engine master (`set_master_volume`). Called by `play`,
  `preload`, `_start_crossfade`, startup preload.
* `set_volume(v)`: clamp, store, push. `set_enabled(True)` stores the (unclamped) target without pushing; `set_enabled(False)` while playing = `fade_out(500)`.

### Normalisation (deck gain)
* Active only when `simple_audio_mode` is false (default true!) and `bg_normalize_enabled` (default true). Otherwise every gain is 1.0 and no cache is touched.
* `_bg_norm_factor_for_path` (used by preload, startup preload, crossfade start): cache miss -> `analyze_loudness_async(path)`, gain 1.0
  (`BGM_UNKNOWN_ANALYSIS_PREGAIN`); hit -> `gain_linear` (`or 1.0`, so 0 means 1.0) clamped to 0.05..4.0.
* `_refresh_bg_normalize` (used by `play`, `fade_in`, `preload`): sets the PRIMARY deck gain to the cached gain **unclamped**, then asks for the next
  track's analysis ("warm"). See oddity 2.

### Unplayable files
* A failed load/crossfade/preload marks a track unplayable only when the failure is permanent: the file no longer exists, or the error text contains
  `bass error 2` or `bass_error_fileopen` (case-insensitive). The mark stores `(path, size, mtime_ns)`; a later check forgets the mark when the stat changed
  (the file was replaced), so an edited file is eligible again without a restart.
* `_next_playable_bg_index(1)`: tries offsets 1..=len, **including the current track itself at offset len**, returns the first not-unplayable index (each check
  may drop a stale mark, so evaluation order is observable), else None.

### The tick
1. Smooth the meter: `meter = meter*0.70 + engine_meter*0.30`.
2. Return unless playing with a primary deck.
3. Crossfade active: if the engine says it finished -> `_complete_crossfade(current generation)`; return (nothing else runs during a crossfade).
4. Source ended (and no crossfade): `stop_after_current` -> clear it and `singleShot(0, stop)`; else `_start_crossfade(duration 100 ms)` and, if refused,
   `singleShot(0, next_track)`. Both then `singleShot(100, update_bg_button_state)`. Return.
5. `stop_after_current` set: return (no preload either).
6. Read position/duration; run `_prepare_next_background_track`.
7. `(start, audio_end) = bgm_audio_window(record, duration)` (whole file when `seamless_transitions_enabled` is off, no record, invalid record or a record whose duration differs by more than 1 s);
   `time_left = audio_end - pos - crossfade_lead_seconds`.
8. `adaptive_ms` = `select_bgm_crossfade_seconds(record, configured)` with `configured = clamp(crossfade_duration_ms, 2000, 8000)`, rounded half-to-even to ms
   (whole `configured` when seamless is off).
9. Trailing-silence detector (only when `bg_end_silence_trim_enabled`, default off): inside the last 45 s of the file (`duration - pos`), the smoothed meter at or below 0.02
   accumulates the time since the previous tick (a gap over 2 s or no previous tick counts 0); at 1.2 s accumulated -> `_start_crossfade(100 ms)` ("advance early") and return.
10. Else, if `duration > 0` and `time_left <= adaptive_ms/1000`: `_start_crossfade(min(adaptive_ms, max(100, int(time_left*1000))))`. A stalled tick therefore shortens the fade to
    the audio that is left instead of stretching it.

### Preload of the next track
* Skipped while crossfading, while a preload is in flight, or when no playable next track exists.
* De-duplicated on `(primary deck identity, next path)`: one attempt per deck per path. On an attempt: `invalidate_secondary_preload`, compute the norm gain,
  spawn the worker (`prepare_primary(path, norm_gain)`), set `in_flight`.
* Hand-back (UI thread, after the worker): clear `in_flight`; "still next" requires the same primary deck, no crossfade, a non-empty playlist, the same next playable
  index and the same path (checked in that order, short-circuit). If not still next, or the worker failed: discard the prepared source; a failure that is permanent
  marks the track unplayable, clears the dedupe key and re-schedules `_prepare_next_background_track` (0 ms). Otherwise `install_prepared_secondary`.

### Starting and completing a crossfade
`_start_crossfade(target_index?, duration_ms?)`: reset the silence detector; refuse if already crossfading or the playlist is empty; push the target volume; pick the target (given, else next playable);
`selected = clamp(duration or configured, 100, 8000)`; shrink to `max(100, int((end_of_current_content - pos - lead)*1000))`; norm gain of the next file; start offset and length of the next
file's content window, `selected = min(selected, max(100, int(window*1000)))` when the window is non-empty; `engine.start_crossfade(file, ms, norm_gain, start_seconds)`. Refused -> False, no state
change; exception -> mark unplayable (when permanent), False. Success -> `crossfade_active`, target index, generation +1.
`_complete_crossfade(gen)`: ignored for a stale generation or when not crossfading; `engine.complete_crossfade()` refused -> nothing; success -> index = target, clear, host `update_bg_track_display`, `singleShot(200, artwork precache)`.

### Transport
* `play`: ignored with no playlist / karaoke active / disabled; queued (`("play",)`) while a startup preload is in flight; a pending `playlist` realign stops an idle primary first; push volume;
  load the primary paused if absent (`preload`), refresh normalisation, push volume, `engine.play()`, push volume again, `is_playing = True`. A refused play leaves `is_playing` false.
* `pause`: generation +1, `engine.pause()`, `is_playing = False`, meter 0. `stop`: startup generation +1 and its flags cleared, both generations +1, `engine.stop()`, crossfade state cleared, meter 0.
* `next_track` while playing: ignored during a crossfade; else crossfade to the next playable track (full length); if refused, fall through to hard `stop()` + `play()` of the next track.
  Not playing: set the index, `update_bg_track_display`, `stop()`, precache timer. `previous_track` is the same with `index-1` and no unplayable check.
* `stop_with_fade(ms)` (the Stop button, 1000 ms): not playing / no primary / `ms <= 0` -> plain `stop()`; else bump both generations, cancel the crossfade, `slide_master_volume(0, ms)`, `singleShot(ms+30, _stop_after_stop_fade(gen))`;
  a stale generation (Play/Pause/another fade in between) does nothing.
* `fade_out(ms)` (karaoke starts): no-op unless playing; cancels the crossfade, `slide_master_volume(0, ms)`, `singleShot(max(1, ms + engine settle delay), _bgm_stop_after_fade(gen))` which pauses.
* `fade_in(target?, ms, allow_during_karaoke)` (karaoke ended / pre-fired): refused while karaoke is active unless allowed, or when disabled; queued behind a startup preload; otherwise load if needed, bump the generation,
  refresh normalisation, master 0, `engine.play()`, `is_playing = True`, `slide_master_volume(target, ms)`.
* `ensure_audible`: if the flag says playing but the engine is paused / not playing / master volume at or below 0.05: bump the fade generation, master 0 + play when paused or not playing, `slide_master_volume(target, 500)`.
* `cancel_pending_fades(restore)`: both generations +1, crossfade cleared, `cancel_crossfade`; with `restore`, the clamped target volume is stored and pushed.

### Startup preload
`preload_current_track_paused_async` (not on the GUI thread): no-op (True) if a primary exists or one is in flight; else push volume, compute norm gain, startup generation +1, spawn the worker. Hand-back: stale when the generation moved, the
playlist is empty or the current file changed -> discard, and only the owner of the current generation clears the flags; otherwise `install_prepared_primary(prepared, volume)`; then the queued `play`/`fade_in` request (if any) runs, also after a refused install.

### Fade-in floor and overlap (host side, `bgm_fade_in_floor_db`, `bgm_overlap_before_audio_end_sec`)
* `bgm_fade_in_floor_db` (default -60): `_attach_host_audio_chain` pushes it to the engine with `set_fade_in_floor_db`. It is the engine that applies it (fades from silence start at the floor instead of -inf; never fades out). Ported as the `attach` event.
* `bgm_overlap_before_audio_end_sec` (default 2.0, 0 = off): while karaoke plays, once `audio_end - overlap <= elapsed < audio_end` of the scanned audio end, and `karaoke_trim_verified_tail` is on, the host calls `_prefire_bgm_at_verified_audio_end`
  (`fade_in(None, 3000, allow_during_karaoke=True)` once per song, only if BGM is not playing, enabled, autoplay-on-idle, crossfade enabled and auto-advance off). Not ported: it needs the karaoke clock.

## Surprises (Python behaviour that looks wrong; reported, NOT changed)
1. **A tick can undo a fade-out / Stop fade.** `fade_out` and `stop_with_fade` leave `is_playing` true until their timer fires, and the tick does not look at fades: if the track is within its crossfade window, the next tick calls
   `_start_crossfade`, whose first act is `set_master_volume(target)` (cancelling the slide the fade started) and starts a crossfade; the pending `_bgm_stop_after_fade` then pauses mid-crossfade. Reproduced: track 20 s, `fade_out(3000)` at 14.6 s, crossfade starts at 15.0 with master back at 0.8, pause at 17.6.
2. **Two different normalisation gains.** Crossfade/preload use the cached gain clamped to 0.05..4.0; `_refresh_bg_normalize` (play/fade_in/preload) uses it unclamped. A cached 9x gain plays at 9x when the track is started directly and at 4x when it arrives by crossfade.
3. **`_schedule_bg_normalize_retry` is dead code.** Nothing calls it; the "first track not normalised" retry its comment promises does not exist.
4. **A transient preload failure is never retried for that deck.** The dedupe key `(primary, path)` stays set after a non-permanent error and only a permanent error clears it, so the next track is not preloaded for the rest of the current track (the crossfade then loads it synchronously).
5. **`_bg_end_silence_enabled` has two defaults:** `True` when the parent has no settings object, `False` (the shipped default) when it has one without the key.
6. **`preload_current_track_paused` leaves the engine master at 0.0 if `load` raises** (it zeroes the master before loading and only restores it on success). The next `play()` pushes the target again.
7. **Volume clamping is inconsistent:** `_target_volume_from_ui` returns an unclamped saved `bg_volume`; `set_enabled` and `_stop_after_stop_fade` use it as is (a saved 1.6 reaches the engine master as 1.6), every other path clamps.
8. **Next/Previous are ignored for the whole crossfade (up to 8 s).** Pressing Next during a fade silently does nothing; the click is not queued.
9. **The "next" track can be the current track:** `_next_playable_bg_index` loops over offsets 1..=len, so a one-track playlist (or one where every other track is unplayable) crossfades into itself.
10. **`stop_after_current` set during a crossfade has no effect on that crossfade;** the new track plays and the flag stops it at its own end. The tick also stops preloading while the flag is set.
11. **`id()` identity as a cache key:** the preload dedupe compares `id(primary)`; CPython may reuse an id after a deck is freed, which could suppress a needed preload. **Observed** (seed 953 of the 1,500-seed run, only when it ran after other scenarios): Python's `id()` of a freed fake deck was reused, the dedupe key matched, and a needed preload was skipped; a fresh process did not reproduce it. The harness now keeps every deck alive so ids stay unique; the Rust policy uses the engine's own deck ids.
12. `_complete_crossfade`'s timer form (`singleShot(ms, ...)`) is only scheduled when the engine has no `crossfade_finished`; the Rust engine always has it, so that path is dead.

## Coverage and limits
* Identical on the scripted scenarios plus seeded-random ones (see the report in the final message / HANDOFF for the counts). Mutation: `tools/mutate_bgm_policy.py` (mutants in `tools/mutants_bgm_policy.py`), survivors documented there.
* Not covered: the libmpv fallback, output-device change, shuffle/edit while a preload is mid-flight beyond what the random `playlist` event produces, real engine timing (the fake completes slides at once and reports the end value), a real Qt event loop, real worker threads (ordering is made deterministic), and the host-side prefire/overlap logic.
* Run: `cd rust && CARGO_TARGET_DIR=~/.cache/singws-rust-target-bgm-policy cargo build --release -p singws-bgm-policy`, then
  `SINGWS_HOME=$(mktemp -d) /path/to/.venv/bin/python tools/bgm_policy_parity.py --seeds 1500` (the venv must have PyQt6). Mutation: `tools/mutate_bgm_policy.py`.
