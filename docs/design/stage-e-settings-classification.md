# Which settings the shell may change (Stage E step 5, first slice), 2026-10-29

`configure_settings` touches 77 keys. Rule (from the merge session and AGENTS.md rule 4/6): a remote may change a setting only when it has **no live effect on a native surface or running engine**, is **not a secret, URL, path or device**, and is **read at the moment of use**. `KaraokeApp._SHELL_SETTINGS_SCHEMA` (app) and `SETTINGS_SCHEMA` (`singws-control`) list the allowed keys; all three copies (the app, `SETTINGS_SCHEMA`, the cases file) are tested against each other (the cases file has every refusal below that a remote could plausibly try).

## Allowed (12 keys: group 1 = 9, group 2 = 2, group 3 = 1)
| Key | Type | Why it is safe (checked in the code that reads it) |
|---|---|---|
| `ticker_custom_enabled`, `ticker_custom_message` | bool, text <= 200 | read by `get_singer_names_for_ticker`; the command only drops the cached text and schedules the usual debounced refresh |
| `loudness_scan_holds_for_playback` | bool | read when a library scan asks whether to pause for a playing song |
| `karaoke_auto_advance` | bool | read at each song end |
| `bg_end_silence_trim_enabled` | bool | read when the next background track is prepared |
| `karaoke_bgm_crossfade_enabled` | bool | read at the song-end hand-off |
| `end_silence_trim_threshold_sec` | number 0.5-60 | read at the early-end check; stored as a float like the dialog |
| `ui_record_confirm` | bool | read at the click on the hero record |
| `karafun_include_online_search` | bool | added in the 2026-10-30 full review. The KaraFun tab only stores and saves it (`_save_karafun_choice`, nothing else); the song search reads it when a search starts to decide whether to merge the server's catalog rows. No launch, no automation, no restyle |
| `rotation_estimate_include_all_songs` | bool | group 2 (queue ETA text). Read when the queue display is rebuilt; the dialog's handler stores, saves and calls `update_queue_display()`, and so does the command (the display text only, nothing restyled) |
| `bg_to_karaoke_gap_sec` | number -3 to 3 | group 3. The dialog stores it and sets the live attribute `_transition_gap_sec`, which the next song start reads; the command does exactly that and nothing else |
| `rotation_padding_sec` | whole number 0-600 | group 2. Same as above; the dialog's spin box range is 0-600 and it stores an int, so a fractional number is refused |

## Refused (everything else), with the reason
* **Secrets / network identity:** `api_key`, `host_controls_pin`, `base_url`, `user`, `tenant`, `karafun_request_url`, `auto_update_*` (repo, manifest URL, download dir, enabled, download), `crash_auto_send_logs` (sends logs off the machine).
* **Audio device / chain (live engine):** `audio_output_name`, `karafun_audio_output_*`, `master_audio_*` (11 keys), `simple_audio_mode`, `karaoke_normalize_enabled`, `bg_normalize_enabled`, `keep_mac_awake`, `seamless_transitions_enabled`, the `karaoke_trim_verified_tail` trio (three keys set together with a live reset).
* **Windows, surfaces, styling:** all `ui_*` redesign switches except `ui_record_confirm` (each calls an `_apply_*` that restyles widgets), `ticker_color/bold/size/speed/vfx/enabled`, `rotation_announcement_*`, `rotation_vfx_enabled`, `show_screen_vfx_enabled`, `quick_gpu_surfaces` (changes the render path at the NEXT launch: a wrong value is a black screen at the next show), `cdg_*`, `lyrics_background_video_opacity`, `bg_video_*`, `list_font_scale`, `show_tooltips`, every `background_*` key (copy files / take paths).
* **Behaviour with side effects:** `defer_remote_adds_until_between_singers` (flushes the deferred adds when turned off), `queue_mode`, `rotation_locked`, `venue_profiles_enabled`, `daw_singer_screen_preview_enabled` (retunes a timer), `performance_debug_enabled` (starts a timer), `disc_id_priority`, `filename_format`, `karafun_*` switches (launch / automation).

Next slices would each need the same per-key check of what reads the setting; reviewed and kept refused (2026-10-29): `show_tooltips` (its apply walks every widget of every top-level window, the audience windows included, and strips/restores tooltips), `list_font_scale` (re-lays out the lists), `seamless_transitions_enabled` (resets live hand-off state), `karaoke_normalize_enabled` / `bg_normalize_enabled` (start or gate the loudness workers; the bg one pokes the running engine), the `karaoke_trim_verified_tail` trio (three keys set together with a live reset). What is left is the same shape: each either restyles a live window, touches a running engine or worker, or is a secret/URL/path.

## Full per-key review, 2026-10-30 (the 66 keys that were refused, re-read in `0.2.18.1.py`: the dialog's handler and every reader)
Result: ONE more key allowed (`karafun_include_online_search`, above). The other 65 stay refused. "Handler" is what the Settings dialog does on a change; the command may do no more than that and no less.

| Key(s) | Decision | Reason |
|---|---|---|
| `api_key`, `host_controls_pin`, `base_url`, `user`, `tenant`, `karafun_request_url` | refused | secrets / network identity (the Settings page masks these too) |
| `auto_update_repo`, `auto_update_manifest_url`, `auto_update_download_dir`, `auto_update_enabled`, `auto_update_download` | refused | URL / path / the updater downloading code |
| `auto_update_check_interval_hours` | refused | a bounded int whose handler only stores it, but it belongs to the updater; the schema guard keeps every `update` name out |
| `crash_auto_send_logs` | refused | sends logs off the machine |
| `audio_output_name`, `karafun_audio_output_follow_singws`, `karafun_audio_output_name`, `simple_audio_mode`, `master_audio_*` (11), `keep_mac_awake` | refused | audio device / chain: the handlers reconfigure a running engine |
| `karaoke_normalize_enabled`, `bg_normalize_enabled`, `seamless_transitions_enabled` | refused | start or gate loudness workers / reset live hand-off state |
| `karaoke_trim_verified_tail`, `karaoke_allow_early_silence_trim`, `end_silence_trim_enabled` | refused | one dialog switch sets all three together (`on_end_silence_toggled`), and `end_silence_trim_enabled` also gates the lead-silence prescan; one alone leaves the app in a state the dialog cannot produce |
| `karafun_auto_queue_enabled`, `karafun_auto_key_tempo`, `karafun_dual_renderer_capture` | refused | one dialog switch sets all three (`_on_karafun_integration_toggled`); it drives KaraFun with Accessibility automation |
| `karafun_launch_at_startup` | refused | launches another application at the next start |
| `bg_video_enabled`, `bg_video_folder`, `bg_video_shuffle`, `lyrics_background_video_opacity` | refused | folder is a path; the handler restarts or stops the live background video |
| `cdg_display_mode`, `cdg_stretch_fill`, `cdg_near_black_cleanup`, `cdg_near_black_threshold` | refused | the handlers invalidate the scaled frame of the audience and preview windows and repaint them |
| `ticker_vfx_enabled`, `show_screen_vfx_enabled`, `rotation_vfx_enabled` | refused | read by live audience surfaces (`set_effects_enabled`, `set_show_vfx_enabled`) |
| `ui_bgm_redesign`, `ui_chat_redesign`, `ui_dialogs_redesign`, `ui_history_redesign`, `ui_show_redesign`, `ui_waitlist_redesign`, `ui_preview_overlay`, `ui_ticker_glass`, `ui_hero_glow`, `ui_hero_record_animate` | refused | each handler calls an `_apply_*` that restyles live widgets |
| `ui_queue_pin_current` | refused | handler calls `_schedule_pinned_current_sync()`, which re-lays out the queue list |
| `list_font_scale`, `show_tooltips` | refused | re-lay out lists / walk every top-level window |
| `quick_gpu_surfaces` | refused | changes the render path at the next launch: a wrong value is a black screen at the next show |
| `defer_remote_adds_until_between_singers` | refused | flushes the deferred adds when turned off |
| `queue_mode`, `rotation_locked`, `rotation_lock_insert_count`, `venue_profiles_enabled` | refused | change how the queue is ordered / rebuild it; the counter is internal state |
| `daw_singer_screen_preview_enabled`, `performance_debug_enabled` | refused | retune or start a timer |
| `disc_id_priority`, `filename_format` | refused | free text that changes how requests and files are matched; needs its own normalisation (the dialog uses `normalize_disc_priority`) |

The refusal of the closest candidates is pinned by cases in `settings_set_cases.json`, and three mutants (`tools/mutate_settings_set.py`) add a refused key to the app schema.

## The Settings page (read-only)
`rust/crates/singws-shell/qml/SettingsPage.qml` lists every key that is in `settings.json` (search box, file order). A value is replaced by "hidden" when any word of its key is `key`, `pin`, `token`, `password`, `secret`, `url`, `user`, `tenant` and the like (`singws-uimodel::settings_view`); lists and objects show only their size. Only the allowed keys above get a control. Keys the app has never saved are not in the file and so not listed. Tests: the unit tests in `settings_view.rs`, `SettingsPageMaskingParityTests` (the Rust word list against the app's real key names and the shared cases file) and the shell scenario in `test_shell_panels.py` (a profile full of secrets, URLs and identity keys: nothing leaks to stdout/stderr, also with a search that matches only masked keys).
