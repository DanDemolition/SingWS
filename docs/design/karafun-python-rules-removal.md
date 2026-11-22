# Removing the Python KaraFun song rules (plan only: nothing is deleted yet)

Status 2026-11-18: `karafun_web_engine` defaults to `rust` (commit 416cc1b). The Python host (`karafun_web_host.py`) still exists as the fallback when the Rust
program or the display program is not found at launch (`karafun_web_host_rust.make_host` returns None). This note maps what could go, what has to stay, and what
must be true first. The rule from the operator is: Rust owns everything, but a rule is only retired after a clean show (rehearsals count) and never silently.

## Status 2026-11-22: gate NOT met, nothing deleted

The 2026-11-21 Intel show was not clean: KaraFun songs started but the player's clock never moved (six starts), and after the last Stop the page ignored 620 stops.
Rules added to the Rust driver for it (not in the Python host): `STUCK_CLOCK_S`, `STRAY_STOPS_BEFORE_CLOSE`; the parity tools run with `SINGWS_KF_OLD_RULES=1`.
Step 3 (the frozen oracle) is done in `tools/oracle/` (copies, not yet used). Steps 1 (a clean show) and 4 to 6 wait for a show where `karafun_rust_report.py --require-all` is CLEAN.
The Python host also stays as the fallback until then.

## What the Rust host already replaces, and what it still borrows

`KaraFunWebHostRust` subclasses `KaraFunWebHost`. Measured on the code (2026-11-18, `ast` over both files):

* 54 methods in the Python host class. The Rust host overrides 10 (`__init__`, `_ask_for_browser`, `active`, `apply_key_tempo`, `ensure_service`, `on_finish`,
  `seek_to`, `shutdown`, `start`, `toggle_pause`).
* Reached from the Rust host or the app (must stay, about 220 lines, all Qt or app glue): `_audience_rect`, `_place_picture`, `_place_preview`, `_preview_rect`,
  `_restack_overlays`, `_start_frames`, `_stop_frames`, `stack_base`, `_route_sound`, `_entry_current`, `_song_id`, `_shadow_observe`, `enabled`, `_log`, `_diag`.
* NOT reached under `rust` (song logic only the Python host uses, about 440 lines): `_start_impl`, `_started`, `_follow`, `_recover`, `_fail`, `_complete`,
  `_check_body`, `_handle`, `_handle_impl`, `_on_message`, `_tap`, `_state_changed`, `_state_changed_impl`, `_send_play`, `_play_after_hold`, `_hold_over`,
  `_hold_left`, `_arm_watchdog`, `_stop_stray`, `_toggle_pause_impl`, `_seek_to_impl`, `_apply_key_tempo_impl`, `_on_finish_impl`, `_saved_login`,
  `_service_state`, `_shadow_world`, `_last_status_summary`, `_fx`, `can_start`.
* `karafun_web.py` (468 lines): `KaraFunWebService` (the sign-in state machine) and the pipe handling of `KaraFunWeb` go unused under `rust`; the Rust host's
  `_RustWeb` inherits only the command-sending methods (`window`, `order`, `audience`, `refresh`, `output`, `signout`, ...) and the browser download helpers.
* `karafun_web_shadow.py` (286 lines) only compares the Python host with the Rust twin: pointless once Python no longer acts.

## What has to stay (Qt or app facts Rust cannot own)

The picture windows and their stacking, the sound routing, the Chromium question dialog, the Keychain UI in Settings (`karafun_credentials.py` writes it;
Rust only reads it), `karafun_top100.py`, `karafun_bridge.js`, and the display program (`singws-karafun-display`).

## Order of removal (each step is its own commit, each behind a green suite)

1. Gate: `python3 tools/karafun_rust_report.py --require-all` is CLEAN on the Intel show Mac in a rehearsal or a show, AND one more show passes with
   nothing in ATTENTION. `tools/karafun_web_preshow_check.sh` still applies before the show.
2. Make the Rust program non-optional in the build: the installer builds fail (not warn) when `singws-karafun-svc` did not build. Today `build_singws_mac_*.sh`
   only echo a warning, and `make_host` falls back to Python, which is the whole reason Python must stay until this step is in a shipped build.
3. Freeze the oracle: move the Python song logic that the parity tools compare against (`tools/karafun_host_parity.py`, `karafun_service_parity.py`,
   `karafun_driver_parity.py`, `mutate_karafun_*.py`) into a frozen `tools/oracle/` copy so the Rust code keeps a reference after the app stops using it.
4. Delete the unreached Python host methods (list above) and `KaraFunWebService`; the Rust host stops inheriting and takes the ~220 lines of Qt glue itself.
5. Delete `karafun_web_shadow.py` and its wiring (`KFWEB-SHADOW` row of `tools/shadow_report.py`, the spec entries, the settings key `karafun_web_engine`
   values `shadow`/`python`).
6. Port or delete the tests that exercise the Python host: `test_karafun_web_host.py`, `test_karafun_web.py`, `test_karafun_web_shadow.py`, and the parts of
   `test_status_snapshot.py` that read the Python host; `test_karafun_host_rust*.py`, `test_karafun_driver.py` and the crate tests stay.

## Not decided here

* Whether a driver that dies mid-song should fall back to the Python host for the REST of the session. Today it fails that song and the next request starts a
  fresh driver (`_ensure_proc`); a second crash in a row is not handled. Removing Python also removes the option of a fallback, so decide this first.
* The stall detection (`host_stall`) is Rust's job per the operator; it lives in `host.rs` and is covered by the parity tools and the mutation run.
