# Which settings the shell may change (Stage E step 5, first slice), 2026-10-29

`configure_settings` touches 77 keys. Rule (from the merge session and AGENTS.md rule 4/6): a remote may change a setting only when it has **no live effect on a native surface or running engine**, is **not a secret, URL, path or device**, and is **read at the moment of use**. `KaraokeApp._SHELL_SETTINGS_SCHEMA` (app) and `SETTINGS_SCHEMA` (`singws-control`) list the allowed keys; both are tested against `docs/design/settings_set_cases.json` (99 cases, every refusal included).

## Allowed (11 keys: group 1 = 8, group 2 = 2, group 3 = 1)
| Key | Type | Why it is safe (checked in the code that reads it) |
|---|---|---|
| `ticker_custom_enabled`, `ticker_custom_message` | bool, text <= 200 | read by `get_singer_names_for_ticker`; the command only drops the cached text and schedules the usual debounced refresh |
| `loudness_scan_holds_for_playback` | bool | read when a library scan asks whether to pause for a playing song |
| `karaoke_auto_advance` | bool | read at each song end |
| `bg_end_silence_trim_enabled` | bool | read when the next background track is prepared |
| `karaoke_bgm_crossfade_enabled` | bool | read at the song-end hand-off |
| `end_silence_trim_threshold_sec` | number 0.5-60 | read at the early-end check; stored as a float like the dialog |
| `ui_record_confirm` | bool | read at the click on the hero record |
| `rotation_estimate_include_all_songs` | bool | group 2 (queue ETA text). Read when the queue display is rebuilt; the dialog's handler stores, saves and calls `update_queue_display()`, and so does the command (the display text only, nothing restyled) |
| `bg_to_karaoke_gap_sec` | number -3 to 3 | group 3. The dialog stores it and sets the live attribute `_transition_gap_sec`, which the next song start reads; the command does exactly that and nothing else |
| `rotation_padding_sec` | whole number 0-600 | group 2. Same as above; the dialog's spin box range is 0-600 and it stores an int, so a fractional number is refused |

## Refused (everything else), with the reason
* **Secrets / network identity:** `api_key`, `host_controls_pin`, `base_url`, `user`, `tenant`, `karafun_request_url`, `auto_update_*` (repo, manifest URL, download dir, enabled, download), `crash_auto_send_logs` (sends logs off the machine).
* **Audio device / chain (live engine):** `audio_output_name`, `karafun_audio_output_*`, `master_audio_*` (11 keys), `simple_audio_mode`, `karaoke_normalize_enabled`, `bg_normalize_enabled`, `keep_mac_awake`, `seamless_transitions_enabled`, the `karaoke_trim_verified_tail` trio (three keys set together with a live reset).
* **Windows, surfaces, styling:** all `ui_*` redesign switches except `ui_record_confirm` (each calls an `_apply_*` that restyles widgets), `ticker_color/bold/size/speed/vfx/enabled`, `rotation_announcement_*`, `rotation_vfx_enabled`, `show_screen_vfx_enabled`, `quick_gpu_surfaces` (changes the render path at the NEXT launch: a wrong value is a black screen at the next show), `cdg_*`, `lyrics_background_video_opacity`, `bg_video_*`, `list_font_scale`, `show_tooltips`, every `background_*` key (copy files / take paths).
* **Behaviour with side effects:** `defer_remote_adds_until_between_singers` (flushes the deferred adds when turned off), `queue_mode`, `rotation_locked`, `venue_profiles_enabled`, `daw_singer_screen_preview_enabled` (retunes a timer), `performance_debug_enabled` (starts a timer), `disc_id_priority`, `filename_format`, `karafun_*` switches (launch / automation).

Next slices would each need the same per-key check of what reads the setting; reviewed and kept refused (2026-10-29): `show_tooltips` (its apply walks every widget of every top-level window, the audience windows included, and strips/restores tooltips), `list_font_scale` (re-lays out the lists), `seamless_transitions_enabled` (resets live hand-off state), `karaoke_normalize_enabled` / `bg_normalize_enabled` (start or gate the loudness workers; the bg one pokes the running engine), the `karaoke_trim_verified_tail` trio (three keys set together with a live reset). What is left is the same shape: each either restyles a live window, touches a running engine or worker, or is a secret/URL/path.
