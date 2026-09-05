# SingWS 0.4.6.9 pre-show audit — 2026-09-05

> Post-audit update: the verified rotation visibility fix was versioned and
> built as SingWS 0.4.7.0. Singer Rotation effects remain
> enabled in the operator profile and automatically resume whenever the window
> is visible.

## 1. Repo findings

- Installed app: `/Applications/SingWS.app`, version 0.4.6.9, arm64, valid code signature.
- Source: `main` at `7347c31`, tag `v0.4.6.9`. The audit began with no tracked changes.
- UI and show control remain in Python/PyQt (`0.2.18.1.py`). Karaoke playback is the persistent native libmpv bridge (`mpv_karaoke_transport.py`, `mpv_playback_iina.py`, `native/mpv_bridge`). BGM uses the BASS/libmpv backends. Queue, waitlist, server relay, history, DAW preview, scanning, and loudness work are separated into timers/workers and tested adapters.
- Highest-risk surfaces remain native child-view stacking, output-device changes, third-party KaraFun focus/fullscreen behavior, network reconnect state, and long-running Qt Quick surfaces.
- One unnecessary idle workload was found: the retained but hidden Singer Rotation window continued running Qt Quick animation loops.
- Conservative cleanup decision: no fallback, migration, legacy-looking path, or untracked local artifact was removed during this audit. None was proven safe enough to remove immediately before a show.

## 2. Tests performed

- Installed-bundle launch/exit smoke test, including five isolated automatic-exit repetitions.
- Installed-bytecode marker verification and native-architecture/signature verification.
- Idle CPU/RSS/thread/open-file sampling and an 8-second macOS stack sample.
- Real Sound Choice ZIP MP3+CDG decode/render probe using `SC17631 - PM Dawn - Set Adrift On Memory Bliss.zip` at 4x tempo.
- 226 queue/server/history tests.
- 293 playback/audio/BGM/KaraFun/DAW tests.
- 357 reliability, show-cycle, model/view, performance, scan, identity, profile, archive, and failure-path tests.
- 14 real Qt rotation-render lifecycle tests.
- Five disposable PHP server/history/request regression scripts.
- Source compile and diff-whitespace checks.

## 3. Bugs reproduced

- Hidden Singer Rotation animations consumed material CPU while the audience window was closed. With the configured animation setting on, samples were 15.5–30.6% CPU. Temporarily disabling only Singer Rotation effects allowed the process to settle to 8.8–10.1% after the change propagated.
- A real exit-time crash report exists from 09:19:45: `EXC_BAD_ACCESS` in OpenSSL cleanup during normal application termination. It did not involve playback and could not be reproduced in five isolated installed-app launch/exit cycles.

## 4. Root causes found

- `RotationView` intentionally retains its native QQuick surfaces when hidden to avoid unstable destroy/recreate ordering on macOS. Its QML animations were gated by the user setting but not by window visibility, so the scene graph continued rendering off-screen.
- The exit crash stack is in `OPENSSL_cleanup`/`CRYPTO_free_ex_data` during process teardown. The bundled `_ssl`, `libssl`, and `libcrypto` are a matched bundled set. There is not enough evidence for a safe pre-show code change.

## 5. Safe fixes applied

- Rotation native surfaces are still retained, but their `effectsEnabled` property is now false while `RotationView` is hidden and restored to the operator's configured value when shown.
- No playback, audio, queue, server, layout, or native surface creation-order code was changed.
- This fix is included in 0.4.7.0. The Singer Rotation setting remains on; only the hidden window's animation loops are suspended.

## 6. Exact files changed

- `0.2.18.1.py`
- `test_rotation_render_thread.py`
- `docs/verification/2026-09-05-preshow-audit-0.4.6.9.md`

## 7. Automated tests added or updated

- Extended `test_rotation_view_close_hides_and_retains_native_surfaces` to prove effects are enabled while visible, disabled while hidden, restored on reopen, and that native surfaces remain retained.

## 8. Exact build commands used

- `./build_all.sh` produced `SingWS-0.4.7.0-arm64-installer.dmg`. The exact packaged app passed signature, architecture, minimum-macOS, native-library, launch, and clean-exit checks.

## 9. Exact test commands used

```sh
SINGWS_HOME=/private/tmp/singws-preshow-queue ./.venv/bin/python -m unittest test_queue_sync_authority test_remote_request_tombstones test_queue_selection_server_adds test_host_manual_request_sync test_request_relay test_request_attention_visibility test_singer_rename_merge test_duet_song_limit test_undo_queue_actions test_rotation_identity test_rotation_lock test_singer_history_counts test_singer_history_last_sang test_singer_history_song_search

SINGWS_HOME=/private/tmp/singws-preshow-playback ./.venv/bin/python -m unittest test_mpv_karaoke_transport test_karaoke_engine_selection test_per_song_key_tempo test_intro_loop test_audio_output_pinning test_mpv_audio_filters test_master_audio test_master_audio_gating test_bgm_gapless test_bgm_master test_bgm_volume_init test_bass_fade_curve test_bass_soundboard_engine test_soundboard_routing test_bg_video_lyrics test_transition_analysis test_karafun_provider test_karafun_monitor_safety test_karafun_duration_estimates test_daw_relay

SINGWS_HOME=/private/tmp/singws-preshow-safety ./qtvenv/bin/python -m unittest test_recent_regressions test_show_critical_regressions test_show_cycle_simulation test_model_view_qa test_performance_safety test_exception_handler test_library_scan_worker test_loudness_cache_repair test_multi_library_locations test_song_identity_matching test_profile_isolation test_repack_deflate64_archives test_phrase_markers

SINGWS_HOME=/private/tmp/singws-preshow-rotation QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest test_rotation_render_thread

python3 SingWS-Server/tools/run_history_regressions.py

SINGWS_HOME=/private/tmp/singws-preshow-media QT_QPA_PLATFORM=cocoa DYLD_LIBRARY_PATH=/Applications/SingWS.app/Contents/Resources ./qtvenv/bin/python tools/probe_cdg_render.py '/Users/daniel/Music/Karaoke/Karaoke Library/SC17631 - PM Dawn - Set Adrift On Memory Bliss.zip' --speed 4

python3 -m py_compile 0.2.18.1.py test_rotation_render_thread.py
git diff --check
```

## 10. Verification results

- Queue/server/history: 226 passed.
- Playback/audio/BGM/KaraFun/DAW: 293 passed.
- Broad safety/failure/show-cycle group: 357 passed.
- Rotation Qt lifecycle: 14 passed.
- Disposable server regressions: 5 scripts passed.
- Real SC CDG: native raw frames and visible output were non-uniform at 2, 5, 10, and 20 seconds; duration 261,493 ms; result `ok: true`.
- Installed app: launches, initializes native mpv/BASS/Qt Quick, and exits cleanly in repeated smoke runs.
- The repository-wide helper cannot currently run all GUI modules in one discovery process because the older `.venv` has the documented PyQt platform-plugin mismatch. GUI coverage was run explicitly with the working arm64 Qt environment. Two mistyped nonexistent module names in an early command produced loader errors; the correct rotation module subsequently passed all 14 tests.
- Not directly validated with audible room hardware tonight: physical output routing, AirPlay/display hot-plug, VST host hardware, visual MP4/CDG synchronization across the actual three-display setup, and uninterrupted real audio during all UI/network actions.

## 11. Performance measurements before and after

- Installed app after startup: approximately 315–480 MiB resident depending on macOS accounting/sample time, 36–45 threads, 238–251 open files, no decoder subprocesses.
- Hidden rotation effects enabled: 15.5–30.6% CPU in five samples; the earlier settled series was mostly 24.5–28.3%.
- Rotation effects temporarily disabled: 20.6%, 15.1%, 10.1%, 8.8%, 9.2% as the render workload drained.
- Stack sampling placed the dominant idle work in Qt Quick scene-graph/Metal render threads, consistent with the visibility-gating defect.
- The patched lifecycle behavior is functionally verified. A post-build measurement of the exact patched bundle is still required before claiming its final idle CPU number.
- No multi-hour upward RSS/thread/file-descriptor trend was established; automated repeated show-cycle and worker-cleanup tests passed, but this audit did not run for four continuous hours.

## 12. Remaining known risks

- One non-reproducible OpenSSL exit-only crash report remains unexplained. Impact is limited to quitting, based on current evidence, but it should be monitored after the show.
- The CPU fix is released as 0.4.7.0 for Apple Silicon. The Intel build remains pending a native Intel-machine build.
- KaraFun window focus/fullscreen remains partly controlled by another application's macOS accessibility/window behavior.
- Hardware-specific AirPlay/output-device loss, external/network storage removal, VST failure, and actual server restart during active audible playback were not physically injected.
- A true four-hour media soak and real three-display visual inspection were not completed; automated simulations cannot prove audible continuity or native-view stacking.

## 13. Recommended show-night precautions

- Keep Settings → Display → Singer Rotation enabled. Version 0.4.7.0 pauses those effects only while their window is hidden.
- Reboot before the show, connect displays/audio/AirPlay before launching SingWS, and run the built-in Pre-Show Check.
- Play one local SC ZIP, one MP4, one KaraFun song, test a seek and tempo/key change, confirm BGM handoff, and inspect both audience displays before admitting requests.
- Avoid changing display topology, AirPlay, or audio devices mid-song. If required, do it between singers and verify the host preview and both show screens.
- Keep a local-song fallback ready for KaraFun/network trouble. Do not install an unlaunched build immediately before doors.
- If quitting produces an OpenSSL crash report, preserve it; do not infer that the preceding show playback was corrupt.

## 14. Issues deferred until after the show

- Root-cause the one OpenSSL teardown crash with additional exit-focused diagnostics or dependency isolation.
- Run a real four-hour soak with room audio, three displays, AirPlay cycling, mixed CDG/MP4/KaraFun tracks, server reconnects, and periodic resource snapshots.
- Audit and remove dead code only with reference proof and a separate regression cycle; no speculative cleanup tonight.
- Perform broad UI style consolidation after the show. No cosmetic refactor was justified during this reliability pass.
- Improve the test runner so it selects the working Qt environment automatically and cannot silently lose GUI coverage.

## 15. Final go/no-go assessment

**GO WITH CAUTION.** The critical automated playback, queue, waitlist, reconnect, history, and simulated show-cycle coverage passed, and a real Sound Choice CDG rendered correctly. The rotation CPU fix is in 0.4.7.0 with effects still enabled. No reproducible in-show crash, audio-state failure, duplicate-request failure, or host-authority failure remains. It is not an unconditional GO because one exit-only native crash is unexplained and the requested four-hour real-hardware soak was not completed. Complete the short room-hardware rehearsal above before the show.
