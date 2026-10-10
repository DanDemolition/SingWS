# SingWS handoff

Updated 2026-09-29.

## VERSION POLICY (operator instruction, 2026-09-29)

**Do not bump the version for test builds.** 1.0.0.7 (re-released 2026-10-03 evening) is the latest release; rebuild for testing without changing it
and bump only when the operator says release. (1.0.0.5 was published 2026-09-29 and re-released with host chat on 2026-09-30.)
Iron rule: iCloud "Desktop & Documents" sync is ON and the project folder is ~24 GB, so builds make fileproviderd/cloudd/bird
saturate the Mac (load average >50) and cause video slowdowns while the capture itself logs a steady 29 fps. Do not build while the
operator is testing playback. Menu bar hidden on the virtual test screen in full screen is BetterDisplay's max-level overlay, not
SingWS; the operator decided full screen works as is.

## Singer notifications auto-clear - 2026-10-05 (server code committed and pushed, NOT deployed; no app change needed)
Operator: notifications on the server should clear after ~18 h, or go away once seen. Server `a795678`: every singer notification (stage cues AND host/singer chat messages) now expires with the venue's
retention (`chat_retention_hours`, default 18, the same setting that clears room chat) - on every singer fetch (`purge_stale_singer_notifications`) and in the hourly `tools/chat_cleanup.php`
(`purge_expired_singer_notifications`). Opening the singer page's Notifications panel sends `mark_seen=1` (`singer_session_ui.js?v=15`): what it showed gets `read_at`, and rows with `read_at` are no longer listed
(the rows are kept, so the host's Host chat conversation is intact; the singer's own replies, inserted already-read, no longer clutter the panel). Also fixed: the delivered-at update mixed named and `?`
placeholders and only updated some rows (documented SQLite3 behaviour), so the existing "cleared 15 min after delivery" rule now applies to every stage cue. Old rule "chat is NEVER auto-deleted" in
`tools/test_singer_notification_expiry.php` was changed to the new rule; new `tools/test_singer_notification_retention.php` (endpoint, seen marking, host chat intact, per-venue retention in the cron). All 9 PHP suites pass.
**To go live:** operator runs `server-deployments/notification-expiry/deploy.sh` (five existing files; checks live == the previous commit's files first, backs up, php -l, hashes, undo script). Old singer pages (cached JS) keep working but only get the 18 h expiry.

## Bug reports to the developer - 2026-10-05 (server DEPLOYED and verified end to end; app code committed, NOT yet in any build)
Operator asked: permanent bug email to dan@wildstylekaraoke.com on every build (no recipient picker), sending only the LAST SHOW's log, not 3 days.
**Design:** the app never mails anything itself and holds no mail credentials (the app repo is public). It POSTs a sanitized ZIP to the venue's own server (`api/v1/support_logs.php`, venue API key
header); the server keeps a copy (`tenants/<user>/support_logs/`, newest 25) and mails it to the fixed address `SUPPORT_LOG_TO` in `api/v1/_support_mail.inc` via the Resend account password reset already uses
(`resend_email.inc`: untracked, git-ignored, live key; never print it). Limits: 12 MB, 6/hour and 30/day per venue, ZIP magic check; destination cannot be chosen by the app or request.
**App:** `_build_last_show_package` / `prepare_log_email_package` pick the last RUN of the app that lasted >= 10 min (a short relaunch to export logs is skipped), merged across the midnight rotation (files ordered by mtime,
not name), as ONE sanitized log with a header; `send_log_package_to_developer` posts it; Settings > Advanced > Logs & Crash Reporting now has "Send Last Show's Logs" and "Automatically send a bug report ... after a crash"
(default ON, `crash_auto_send_logs`). The recipient box, SMTP fields and all SMTP code are gone; the old keys (`crash_log_email_to`, `log_smtp_*`, including a saved mail password) are removed from settings.json at launch.
The venue API key field in Settings > Network is now always masked. Tests: `test_last_show_logs.py` (18+), server `tools/test_support_logs.php` (17 checks), 1,273 app tests + 96 native pass.
**Server deployed by the operator 2026-10-05** (`server-deployments/support-logs/deploy.sh`; undo path printed by the script under `/root/singws-chat-deploy/support-logs-<time>/rollback.sh`). Verified live from outside: no login -> 400 `missing_user`,
wrong key -> 401, GET -> 405, `resend_email.inc` and `config.inc` -> 403 over the web, and a real multipart upload with the venue key returned `{"ok":true,"stored":true,"emailed":true}`; the operator confirmed both the direct Resend test email and the
end-to-end email arrived. **Still needed:** a new app build installed (installed apps still have the old SMTP settings UI and send nothing to the server).
A real test email was sent 2026-10-05 through the exact server mail code (Resend accepted it); inbox/spam receipt to be confirmed by the operator. **SECURITY:** while reading `resend_email.inc` a session PRINTED the live Resend key into the
conversation: the operator must rotate it in Resend and update `resend_email.inc` on the server. Scans found no secret in the public repo (tree and history), the server repo history or any log. AGENTS.md now has a "Secrets never appear in output" rule.

## Cleaner artist/title on the background-music card — 2026-10-05, committed, NOT built/installed
`track_display.py` (new, pure Python) turns messy BGM file names into a tidy artist/title: drops track numbers ("01.", "00."), pack/album
middle segments ("Ultimix 330"), "(Ultimix By ...)" and trailing BPM, "[Official Video]"/"(Explicit)"/"(Radio Edit)" noise, fixes `Don_t`/`Lil_`,
"F."/"ft." -> "feat.", capitalises all-lowercase titles, and drops the duplicated half of "x- x". Good file tags win; junk tags (Unknown, Various Artists,
Track 01, URLs) are ignored and the file name fills gaps. Used by the audience idle card (`_get_idle_bg_overlay_info`, tags read once per track) and the host
panel (`_bg_track_title_artist`). Nothing is renamed on disk. Tests: `test_track_display.py`; full suite 1309 pass. Look at the card on screen with a few real tracks.

## RE-RELEASED 1.0.1.0 a THIRD time (second build on 2026-10-09) - Rust default; published, latest; NOT installed anywhere
Operator: "DO THE BUILD" after making Rust the default. App code = commit `588e4c8`; release commit `fd19fed`; tag `v1.0.1.0` force-moved (was `e8647fe`); DMGs replaced with `--clobber`; notes updated. arm64 `bf2cb239...` (137,067,939 B), x86_64 `3563ae66...` (155,438,313 B). Verified: re-downloaded `latest/download` SHA-256s equal `docs/release.json`; Pages manifest and download page serve them (took ~1-2 min to update). Both: strict signature, version 1.0.1.0, dylib has `singws_transport_step_json`, `singws-analyze` executable, Intel 708 Mach-O min <= 12.3 (arm64 is enforced at 14.0 by its build script, so the 12.3 check reports 431 exceed: expected), scratch launches clean with clean shutdown (Intel under Rosetta), live log unmoved. Supersedes the earlier 2026-10-09 hashes (`0e3fc657`/`5eb8ef20`) anyone may have downloaded: the same version number, so tell people to re-download. The operator TODO below still applies and now installs THIS build (Rust is its default; the venue Mac already had rust set).

## BGM limiter was dormant - FIXED 2026-10-10 (commit `07ca053`); rebuilt, NOT installed, NOT released, version stays 1.0.1.0
Operator asked that all DSP stages and the EQ be proven active for BGM AND karaoke. Findings: karaoke chain (lavfi `alimiter`) and the BGM EQ (ten BASS PARAMEQ bands) worked; the BGM master LIMITER in `singws_master_audio.py` / Rust `singws-dsp` was a smoothed-detector design that essentially never limited (the output clip at -0.1 dBFS did the work). Replaced in BOTH implementations by a look-ahead brickwall limiter (look-ahead = `limiter_detector_ms`, default 1.2 ms; guarantees output <= ceiling; stereo-linked; release `limiter_release_ms`). Cost: the BGM signal is delayed by L-1 samples (~1.2 ms) while the limiter is on; irrelevant for background music. Python-vs-Rust parity 3e-8. Tests: `test_master_limiter.py` (8), 7 new Rust `limiter_tests`; full suite 1,429 pass (live log unmoved). Proof tools (committed): `tools/master_dsp_stage_check.py` (needs `SINGWS_DSP_LIB=<dylib>`; all stages incl. limiter -1.00 dBFS on, matches Python), `tools/karaoke_dsp_stage_check.py` (all 8 stages on the real mpv af chain), `tools/bgm_eq_check.py` (all 10 bands). Analysis cache is path+mtime+size only, so switching engines never forces a rescan.
**Builds (repo root, git-ignored):** arm64 `d188e959...` (137,059,562 B), x86_64 `3397862c...` (155,441,816 B). Both: DMG strict signature, version 1.0.1.0, bundled `libsingws_dsp_ffi.dylib` code == the fixed build, `singws-analyze` present, bridge has render-thread exports; scratch launches clean (Intel under Rosetta). The x86_64 build needs NO env overrides: `SINGWS_BUILD_PYTHON=$PWD/.venv-build-intel-rosetta/bin/python ./build_singws_mac_intel.sh` (it rebuilds its own bridge; the Frameworks-intel-release override fails the runtime check). **Rollback for the Intel Mac:** `local-installs/20261010-intel-rollback/`.
**Next:** operator installs the arm64 DMG here and rehearses; then re-release both under 1.0.1.0 (procedure above); the Intel Mac gets the first test of this build at the show.

## BGM chain now attaches at launch - fixed 2026-10-10 (operator: "fix whatever the issue is"), committed, in the 10:23-10:26 builds, NOT installed, NOT released
The "known quirk" (master chain / EQ / counters missing until Settings was toggled) is FIXED, superseding the 2026-10-08 "declined" note. Cause: `BackgroundMusicPlayer(self)` is built before `self.settings` exists, so the attach in `_init_bass_engine` saw no settings and skipped silently. The attach is now `BackgroundMusicPlayer._attach_host_audio_chain` (called by `_init_bass_engine` and again from `QTimer.singleShot(0, self._attach_bgm_chain_after_init)` right after settings load); log line `[MASTER-AUDIO] BGM chain attached at launch master=N proc=N` (expect 1/1 on a profile with master audio on, then `engine=rust`). Tests: `test_bgm_chain_attach_at_launch.py` (5); full suite 1,434 pass. Builds: arm64 `74d8a510...` (136,736,609 B), x86_64 `337f8501...` (155,445,757 B); both DMG-signature OK and launch clean on scratch (line present, master=0 there because the scratch profile has master audio off). NOT yet seen with the operator's real profile. Limiter fix (above) was operator-tested on the previous arm64 build: "everything seemed to work fine".

## ROADMAP agreed 2026-10-10: more Rust (operator's list, NOTHING started yet)
Why: the BASS licence could change, and the operator wants a Windows port eventually. Operator: "I will add 1, 2, 3 and 4 to the list of stuff to do." In order: **1. Rust background-music (and soundboard) engine replacing BASS** behind a `bgm_engine` setting (default "bass" until the end): M1 write down the BASS contract (~30 operations in `bass_background_engine.py`: load/play/pause/seek, crossfade + preload, volume slides, normalization, EQ, master compressor, meter, times) and build the engine offline on cpal + symphonia (the crossfade mixer core already exists in `rust/crates/singws-bgm`); M2 A/B render against BASS (targets from the plan: gap under 5 ms, crossfade level within 0.5 dB); M3 sound-card handling (named output, hot-plug, sample-rate change, latency on the real interface); M4 parity (normalization, slides, a 10-band EQ ported from `singws_eq.py`, compressor, meter, seek; the Rust master DSP attaches directly); M5 wire in behind the setting; M6 rehearse: 4 h soak here, the Intel Mac, two real shows, only then flip the default, BASS stays in the build until after that. **2. A Rust video bridge driving libmpv** (replaces the macOS-only `bridge.mm`; the key piece for Windows; the render-thread design is built in from the start). **3. Move karaoke audio onto the Rust engine** (gives karaoke the exact master chain instead of the 18-stage lavfi approximation). **4. Keep mpv and FFmpeg as the decoders** until there is a specific reason to replace them (video/hardware decode and rubberband key/tempo are C/C++ libraries Rust would only wrap; mpv carries years of A/V-sync and odd-file fixes).
Not part of the list: karaoke playback stays mpv today; Tauri UI stays on hold; Stage 3 only drives playback after the shadow acceptance (>= 3 clean shows, `tools/transport_shadow_report.py`). Windows port is BIGGER than the audio engine: it also needs the video bridge replaced and a substitute for the KaraFun AppleScript automation. First action when the operator says go: milestone 1 of item 1 (additive, no app change).

## BGM crossfade envelope now verified for Rust - 2026-10-10, committed, NOT in any build/release
Operator asked whether the envelope still needed work. It did not: the old "not equivalent" label (`RUST_ENVELOPE_VERIFIED = False`) was caused by libmpv's 109 ms windows at 44.1 kHz, fixed by the 2026-10-09 `aresample=48000`. Re-measured 2026-10-10 on 40 real local tracks (arm64, `/private/tmp/.../env_parity.py`, libmpv envelope vs Rust envelope, each run through `transition_analysis.build_audio_transition_analysis`): derived `audio_start` identical 40/40, `audio_end` and `fade_start` within 0.1 s, envelope length ratio 0.9992-1.0000, mean envelope difference 0.00 dB after quantisation, no record missing on either side. So in `analysis_engine = "rust"` (the default) BGM envelopes now come from Rust too; falls back to libmpv on any Rust trouble. Tests: `test_rust_analysis.py` (43, incl. the unverified-flag fallback); full suite 1,421 pass. NOT tested: Intel hardware, a full BGM scan in the real app, files other than the 40 sampled (mostly 44.1 kHz MP3/FLAC).

## Rust analyzer ON by default + Stage 3 shadow ON by default and completed - 2026-10-10, committed, NOT in any build/release
Operator: "can we turn the rust analyzer on by default?" and "turn stage 3 on by default and finish it." **Analyzer:** `DEFAULTS["analysis_engine"]` is now `"rust"` (the launch call defaults to "rust"; the module-level state stays "libmpv" until launch applies it, so tests and bare imports never touch the helper; bad values still mean libmpv). Rust answers loudness, peak, duration and the silence boundaries; the BGM crossfade envelope and ANY Rust trouble stay on libmpv (RUST_ENVELOPE_VERIFIED is still False). Evidence: Intel 500 real files loudness 500/500 within 0.1 LU, peak 442/443; arm64 150 real files identical; 3 real library ZIPs on this Mac (the full library lives on the Intel Mac) loudness/peak exact, boundaries within 0.4 ms, one duration 52 ms apart (SC3408 Van Hunt - Dust, VBR MP3: harmless). NOT tested: a large sample of ZIPs for the boundaries (only 3), and a full library scan in the real app. The app extracts the MP3 from a ZIP first and hands the MP3 to the helper, so the zip-reading code in Rust is not on the app's path. `"libmpv"` in settings.json restores the old engine; `"shadow"` compares and logs.
**Stage 3:** `transport_shadow` is now ON by default (log-only; every call wrapped; no dylib symbol = unavailable). Finished: hooks for NATIVE songs (`_start_karaoke_transport` start + playing, `_handle_media_end_safe` media-end/duplicate/stop-in-progress, `_finish_media_end_cleanup` finish, `stop_playback` manual stop) and the KaraFun duration watchdog; `return_to_queue`/stop/skip is a stop (StopRequested), not a manual Complete; events for a session that is not the current one are dropped silently; the CleanupDone/AdvanceRotation result is now logged. New `tools/transport_shadow_report.py FILE...` (one file = one show; `--` no args reads ~/SingWS/logs) prints songs watched, disagreements, rules that fired, and the plan's acceptance verdict (>= 3 shows, all with zero disagreements). Tests: `test_transport_shadow.py` (19, real Rust machine), `test_transport_shadow_report.py` (3). Full suite 1,420 pass. **Deliberately NOT done:** letting the machine DRIVE playback; the plan's own rule requires 0 unexplained disagreements over >= 3 shows first. **Next:** run shows, then `python3 tools/transport_shadow_report.py <last-show exports>`; any MISMATCH is either an app bug or a machine rule to fix.

## RE-RELEASED 1.0.1.0 a FOURTH time (2026-10-10) - render thread default ON, BGM playlist search, stall diagnostic; published, latest; installed on this Mac (arm64) only
Operator: "build the intel one and release them both on github with the same version number." App code = commit `4d0a65f` (HANDOFF-only commits since); release commit `3d433a8`; tag `v1.0.1.0` force-moved (was `7bcb733`); DMGs replaced with `--clobber`; notes replaced. Shipped the arm64 build he had already installed and rehearsed (sha256 `c480b262...`, 136,734,907 B, exe `91ff3e42...`) plus a NEW Intel build (sha256 `aee19351...`, 155,430,586 B). Verified: re-downloaded `latest/download` SHA-256s equal `docs/release.json`; Pages manifest and download page serve them. Intel build: strict signature, version, x86_64, bundled bridge has the render-thread exports, `singws-analyze` + `singws_transport_step_json` present, 708 Mach-O min <= 12.3, scratch launch under Rosetta clean (`karaoke_render_thread=on`, clean shutdown). Suite 1,408 pass; native 96 not run. Supersedes the earlier 2026-10-09 hashes (`bf2cb239`/`3563ae66`): same version number, so tell people to re-download.
**Open risk, accepted by the operator:** the Intel build has the render thread ON by default and has NEVER run on a physical Intel Mac (Intel GPU + multithreaded GL). Fallback: `"karaoke_render_thread": false` in settings.json, applies at next launch. **To check on the Intel Mac after installing:** log lines `[RENDER-THREAD] karaoke_render_thread=on`, `[bridge] render thread mode=ON`, `output view presenting`; play a CDG with background video, resize/hide/show the audience window, and watch for a black or frozen TV picture; `grep "system during a >1s stall"` for any freeze snapshot. Then the earlier Intel TODO (Force BGM rescan; optional `transport_shadow`).

## Karaoke render thread (DEFAULT ON in source since 2026-10-10, UNTESTED in the real app) - committed, NOT in any build or release, version stays 1.0.1.0
Operator: "draw the tv video on its own." Fixes the finding below (the TV picture froze with the GUI thread). **OPERATOR DECISION 2026-10-10: "switch it on by default then we test it"** (I noted that the setting being on gives the same rehearsal as the default, and that the released 1.0.1.0 DMGs are still default-off; he chose default ON anyway). **REHEARSAL RESULT 2026-10-10 (this Mac, arm64): PASSED.** Built `SingWS-1.0.1.0-arm64-installer.dmg` (sha256 `c480b262...`, exe `91ff3e42...`; verified signature, version, bridge exports, scratch launch showed `karaoke_render_thread=on`), operator installed it at /Applications and ran the whole checklist (CDG + background video, singer transition, rotation screen, KaraFun song, resize/hide the audience window, Chrome opened during playback): "worked perfectly". Log evidence: the launch line's exe hash matches the DMG, `[bridge] render thread mode=ON (private render queue)`, output/preview views cycled `presenting` <-> `view-hidden` correctly as the window was hidden/shown, 0 tracebacks, no SingWS crash report (the two `Python-*.ips` reports today are test/probe processes, not the app). **Honest limit:** NO GUI stall over 1 s was logged during the Chrome test (no `[STALL] system during a >1s stall` line), so that run did not actually reproduce a long freeze; the freeze case itself is proven only by `tools/render_thread_probe.py`. **The guard that remains:** the Intel Mac has NOT run this (Intel GPU/driver + multithreaded GL is the open risk): install the Intel build there, run a rehearsal, and keep `"karaoke_render_thread": false` available as the one-line fallback in settings.json until it has run a full show. (Old guard, now satisfied for this Mac:) **do not build a RELEASE or install this on the Intel Mac / a show Mac until the real-app rehearsal on this Mac has passed** (CDG + background video, singer transition, rotation screen, KaraFun song, resize/hide the audience window, the Chrome test); if it fails, set the default back to False (one line, plus `test_render_thread_setting.py`). **Setting `karaoke_render_thread` (default True now; `false` in settings.json restores the old main-thread drawing; read at launch)** sets env `SINGWS_RENDER_THREAD=1`, which `bridge.mm` reads when it creates its renderer. **Off = byte-for-byte the old behaviour** (`_glQueue` is the main queue, so every dispatch is the old main-queue one). **On:** one private serial queue (`com.singws.mpv.render`, QoS user-interactive) owns ALL OpenGL work: mpv render/update, `presentView` into the output/preview/rotation/spotlight views, background-video render, mpv event draining, the load-time clear of the retained targets, `grabFrame`, render-context create/free. Main-thread-only AppKit reads are replaced by a per-view cache (`presentable`, `backingW/H`) refreshed on the main thread by `viewGeometryChanged:` (reshape / viewDidMoveToWindow), a 100 ms main-run-loop timer and the attach/refresh paths; a frozen main thread just leaves it stale. `BridgeVideoView -update` takes `CGLLockContext` in this mode (Apple's multithreaded-GL rule); `_hasFrame`/`_isCdg` are now atomics; rotation/spotlight view pointers are written on the GL queue. `drawRect:` and every other main-thread caller go through `presentView:` which hands the draw to the render queue. New exports: `singws_bridge_set_render_thread`, `_render_thread_active`, `_present_count`.
**Proof (this Mac, arm64, real windows, silent): `tools/render_thread_probe.py`** (`QT_QPA_PLATFORM=cocoa QT_QPA_PLATFORM_PLUGIN_PATH=/tmp/singws-release-qt-platforms .venv-test-arm64-fresh/bin/python tools/render_thread_probe.py --dylib <scratch bridge dylib built with native/mpv_bridge/build_bridge.sh --arch arm64 --frameworks ../mpv_runtime/artifacts/arm64/Frameworks --out <path>> --mode off|on --scenario freeze|cdgshot|resize|soak [--hold-gil]`; a scratch `Frameworks` symlink next to the dylib is needed). **Freeze 5-6 s of the main thread: OFF = 0 frames drawn, screenshots identical (the bug reproduced); ON = 170-232 frames drawn, screenshots differ (picture kept moving), also with the GIL held.** Real CDG song (SC17631 PM Dawn) at the same seek point: ON vs OFF screenshots are pixel-identical (0 of 844,800 differ) and show correct lyrics. Resize 640x360 -> 900x520: picture refills in both modes. 45 s soak (7 freezes, 9 resizes, 2 hide/show, 5 MP4<->CDG loads): survived in both modes, still drawing 120 frames in the last 2 s, no new crash reports. The probe fix that mattered: signals must be emitted from the observer thread (a bare `QTimer.singleShot` from a plain thread never fires, and the first "off" run falsely said the picture kept moving).
**NOT proven:** the real app (Qt-embedded views in the audience window, rotation/spotlight views, KaraFun capture, window transitions, the background video loops together with a CDG), any Intel hardware (the Intel bridge only COMPILES; an Intel GPU/driver may behave differently with multithreaded GL), and a real Chrome-style stall. **Next step:** build, set `"karaoke_render_thread": true` on THIS Mac, run a rehearsal (a CDG with background video, singer transition, rotation screen, KaraFun song, resize/hide the audience window), then try the Chrome test; only then the Intel Mac, and only for a show after that. Do not make it the default before the Intel Mac has run a full show with it.

## Stall diagnostic added - 2026-10-10, committed, NOT in any build, version stays 1.0.1.0
Operator: "add the diagnostic too." New `system_snapshot.py` (+ both specs list it): when the main-thread watchdog sees the GUI blocked for >= 1 s it logs ONE line `[STALL] system during a >1s stall: load=.. mem_pressure=.. ram_free=.. swap=.. singws_cpu=..% cpu: <top 4 processes> | mem: <top 4>` (CPU measured with a real 0.25 s sample on the watchdog thread; setting `stall_system_snapshot`, default on). `FREEZE_DETECTED`'s bogus "SingWS CPU: 0.0%" line is replaced by the load average. Read-only (psutil + one `sysctl kern.memorystatus_vm_pressure_level`, 1 s timeout). Tests: `test_system_snapshot.py` (6); full suite 1,402 pass. Run on this Mac it named `fileproviderd`/`cloudd`/`bird` (iCloud sync) as the busiest processes, the same load source as the iCloud rule above. NOT done on purpose: moving the BASS engine re-init (an audio OUTPUT DEVICE change, `[AUDIO] output pinned` then `BASS background engine ready`, ~2.4 s on Intel) off the GUI thread: inherent to switching devices, operator-initiated, and live audio setup is not worth risking for it.

## FINDING 2026-10-10: the TV karaoke picture is rendered on the MAIN (GUI) thread; the 21:55:16 7.4 s freeze froze the TV too
Operator remembers the 2026-10-09 21:55 freeze: he opened Google Chrome during playback; "the TV video froze too" (audio and the song clock kept running: END-AUDIO position and BG-VIDEO clock advanced in real time through the freeze). Cause of the TV freeze: `native/mpv_bridge/bridge.mm` `scheduleRender` does `dispatch_async(dispatch_get_main_queue(), ... [self renderFrame])` (and `scheduleEvents` drains mpv events the same way), so every karaoke video frame is drawn on the main thread; any main-thread stall freezes the TV picture while audio (separate thread) continues. The "SingWS CPU: 0.0%" in FREEZE_DETECTED lines is an ARTIFACT (`log_freeze_detected` calls `psutil.Process().cpu_percent()` on a fresh object with no interval, which always returns 0.0): do not use it as evidence. Why the main thread stalled when Chrome launched is NOT known (no stack capture by design).
**Options proposed to the operator (not done):** (A) log-only diagnostic on stalls >1 s: memory pressure, load average, top processes by CPU/memory, and a real CPU reading; (B) the real fix: render karaoke frames on a dedicated render thread (the bridge already uses `CGLLockContext`/`flushBuffer`), behind an off-by-default setting, rehearsed by deliberately blocking the main thread and watching the TV; high risk (rendering/surface changes cannot be unit-tested, AGENTS rules 4 and 6), needs a plan and an on-screen rehearsal before any show.

## BGM playlist search + Queue Next - 2026-10-10, committed, NOT in any build/release, version stays 1.0.1.0
Operator: "a search bar for the background music section... make it easier to reorder stuff... on right click you can queue next." `BackgroundMusicManager` (`0.2.18.1.py`): `playlist_search` box above the Current Playlist (debounced 120 ms, every word must appear in the track name, count shows "N of M tracks"); right-click menu on the playlist with **Queue Next** (selected upcoming tracks go right after the current one, order kept) and **Move to End of Playlist** (my addition). The existing rule that hides played and current rows is kept: search never resurrects them, and `highlight_current_track` / drag-drop re-apply the filter. Only rows after the current track can be moved, so `player.current_index` stays valid. New: `_apply_playlist_filter`, `_selected_upcoming_rows`, `_move_playlist_rows`, `queue_selected_next`, `move_selected_to_end`, `_show_playlist_context_menu`. Tests: `test_bgm_playlist_search.py` (9, real manager UI offscreen). Panel rendered offscreen and looked at (search box matches the browser box). NOT seen with real music playing. Full suite 1,396 pass.

## Intel show 2026-10-09/10 log review (build = the second 2026-10-09 re-release, exe `8949594e...` = the released x86_64 DMG) - 2026-10-10
Operator sent `singws_last_show_2026-10-10_01-36-45.log` (the 30-minute fix picked the whole 274 min show, 21:01-01:36). **Healthy:** no errors/tracebacks; `[AUDIO-DIAG] master-dsp[rust]` 91 lines, 97,905 blocks, over_budget 0, gaps>100ms 0, mixer stalls 0, longest gap 64 ms, slowest block 1.2 ms; 46 songs, song-end handler avg 139 ms (max 357); KaraFun 3/3 FOUND on attempt 1 and all three completed by `karaFun_idle_expected_end`; 341 GUI stalls (median 146 ms, 4 over 1 s); memory 911 -> 2,714 MB (known unexplained growth).
**Settings freeze explained (likely):** `ui_settings_build` was 48 ms and 43 ms on Intel, so the dialog is fast. The ~2.4 s stall (21:02:23-25) coincides with the BGM BASS engine being rebuilt twice right after Settings was saved ("BASS background engine ready" x2), and the 2026-10-09 03:13:33-36 freeze ended exactly at the master-DSP attach. Cause: a master-audio settings change rebuilds the engine on the GUI thread (~2.4 s on Intel); only matters when changing audio settings mid-show. Code path NOT yet read to confirm; offer: move the rebuild off the GUI thread (not done, low priority).
**Unexplained:** one 7.4 s GUI freeze at 21:55:16-24 during a KaraFun song (FREEZE_DETECTED, audio kept playing, no operation logged; stack capture is off by design). Once in 4.5 h. **Not seen:** the Force BGM rescan (no analysis lines; crossfades still `verified_dead_tail` 5 s); `transport_shadow` was off.

## TODO for the operator (he asked to be reminded when he reopens, 2026-10-09)
1. Install the x86_64 DMG on the Intel venue Mac by hand (same version, no auto-update; not right before a show). 2. Force rescan of the BGM playlist there. 3. Open Settings once, `grep ui_settings` the log and send it. 4. Optional: `"transport_shadow": true` for a show, then `grep TRANSPORT-SHADOW`.

## Rust is now the DEFAULT master processor - 2026-10-09, committed AND RELEASED in the second 1.0.1.0 re-release (below)
Operator: "rust worked great last night. id say make it default." `DEFAULTS["master_dsp_engine"]` is now `"rust"` (and the getter/`tools/set_master_dsp_engine.py` fall back to "rust" when the key is unset); the Python processor remains the automatic fallback if the dylib is missing or fails to load (logs `Rust master DSP unavailable (...)`), and `"python"` in settings.json still selects it. A profile that explicitly saved `"python"` keeps Python. Full suite 1,387 pass. Shipped in the second 1.0.1.0 re-release of 2026-10-09.

## RE-RELEASED 1.0.1.0 (same version number) AGAIN - 2026-10-09, published, latest; NOT installed on this Mac or the Intel Mac
Operator: "build it...release too under the same version". App code = commit `346f227`; release commit `7fda3b4`; annotated tag `v1.0.1.0` force-moved (was `92621c9`) and force-pushed; both DMGs replaced with `--clobber`; notes replaced (media-runtime source tar untouched). arm64 `0e3fc657...` (136,740,771 B), x86_64 `5eb8ef20...` (155,439,774 B). Verified: re-downloaded `latest/download` SHA-256s equal `docs/release.json`; GitHub Pages manifest and download page serve the new hashes; asset sizes equal the local DMGs.
Both builds: hdiutil, strict signature, version 1.0.1.0, arch (Intel 708 Mach-O min <= 12.3), `libsingws_dsp_ffi.dylib` has `singws_transport_step_json`, `singws-analyze` bundled and executable (arm64 Mach-O), `rust_analysis.py`; scratch-profile launches clean (arm64 clean shutdown; Intel under Rosetta, BASS ready, I stopped it by kill after my wrong exit variable: the variable is `SINGWS_SMOKE_EXIT_MS`); packaged arm64 dylib passes `test_transport_shadow` and `test_native_master_dsp` (3e-8). Live log unmoved (424 lines). Full suite 1,387 pass; native 96 not run (`.venv-universal` missing).
**Contents beyond the 2026-10-08 re-release:** 44.1 kHz BGM envelope fix (true 100 ms windows; effective per track after a Force rescan of the BGM playlist), last-show log 30-minute cut-off, cleaner BGM card names, `analysis_engine` (libmpv default | shadow | rust), `transport_shadow` (off), `ui_settings_*` timing probes. The spec bundling of `singws-analyze` and `transport_shadow.py` is now proven by a build.
**Caveats:** auto-update will NOT offer this to anyone on 1.0.1.0 (same version): the Intel venue Mac needs the x86_64 DMG installed by hand. The Intel build has still only run under Rosetta here. Do not install it right before a show. Next release: bump to 1.0.1.1 unless told otherwise.

## Stage 3 shadow hook - 2026-10-09, committed, OFF by default, NOT in any build, version stays 1.0.1.0
Operator: "do the shadow hook". The Rust `singws-transport` machine is now reachable from Python: `singws_transport_step_json` is exported from the SAME dylib the master DSP uses (`libsingws_dsp_ffi.dylib`, so no new bundling; ABI version unchanged, new symbol only), and `transport_shadow.py` (`TransportShadow`) feeds it events. **Shadow only**: it logs and never acts; every method swallows errors; a dylib built before this (no symbol) just means "unavailable".
**Setting** `transport_shadow` (default False, read lazily, needs a restart to change). **To rehearse:** quit SingWS, set `"transport_shadow": true` in settings.json, play KaraFun songs (the only path hooked so far), then `grep TRANSPORT-SHADOW ~/SingWS/logs/*.log`. Lines: `cmd` (what the machine would do; each Ignore reason once per session), `MISMATCH` (app auto-completed a song the machine had not, or the machine completed one the app never finished within 20 s), `session N summary`.
**Hooks in `0.2.18.1.py`** (all try/except): `_start_external_karafun_playback` (start, stores `_shadow_id` in the active-session dict), the KaraFun monitor (playing / idle / end-clock observations right before `should_complete`), `_complete_near_end` (auto-complete notice), `_finish_external_karafun_playback` (finish; one with no auto notice is treated as a manual Complete). `_transport_shadow()` is the accessor.
**Not hooked yet:** native (non-KaraFun) media end (`_handle_media_end_safe`), the duration watchdog (`WatchdogExpired`), stop/skip. The plan's acceptance (0 unexplained disagreements over >= 3 shows) is far off: first get real KaraFun show data. `rust/build_dsp.sh` must be rerun before a build so the bundled dylib has the symbol. Tests: `test_transport_shadow.py` (10; uses `~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib` or `SINGWS_DSP_LIB`, skips if absent), Rust 11 transport tests incl. the JSON bridge. Both specs list `transport_shadow` (untested until the next build).

## Rust Stage 3 + Stage 4 groundwork - 2026-10-09, committed, additive only (nothing in the app imports it), version stays 1.0.1.0
Operator: "lets do 3 and 4. hold off on tauri for now." Both are new crates in the `rust/` workspace; no app code, no build, no behaviour change.
**Stage 3 `singws-transport`:** pure `step(state, event, stop_in_progress) -> (state, commands)` song-lifecycle machine encoding the show-found rules (duplicate media-end ignored during hand-off, stale-session events ignored, external/KaraFun needs two playing hints, idle before confirmation never completes, idle after confirmation completes only with fresh end-clock evidence or elapsed verified duration, watchdog only warns, manual Complete always completes, rotation advances only after cleanup). 10 tests incl. the 2026-08-31 false-completion case and a 2,000-sequence fuzz. **NOT done:** the PyO3/Python shadow hook and log-replay (need an agreed event vocabulary from the app's log lines); the plan requires 0 unexplained shadow disagreements over >= 3 shows before it may drive anything.
**Stage 4 `singws-bgm`:** device-free two-deck mixer core with sample-accurate equal-power crossfade (block-size independent, no allocation in `mix`, gap meter; 6 tests incl. < 0.5 dB level swing). **Stage 4 itself stays GATED** (plan: only if the BASS licence is a problem, BASS has unfixable issues, or a cross-platform port is scheduled; none is true today). No cpal/symphonia, no device code, BASS remains the engine. Run: `export PATH="$HOME/.cargo/bin:$PATH" CARGO_TARGET_DIR=$HOME/.cache/singws-rust-target; cd rust && cargo test --release`. Tauri on hold (operator).

## 44.1 kHz BGM envelope fixed + Settings-open timing probes - 2026-10-09, committed, NOT in any build, version stays 1.0.1.0
Operator said "do 2 and 3". **(2)** `libmpv_media_jobs._configure_ebur128_job` now inserts `aresample=48000` before `asetnsamples=n=4800`, so BGM envelope windows are true 100 ms at every sample rate (were 109 ms at 44.1 kHz, envelope ~8% short, rejected by `bgm_audio_window`'s 1 s check). Checked on 8 real 44.1 kHz MP3s: envelope length now equals true duration within 0.06 s. **Effect:** once a track is re-analysed, crossfades skip its silent head/tail like 48 kHz tracks already did (a crossfade-timing change the operator approved). Already-analysed tracks keep the old (ignored) record until a Force BGM rescan; analysis version NOT bumped (would invalidate karaoke records too). Test: `test_performance_safety.py` pins the filter string.
**(3)** Settings-open ~3.2 s freeze on Intel NOT reproduced or explained; log-only probes added in `configure_settings`: `[PERF-DIAG] ui_settings_audio_devices` (output-device lookup) and `ui_settings_build` (whole build). Open Settings once on the Intel Mac with a build containing this and grep `ui_settings`. Full suite 1,377 pass.

## Last-show export picked the wrong session - fixed in source 2026-10-09, committed, NOT in any build, version stays 1.0.1.0
The operator's "Send Last Show's Logs" file from the Intel Mac (`singws_last_show_2026-10-09_03-22-46.log`) held only the 10 min 11 s settings-tuning session at 03:12-03:22 after the show, not the 4 h show: `LAST_SHOW_MIN_SECONDS` was 10 minutes. Now 30 minutes (`0.2.18.1.py`); three tests added in `test_last_show_logs.py` (a 10-minute and a 25-minute session after a show are not the show; a real 50-minute party counts when nothing longer exists). Full suite 1,377 pass. Edge unchanged: a show split by a mid-show restart exports only the latest run >= 30 min.
What that 10-minute log showed (no errors/crashes): opening Settings froze the GUI ~3.2 s on the Intel Mac (03:13:33-03:13:36, a QDialog in the window list; dialog build cost, not investigated, only matters if Settings is opened mid-show); app_startup 5,554 ms with GUI stalls of 4.8 s and 2.5 s vs 2.3-3.1 s in an older review (one data point, compare at the next launch); 22 `BGM master processor attached` lines = slider steps; no AUDIO-DIAG (no music playing); server_sync 230-600 ms every ~15 s (normal); the saved "External Headphones" output is absent so the system default is used; no external screen attached.

## Intel venue show 2026-10-08/09: FULL-NIGHT totals (Rust master DSP + song-end timing) - 2026-10-09
Operator ran a totals command over the two show log files on the Intel Mac (`singws_2026-10-08.log.2026-10-08` + `singws_2026-10-08.log`, 21:xx-01:40; the Oct 9 file is only a 03:12-03:20 settings-tuning session): **101 `[AUDIO-DIAG] master-dsp[rust]` minutes, 134,091 blocks, over_budget 0, gaps>100ms 0, gaps>500ms 0, mixer stalls 0, longest gap 63 ms, slowest block 1,546 us, 0 tracebacks.** (Only ~56 min of BGM audio actually ran through the chain; the rest of the ~4 h was karaoke.) **Song-end handler: 48 logged, avg 165 ms, max 397 ms** vs ~356 ms on the 2026-10-04 show (1.0.0.8): roughly halved (the young-generation GC change; `ui_songend_gc_young` was 1-5 ms). The errors-only grep earlier found only a KaraFun `no_match` line at 00:18:05 (text contains "ERROR"), no real errors.
Operator reports the show "sounded really clean tonight; less bass than normal but that's fine, it was cleaner" (explanation given: the BGM master chain was on, likely compressor + limiter + exciter; Rust = Python numerically). He then retuned master audio on the Intel Mac: compressor 20% (was 30), tilt +10, exciter 15%, limiter -1 dB (the 22 `BGM master processor attached` lines at 03:13-03:14 are slider steps, not faults). Rust is therefore proven on a physical Intel Mac for a full show; the default for new installs is still `python` (flip only if he says).

## Rust analysis helper wired into the app (opt-in) - 2026-10-09, committed, NOT in any build, version stays 1.0.1.0
Operator said "do that" to the proposed next step. New setting `analysis_engine` = `"libmpv"` (DEFAULT, nothing changes) | `"shadow"` (libmpv stays authoritative; the Rust helper runs alongside on its own thread; disagreements logged as `[ANALYSIS-SHADOW] MISMATCH ...` plus a summary line every 25 files; Rust answer never used; Rust failures swallowed) | `"rust"` (Rust answers the kinds marked verified, libmpv on ANY Rust trouble: missing helper, timeout, damaged/unsupported file, no loudness). Read once at launch (`_set_analysis_engine`).
**Code:** `rust_analysis.py` (helper finder, `run_helper`, `RustAnalysisSession` with the same call surface as `IsolatedLoudnessSession`, `HybridAnalysisSession`, `shadow_run`, `compare_results`, `RUST_*_VERIFIED` flags); app hooks in `0.2.18.1.py`: `DEFAULTS["analysis_engine"]`, `_set_analysis_engine`, `_make_analysis_session` (library scan session), `_measure_loudness_with_engine` and an early branch in `_measure_loudness_lufs` (new keyword `_engine`; only for session-less full-mode calls; paced/live requests run the helper under `nice -n 10`); `rust/build_analyze.sh` builds the helper for both arches into git-ignored `native/singws_analyze/<arch>/`; both specs bundle it if present (UNTESTED until the next build; check `Contents/Frameworks/singws-analyze` is an executable and the strict signature holds); `tools/rust_analysis_parity.py` (needs `.venv/bin/python`, uses ONE libmpv session: two in one process mix messages).
**Parity evidence (arm64, real library files, libmpv vs Rust):** 150/150 identical for loudness, sample peak, duration, audio_start and audio_end (max difference 0.000 s) -> `RUST_LOUDNESS_VERIFIED` and `RUST_BOUNDARIES_VERIFIED` are True. The BGM 100 ms envelope is NOT equivalent -> `RUST_ENVELOPE_VERIFIED` False, BGM envelope analysis always stays on libmpv even in "rust" mode. Speed through this path is only ~1.4x per call (libmpv 100.3 s vs Rust 74.0 s for 150 files, one call at a time, Rust pays a process spawn per file); the real gains are parallel scaling (Intel: 2.85 files/s with just 2 jobs) and no libmpv memory growth.
**FINDING for the operator (NOT changed, decide first):** libmpv's envelope uses 4800-sample windows, which is 109 ms at 44.1 kHz (most MP3s), not 100 ms. Its 44.1 kHz envelopes cover only ~92% of the real song (e.g. 186.8 s vs 203.3 s true). `transition_analysis.bgm_audio_window` discards any analysis whose duration differs from the real one by >1 s, so for 44.1 kHz BGM tracks the "skip silent head/tail" crossfade analysis is silently ignored (safe fallback to full-file bounds); it only works for 48 kHz files. Using Rust's exact 100 ms envelope would switch that feature ON for 44.1 kHz tracks and change crossfade timing: a behaviour change that needs his say-so (AGENTS rule 5).
**Tests:** `test_rust_analysis.py` (42; fake-helper script, failure/timeout/cancel, shadow/rust/fallback, the real app dispatcher, the real binary when built); full suite 1,374 pass. A cancelled or timed-out helper request now closes its pipes (found by the tests as a ResourceWarning).
**To try it:** set `"analysis_engine": "shadow"` in settings.json with SingWS closed, run a library scan, `grep ANALYSIS ~/SingWS/logs/*.log`; expect a few MISMATCH lines at most. A build with `rust/build_analyze.sh` run first is needed for the helper to exist inside the app.

## Intel venue Mac: first real show on the re-released 1.0.1.0 with the Rust master processor - 2026-10-08/09
Operator installed the re-released 1.0.1.0 (exe `c43c86c0be76...`, confirmed in the `[LAUNCH]` line), ran the Intel analyzer test before the show, then switched `master_dsp_engine` to `rust` on the venue Mac himself (against my "try it after the show" advice) and ran the show. Verdict from him: "sounded great".
**Analyzer on the Intel Mac (500 real files, 2 jobs, 12 logical cores):** loudness 500/500 within 0.1 LU (max 0.10, mean +0.006), sample peak 442/443 within 0.1 dB (one file 0.16 dB; 57 legacy placeholder entries not scored), 175.4 s wall = 2.85 files/s, median 655 ms/file, p90 910 ms. libmpv Turbo on that Mac earlier: ~2.16 files/s with 4 helpers (different file mix), so ~2.6x per worker; acceptance met.
**Rust master DSP, same night:** `[MASTER-AUDIO] ... engine=rust (native DSP callback)` at 21:34:20 after the 21:33:28 launch (five launches 21:06-21:33 while setting up). `[AUDIO-DIAG] master-dsp[rust]` lines 21:40 through 01:38 (~4 h; the paste showed only the last 80 matching lines, so ~00:00-01:22 was not seen): over_budget=0, gaps>100ms=0, gaps>500ms=0, mixer_stalled_polls=0 in every line seen; longest gap 63 ms (twice); mean 180-250 us/block, p99 <= 0.5-1 ms, slowest block of the night 1.5 ms; `restarts=1` = BGM paused for a song and resumed (normal). Faster and steadier on Intel than on the arm64 laptop (which was busier).
**Not yet seen:** an errors-only grep (Traceback/ERROR/crash) and the `ui_songend_` and `KARAFUN-AUTO` lines from that night; asked the operator to run them. The venue Mac now has `master_dsp_engine = rust` in its settings (his choice); default for new installs is still `python`; do not flip it unless he says.
**Next options (his call, do not ask about building/releasing):** wire the Rust analyzer in as an isolated helper behind a setting (default libmpv, shadow-compare first); read the song-end freeze lines once he sends them; Stage 3/4/Tauri remain unscheduled.

## RE-RELEASED 1.0.1.0 (same version number) - 2026-10-08, published, latest; installed on this Mac (arm64)
Operator: "build and release with same version number". Shipped exactly the two rehearsed builds (not rebuilt): app code = commit `d9fceb5`. Release commit `b527523`; annotated tag `v1.0.1.0` was **force-moved** (previously `7cfb504`) and force-pushed; both DMGs replaced with `gh release upload --clobber`; notes replaced; the third asset `SingWS-1.0.1.0-media-runtime-source.tar.xz` (48,302,484 B) untouched.
arm64 `c8c0865d...` (135,603,800 B), x86_64 `9a23a8db...` (153,641,280 B); re-downloaded `latest/download` sizes and SHA-256s equal `docs/release.json`; GitHub Pages serves the new manifest and download page (verified).
**Also fixed:** `docs/index.html` had been left on "Version 1.0.0.9" with 1.0.0.9 filenames and hashes by the first 1.0.1.0 release, so its download buttons pointed at files that no longer exist on `latest`; now 1.0.1.0 links, sizes and hashes.
**Contents beyond the first 1.0.1.0:** Rust master processor for BGM (opt-in, `master_dsp_engine`, default `python`), `[AUDIO-DIAG]` counters (on by default, `audio_diag_counters`).
**Caveats:** auto-update will NOT offer this to anyone already on 1.0.1.0 (same version): they must download again (the notes say so). The venue Intel Mac still runs the first 1.0.1.0 until the operator reinstalls. The Intel build has only been launched under Rosetta on a scratch profile, never on a physical Intel Mac. Next release: bump the version as usual (1.0.1.1) unless told otherwise.

## Rust master processor rehearsal result + operator decisions - 2026-10-08
Operator rehearsed on this Mac (arm64): switched `master_dsp_engine` to `rust`, toggled master audio off/on in Settings, listened ("sounds good, maybe better", and "faster working which I'm happy with"). Log: `[MASTER-AUDIO] BGM master processor engine=rust (native DSP callback)` at 16:31:11, then `[AUDIO-DIAG] master-dsp[rust] blocks=2379 mean=471us p99<=5000us max=10044us over_budget=1 gap_max=96ms gaps>100ms=0 gaps>500ms=0 restarts=0 | bass_cpu=4.2% mixer_stalled_polls=0`. No Python baseline was taken (Python never attaches at startup), so there is no Python-vs-Rust timing from the live app, only from the benchmark (277 us vs 174 us per block).
**Reminder when interpreting "better":** Rust matches the Python reference to 3e-8, so any audible gain is the master chain being ON for BGM, not the Rust code sounding different.
**KNOWN QUIRK, operator declined to fix (2026-10-08): do NOT propose it again.** The BGM master chain does not attach at app startup, even with `master_audio_enabled` true: `BackgroundMusicPlayer._init_bass` runs before the main window exists (`self.parent()` is None, which also explains `simple_audio=1` in the launch log), so the master/EQ attach is skipped silently and only `_refresh_master_audio_runtime` (a master-audio settings change in a running session) attaches it. Evidence: 107 launches vs 11 "BGM master processor attached" lines in the Aug-Oct logs. To get the chain (Python or Rust) on BGM: Settings > master audio switch off then on after each launch. The operator is happy as is.
**Current state on this Mac:** `master_dsp_engine = rust` in the live profile (switch back with `python3 tools/set_master_dsp_engine.py python`, SingWS closed). Default for everyone else stays `python`. Not yet tried on the Intel venue Mac; do not flip the default or ship the Intel build to a show before that.

## Rehearsal build with the Rust master processor - 2026-10-08, INSTALLED on this Mac (arm64), NOT published, version stays 1.0.1.0
Built both installers at the same version 1.0.1.0 (not a release; nothing pushed to GitHub releases): `SingWS-1.0.1.0-arm64-installer.dmg` sha256 `c8c0865d...`, `SingWS-1.0.1.0-x86_64-installer.dmg` sha256 `9a23a8db...` (repo root, git-ignored). They contain everything committed through `d9fceb5`: audio counters, the Rust master processor, Intel analyzer is separate.
Verified: hdiutil, strict signature, version, `libsingws_dsp_ffi.dylib` + `rust_master_dsp.py` bundled (arm64 444 KB / x86_64 437 KB), arch (arm64 368 / x86_64 367 Mach-O), x86_64 minimum macOS 12.3 (706 files); arm64 is enforced at 14.0 by `build_singws_mac_arm64.sh` since 1.0.1.0 (operator: Apple Silicon minimum does not matter). Scratch-profile launches of both (Intel under Rosetta) clean, live log unmoved. The PACKAGED arm64 dylib passes all 11 `test_native_master_dsp` checks (3e-8 vs the Python reference, real BASS).
**Installed here:** `/Applications/SingWS.app` = the arm64 candidate (exe `c805cd46...`). Backup of the previous app + 14 profile JSON files: `local-installs/20261008-rust-dsp/` (private; contains credentials). The normal profile was NOT launched by me; `master_dsp_engine` is still `python`.
**Switch for A/B:** quit SingWS, `python3 tools/set_master_dsp_engine.py rust` (or `python`; no argument shows the value); it edits only that key (dated backup, prints nothing else). Applies at next launch.
**Log lines when it works:** `[MASTER-AUDIO] BGM master processor engine=rust (native DSP callback)` when BGM starts with master audio on (the music engine is created before the main window exists, so it does NOT appear at launch), then once a minute `[AUDIO-DIAG] master-dsp[rust] ...`. If the dylib fails: `Rust master DSP unavailable (...); using the Python processor`.
**The Intel build has not been run on a physical Intel Mac.** Do not take it to a show before this Mac's rehearsal passes; the venue Mac currently runs the released 1.0.1.0.

## Rust master processor (Stage 2) - 2026-10-08, committed, NOT built into an installer, default stays "python", version stays 1.0.1.0
Operator decided Rust is the better option regardless of data, so the BGM master chain (gate, tilt EQ, exciter, compressor, limiter, clip) now has a Rust implementation: BASS calls it directly (no Python on the audio thread).
**Files:** `rust/crates/singws-dsp` (safe, allocation-free port of `singws_master_audio.MasterAudioProcessor`), `rust/crates/singws-dsp-ffi` (C ABI + a BASS `DSPPROC`-compatible callback; config changes arrive through a try_lock side channel, so neither thread can block the other), `rust_master_dsp.py` (`RustMasterProcessor`, same API as the Python processor; refuses to load if the ABI or parameter order differs), `rust/build_dsp.sh` (builds arm64 + x86_64 at macOS 12.3 into git-ignored `native/singws_dsp/<arch>/`), spec entries in both `SingWS-*.spec` (bundle the dylib if present; NOT yet exercised by a build).
**Engine:** `BassBackgroundEngine._attach_master_dsp` registers the Rust callback when the processor has `native_dsp`; otherwise the Python callback exactly as before. The `[AUDIO-DIAG]` line now reads `master-dsp[rust]` or `[python]` (native counters come from Rust). `close()` now removes the master DSP before teardown.
**Setting:** `master_dsp_engine` = `"python"` (default, the reference) or `"rust"`; applies at next launch; falls back to Python (with a log line) if the dylib is missing. **To rehearse:** set `"master_dsp_engine": "rust"` in `~/SingWS/settings.json` with SingWS closed, launch, play BGM with master audio on; expect `[MASTER-AUDIO] BGM master processor engine=rust` and `[AUDIO-DIAG] master-dsp[rust]`. Listen A/B against python before making rust the default.
**Evidence:** Rust matches the Python reference to 3e-8 over 2 sample rates x 4 signals x 8 parameter sets; BASS itself (decode mixer, no sound device) calling the Rust callback matches the reference; block-size independent; zero allocations while processing/reconfiguring (per-thread counting allocator); finite and ceiling-bounded on extreme input; Rust tests pass as arm64 and as x86_64 under Rosetta. Full Python suite 1,332 pass.
**Honest numbers:** per 25 ms block Python 277 us mean (max 603) vs Rust 174 us (max 247): only ~1.6x faster and both ~1% of real time. The benefit is removing the GIL/Python allocation from the audio thread, not speed.
**Not done:** a build with the dylib bundled (spec change untested until the next build; check `Contents/Frameworks/libsingws_dsp_ffi.dylib`, strict signature, arch), rehearsal on this Mac, rehearsal on the Intel venue Mac, then flip the default. `native/singws_dsp/` is git-ignored: run `./rust/build_dsp.sh` before building installers. Not covered: karaoke (mpv) master chain, which is approximated with lavfi filters inside mpv.

## Audio counters (Rust plan Stage 0) - 2026-10-08, committed, NOT built/installed, version stays 1.0.1.0
Operator said yes. New `[AUDIO-DIAG]` log line once a minute (only when background music is playing through BASS with the master processor attached): blocks, mean/p99/max time of the Python master-processor callback
that runs on BASS's audio thread (`bass_background_engine._dsp_proc`), blocks over half their own duration, gaps between callbacks (>100 ms, >500 ms, max), BASS CPU, polls where the mixer reported "stalled".
Code: `AudioCallbackStats`, `format_audio_diag`, `BassBackgroundEngine.set_audio_diagnostics / audio_diagnostics_snapshot`, host wiring `BackgroundMusicPlayer._start_audio_diag_counters / _log_audio_diag`.
**Default ON** (setting `audio_diag_counters`, set False to switch off) - changed from the "off by default" I first told the operator, because the cost is two clock reads and a few additions per block (measured 0.52 us per block; a block is ~25,000 us) and otherwise he would have to edit settings.json. Tell him so.
Facts found while doing it: the BGM **EQ uses BASS native effects** (the Python EQ callback only attaches with env `SINGWS_ALLOW_PYTHON_BGM_EQ_DSP=1`); only the **full master processor** is Python on the audio thread. The dev profile has `master_audio_enabled = True`, so it is live when BGM plays. Karaoke (mpv) is not covered: no bridge change was made.
How to read it after a show: `grep AUDIO-DIAG ~/SingWS/logs/*.log`. Healthy: over_budget=0, p99 well under 5000us, gaps>500ms=0, mixer_stalled_polls=0. Anything else is the evidence for or against Rust Stage 2.
Tests: `test_audio_diag_counters.py` (12, drives the real callback through a stub BASS); full suite 1,321 pass (native 96 not run: `.venv-universal` is missing on this Mac). The old `test_bgm_master.py` bare-engine tests needed the engine to create its counter state lazily (`_diag_state`).
Test-env note: the Qt plugin folder `/tmp/singws-release-qt-platforms` was cleared again; rebuild it with `cp -R .venv-test-arm64-fresh/lib/python3.13/site-packages/PyQt6/Qt6/plugins/platforms /tmp/singws-release-qt-platforms` then `codesign --force --sign - /tmp/singws-release-qt-platforms/*.dylib` (venv is python3.13, not 3.14).
Key check 2026-10-08 (operator asked that the Resend key never appear anywhere): no file, no git history of either repo, no log and no Claude data folder contained it; the only copy was in this session's own transcript and was overwritten in place with filler (0 left under `~/.claude`).

## Rust migration planning + Stage 1 prototype - 2026-10-08 (documents committed `5b93269`; prototype files UNCOMMITTED, to finish 2026-10-09)
Operator asked for a Rust architecture audit/plan: `RUST_ARCHITECTURE_AUDIT.md`, `RUST_MIGRATION_PLAN.md`, `RUST_PROTOTYPE_DESIGN.md` (repo root, committed and pushed). Answers given: install Rust yes (done: rustc/cargo 1.99.0 in
`~/.rustup`, `~/.cargo`, targets aarch64+x86_64 apple-darwin, `--no-modify-path`, so `cargo` is NOT on PATH in new shells: use `export PATH="$HOME/.cargo/bin:$PATH"`); Intel stays at macOS 12.3, Apple Silicon minimum does not matter; the operator wants the
highest-end sounding key/tempo (Signalsmith was his guess; shipped code uses mpv rubberband R3: proposed a blind A/B render of both before any switch, NOT done); keep BASS; he DOES use the BGM EQ/master processors in shows, so Python-on-BASS-audio-thread (Stage 2) is a live risk;
documents committed. **The app version stays 1.0.1.0 (operator: keep same version); no app code changed.**
**Prototype (committed `a457340`, additive only, nothing in the app imports it):** `rust/` workspace + `tools/rust_analysis_compare.py`. Build outside iCloud: `export PATH="$HOME/.cargo/bin:$PATH" CARGO_TARGET_DIR=$HOME/.cache/singws-rust-target; cd rust && cargo test --release`.
18 tests pass. Read-only accuracy+speed report built into the binary: `singws-analyze --compare ~/SingWS/loudness.json --jobs 4 [--limit N]`.
Result on this Mac (arm64, 258 real files): loudness 258/258 within 0.1 LU, sample peak 251/251 within 0.1 dB; 8.3 files/s on 4 threads, ~20 MB RSS; libmpv baseline 568 ms/track sequential vs Rust median 475 ms/file (2x speed target NOT yet shown; needs the Intel venue Mac).
The 7 earlier "peak" mismatches were legacy cache entries (unrounded loudness + placeholder peak 0.0) from an older writer, NOT an engine difference; do NOT clamp peak (libmpv stores positive peaks to +1.6 dBFS). The compare mode skips those entries and says so.
**Intel build for tonight:** `~/Downloads/singws-analyze-intel/` (x86_64, min macOS 12.3 verified, signed with the local identity, sha256 `e1454ccb...`; correct under Rosetta here: 60/60 loudness, 55/55 peak). The operator runs it on the venue Mac BEFORE the show (README.txt in that folder) and sends back the 5 lines it prints.
**Still to do:** read the Intel numbers; decide PyO3 module vs command-line helper (recommendation: command-line helper, same pattern as the existing isolated analysis helper); Stage 0 counters are done; blind A/B of rubberband vs Signalsmith not done.

## RELEASED 1.0.0.9 - 2026-10-05 (published, latest; NOT installed on this Mac; operator to install on the venue Intel Mac)
Tag `v1.0.0.9`, release commit `d99bb48` (version bump `6693707`); https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.9.
arm64 `83a8a0cc...` (127,067,050 B) and x86_64 `db3f4ccc...` (152,864,638 B); re-downloaded `latest/download` sizes and SHA-256s equal `docs/release.json`; download page updated.
GitHub Pages: the builds for today's pushes errored/stalled (GitHub Actions was degraded); a re-requested build (`POST repos/.../pages/builds`) finished and the Pages manifest now reads 1.0.0.9.
Checks (both): hdiutil verify, strict signature, arch (arm64 420 / x86_64 432 Mach-O), macOS min <= 12.3 (816 / 840), launch on scratch SINGWS_HOME as 1.0.0.9 with clean shutdown (Intel under Rosetta), live log unmoved.
Tests: 1,309 pass today; the 96 native tests were NOT run (`.venv-universal` is missing on this Mac). Frozen bytecode was not compared with the source this time (launch proves the new modules import).
**Intel build note:** the shared `native/mpv_bridge/libsingws_mpv_bridge.dylib` is arm64 after the arm64 build, and the old `-intel.dylib` / `Frameworks-intel-release` bridge predated the Sept 20 `bridge.mm` change. The Intel bridge was rebuilt from the current `bridge.mm`
(clang++ -arch x86_64 -mmacosx-version-min=12.0, output in the session scratchpad) and the build run as
`SINGWS_BUILD_PYTHON=.venv-build-intel-rosetta/bin/python SINGWS_MPV_FRAMEWORKS=native_dual_view/Frameworks-intel-release SINGWS_MPV_BRIDGE=<rebuilt bridge> ./build_singws_mac_intel.sh`. Earlier Intel releases may have shipped the older bridge;
`bridge.mm` has not changed since Sept 20, so behaviour should be the same, but watch Intel playback on the first run.
Contents: developer bug-report email path (last show's log, via the server), cleaner BGM artist/title (`track_display.py`), KaraFun skip-ahead end timing + Stop, no false key/tempo note. Server (location help, notification expiry) already live.
Normal auto-update path (new version number). To check on the Intel Mac: send a bug report from Settings > Advanced; BGM card names; a KaraFun song with a skip ahead; then read the log for `ui_songend_` lines.
Rejected ideas, do not suggest again: reusable deploy tool, YouTube Music BGM. **Do NOT remind the operator to rotate the Resend key or revoke the old SMTP password: he told us to stop (2026-10-08); only make sure the key is never printed.**

## Singer page location help - 2026-10-05 (server commit `84ed134`, pushed, NOT deployed)
When a phone cannot get a location, the singer page opens a bottom sheet with steps for that device (Android Chrome, iPhone Safari/Chrome) and a Try again button
(`showLocationHelp` in `index.php`, also `window.SingWSLocation.help`; used by the request form, Singer History add, and the first-run setup). `singer_session_ui.js?v=16`.
Seen at phone width in the browser pane only; real Chrome/iPhone permission behaviour untested. Deploy = `index.php` + `singer_session_ui.js` (no script prepared).
Open: if the complaint is location TIMING OUT with permission on, that is a different fix - ask which phones/message.

## RELEASED 1.0.0.8 — 2026-10-04 (published, latest; installed on this Mac (arm64); the operator plans to install the Intel DMG on the venue Mac on 2026-10-05 and test it there - NOT yet done)
Tag `v1.0.0.8` at release commit `0acb828` (code `40309d9`, bytecode of both installers identical to the source); https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.8.
arm64 `7bd9870c...` (127,061,222 B) and x86_64 `8a0a0662...` (152,898,047 B); re-downloaded `latest/download` sizes and SHA-256s equal `docs/release.json`; Pages manifest and download page verified.
Published as a normal release on the operator's instruction (he wanted to download it at work to test on the Intel Mac the next day; a pre-release was created first and then promoted).
Normal auto-update path (new version number). Next release: bump to 1.0.0.9, or ask what the operator wants.
What is in it (over the re-released 1.0.0.7):
- **Host chat history vanishing (reported after the 2026-10-03 show, Host chat tab):** sending a host chat message clears `_chat_messages` and re-reads from id 0; a poll already
  in flight asked for "newer than the old last id" and, landing after the reset, became the whole history (only the newest line) until the next send. Fix: the send bumps
  `_chat_data_generation` so the stale poll is discarded (`test_host_chat_send_race.py`, reproduced then fixed).
- **Song-end timing probes (log only):** `@_perf_timed("ui_songend_...")` on ten song-end steps (stop_playback, handler, cleanup, idle background, outro, QR refreshes, video surfaces,
  now-singing, bg button). Slow steps (>16 ms) log `[PERF-DIAG] ui_songend_... took Nms`. Purpose: the ~450 ms hitch at most song ends on the Intel Mac (67 of 102 song ends in the
  2026-10-03 show) has no known cause and stack capture must stay off. Read the next Intel show log for these lines. Ruled out already: logging cost (async queue, 72 lines/min),
  `gc.collect()` at song end (about 2 ms with 134k rows), idle background image decode (images are ~1200x675, 12 ms here), `server_sync` (6% overlap with stalls vs 3% chance).
- **Show-screen preview, singers vs DAW (operator: DAW must stay frequent and clean, singers may be lighter):** server `api/show-screen/snapshot/index.php` records
  `daw_viewer_seen_at` (DAW browser session) and `singer_viewer_seen_at` (singer token) next to `viewer_seen_at` (anyone; unchanged meaning, so old apps behave as before). App: when only
  singers are watching it grabs a frame every 3 s (`_daw_snapshot_singers_only`) at 320x180 JPEG quality 22; a DAW page, or a server that reports no kinds, keeps 0.25 s/1 s timer, 426x240 q28.
  **Server deployed by the operator 2026-10-04** (script `server-deployments/preview-viewer-kinds/deploy.sh`, git-ignored; verified live: the endpoint now returns both new fields; rollback
  path is printed by the script under `/root/singws-chat-deploy/preview-viewer-kinds-<time>/`). Tests: `test_daw_preview_audience.py`, server `tools/test_singer_screen_preview.php` (+3 asserts).
  Why it matters: ~30 preview captures cost ~250 ms each on the Intel Mac's main thread (about 7% of the 335 freezes); the preview was watched ~51 of 215 show minutes.
- **Show-log review of the 2026-10-03 Intel show (21:15-01:32, build = the re-released 1.0.0.7, exe hash matched the DMG):** no tracebacks/errors, no network drops, 3/3 KaraFun searches
  FOUND on attempt 1 (8, 8, 10 s query-to-playing on Intel), fast picture start worked on Intel, 335 GUI freezes over 120 ms (median 194 ms, p90 400 ms, ~78/hour; 3 over 1 s, all near
  startup/idle-overlay change). Quits at the end of nights log `closeEvent` then `network transports stopped` and no `clean shutdown` line (3 of 4 quits in the 3 logs); the operator
  confirmed those were him closing the app and there was no crash, so it is a logging gap, not a fault. Background music coming in late was a settings value (`karaoke_trim_verified_tail`), see below.
**Operator-confirmed 2026-10-05 after the 2026-10-04 show (Intel, build 1.0.0.8, exe `8713fbbd...` = the released DMG): the DAW page and the singers/DAW preview split are good.** Log evidence agrees: singer-only mode ran
(3 s timer, 320x178 frames ~2.9 KB) and DAW mode ran (1 s timer, 426x215 ~15.7 KB); only 1 slow preview capture (18 ms) in 4.1 h, but the preview was watched only ~2 min, so it was barely exercised.
**Show-log findings 2026-10-04 (46 songs, 21:34-01:40):** no errors; 326 GUI freezes (79/h, median 173 ms, total ~80 s): song end 49 x ~439 ms, song start 134 x ~174 ms, other 143 x ~144 ms; the `ui_songend_` probes
show the song-end handler = ~356 ms (cleanup 148, stop_playback 98 inside it, `idle_background` 44, video_surfaces 28, qr_show 22, outro 23), so ~200 ms before cleanup is untimed and by code reading is the full `gc.collect()` on the first
lines of `_handle_media_end_safe` (real-app heap measured here: 7-18 ms; Intel is several times slower and the heap grows) - NOT yet confirmed on Intel. Proposed for 1.0.0.9: young-generation collect (or none) + a gc probe, song-start probes, split cleanup.
Memory on the Intel Mac grows ~350-470 MB/h and does not plateau (same on 10-02, 10-03, 10-04: 749 -> 3,226 MB over the night); cause unknown (native?); last night's BG loops were `VJ Loops/Halloween VJ` (15 mp4, 25% opacity, software decode). Host chat bug: no evidence either way (app logs nothing about chat).
**To check on the Intel Mac (operator, 2026-10-05):** DAW page looks as clean/frequent as before; a singer's phone preview looks acceptable but lighter; send a host chat message while a singer message
arrives and confirm the conversation does not collapse; KaraFun + CDG + MP4 songs; then read the log for `ui_songend_` lines. Intel still has never been run on a physical Intel Mac
with a build from this machine other than through the operator's venue Mac (he runs the released builds there).

## RE-RELEASED 1.0.0.7 (same version number) — 2026-10-03 evening, published, latest; installed on this Mac (arm64)
Tag `v1.0.0.7` force-moved from `2e75508` to release commit `0eecc37`; both DMGs replaced with `gh release upload --clobber`, notes replaced.
arm64 `cf1d09ce...` (127,059,391 B, the exact tested build 4, exe `949b43cf...`) and x86_64 `a752a978...` (152,910,214 B); re-downloaded sizes and
SHA-256s equal `docs/release.json`; Pages (manifest + download page) and `latest/download` verified. Checks: hdiutil, strict signature, arch, version 1.0.0.7,
macOS min 12.3, markers (fixed `delay 3` present, `lowCount` absent, no_match diagnosis, fast picture start, host chat media, shutdown breadcrumbs),
Rosetta smoke launch of the Intel app on scratch data (BASS ready, clean shutdown, live log unmoved; the first attempt did not exit within 40 s right after
the build, a second exited on its own in 9 s: unexplained, probably load). Contents beyond the first 1.0.0.7: the KaraFun search fixed-wait restore
(the first 1.0.0.7 could fail to start a song when KaraFun began on its Discover page), no-match diagnosis log line, duration guard.
**Caveats:** auto-update will NOT offer this to anyone already on 1.0.0.7, so they must download again (the notes say so). Intel has still never run on a
physical Intel Mac; the Intel-only stalls are unexplained. Next release: bump the version as usual (1.0.0.8).

## Rehearsal of the post-1.0.0.7 test build — 2026-10-03 (installed on this Mac: test build 4, label 1.0.0.7, NOT published, 6 local commits pushed)
Installed exe starts `949b43cf...` (arm64). Operator-confirmed on real hardware: KaraFun auto-start of two songs (Avenged Sevenfold / A Little Piece of
Heaven; Bruno Mars / Finesse remix), the fast picture start (`starting at once from the last pane region` then `last pane region confirmed`), seek,
live key/tempo, song end + rotation advance + background music return, an MP4 song, CDG songs with background video, history, waitlist, host/phone chat on a
real phone, a 30 s Wi-Fi cut (relay reconnected in ~5 s, 72 requests reconciled, no duplicates, playback unaffected). No tracebacks or crash reports.
- **KaraFun search wait (the "still isn't starting" bug):** the poll-for-rows version of `_karafun_search_script` (commit 3aa6010) broke every search that
  started from KaraFun's Discover page (window renamed while polling; 16 s, no match, measured twice against twice from a fresh KaraFun). The fixed
  `delay 3` is back (commit 78efa4e, with a warning comment). Kept: cheaper row scans, a result needs a visible length (38b2b98), and a read-only
  `no_match diagnosis` log line (cc1ec5b). The "KaraFun search ~3 s faster" claim below is SUPERSEDED: search is ~5 s again. Do not reintroduce polling without
  testing from a freshly launched KaraFun. The wake-window attempt (b796fef) was reverted (503166f). Still open: ~5 s of "UI not ready" retries on the
  first search after KaraFun is launched hidden.
- **Background music coming in late was a SETTING, not code:** the operator's `~/SingWS/settings.json` had `karaoke_trim_verified_tail: false` (checkbox
  "Skip verified silence at the end of karaoke songs"), which skips the audio-end scan entirely, so the music fell back to the fixed 3 s before the file
  end. After he switched it on, `[END-AUDIO] holding; audio continues to ...` appears and he said the music came in "right when I would have".
  The shipped default is on.
- **Background video loops:** `~/Music/Karaoke/VJ Loops` are Photo-JPEG .mov at 70 Mbit/s (1.3 GB), software-decoded (`hwdec=no` for the background
  player, pinned in test_bg_video_lyrics.py). H.264 copies made in `~/Music/Karaoke/VJ Loops H264 test` (182 MB, same frames, looks identical). On Apple
  Silicon: libavcodec work in a 6 s sample fell ~80% and total busy work ~43%, but overall CPU stayed ~70%; operator felt it ran better. Expected to help Intel
  more (software decode); NOT measured on Intel. Hardware decode for the H.264 loops was proposed, not done.
- **Load facts (dev Mac):** ~190 idle ffmpeg worker threads appear at every song start and vanish at the end (harmless); idle memory steps 594 -> 868 MB
  (first MP4/first song) -> ~1,030 -> ~1,065 MB then plateaus (IOSurface pools 424 MB, constant); no per-song leak seen on CDG. Whether the first step was
  "first song" or "MP4" was not separated (no second MP4 available); the operator chose to drop it.
- **Not done:** Intel never run; Intel-only 120-400 ms stalls unexplained; the commits are on `main` but NOTHING is released. Release 1.0.0.8 only when the operator says.

## Between-singer animations clipped in the host preview — fixed in source 2026-10-03, committed locally, NOT built/installed
Operator report: the applause / singer-start animations looked zoomed in and cut off. Seen in his screenshot: only in the small Karaoke Preview
panel of the main window (about 430x200); the real audience window was fine. Cause: `QML_SHOW_SCREEN_VFX_SOURCE` Text items had fixed
`minimumPixelSize` floors (42/36/30/18/16) that cannot shrink into a small panel, so long names ("Christopher Montgomery") overflowed; three more
lines (GET READY banner, Next Up artist and on-deck) had no fit at all. Now every floor is `Math.max(8, Math.min(N, root.height * 0.07))`
(identical on a TV: 0.07 x 1080 > 42) and those three lines shrink to fit. Checked by rendering the real QML offscreen before/after at 430x200
and 1920x1080 with long names (outro, singer start, next up). The QML itself was never wrong at normal sizes and had not changed in weeks.
Tests: `SmallPanelTextFitTests` in test_show_screen_vfx.py.

## Stall capture on the dev Mac — 2026-10-03, STOPPED at the operator's request (no further runs without asking)
Scratch app copy (SINGWS_HOME=/tmp/kf/cap_home, no server) playing a CDG song, native `sample` of the main thread. First run: ~38% busy, widget
repaints ~23%, Qt Quick sync ~10%. The song end caused no stall on Apple Silicon, so the Intel 400 ms stall cannot be reproduced here, and
Python-level stall stacks mostly end at app.exec (time is inside Qt painting), so only native sampling helps. The effect-toggle comparison
(rotation/ticker/show-screen/Quick surfaces) was inconclusive: results split into two modes (~0% vs ~18% widget repaint) caused by whether the
test window was covered (macOS stops repainting covered windows), not by the setting; a controlled re-run was started and cancelled. These runs
take over the operator's screen, so do the rest in the audit, on the Intel Mac, only when he agrees. Nothing here changed app behaviour.

## KaraFun picture ~2 s sooner — 2026-10-03, committed locally, NOT built/installed, early-start path NOT yet seen on a real song
After the double-click the first frame came ~3-4 s later, mostly because capture waited for the pane-finding probe (~1.8 s, measured live) and
only then started ScreenCaptureKit. Now `_start_karafun_preview_capture` remembers the last pane region (6 h) and, for the next song, starts the
capture at once from it while the same probe checks it; if the pane moved it is re-aimed (`[KARAFUN-CAPTURE] the pane had moved since the last
song; re-aimed`), else `last pane region confirmed`. The first song of a session still waits for the probe. Warm-up frames of an idle player
are held back until the entry status is "playing" (never longer than 6 s: `_karafun_frame_gate_until`), so the TV does not flash KaraFun's
black player. Also: the probe no longer reads element names (1.8 s -> 1.45 s; the pane logic never used them). Exposure: if the operator
moved/resized KaraFun's window or dragged the splitter since the last song, the TV shows the wrong crop for the ~1.5 s until the probe re-aims.
Tests: test_karafun_fast_capture.py (7). To check on a real song: log lines above, and the picture should appear ~2 s earlier than before.

## KaraFun search ~3 s faster — 2026-10-03 — SUPERSEDED: the polling was reverted (see the rehearsal section above); search is a fixed 3 s wait again
`_karafun_search_script` (0.2.18.1.py) took ~6 s per search: 0.45 s to find the search field, 0.2 s typing, a FIXED `delay 3`, then ~2.3 s
scanning rows. Measured live: the results window (the main window retitled "Results for ...") holds ~71 elements until the rows arrive
(~0.6 s after Enter), then jumps to 116-176 and stays. Now: poll `count of entire contents of mainWindow` (0.32 s per poll), carry on once it
changed from the first poll and held for two polls, cap `repeat 9 times` (~3 s, the old wait). The row scans read the slow position/size only
after the cheap name / duration-text checks (same conditions, fewer Accessibility calls). A/B against the old script on 6 queries (incl. a
missing song and an apostrophe mismatch): identical answers every time, 6.0 s -> 2.9-3.3 s for normal searches (nonexistent song 9.8 -> 5.6 s).
Remaining risk: if rows ever arrive in several chunks more than ~0.3 s apart the poll could settle early; the existing retry ladder then
applies. Watch `[KARAFUN-AUTO] search attempt=1 result=` in the next show log (expect FOUND at attempt 1 and ~3 s after the query).
Tests: `SearchTimingTests` in test_karafun_search_fix.py. The harness used is /tmp/kf/search_timing.py (not in the repo).

## RELEASED 1.0.0.7 — 2026-10-03 (published, latest; NOT yet installed on this Mac)
Tag `v1.0.0.7`, release commit `2e75508`; https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.7. arm64 `20e41877...` (127,052,832 B), x86_64
`0b403a30...` (152,908,214 B); re-downloaded sizes and SHA-256s equal `docs/release.json`; Pages and both `latest/download` links verified.
Checks: hdiutil, strict signature, arch, version 1.0.0.7, macOS min 12.3, new code present and removed code absent in the frozen bytecode, smoke
launch of both (Intel under Rosetta, scratch profile, clean shutdown, live log untouched). Tests: 1,233 + 92 app, 19 server suites. Contents: faster
KaraFun search (~3 s) and picture (~2 s), Host chat pictures/GIFs + phone chat layout (server already deployed), animation text fits the small preview,
Reset-to-defaults fix, ~1,900 lines of dead code removed. Normal auto-update path (new version number). Cleanup moved ~4 GB of old builds/venvs to the Trash.
First thing to do: install, play one KaraFun song and one CDG song, check the new log lines (see the audit report).

## Pre-show audit — 2026-10-03: GO WITH CAUTION (report: docs/verification/2026-10-03-preshow-audit.md)
Done headless; nothing built/installed. Fixed: Reset-to-defaults NameError, animation text clipping in the small preview, plus dead-code removal
(86 definitions / 1,901 lines, 3 unused modules, 4 unused settings, folder clutter moved to ~/.Trash/SingWS-audit-cleanup-20261003). All app (1,232 + 92)
and server (19 suites) tests pass; new headless stability check. NEXT: build both arches, install, and rehearse one KaraFun + one CDG song
(watch for the new `starting at once from the last pane region` / `search attempt=1 result=FOUND` log lines) before the next show.

## Server state vs GitHub — 2026-10-03
Host chat pictures/GIFs + phone chat layout: DEPLOYED by the operator (all seven files hash-equal to server commit `8f6fff6`; singer page serves
`room_chat_ui.js?v=2`; chat endpoints 401 without a key; config.inc 403). Server repo `8f6fff6` and app repo are pushed to GitHub.
Full tracked-file comparison (178 files vs live): everything equal except (1) the three deploy scripts (not meant to be on the server),
(2) `tenants/_template/okjweb.db` and `settings.json` (runtime template data, left alone), and (3) **`global.inc`**: the live copy is the version before
`06ed70d` (July 19, request identity/order sync); that commit was deployed except this one file. The repo version adds `PRAGMA busy_timeout=5000`
and skips a write when the state row exists (server PHP 8.3 already throws on PDO errors by default). Low risk, fixes "database is locked" blank
responses when phones submit at the same moment. One-file deploy prepared, NOT yet run: `server-deployments/global-inc-sync/deploy.sh`
(checks live == the old version first, backs up, php -l, hashes, undo script).

## Host chat pictures/GIFs + phone chat layout — 2026-10-03, committed locally, NOT built, NOT deployed, NOT pushed
Operator asked for (1) pictures and GIFs in the app's Host chat window (they existed only in Everyone/Private), (2) a better phone layout
for the chat sheet (close X too small, sheet sitting under the address bar).
**Server** (`SingWS-Server`, one local commit, deploy NOT done): Host chat text still lives in `singer_notifications`; a picture/GIF is a
`chat_messages` row on new channel `host` (thread_key = singer's norm name), so retention, Clear Night and picture authorisation reuse the room-chat
rules. New in `_chat.inc`: `chat_hostchat_send_media`, `chat_hostchat_list_for_singer`. `host_chat_moderation.php say` accepts `channel=host`
(+ `a`, `singer_name`, `media_id`/`gif_id`); `singer_chat.php` `send` accepts `media_id`/`gif_id`, `list` returns `media_messages` with its own
cursor `since_mid`; `chat_media.php` allows uploads when host chat is on; `host_chat.php clear_history` also clears them. Singers are muted /
rate limited as in the rooms; the host never is. The phone Host chat tab only exists when Everyone or Private is on (old text-only overlay
otherwise). `room_chat_ui.js` cache-buster bumped to `?v=2` in `index.php` (phones would otherwise keep the old file).
Phone layout (`room_chat_ui.js`): safe-area padding (page uses viewport-fit=cover, so the header/X sat under the notch/status bar), 44 px round
close button, 44 px tabs/buttons, sheet follows `visualViewport` (address bar, keyboard), page scroll locked while open, Escape closes.
Seen in the browser at 390x760 and 320x568 with a stubbed server (real keyboard/notch behaviour needs a real phone).
**App** (`0.2.18.1.py`): Host chat tab has GIF + Photo buttons (reuse `_room_say_gif/_room_say_photo` with `deliver=`), transcript is a
clickable QTextBrowser merging text and pictures by time (`_render_host_chat_transcript`), `_room_attachment_html` is shared with the room view,
unread counts include singers' pictures, host-channel rows never count as Everyone messages. Tests: 1220 pass (`test_host_chat_media.py` new,
`test_room_chat_tab.py` +1); PHP: 6 suites pass incl. new `test_host_chat_media.php` and an endpoint section in `test_room_chat_http.php`.
**Deploy order:** server first (the app and phone need the new endpoints; old app + new server is fine), then app build.

## Log review 2026-10-03 of the 2026-10-02 show (1.0.0.5 -> 1.0.0.6, Intel venue Mac) — source fix committed, NOT built/installed
No errors or Python tracebacks; all 4 KaraFun songs started (13 s from queue to playing; the AppleScript search is ~7 s of that),
capture 5-7 ms/frame at 24-28 fps. One real fault: **21:26:59 closeEvent ran, no "clean shutdown", app relaunched 2 min later** (a
network blip: server_sync 1.9 s just before). `network_lifecycle.wait_for_idle()` and `QThread.wait()` in `_shutdown_network_transports` have no limit, but the operator does not
remember any hang and this is the only occurrence, so NO behaviour change was made: only `[SHUTDOWN]` breadcrumbs in `closeEvent` and
before the two waits (a forced-exit watchdog was added then removed). If it recurs, the last `[SHUTDOWN]` line shows where it stops.
Not changed (no evidence of harm): ~1 GUI stall/min of 120-400 ms in every state (stack capture is off), ~2 per song change (median
235 ms), 400 ms stall after each song end when "rotation decorative effects resumed" (consistent ~410 ms; the rotation_effects probes
never fired, so the cost is elsewhere), 2-3 s stall at every launch (app_startup 2.3-3.1 s), key/tempo readback returns None every
minute (no stall correlation), host-state POST ~400 ms every ~15 s (new TLS connection per call; TESTED 2026-10-03 against wskar.com: a reused
connection takes 65 ms vs 258 ms fresh when calls are <=3 s apart, but Apache closes idle connections, so at the real 8-60 s spacing
reuse gives no gain (255 ms either way). Not worth doing; only back-to-back bursts would benefit), network_sync_check 3 s (five sequential checks, operator-triggered).

## FINAL 1.0.0.6 (re-released twice, 2026-10-02; installed on this Mac; operator confirmed "works perfectly")
Release commit `c6525f6`, tag `v1.0.0.6` force-moved to it. arm64 `b3d3a277...` (127,080,884 B), x86_64 `3f4a70fb...` (152,934,969 B);
re-downloaded hashes equal `docs/release.json`; Pages and `latest/download` verified. Installed here: the arm64 build (backup of the previous
app + profile in `local-installs/20261002-rebuild/`, private). Beyond the section below: single "Use KaraFun integration" switch +
"Launch KaraFun when SingWS starts"; preview-pane capture is the only method (no video window, no parking, no fallback); picture cropped
to 16:9 x `KARAFUN_FILL_ZOOM_OUT` (1.08) so the corner logo fits; Now Singing card shows KaraFun artist/title. Operator confirmed the
logo and card on a real song. Anyone who installed an earlier 1.0.0.6 must download again (same version number). The parked-window and
fallback descriptions below are SUPERSEDED. Intel still never run on a physical Intel Mac.

## RELEASED 1.0.0.6 — 2026-10-02 (published, latest; NOT yet installed on this Mac)

Tag `v1.0.0.6`, code commit `605c130`, release commit `98bd873`; https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.6.
arm64 `96d8521b...` (127,082,643 B), x86_64 `82b1f88d...` (152,949,249 B); sizes and re-downloaded SHA-256s equal `docs/release.json`,
live on Pages with the download page and both `latest/download` links. Checks: hdiutil, strict signature, arch (420 / 433 Mach-O),
macOS min <= 12.3 (816 / 842), markers for every feature below, search-abort present in the frozen `song_index`, smoke launch of both
(Intel under Rosetta; scratch `SINGWS_HOME`, live log unmoved). Tests: 1224 + 92 (environment group) pass.
Contents (all from the 2026-10-01 show-log review and the operator's reports):
- KaraFun cold start: `_karafun_wait_until_ready` (KaraFun running + main window + 2 s settle, 60 s limit) before any search; the
  "is it playing" check waits 30 s when KaraFun was just launched (12 s warm). The cold run took ~16 s to start playing and was
  declared failed at 12 s.
- Search result bug: a "length" only had to contain a colon and be under 9 chars, so the label "Display:" became the click target
  (22:03 song took 63 s). Now `isDurationText` (digits and colons only); titles match without trailing "(live)"/"[Remix]".
- KaraFun video window ("Dual Renderer", window level above normal windows) used to open maximised over the host screen. Now parked so
  a 3 px strip stays on one screen edge (`karafun_park_position`, host screen preferred, never touches a second screen); re-checked every
  2 s; if no picture for 10 s it is moved back and parking stops for that song. Setting `karafun_park_video_window` (default on).
- Optional preview-pane capture (`karafun_capture_source` = `preview_pane`, default `video_window`, Settings > KaraFun, off by default):
  captures the preview built into KaraFun's MAIN window via the new native region mode (`singws_karafun_capture_start_region` /
  `set_region`; Retina pane ~1566 px wide, output capped at 1600). Pane found from the accessibility tree (`karafun_preview_pane_rect`:
  right of the tall splitter, below the toolbar, above the overlay controls, minus 14 pt). Shown fitted with bars (the pane is ~2.7:1).
  Automatic per-song fallback to the video window if the pane is not found, the stream will not start, or no picture for 10 s while
  playing. **NOT yet tried on a real song.** Unknown: whether KaraFun keeps drawing when SingWS fully covers its window.
- Network settings: Detect Now runs in a background thread; OK no longer waits for location detection (it ran 25 s on the GUI thread
  each time, replayed clicks made it minutes). Failure text now points at Wi-Fi. Log lines `[NETWORK-DIALOG] ...` time both.
- Search: a cancelled library search stops its SQLite scan (`should_abort` progress handler) and is never cached.
- Emoji button (😀) on the Everyone/Private bar and the Host chat tab.
- Log: `[LAUNCH] clean shutdown (aboutToQuit)` and `main window closing` (a run that just stops was killed/crashed); PERF probes
  `rotation_effects_now_singing` / `rotation_effects_rail` (21 song-end freezes of 0.4-0.8 s have no known cause yet).
Still open: real-song test of the parked window and of preview-pane capture; Intel has run a real show (1.0.0.5, 2026-10-01, no
crash, Detect Now worked at first and failed at the venue with CoreLocation "unknown") but not these features; song-end screen freezes;
KJ Genie research: it is Windows-only and uses KaraFun's Windows-only websocket Player API; the Mac app opens no local port.

## Host can speak in room chat — 2026-09-30 (server LIVE; now INCLUDED in the re-released 1.0.0.5, see below)

Operator asked: host must be able to talk in Everyone, interject in Private conversations, and pick a host name.
**Server** (`c7ea450`, pushed, deployed 2026-09-30 by the operator with `server-deployments/host-say/deploy.sh`; live SHA-256 of all four
files verified equal to the commit): `host_chat_moderation.php` new POST `say` (channel group|dm, a, b, message, gif_id, media_id, host_name)
and `gifs` search; `chat_media.php` accepts host uploads; `_chat.inc` `CHAT_HOST_KEY='@host'`, `chat_host_send`, `chat_host_name` (24 chars,
default "Host"), host messages visible to BOTH singers of a private thread, counted unread, `host:true` flag; `room_chat_ui.js` HOST tag +
highlight. A singer named "@host" is refused (`invalid_sender`/`invalid_recipient`) so nobody can pose as the host. Venue switches still
apply (group/dm/gifs/photos off = refused). Undo: `/root/singws-chat-deploy/host-say-<UTC time>/rollback.sh`. Tests: `tools/test_host_say.php`
(in `tools/run_room_chat_tests.py`, all five suites pass in a disposable copy).
**App** (`f2f3fab`, label stays 1.0.0.5 by the operator's choice; the published 1.0.0.5 DMGs were replaced with builds containing it): message bar under Everyone,
"Interject" link under each private message (target shown as "Interjecting in A <-> B"), GIF picker (server-side GIPHY search) and photo
upload, setting Settings > Network > Connection > "Chat name" (`host_chat_name`, default "Host"). Host's own messages are not counted as new.
Tests: 1152 pass (`test_room_chat_tab.py` +9). Installed exe `bce6f0c6...` (backup `local-installs/20260930-host-chat/`); operator tested
("all good").
Test-env note: the Qt platform plugins in `.venv-test-arm64-fresh` will not load; copy them to `/tmp/singws-release-qt-platforms` and
`codesign --force --sign -` them, then use `QT_QPA_PLATFORM_PLUGIN_PATH` (the folder is lost when /tmp is cleaned).

## RE-RELEASED 1.0.0.5 with host chat — 2026-09-30 (same version number, assets replaced; latest; installed on this Mac)

Operator asked for both chips rebuilt and the existing v1.0.0.5 GitHub release replaced (same version). Done: release commit `6d0c745`;
tag `v1.0.0.5` was **force-moved** from `8300e55` to `6d0c745`; both DMGs replaced with `gh release upload --clobber`, notes extended.
arm64 `3f086e51...` (127,076,211 B) and x86_64 `c2caaac6...` (152,901,891 B); re-downloaded sizes and SHA-256s equal `docs/release.json`,
live on Pages with the download page and both `latest/download` links. Installed here: the arm64 build (exe `bce6f0c6...`), byte-identical
to the released DMG's app. Checks: Intel hdiutil, strict signature, x86_64 (433 Mach-O), macOS min <= 12.3 (842), markers (chat say, chat
name, seek, capture-only), Rosetta smoke launch (BASS ready, clean exit, live log unmoved). Tests: 1152 app tests, five PHP suites.
**Caveats:** auto-update will NOT offer this to anyone already on 1.0.0.5 (same version), so they must download it again (the notes
say so). Intel has still never run on a physical Intel Mac (Detect Now there unconfirmed). Real-TV rehearsal (KaraFun window vs
audience window) still pending. Next release: bump the version as usual.

## Previously released 1.0.0.5 — 2026-09-29 (superseded by the re-release above; hashes below are the FIRST 1.0.0.5 DMGs)

Tag `v1.0.0.5`, release commit `8300e55`, https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.5. arm64 `65f19e24...`
(127,068,526 B) and x86_64 `20f2f4f4...` (152,919,340 B); sizes and re-downloaded SHA-256s match `docs/release.json`, live on
Pages with the download page and both `latest/download` links. Contains: capture-only KaraFun video (no fullscreen fallback),
placement log, KaraFun seek via Skip 10s, faster end detection, steadier tempo readback, Detect Now fix (see the sections below).
Checks: 1142 tests + 92 environment-group tests (scratch `SINGWS_HOME`, live log unchanged); both DMGs hdiutil-verified, strict
signature, arch (arm64 420 / x86_64 433 Mach-O), macOS minimum <= 12.3 (816 / 842 files), markers present, old fallback absent.
Intel smoke-launched under Rosetta only (BASS ready, clean exit); **never run on a physical Intel Mac** (Detect Now there is
still unconfirmed). Installed here 12:28: the released arm64 DMG (exe `bda75d45...`); backup `local-installs/20260929-release-1.0.0.5/`.
Still open: rehearse on the real TV (KaraFun's window must never cover the audience screen; guard = floating level + 2 s reassert,
skipped when host and audience share a screen); dead code `_renderer_give_up` / `fallback` decision; slow `server_sync` (1-3 s, worker thread).
Server: unchanged today, repo `f5bb7a7` clean and pushed; 4 non-code files differ live vs repo (see 1.0.0.4).
Version policy: next test builds stay at 1.0.0.5 until the operator says release again, then bump.

## Was installed 2026-09-29 12:02 — capture-only KaraFun video + seek (now committed as `b38ac74` and released in 1.0.0.5)

Installed exe `3305f3e5...` (DMG verified, signature ok, identical to installer). Backups of the previous app + profile:
`local-installs/20260929-*` (private). Three changes over commit `170b68e`, all in `0.2.18.1.py`, **not committed yet**:
1. **Fullscreen-handoff fallback removed** (operator: "no failsafe anymore that is too broken"). In `_ensure_renderer_windowed`, when
   System Events cannot see the renderer but CoreGraphics lists it in any Space, capture starts immediately
   (`renderer exists in another Space; starting capture`). If the window never appears the capture just stops (black preview).
   The old `_renderer_give_up` helper and the `fallback` decision in `karafun_renderer_press_decision` are now dead code.
   Real-song results: cold start pressed the video button once, first frame 7 s after play, looked smooth; regular start
   pressed nothing, 29 fps. The earlier black screen (renderer absent, capture gave up after 8 s and never restarted) is fixed.
2. **Placement log** at capture start: `[KARAFUN-CAPTURE] placement renderer=[...] audience=(x,y,w,h) same_screen_as_host=..`.
   On the virtual test screen KaraFun's window lands on the same screen as the audience window (x=1728). The existing guard
   (`_set_show_window_capture_level` floating level + 2 s `karafun_capture_guard` reassert) keeps the audience window above it,
   but is skipped when host and audience share a screen. **Not yet rehearsed on the real TV: watch for KaraFun's window over it.**
3. **KaraFun seek** (`_karafun_seek_to`): dropping the SingWS seek bar clicks Playback > Skip Forward/Back 10s (KaraFun's progress
   bar is not in the accessibility tree), so it lands within ~5 s of the drop, never within 8 s of the end, max 60 clicks. Verified
   on a real song 12:03: +30s, -30s, +150s all clicked fully; end-of-song followed correctly; operator: "seemed perfect".
Tests: 303 related tests pass (`test_karafun_seek.py` is new; source-guard style). Music fade in/out confirmed perfect.

## Earlier today 11:12 — KaraFun black-screen fix (commit `170b68e`, label 1.0.0.5, arm64 only, unpublished)

Black screen at song start was caused by blind toggling of KaraFun's video button. `_ensure_renderer_windowed` now lists
"Dual Renderer" windows across all Spaces (`karafun_dual_renderer_windows`) and presses at most twice, only when the
renderer is absent everywhere (`karafun_renderer_press_decision`; 11 tests in `test_karafun_renderer.py`). Installed exe
`04748d95...` (identical to the DMG; strict signature ok; marker present). Backup of previous app + profile:
`local-installs/20260929-renderer-fix/backup/` (private). NOT yet tested on a real song: watch the log for
`renderer absent in every Space; pressing video button (n/2)` (at most once per song, no black screen).
Operator-confirmed 11:14: a KaraFun cold-start song (Test / Bruno Mars / Dirty Diana) looked right with no slowdowns; renderer
was already present (no presses), capture steady at 29 fps. The renderer-absent case is still untested on real hardware.

## INCIDENT 2026-09-29 04:23 — singer history deletions by mistake (fully restored)

While removing the "Test" singer from Singer History by scripting the UI (System Events), a position click on the
filtered list triggered **five** `history_singer_delete` exports instead of one. Removed: Test (intended) plus **Aryana A.,
Bill F, Codex Capture Test, Codex Playback Test** (not intended; two are real singers). Cause of the extra four not
established. **Restored within minutes from backups, both sides, verified:** local `singer_history.json` +
`singer_preferences.json` from `local-installs/20260929-location-fix/backup/profile/` (pre-restore copies in
`.../pre-restore/`); server `tenants/wsk/history.db` via one transaction from
`/root/singws-chat-deploy/history-before-test-cleanup-20260929T093632Z.db` (safety copy of the pre-restore live DB:
`history-before-restore-20260929T112533Z.db`). The four singers' deletion markers were removed on both sides; Test stays deleted.
After relaunch and sync: local and server both 92 singers / 791 deletion markers; Bill F 19 songs; Test absent; DB quick_check ok.
The rotation's empty "Test" slot also went (expected). **Lesson: do not script clicks/keystrokes against destructive UI
(Delete Singer / Clear ...). Have the operator do those, or use the server tool with an explicit target.**

## RELEASED 1.0.0.4 — 2026-09-29 (published, latest; installed on this Mac)

Fixes "Detect Now" location on Intel Macs (operator report: could not detect no matter how many tries). New
`LocationFixCollector` (pure Python, 10 tests in `test_location_fix.py`): ignores cached fixes older than 30s, keeps the
most accurate fresh fix, treats kCLErrorLocationUnknown/Network as transient (keeps waiting), stops early only within
100 m, accepts up to 3 km at the deadline; Detect Now waits up to 25 s (startup 15 s); logs
`[SESSION-LOCATION] detected accuracy_m=..`. **Verified on real hardware here (Apple Silicon): 35 m in 0.2 s at startup.**
NOT yet verified on a physical Intel Mac - ask the operator to press Detect Now there and check the log line.
Release https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.4 (commit `3e55987`): arm64 `c39e60eb...`
(127,056,396 B), x86_64 `427c4dba...` (151,828,085 B); sizes and re-downloaded SHA-256s match `docs/release.json`, live
on Pages with the download page. Installed on this Mac: exe `0fd53a23...`, backup in `local-installs/20260929-location-fix/`.
Remaining: 4 non-code server files (.gitignore, config.inc.example, two tools/run_*.py) differ live vs repo (deploy with
`scripts/deploy-wskar-rsync.sh --apply` from a clean `git archive` export, not the private checkout); "Test" singer
history removed 2026-09-29 (see incident note); phone-requested song with key/tempo: operator confirmed working 2026-09-29.

## Previously: 1.0.0.3 (superseded)

Full release, both installers, published at https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.3 (tag `v1.0.0.3`,
release commit `241367f`). Apple Silicon `2525f183...` (127,054,926 bytes) and Intel `3b68c025...` (151,839,315 bytes);
sizes and re-downloaded SHA-256s match `docs/release.json`, which is live on GitHub Pages (auto-update clients see it)
along with the updated `docs/index.html` download page (was stale at 1.0.0.1). Both builds verified: signature, arch
(arm64 420 / x86_64 433 Mach-O), macOS minimum <= 12.3, new code present, clean launch (Intel under Rosetta only).
Order followed: version bump -> build -> verify -> manifest -> tag pushed alone -> draft + upload -> size/hash check ->
publish -> `main` pushed last. **This Mac still runs the pre-release 1.0.0.2-labelled build (same code); it will be
offered 1.0.0.3 by the updater, or install the 1.0.0.3 DMG.** Intel build not run on physical Intel hardware.

## Chat, GIFs, request expiry — 2026-09-29 (server LIVE and tested, app installers built, NOT installed)

**Server (wskar.com), all deployed and checked:** room chat (Everyone / Private / Host chat on the singer
page), GIPHY GIFs (key in live `config.inc`, before the closing `?>` — a first attempt appended after it and
printed the key on every page for a few minutes; fixed and restored), photos (needed `php8.3-gd`, now installed),
singer show-screen preview, hourly `/etc/cron.d/singws-chat-cleanup`. Chat is OFF for singers until ticked in the
dashboard's chat card. Undo scripts and backups: `/root/singws-chat-deploy/`. Server repo: `17c290c` (chat),
`16ff820` (older request edits), `4e908a4` (expiry).

**Request expiry (fixes "You already have 2 songs in"):** never-sung requests older than 18h (venue setting
`request_expire_hours`, 4-72, no dashboard control yet) no longer count toward the song limit, the waitlist limit,
the duplicate check, or the singer's Requests tab. Nothing is deleted. Cause: requests stayed `pending`/`delivered`
forever; Warren had two Sept 20 "Life On Mars" rows. Verified live: Warren 2 -> 0, venue 7 -> 2. **Warren's
complaint is fixed** (operator could not confirm with him; verified against live data instead).
"Already sung tonight" (6h) was already live since 2026-09-20.

**Also deployed later 2026-09-29:** GitHub `b92ee07` (cross-store request completion conflicts:
`complete_remote_request.php`, `report_pending_request.php`) and a dashboard control for the expiry window
("Old requests clear themselves after (hours)", server commit `efb5b26`). All six changed server files verified
live by hash. Chat was switched on in the dashboard and confirmed working end to end with two phones
(text, GIF, photo, host moderation).

**Installed 2026-09-29 01:05:** Apple Silicon build `e98c719b…` at `/Applications/SingWS.app` (normal profile;
backup of previous app + profile in a backup that has since been deleted, private). Chat page seen on
screen: three tabs, GIF thumbnail, photo, moderation links all work. Its tab bar was plain/unstyled.
**KaraFun real-song test 2026-09-29 (operator confirmed by ear + panel):** three real "Test / NOFX / Linoleum" songs
through the installed Apple Silicon build. Verified: launch, exact search, start, ~29fps capture with no lag, BGM handling,
natural end detection, rotation advance, server-confirmed completion. **Key/tempo:** live SingWS Key +/- (1 semitone) and
Tempo +/- (1% per press) work during play; a queued song's own key/tempo (right-click > Change Key... /
Change Speed / Tempo...) is applied automatically at start (log: `automatic key/tempo ... key=+2 tempo=90% ... ok=1`).
The panel Key/Tempo set BEFORE a song starts does NOT carry to it (start reads the queued entry) - a mistake in my
first test instructions, not a bug. Not tested: a song requested from a phone with a key/tempo (same start path).
Three "Test" performances from these tests are in real history / Fun Stats (clean up if wanted).
Intel installer rebuilt with the tab fixes and verified (signature, x86_64, markers, Rosetta smoke launch): exe
`b871cec0...`, DMG sha256 `d598c159bee42c30...`, NOT installed and not run on real Intel hardware.

**Installed 2026-09-29 01:32 (current):** Apple Silicon build exe `ca6ecbe6…` (commit `3b16397`) at
`/Applications/SingWS.app`. Chat tabs are pill-styled, bright, never elided, left-aligned; seen on screen and readable.
Two earlier tab bugs fixed along the way (labels cut to "Ever...", tabs centred; then dim text). Backups of the
previous apps + profile: `local-installs/20260929-*` (private). Still open: Intel installer needs a rebuild with the
tab fixes; KaraFun end to end with a real song and a real Intel Mac are untested; version bump + release publish is
deferred until the operator is done. GIFs now switch off for the night when the GIPHY allowance runs out (server
`f5bb7a7`, live; button disappears, resets when chat is cleared or after 12h). Server rebooted onto kernel 6.8.0-142.

**App:** commits `4b93f5b` (KaraFun) and `be401f7` (three-tab Chat page, GIF thumbnails) pushed. Installers
`SingWS-1.0.0.2-arm64-installer.dmg` and `-x86_64-installer.dmg` are built, signed, verified and smoke-launched
(scratch profile) but **not installed and not published**; version still 1.0.0.2. Chat page layout not yet seen
on screen. Stray Finder-duplicate refs `main 2` in `SingWS-Server/.git` were moved to `.git/stray-refs-backup/`.

## Earlier (2026-09-06)

## Release 0.4.7.1 candidate — 2026-09-06

Intel was built and packaged first. Both architectures use the approved purple/neon-green rotation design. The later candidate removes the separate animated lyrics preview: live CDG/MP4 remains as the transparent full-screen underlay, while a brief neon sweep, particle burst, and staggered queue pulse spotlights the next singer every 30 seconds without covering the QR code or ticker.

Intel: strict signing, x86_64 architecture, macOS 12 minimum, DMG verification, and clean Rosetta launch/exit passed. Apple Silicon: strict signing, arm64 architecture, macOS 12.3 minimum and DMG verification passed. Installed at `/Applications/SingWS.app` and launched with normal profile/server connection. Backup: a backup that has since been deleted. The system location permission prompt is left for the operator.

Tests: initial Intel root suite 1085 passed; 22 failures were resolved by two test-fixture updates, rerunning the timing test without concurrent build load, and running native-media tests with their matching ARM source runtime. The revised queue spotlight passed 47 focused tests. Actual CDG animation screenshots are in a backup that has since been deleted. Physical Intel hardware and venue TV remain operator rehearsal checks.

Build commands and detailed verification: `docs/verification/2026-09-06-rotation-release.md`.

## Release 0.4.6.7 Apple Silicon build published — 2026-09-04

The existing public 0.4.6.7 release now has a native arm64 installer alongside
the Intel installer. The build uses the current 0.4.6.7 Python source and the
current Apple Silicon native bridge, including the CDG GPU fence correction.
Packaging was made reproducible on Apple Silicon by matching PyQt6 and Qt 6.9.1,
accepting the bundle's actual macOS 12.3 minimum, and locating Homebrew `7zz`
through `PATH` instead of the Intel-only `/usr/local` location.

The exact DMG was mounted and verified: strict signing passes, 417 Mach-O files
contain arm64, 811 Mach-O files have a deployment target no newer than macOS
12.3, and the bundled media core loads. Installer SHA-256:
`228dc6d4aa88d818a8d104d74d6618e5f6f0e85bbfb1f3f17e5fa88272f55e21`.
GitHub reports the same digest and 123,892,502-byte size.

The exact app is installed at `/Applications/SingWS.app` under the normal app
identity and profile. It launched on Apple Silicon, remained running, connected
and synchronized with the configured server, initialized mpv and BASS, and its
main/audience windows, QR artwork, ticker and controls were inspected on screen.
No new SingWS native crash report or logged Python error appeared. The previous
0.4.6.6 app and normal profile are backed up under
a backup that has since been deleted. Automated verification passed 994 tests
plus 39 subtests and 86 native tests. Full-song karaoke playback and a physical
external-display rehearsal remain unverified.

Report: `docs/verification/2026-09-04-release-0.4.6.7-arm64.md`.
Release: `https://github.com/DanDemolition/SingWS/releases/tag/v0.4.6.7`.

## Silicon CDG blanking / request retry fixes — source only, not built

The 2026-09-02 Apple Silicon show logs and the exact 22 local MP3+G archives
were exercised through the native bridge. Every archive produced a non-blank
visible native surface, ruling out damaged CDG content. The bridge had no
explicit completion dependency between the master OpenGL context that renders
libmpv's shared texture and the two contexts that sample it. `bridge.mm` now
publishes a GPU fence after each karaoke render and queues a server-side wait in
each consumer context. This avoids the intermittent Apple Silicon stale/blank
texture race without restoring the GUI-blocking `glFinish` removed previously.

The same probe exposed a separate definite defect: libmpv `screenshot-raw`
returned a uniform black image for CDG rendered into the caller-owned FBO, so
DAW/show-screen snapshot capture was blank even while the room surface was
correct. CDG capture now reads only the retained 300x216 texture; normal video
keeps the non-invasive screenshot path. Before/after on exact `LG208 12 - ZZ
Top - Blue Jean Blues.zip`: raw capture changed from one black color at every
sample to 4–27 palette colors, while the visible surface remained non-blank.
`tools/probe_cdg_render.py` is the reusable read-only exact-file probe.

The logs also showed six durable terminal request records being posted every
2–4 seconds during DNS failure, producing about 1,962 failures and reaching
571 attempts. Terminal retries now use persisted 5/15/30/60/120/300-second
backoff while a newly completed song still sends immediately. Watchdog logging
is rate-limited. The server repository already contains the coordinated
idempotent cross-store completion correction in `complete_remote_request.php`;
it must be included in the server deployment paired with this app build.

Verification: native x86_64 bridge compiles; exact-file native playback and
capture pass; 81 tombstone tests, 33 focused renderer/queue tests, and the PHP
pending-completion integration suite pass. Python compilation and
`git diff --check` pass. The full runner is currently limited by the local Qt
test environment (`minimal`/`offscreen` platform plugins are not discovered in
subprocess tests); it reached 101 passing tests before that environment failure.
Apple Silicon compilation/runtime and external-display hot-plug rehearsal still
require the target machine. No app was built or installed and no server was
deployed.

## Release 0.4.6.6 built and locally installed — 2026-08-31 12:03 PDT

The operator requested publication after testing the normal app. The release
contains the accumulated fixes below, with only APP_VERSION changed from the
tested 0.4.6.5 candidate. Both spec versions match 0.4.6.6. Intel/macOS 12+ only,
matching the prior public release; no untested ARM build is advertised.

991 desktop tests + 39 subtests, 86 native tests, 282 independent focused GUI
tests, five isolated server-history suites and 16 versioned release-tool tests
pass. The first full-runner attempt failed in its documented broken Qt test
environment; it was rerun fully with working Qt, not counted as a pass or
silently skipped. The exact release app is installed/launched, the host toolbar
is visible and relay connected, with no new Python/native errors. The operator
was actively using Settings; it was left untouched. Server config and history
were unchanged at verification. Full KaraFun/external-display rehearsal remains
unverified and is explicitly disclosed in the release notes.

Executable SHA-256:
`422ad95492e1b4f4414289e8b890726137f69719811c17816d68a7d498f2068b`.
Installer SHA-256:
`e1eb87748c23be0210c9faed7b52f07cf297c8e8790019b8e37c591e468dfc41`.
Frozen code/native-load/architecture/macOS/signing checks pass. The mounted
installer's 3,183 entries exactly match the signed app; hdiutil verification,
helper and shortcut checks pass. The old download page was updated to the
new installer-derived version/link/size/hash alongside docs/release.json.
Private backup/build/test receipt: a backup that has since been deleted.
Report: `docs/verification/2026-08-31-release-0.4.6.6.md`.
Published as the latest GitHub release after its remote size and SHA-256
matched the verified local DMG. The final follow-up commit pushes main only
after publication, making docs/release.json visible to auto-update clients.
Release: `https://github.com/DanDemolition/SingWS/releases/tag/v0.4.6.6`.

## Ticker no longer covers host controls — installed 2026-08-31 11:27 PDT

The detached painter's floating Tool window was at native level 3 above the
level-0 host. `DetachedPainterTicker` now matches the audience window's level
and orders only its own window directly above that audience window. The ticker
ignores mouse input, hides when its output is minimized/hidden, and hides on
ordering failure rather than leaving a floating strip. No native mpv view
reparenting, host/audience global raise, audio, queue, history, or server edits.

307 regressions and eight isolated native checks pass. Actual installed host
toolbar is visibly uncovered, and a normal mouse click on its bottom Settings
button opened Settings successfully; closed without changes. No new Python
errors or native crashes. History and server configuration preserved.
Normal app `/Applications/SingWS.app`, still version 0.4.6.5, executable SHA-256:
`2af5d6fe2909af944ea820172d981858285da621e025ff31f4640d261462edfe`.
Frozen code, native loads, architecture/macOS compatibility and strict signing
verified. Previous app/data and private desktop evidence are in
a backup that has since been deleted. Nothing published. Source changes only main
relative to prior candidate. Report and commands:
`docs/verification/2026-08-31-ticker-host-overlap.md`.
The external-display/full-song KaraFun rehearsal remains pending.

## KaraFun false completion fixed and installed — 2026-08-31 11:08 PDT

The operator confirmed they did NOT stop/skip the song. That resolves the audit
question below: the 10:50:25 completion was incorrect. `0.2.18.1.py` no longer
uses empty-queue text as idle, retries only explicit idle before any playing
hint, and completes only on idle corroborated by fresh end-clock evidence or
the verified duration since confirmed playback. Unknown readings, early idle
and watchdog expiry cannot advance rotation. The independent watchdog now
requests manual Complete if it cannot verify an end, rather than forcing it.

18 new state/timer scenarios and 325 combined regressions pass. Manual Complete,
normal-end single-observation transitions and stale-session guards are covered.
No renderer/audio/server changes or history cleanup. The slow accessibility
scrape remains; physical full-song/end/next-video testing is still required.
Normal-app candidate build/receipts: a backup that has since been deleted.
Report: `docs/verification/2026-08-31-karafun-false-completion.md`.
Installed `/Applications/SingWS.app` (normal identity/profile, version 0.4.6.5),
executable SHA-256:
`9adb6b0c305c03bd150a3e0c085b475f699590e7f8c9853d274f66cff1740dff`.
All 18 safety cases also passed against the actual frozen monitor in the new
bundle. Frozen-source, 432 architecture, 840 minimum-macOS, native-load and
strict signing checks pass. Previous app and normal data backed up in the
receipt directory. Launched at 11:08: normal library and relay loaded, requests
closed, audience artwork/ticker visually checked, no new error/crash report.
Server settings and history content remain unchanged. No publication or
physical karaoke playback by this task. Full-song/end/next-video rehearsal is
still needed; do not assume the old permission grant necessarily survives an
ad-hoc rebuilt executable if macOS requests it again.

## Latest actual-app log audit — 2026-08-31 ~10:52 PDT

Read-only audit of the 10:47/10:49 candidate sessions found no new matching
native crash report or Python traceback; server calls are successful. The
earlier Accessibility failure was resolved after the operator restarted:
KaraFun search/activation/fullscreen verification succeeded at 10:49–10:50.

Two monitor behaviors need attention: unknown state (`idle=0 playing=0`) caused
an automatic result retry at 10:50:01, and the first idle reading at 10:50:25
completed a 263-second song with 251 seconds remaining. Local Lol history now
contains one play, acknowledged by the server on its first attempt. Asked the
operator whether they stopped/skipped intentionally; awaiting their answer
before choosing early-stop versus bad-idle handling. Do not delete that test
performance or manipulate their active session without instruction.

Simulated the exact installed frozen monitor with fake UI/network/timers:
unknown -> playing x3 -> idle produces retry at 10s and completion at 34.4s.
No real clicks/playback or live data writes. Probes still take 4.9–10s. The
saved External Headphones device is absent and the normal default fallback is
active. No code/build/settings/server changes were made. Full findings and
smallest proposed changes: `docs/verification/2026-08-31-current-app-log-audit.md`.

## Actual app installed; separate test setup retired — 2026-08-31 10:47 PDT

The operator explicitly requested future manual testing in the actual app with
the normal profile/server, not a separate SingWS Test setup. This preference is
now in AGENTS.md; automated tests still require scratch SINGWS_HOME.

`/Applications/SingWS.app` now contains the same production source hashes as the
private candidate the operator tested, rebuilt using the normal Intel spec and
`com.singws.app` identity. No TEST title/profile hook. Version is still 0.4.6.5;
no publication, installer replacement, or server configuration change occurred.
Installed executable SHA-256:
`4ab3b8f768f4d06081e18f02f5d6acd69289617f568af508b09099f566367751`.

The old app ZIP and 16 normal profile JSON/SQLite files were backed up under
a backup that has since been deleted. The profile backup is private and contains
credentials: do not publish it. Old ZIP integrity/hash, SQLite quick_check,
57 focused regressions, frozen code equivalence, 432 Intel architecture checks,
840 macOS-12 checks, native-library loads and strict deep signing passed.

The installed app was launched, loaded the normal 133,113-track library and
connected its host relay; request accepting remained off. Its exact frozen
catalog method with normal settings returned 94 online KaraFun rows for Adele.
This was a read-only lookup, not a physical playback test. The initial screenshot
shows the audience artwork/ticker and a macOS location-permission prompt. The
operator was asked to choose Allow or Don't Allow; that privacy choice was not
automated. At 10:48:34 the operator started a KaraFun request under Lol and
reached KaraFun launch, but the app reported missing macOS Accessibility
permission. The operator was notified to enable the normal SingWS app in
Privacy & Security > Accessibility, then quit/reopen it. Do not grant that
permission automatically or interrupt the operator's active test. Successful
KaraFun playback/completion/next-video rehearsal remains pending.

The separate app was moved from personal Applications to Trash. Its test data,
ZIP and logs are archived at `../retired-test-builds/20260831-040139/`; the old
launcher is disabled. No test history/settings were imported into the normal
profile. The independent-catalog proposal below is superseded by this choice.
Manual song completions now affect actual history/Fun Stats, as explained to
the operator. Build/install/search receipts: a backup that has since been deleted.

## Private test KaraFun search diagnosis — 2026-08-31

The operator reports the other fixes passed the tests they ran; KaraFun playback
is still untested because catalog results are absent. This is operator-reported
QA, not confirmation of every individual checklist item or hardware scenario.

The installed test executable still matches `19f881e6...76dad`. Its frozen
`SongSearchThread._karafun_rows` returns immediately without a tenant. Both
KaraFun provider/search toggles are enabled, but the current test settings have
no `user`/`tenant` and use `https://beta.wskar.com`. A verified HTTPS catalog GET
to that beta host returns 404; the production public endpoint returns HTTP 200
and 94 Adele results without credentials. Thus there are two configuration
obstacles, not evidence of a KaraFun playback regression. No SingWS bundle
process was running at the diagnostic check.

The private profile's isolation omitted the catalog dependency. Do not fix this
by copying live host credentials: that would reconnect rehearsal queue/history.
The smallest proposed code change is independent public catalog access without
a tenant requirement, using an explicit working catalog URL for the test app.
This diagnosis did not change source, the installed bundle, settings or server.
KaraFun search remains blocked in this test build pending that change.

## Statistics server deployed — 2026-08-31 10:23 PDT

The user explicitly requested production deployment. The five coordinated PHP
files are now live on wskar.com: `api/v1/singer_history_sync.php`,
`api/v1/manage_singer_history.php`, `submitreq.php`, `history_admin.php`, and
`funstats.php`. Deployed hashes match the tested source. WSK requests were
closed, and other open tenant flags had stale/empty queues rather than recent
show activity.

All 16 existing tenant history databases and the previous five PHP files were
backed up under `/root/singws-statistics-deploy/20260831T172139Z/backup`, outside
the webroot in a private directory. Only the additive `provider_metadata`
column was explicitly migrated in WSK history. Every pre-existing WSK history
row and tombstone matches the backup; queue, settings, config and request
accepting flag hashes are unchanged. No history reset or app release occurred.

Five isolated PHP suites passed again. The production PHP interpreter and the
deployed helper each passed a scratch-database probe: twelve syncs preserve one
streaming performance and its provider ID. Live Fun Stats and history admin
return HTTP 200; the updated Fun Stats label confirms new code is served.
Authenticated history sync returns HTTP 200/ok with zero singers/songs/plays;
history management still rejects an unauthenticated request with HTTP 401.

Deployment manifest, HTTP checks and receipt:
`/Users/Daniel/Documents/SingWS/server-deployments/20260831T172139Z/`.
Guarded code-only rollback is prepared at the remote deployment directory's
`rollback.py`; it was not executed. Do not restore an old database over new
performances. Full report: `../SingWS-Server/deploy/2026-08-31-statistics.md`.

The separate SingWS Test profile remains disconnected from the live website;
deployment did not change its credentials or upload its test history. The
operator has confirmed the waveform works in that build. Remaining playback
rehearsal and any deliberate live-client connection are separate steps.

## Private Intel test build — 20260831-040139, not released

The user requested a build to rehearse before releasing. A separately named
and identified app now exists at
`/Users/Daniel/Applications/SingWS Test.app` (bundle ID `com.singws.app.test`).
Executable SHA-256:
`19f881e6fc5ff04ef984afd9f0d7c168a584669bcb1d0616de2d32a00ae76dad`.
The public version remains 0.4.6.5; no release, installer replacement or server
deployment occurred. `/Applications/SingWS.app` is still `ecfcb0e0...a0489`.

The test bundle is frozen from a source snapshot with all pending fixes. Only
that snapshot adds the timestamp to the host window title and a runtime hook
defaulting SINGWS_HOME to a persistent private test profile. The profile,
launcher, ZIP, exact source diffs, manifest and checklist are in
`/Users/Daniel/Documents/SingWS/Test Builds/20260831-040139/`.
It copies the library, playlist, artwork and caches but starts with empty
queue/history, no website credentials and no automatic updates/log emailing.
Real media files are referenced, not duplicated or changed.

Preparing this profile revealed that song_index and three BGM playlist paths
ignored SINGWS_HOME. `song_index.py` and `0.2.18.1.py` now honor the same data
root as the rest of the app; the ordinary default is unchanged. Three tests in
`test_profile_isolation.py` verify database/phrase paths and playlist
load/analysis/save without altering a simulated live profile.

559 desktop regressions, 54 native-backend tests and five isolated PHP suites
pass. The bundle passes 432 Intel architecture checks, 840 macOS-12 checks,
native-library load probes and strict deep signing. Actual frozen main,
BGM engines, transition analysis, song index and profile hook match the build
snapshot. The packaged helper handles a missing file then measures valid audio.

The test app was launched, but its initial GUI inspection reached the macOS
Documents-access permission prompt. The user was asked to click Allow; startup
and physical playback cannot yet be signed off. The old app declined a graceful
AppleScript quit (-128); it was not killed. Ask the operator to close that copy
before playback tests. Only the built-in display is attached. Full status:
`docs/verification/2026-08-31-private-test-build.md`.

## Log audit / safe crash reporting — source fix, not installed

Reviewed the user's three Aug 30–31 logs and macOS crash reports. The four
14:30 Aug 30 native aborts belong to a temporary `/private/tmp/.../SingWS.app`
bundle, not the evening installed session. Their faulting stacks enter
`QMessageBox.critical` through Python's exception hook. The current handler
still tries to construct that widget without checking for a GUI application.

`exception_handler` now shows its existing dialog only on a live QApplication's
GUI thread, never during shutdown or in headless/startup contexts. Report-write
and optional send failures cannot suppress the original exception on stderr;
the dialog no longer claims a report was saved if writing failed. Eight isolated
process tests cover these cases, including a real unhandled exception exiting
with Python status 1 rather than a native abort. Two regressions failed before
the change. The combined app/history/playback/analysis run passes 530 tests.

The 01:35:14 completion conflict for request 1788161651096 recovered at
01:35:20 on attempt 2. No server lifecycle changes were justified by that log.
Older helper timeouts and unsupported-compression records precede the installed
analysis fixes and documented archive recovery below. Preview-server timeouts,
retryable decoder errors, and startup stalls remain observations, not proven
new root causes. Details and exact test command:
`docs/verification/2026-08-31-log-audit.md`.

Installed executable remains `ecfcb0e0...a0489` (0.4.6.5). The app was running
and its live log continued normal polling during this audit; all tests used
scratch SINGWS_HOME. No build, install, server deployment, media edit, or live
show-data reset was performed. Rehearse a rebuilt app before show installation.

## Singer/Fun Stats inflation — source fix in both repos, not deployed

Reproduced the cause of Shawn's growing counts with real app/PHP sync round
trips against scratch SQLite. Python kept KaraFun provider/track identities;
PHP stripped provider metadata and returned the same song as a local row.
Subsequent syncs summed the streaming records into that growing local snapshot.
The pre-reset backup shows this exact pattern (Self Aware: local 2,925 plus two
KaraFun rows of one play each; Apocalypse: local 1,555 plus a KaraFun row of one).
The deployed PHP file hashes match the pre-fix source.

Server history now retains provider metadata in an additive JSON column and
uses the same song keys as the app, including provider-specific deletions.
Duplicate cumulative snapshots merge by maximum, not addition. Desktop remote
merges preserve newly completed plays even when a response has an equal/older
timestamp. Singer totals returned by sync derive from the retained song counts.
Web submission no longer records a performance; the existing host completion
path does. Fun Stats ignores zero-play saved songs. The recent-song card is
accurately labelled with lifetime totals for recently sung songs: the existing
data does not contain dates for every performance, so it cannot calculate true
30-day totals.

514 combined app regressions pass, including actual Python↔PHP round trips,
provider preservation, repeated sync, legitimate repeats, stale responses,
deletion, and duplicate/aborted completion callbacks. Five PHP suites cover
history, Fun Stats, request retries, singer management and pending completion,
using a disposable server copy. Run them safely with
`python3 ../SingWS-Server/tools/run_history_regressions.py`.
Full findings, test commands and rollout boundaries:
`docs/verification/2026-08-31-statistics-counts.md`.

The cleared live history and its backups were not modified. Existing inflated
song snapshots cannot be repaired reliably from aggregate totals; do not
restore the old history. These changes are not in the installed app or on
wskar.com yet. Deploy the coordinated server files outside a show, then build
and validate the app with the other pending fixes before installation.

## Background track transitions — source fix, not installed

The user requests smoother BGM crossfades with no silence between tracks.
The old scheduler selected `verified_dead_tail` but still counted back from
the file's duration, shortening the overlap to two seconds. Show-log examples
include Moliy / Shake It To The Max (21:50:03), Dorothy / TOMBSTONE TOWN
(22:28:25), and Jimin / Who (23:06:37). Their cached silent tails are 2.6,
2.2, and 2.2 seconds respectively: that policy could begin after audible
content had already finished. Paired native logarithmic slides also did not
use the equal-power curve already described in the player.

`0.2.18.1.py` now schedules against validated audible content bounds and
compensates for queued BASS audio. Known incoming silence is skipped with a
small analysis margin; unknown/stale metadata retains the full file bounds.
Late/manual transitions shorten to fit remaining audio, with a 100ms recovery
fade after EOF or detected silence. A worker prepares the next BASS source;
its result is discarded after a playlist, source, or engine change.

`bass_background_engine.py` holds the next source paused, not merely muted,
and applies paired equal-power BASSmix envelopes on the native audio clock.
Normalization and mixer-master fades remain separate. Completion follows the
incoming audible position, so pausing cannot cause a GUI timer to promote an
unfinished fade. The meter ignores paused preloads. The libmpv fallback accepts
the same incoming content offset; its mixing implementation is unchanged.

Native decode-only mixer tests cover continuity, normalized power, untouched
preloaded intros, cancellation, partial envelope failure, EOF and short files.
Scheduler tests cover silent padding, queued audio, late/manual starts,
stop-after-current, pause, and stale preload results. Details and commands:
`docs/verification/2026-08-31-bgm-crossfades.md`.
Installed 0.4.6.5 remains `ecfcb0e0...a0489`. No build/install or physical-output
listening test was performed. Do not present this as an installed/show-tested
fix; a rebuilt app still needs repeated playlist transitions and karaoke
interrupt/resume checks with the actual output device.

## KaraFun premature completion / orphaned launch — source fix, not installed

Dan / Sugarcult / Memory was marked complete at 00:12:59 in
`singws_2026-08-30.log`, while search was still running. The worker activated
the result and published playing at 00:13:09 after the active session and
Complete dialog were already gone. No monitor existed at the premature
completion, and no monitor completion event was logged.

Reproduced a matching trigger against the pre-change dialog: Qt Return invokes
the default Complete button. The dialog was shown/activated after automation
started, while its search sends Return. Actual key routing that night is not
logged, but the old dialog's erroneous Return behavior is reproduced.

The dialog now has no default/auto-default buttons, opens before the launch
worker, and does not activate itself for automatic playback. Explicit Complete
still works. Launch script calls check active-session identity before/after;
queued handoff/fade/success callbacks ignore ended sessions. Monitor results,
queued completions, and dialog buttons are bound to their original active
session, even if the same request object is later replayed. Finish requests now
log action and submission state. Already-running external AppleScript cannot
be interrupted by these Python checks; they prevent subsequent launch actions
and stale result application.

Added `test_karafun_lifecycle.py`: keyboard versus explicit completion,
cancellation during search, old-dialog rejection, and monitor completion versus
a replacement session. 136 focused lifecycle/provider/performance tests pass
with scratch SINGWS_HOME and qtvenv offscreen. Live logs stayed unchanged.
The installed 0.4.6.5 executable is still `ecfcb0e0...a0489`; no new build
has been installed. Before treating this as show-ready, run a built-app test:
KaraFun search → full song → automatic/manual completion → next local CDG/MP4
with visible audience video, ticker, and correct focus; repeat with a cancelled
launch and a returned/replayed request. This is not yet end-to-end validated.

## Respect laptop background work — source fix, not installed

The user reports SingWS raising over other laptop apps. Installed 0.4.6.5
still contains the one-second ticker restack guard and unconditional KaraFun
host activation; its hash remains the documented `ecfcb0e0...a0489`.
The ticker guard now skips restacking while the application is inactive,
without stopping playback or rendering. Delayed startup focus requests only
activate the host if SingWS is still foreground. KaraFun host-return requests
(including the delayed retry) only reclaim focus while SingWS or KaraFun is
foreground; a browser/editor remains in front. Transparent audience restoration
no longer explicitly activates the entire application.

Audience geometry, native child ordering, display placement and playback are
otherwise unchanged. Three regressions cover background ticker restacking,
foreground-app selection and delayed focus restoration. Verify with scratch
`QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest
test_performance_safety test_karafun_provider`. Native macOS focus behavior and
audience ticker visibility while switching apps still require an installed
on-screen test; these changes have not been built or installed.

## Live singer-history reset completed 2026-08-31

At the user's request, local and wskar tenant `wsk` singer history were backed
up and cleared while SingWS was closed and the local queue empty. The legacy
server `history` table was also cleared so old totals cannot reappear when a
singer returns. Deletion markers were retained/added to block older sync data.
Live history sync and Fun Stats both verify zero singers and performances.
Backups and reset details:
`../history-reset-backups/20260831-021037/README.md`.
Queue, library and settings were unchanged. This was a data reset, not a fix
for the underlying statistics inflation. Do not restore old history casually.

## Audience rotation: hide singers without songs — source only

At the operator's request, `RotationView.update_rotation` excludes empty and
all-skipped-song slots from the audience rail, displayed count, rotation-start
header and upcoming-singer panels. Host slots, ordering, markers, history and
server data are untouched. The existing model-view regression now verifies
the new audience filtering and preservation of host data. This change is not
built or installed; installed 0.4.6.5 remains unchanged.

## KaraFun activation and slow verification — source fix, not installed

Athena / Mahalia / Sober in `singws_2026-08-30.log.2026-08-30`:
search began 23:56:22, exact result at :30, renderer handoff finished :53
after a retry, first idle probe at 23:57:09, recovery at :09, first positive
playback hint at :28 (with an incorrect manual warning), confirmation at :46.
These are local automation delays; logs do not establish a streaming/network
failure or the exact instant audio started. The installed executable matches
the documented 0.4.6.5 hash and contains the earlier nonblocking handoff fix.

`0.2.18.1.py` now completes result activation and its existing 0.8-second
settling delay before scheduling the renderer handoff, preventing overlapping
coordinate-click sequences. Completion polling reads role plus only relevant
button/static-text attributes, instead of name/description/help/value on every
node; polls taking >=4 seconds log their measured duration. A first positive
playing hint suppresses the erroneous manual-start warning while the second
observation is pending. Existing matching, renderer recreation, screen placement,
two-observation confirmation and end-of-song behavior remain in place.

Focused tests: scratch-data `QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest
test_karafun_provider test_performance_safety`. The new monitor simulation covers
idle recovery followed by slow positive observations without a false warning.
The generated monitor script compiles with `osacompile`. No live automation
was executed; actual startup speed and display behavior still need a built-app
song test before shipping. The now-singer animation fix below is also unbuilt.

## Now-singer bar animation — source fix, not built or installed

The operator card's `BarLevelMeter` still expected level samples from retired
audio paths; current mpv playback has no level tap. It now accepts a playback
provider and uses decorative motion when no measurement is available. The host
gates this on karaoke playback and its pause state. Measured silence and stale
samples retain their existing zero-level behavior; stopping still stops the
timer. No audio routing, DSP, transport levels, or end detection changed.

Changed `0.2.18.1.py` and added two regressions in
`test_performance_safety.py`. All 109 tests pass using
`SINGWS_HOME=$(mktemp -d) QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest test_performance_safety`.
An isolated offscreen widget run also captured distinct timer-driven active
frames and a paused baseline. The live log count remained 99,025 lines.
Installed 0.4.6.5 executable hash still matches `ecfcb0e0...a0489` above;
decompressed main bytecode confirms the legacy provider and absence of this fix.
A built-app playback/visual check remains before shipping.

## Same-version 0.4.6.5 release refresh

The verified running app is packaged in a new Intel installer without changing
APP_VERSION. Release verification and checksum are documented in
`docs/verification/2026-08-30-loudness-release-refresh.md`. The refresh supersedes
the older analyzer blocker below. Existing 0.4.6.5 users need a manual download
because there is no version increase. The live scan was left running.

## Follow-up: continue past individual analysis failures — installed

The first pipe fix stopped the user's real Full scan after White Hot succeeded
and the next track returned `libmpv ebur128 produced no integrated loudness`.
That safeguard incorrectly treated a reported per-track error like a dead
helper. The follow-up introduces `AnalysisTrackError`: a received error response
skips that track without writing a permanent failure, closes its native helper,
and continues. Transport/pipe failures still use `AnalysisHelperError` and stop
the batch safely. The native worker resets its session after reported errors,
so consecutive problem tracks cannot disable subsequent analysis.

Installed `/Applications/SingWS.app` is still 0.4.6.5, executable SHA-256
`ecfcb0e0771c7ff6b33eee267528540117c85d923ac06acc29fc2122a40a0489`.
Previous bundle:
`/Applications/SingWS-0.4.6.5-before-track-error-fix-20260830.app`.
No new cache repair was required: 14,123 valid measurements and 70 structural
failure records were retained. Both scratch and normal installed launches
passed; the scratch app was closed before normal app reopened at 14:31.

Tests: 805 tests + 24 subtests, plus 247 focused GUI/regression tests.
A scratch run of the next 16 actual library items (diagnostic audio timeout
shortened to 8s) completed without cancellation: 7 measurements persisted and
reloaded unchanged, 9 analysis errors left uncached/retryable. These include
native decode timeouts, not only immediate missing-LUFS responses. The Coolio
MP3 produced many decoder `Header missing` errors and format changes; archive
integrity alone does not imply valid audio. No media files were changed.
The packaged helper was separately verified to report a missing-file error and
then successfully measure a valid tone. Strict signing, frozen-code markers,
432 Mach-O architecture and 840 minimum-macOS checks passed; installed and
staged executable hashes match. No full-library completion or new DMG claimed.

## Loudness helper pipe fix — installed 2026-08-30

The analyzer blocker below is now repaired in `/Applications/SingWS.app`
(still version 0.4.6.5, executable SHA-256
`a6f4f1717d16c9a293f9990e9d1ad324bee1c9fb18525b2a2e606d1cace5ccc9`).
The old app is retained at
`/Applications/SingWS-0.4.6.5-before-scan-fix-20260830.app`.

Root cause reproduced against the installed helper: TextIOWrapper.readline
read ahead past the startup log into the result, while select subsequently
waited on an empty OS pipe. The helper had already finished, and was waiting
for another request. Binary unbuffered pipe reads with explicit line assembly
fix this, including partial-line timeout handling. The batch now stops and
cancels companion workers on helper failure without caching it as a bad song;
the UI reports the stop. Fast mode also propagates helper errors. Failure
schema 3 retries ambiguous version-2 failures. Structural ZIP failures remain
cached. The repair utility refuses pending checkpoints requiring clean quit.

SingWS was closed cleanly before repair: 119,276 ambiguous failure records
removed, all 14,122 valid records then present preserved exactly, including
all 14,120 original measurements. Backup:
`~/SingWS/cache-backups/loudness-poison-repair-20260830-141831/loudness.json`.
70 structural failures remain. No full rescan has been started by this task;
normal Full/Turbo can resume without Force.

Verification: 802 tests + 24 subtests in the non-GUI runner; 244 focused
GUI/regression/transport tests; final repair tests 3/3. Three real previously
poisoned ZIPs successfully measured through the staged helper, persisted and
reloaded unchanged in a scratch home. Binary markers, 432-file x86_64 check,
840-file macOS-12 check, bundled media loading, strict signing, scratch launch
and installed launch passed. Installed executable matches staged SHA exactly.
No new DMG/release was published. Full-library completion and external-display
playback were not tested in this task. Source changes remain uncommitted.

## 0.4.6.5 readiness audit: analyzer blocker found

The installed and running app is 0.4.6.5; frozen-code markers, strict signing,
architecture, minimum macOS version, bundled media loading, and DMG checksum
all pass. The non-GUI runner passes 799 tests plus 21 subtests; a separate
matching-Qt run passes 202 GUI/performance tests after correcting two obsolete
test assertions. All 401 repaired ZIPs pass integrity checks, and the 70
quarantined files are absent and no longer indexed. Details and test outputs:
`docs/verification/2026-08-30-show-readiness.md`.

**Do not treat the loudness repair as complete.** The 08:12 live cache contains
68,667 new-version `no measurable loudness` failures and 24 helper timeouts.
After three helper failures, the isolated session disables itself but the batch
keeps marking subsequent unmeasured songs failed. The old-version repair tool
does not remove these version-2 records. No live data or application code was
changed by the audit. Stop further full/Turbo scans pending a session-failure
guard, real installed-helper validation, and a backed-up repair while closed.
External-display playback, audible crossfades, and KaraFun end-to-end acceptance
also remain unverified by this audit.

## Detached OpenKJ-style ticker repair (installed for visual test)

The Intel show Mac now uses the pre-rendered QPainter ticker that was smooth in
the 0.4.1-era builds, hosted in a separate non-activating transient window.
This restores its elapsed-time, refresh-rate-paced fractional pixmap scrolling
without returning the ticker to mpv's native-child stacking hierarchy.  The
uneven QML cached-layer experiment was removed.  Other platforms retain the
render-thread QML ticker.

The installed `/Applications/SingWS.app` is this build and the replaced bundle
is preserved at `/Applications/SingWS-0.4.6.4-qml-ticker-20260830.app`.
The installed app launched at 08:12 with
`backend=detached-openkj-painter`, while the Qt Quick audience transition layer
also initialized normally.  Automated verification passed: 200 focused GUI /
show-screen tests and the full 799-test plus 21-subtest runner.  The build
passed x86_64, bundled-media, macOS-12, and strict signing checks; only DMG
creation hit the machine's recurring `hdiutil: Device not configured` error.
CDG/video and actual scrolling smoothness still require the operator's live
visual test.

The detached painter now also implements the ticker lighting natively in the
same paint pass: moving ribbon/atmosphere, queue-change flash, countdown pill
and pulse, live indicator, and edge glow.  It exposes the existing
`ticker_vfx_enabled` control, while retaining the isolated painter surface and
pre-rendered marquee.  The follow-up build passed 181 focused tests, the full
799-test plus 21-subtest runner, strict signing, and a scratch launch reporting
`backend=detached-openkj-painter effects=available`.  It is staged at
`/private/tmp/singws-painter-vfx.Ru9c2n/dist/SingWS.app`, but is not installed:
the running normal app rejected the clean quit request with `User canceled`,
so its bundle was deliberately left untouched.

## Karaoke archive recovery after loudness-cache audit

All 471 retained structural loudness failures were audited with 7-Zip. Seventy
unusable archives were moved, not deleted, to
`~/.Trash/SingWS-unusable-karaoke-20260830-0745/` with their library-relative
folders preserved: 19 contained CDG graphics but no real MP3, and 51 failed
integrity testing with no individually extractable MP3 member. Nine of the
failed archives still had extractable CDG data, but no usable audio, so they are
included in the same reviewable Trash set.

The other 401 archives were recovered. 397 healthy Deflate64 ZIPs (1.53 GiB)
were extracted with 7-Zip and rebuilt using standard Deflate. Each replacement
was verified by both Python `zipfile` and 7-Zip before being installed; the
post-run audit found zero integrity failures and zero Deflate64 members. Four
more healthy ZIPs were rebuilt after removing only `__MACOSX`/AppleDouble junk
entries which had been mistaken for duplicate MP3/CDG pairs. Original archives
were subsequently moved to Trash at the operator's request under
`~/.Trash/deflate64-repack-20260830-074824/` (397 files) and
`~/.Trash/deflate64-repack-20260830-075352/` (4 files). The converted library
copies were reverified immediately before that move.

All 471 old failure-cache records are now harmlessly stale: 70 source paths no
longer exist and all 401 repaired archives have changed file signatures, so the
normal (non-Force) loudness scan will retry the repaired songs while retaining
every valid LUFS measurement. A normal library rescan is required once so the
70 trashed tracks disappear from search results.

## Installed post-release loudness-cache repair and smoother ticker

The 2026-08-29 Turbo resource failure had actually left 117,532 legacy ZIP
records as `no measurable loudness`, not only the narrower structural message
handled by the first release fix. The live cache was repaired atomically while
SingWS was closed: all 14,120 valid LUFS measurements were preserved, 117,532
poisoned ZIP records plus 2,399 ambiguous legacy non-ZIP/time-out records were
removed for retry, and 471 specific CRC, deflate, header, unsupported-
compression, or invalid-package failures remain cached. Backups are under
`~/SingWS/cache-backups/loudness-poison-repair-20260830-071049/` and
`~/SingWS/cache-backups/loudness-poison-repair-20260830-073214/`.

New failure records carry `failure_version=2`. Legacy ambiguous
`no measurable loudness` and Turbo-helper time-out records are retried once;
if genuinely bad, they are immediately cached at the current version. The
repair utility performs the same narrow cleanup with a backup and atomic
replace while asserting that valid measurements are unchanged.

The detached Intel QML ticker keeps exactly the validated transient-window and
CDG/video stacking path. Only its moving glyph item changed: text is cached as
a smooth texture layer and `XAnimator` moves that layer with subpixel filtering,
avoiding Intel glyph re-rasterization/pixel snapping that looked choppy.

The complete scratch suite passes 797 tests plus 21 subtests. Matching-Qt
ticker/show-surface coverage passes 67 tests, including construction of the
detached render-thread ticker and the unchanged transition plane behavior.
Python compilation and `git diff --check` are clean. Nothing in this section
was published as a new DMG. The Intel bundle passed its architecture, bundled-
media, macOS 12 minimum-version, signing, staged scratch-launch, and installed
scratch-launch checks. It is installed at `/Applications/SingWS.app`; the prior
tested show build is preserved at
`/Applications/SingWS-0.4.6.4-pre-ticker-20260830.app`. The new ticker still
needs operator judgment with a long real queue on the audience display.

## Installed 0.4.6.4 replacement after live CDG/ticker validation

The final Intel build was installed at `/Applications/SingWS.app` after a real
CDG test confirmed the GPU singer transition, CDG video, background video, and
smooth render-thread ticker all work together. The transition QQuickView now
hands its plane back to mpv when inactive; the ticker QQuickView uses a detached,
non-activating transient surface aligned to the audience ticker strip.

The complete scratch suite passes 793 tests plus 21 subtests, focused GUI
surface coverage passes, the bundle passes x86_64 architecture, bundled-media,
macOS 12 minimum-version, strict signing, and installed scratch-launch checks.
The verified installer is `SingWS-0.4.6.4-x86_64-installer.dmg`, SHA-256
`3b1efbe462580dac3aa33d89b12e69173eeb81956c2baab7846dca2980d8c6bc`.
The previous installed copy is preserved at
`/Applications/SingWS-0.4.6.4-pre-20260830-fixes.app`.

## 2026-08-29 show follow-up: Intel surfaces, KaraFun start latency, analysis failures

The complete show logs confirmed that the installed 0.4.6.4 transition-surface
retirement code ran, but the audience CDG video remained occluded. The operator
identified v0.4.6.1 as a known-good visuals-plus-CDG baseline. History traced
the regression to v0.4.6.2's repeated native/Cocoa ancestor reordering, which
could lift a shared native container above the video. The transition layer now
uses the v0.4.6.1 Qt-only `raise_()` behavior again: no native ancestor walk,
no container restack, no delayed repeated restacks, and no zero-size retirement.
The ticker likewise raises only its Qt widget. The v0.4.6.1 periodic Qt-only
ticker guard is restored at a one-second interval, so an AppKit late restack is
repaired promptly without touching a shared Cocoa ancestor.
`quick_gpu_surfaces=auto` remains enabled on Intel, while the explicit
Off/environment override remains as a safety switch.

A real staged-build CDG test then showed both native mpv views presenting while
the picture remained covered after a `star_tunnel` effect. This disproved the
simple v0.4.6.1 overlay revert on the current stack. The transition container
now occupies the top plane only while QML reports `active`; when the animation
ends it is lowered beneath video without resizing, hiding, destroying, or
walking Cocoa ancestors. The ticker stays on its independent guarded plane.
The follow-up live test confirmed CDG video became visible after the effect,
while the Quick ticker remained behind mpv. Its guard now raises both the ticker
widget and its own non-overlapping bottom-strip container every second, without
reordering any shared native ancestor.

A second live test proved that even the ticker's own container raise does not
move its Quick native child above mpv on this Intel/macOS stack. Intel now uses
the reliable painter ticker independently of the Quick transition capability.
GPU transitions remain enabled and retain the verified video-plane handoff;
only the continuously retained ticker backend changes. `SINGWS_QUICK_TICKER=1`
exists solely as a diagnostic override.

The painter ticker was visible in the third live test but the operator confirmed
its scrolling was choppy. The Intel render-thread ticker is therefore retained
but moved out of the mpv native-child hierarchy: its QQuickView is now a
non-activating transient surface positioned over the audience window's ticker
strip and resynchronised by the existing one-second guard. This preserves QML
`XAnimator` scrolling/effects while avoiding the child-plane conflict.

All four automatic KaraFun searches found and activated the intended result;
three completed and one was explicitly returned to the queue.  Each start
nevertheless waited up to 12 seconds for a Dual Renderer handoff which took
longer and completed afterward.  Result activation now proceeds immediately
after scheduling the handoff, while renderer recreation/fullscreening continues
in parallel.  Exact-match, playback verification, one-shot recovery, and the
operator warning remain unchanged.

The logs also exposed a poisoned loudness failure cache: a Turbo run recorded
roughly 119,000 valid ZIPs as lacking a readable MP3 after a temporary resource
failure.  That specific cached structural claim is now revalidated against the
ZIP directory, so valid single-MP3 archives are retried and overwrite the bad
entry while genuinely invalid archives remain skipped.  ZIP extraction no
longer swallows its underlying exception, and temporary filesystem errors stop
the worker without signature-blacklisting the track.

Focused fresh-Qt transition/KaraFun/performance coverage passes 168 tests.  An
additional 30 focused non-GUI tests pass.  Python compilation and
`git diff --check` are clean.  The complete scratch-data runner passes 792 tests
plus 21 subtests; its documented mismatched Qt environment excluded GUI modules,
which are covered by the fresh-Qt run above.  Three request terminal updates
briefly received `cross_store_sync_conflict`, then succeeded through the durable
second push within one to three seconds; no server change was needed.  Nothing
has been built or installed, and the changes still need an after-show on-screen
CDG plus real KaraFun smoke test.

## Same-version 0.4.6.4 show-fix replacement (2026-08-29)

The Intel replacement candidate includes the stuck Show Screen transition
surface fix and the verified audio-tail BGM handoff fix below.  Release
verification passed 790 tests plus 21 subtests in the scratch-data runner and
168 focused fresh-Qt GUI/transition tests.  The app passed x86_64 architecture,
bundled-media loading, the macOS 12 minimum-version sweep, staged strict
signing, and DMG verification.  A hidden scratch-data Cocoa launch from the
mounted DMG reached BASS, Qt Quick VFX, the ticker, and the audience window in
1.53 seconds and honored the timed smoke exit.  Its frozen offline helper
returned valid LUFS/peak values for the real MP4 fixture.  The replacement Intel
DMG is 151,674,726 bytes with SHA-256
`8c4e189e1d6ce2cbdff1058762ff765cc1e79b9339b54829064c627e5f789f98`.
It is being published as a same-version `v0.4.6.4` replacement; it must not be
installed over the running live-show app until the operator explicitly asks.

## Verified audio-tail BGM handoff (2026-08-29, 0.4.6.4 replacement)

Live 0.4.6.4 playback of Sound Choice `SC8671 10 - David Bowie - Changes`
correctly scanned its last audible audio at 219.73s in a 230.43s file, but still
left 10.70 seconds of dead air.  The CDG visual scan conservatively returned
`static_or_active_nonblank_final_screen`, and the playback path incorrectly
required a verified visual endpoint before it would even start the audio-only
BGM crossfade.

The source now starts the configured three-second BGM fade as soon as playback
passes the verified audio endpoint, even if a static/nonblank CDG final card is
retained.  Actual early song completion still requires the existing verified
visual endpoint and final-lyrics gates, so this closes audible dead air without
cutting lyrics or clearing the audience image.  The installed app and live show
were not touched; after-show verification remains required.

## Stuck Show Screen transition surface (2026-08-29, 0.4.6.4 replacement)

During a live 0.4.6.4 show, a CDG track decoded and advanced normally but the
audience display remained black behind the ticker and request QR after the
`mosaic_tile_reveal` singer-start transition.  The 0.4.6.4 transition container
was raised above the retained libmpv view but remained a full-screen native
child after QML made the transition inactive; on macOS, that nominally
transparent child can still occlude libmpv as solid black.

The source now keeps the existing QQuickView alive but collapses only its native
window container to zero size whenever the transition becomes inactive.  It
restores the saved maximum size immediately before each next-up, singer-start,
or outro animation.  This avoids the historically crash-prone hide/recreate
path.  The focused Show Screen VFX suite passes 18 tests in the fresh Qt venv;
Python compilation and `git diff --check` are clean.  The installed app and live
show data/settings were not touched.  This still needs an after-show macOS
on-screen smoke test with transitions enabled before building or installing.

## Turbo first-batch timeout loop (2026-08-29, released in 0.4.6.3)

Live inspection of the installed 0.4.6.3 build found Turbo stopped at 4/120,428
with all four parent workers waiting for isolated-helper responses. A failed
120-second compact-transition request was silently retried through a replacement
helper for another 120 seconds, making one bad first-batch file look like a
four-minute freeze. Turbo now makes one 45-second isolated attempt (plus the
existing five-second protocol grace), logs the filename and reason with
`retry=0`, marks the unchanged file failed, and advances. Normal non-Turbo scans
retain their 120-second behavior. The scratch-data suite passes with 790 tests
plus 21 subtests. The replacement Intel DMG was mounted and its app and frozen
video helper were launched successfully. Its SHA-256 is
`bf408e233a89e5d2daa3c0a27cd15e88a3a57be69ea028a08ecc7f0f7975af15`.

## Duplicate manager select-all (2026-08-29, released in 0.4.6.3)

The duplicate results dialog now has a `Select All Duplicates` action. It checks
only items carrying the cleanup-eligible checkbox flag, so recommended keepers
and same-audio/different-CDG review versions remain unselected. The scratch-data
suite passes with 790 tests plus 21 subtests. It ships in the same replacement
0.4.6.3 installer described above.

## Turbo MP4 visual-scan stall fix (2026-08-29, released in 0.4.6.3)

Live testing of the installed 0.4.6.3 candidate showed Turbo Full Scan advancing
MP4 files in roughly 43–49-second batches, with many KVDM tracks grouped in the
stalls. Four audio helpers were isolated, but each corresponding Qt worker still
started an in-process libmpv tail-thumbnail decode, causing four simultaneous
video decoders, 1.0–1.7-second GUI stalls, and a 244 MB RSS rise in four minutes.

MP4 visual backfills now run through the recyclable offline helper and are
serialized through one cancellable slot while the four audio helpers remain
parallel. Waiting workers and the helper-response boundary poll cancellation
every 250 ms; cancellation terminates a still-decoding helper. The video-only
job disables audio: a real 20-second fixture tail fell from 20 seconds to 2.28
seconds once null audio stopped pacing the decoder near realtime. The progress
dialog identifies the waiting/analyzing-video-ending stage, and start/finish
diagnostics include the file, elapsed time, and cancellation state.

The isolated video protocol and real subprocess path both pass, along with the
focused transition/recent-regression tests. Python compilation and `git diff
--check` are clean. The GUI source guard was added, but this machine's documented
Qt platform-probe abort prevented that GUI module from running in the combined
command. This change is not built or installed; the currently running scan still
has the old behavior.

Transition results now have the same crash-resume property as loudness results.
Each successful batch audio or visual merge appends a compact JSONL checkpoint;
cache load replays both the normal checkpoint and an interrupted-compaction
checkpoint with later rows winning. A successful atomic full-cache replacement
then removes the checkpoint. Abrupt quits between the per-track result and final
Turbo compaction therefore no longer repeat completed MP4 work. Three focused
checkpoint recovery/compaction/malformed-row tests bring the focused total to
52 passing tests with scratch show data.

Same-version release verification ran on 2026-08-29: 790 tests plus 21
subtests passed through the scratch-data release runner. The fresh Qt run made
198 tests, with 197 passing and only the documented stale ticker source
assertion failing; the removed module was excluded because it no longer exists.
The new Intel app passed x86_64 architecture, bundled-media loading, and the
macOS 12 minimum-version sweep. Its staged copy passed strict signing, launched
cleanly with scratch data, and returned valid samples through the frozen offline
video-tail helper. After a host restart cleared the disk-image service failure,
the replacement DMG was created and verified, and the app and frozen video-tail
helper were both run from its mounted image. The same-version `v0.4.6.3` tag,
GitHub release asset, and update manifest were replaced with this build. The
Intel DMG SHA-256 is
`bf487c439ec67a9d2e1aadefe87f32c95d9b4001c22b77459cbba8640d2194d4`.

## Duplicate Song Manager (2026-08-29, released in 0.4.6.3)

Settings > Search/Library > Library Tools now exposes a review-first duplicate
manager for MP3+G ZIPs. It uses central-directory CRC/size only to narrow the
catalog, then SHA-256 verifies candidate MP3 and CDG members before making any
archive cleanup-eligible. Identical audio with different CDG data is displayed
as review-only. Exact audio+CDG groups receive a deterministic recommended
keeper, but no candidate is preselected. Explicitly checked archives move to a
timestamped `~/SingWS/duplicate-recovery/` folder and are removed from the
persisted library/search index; move failures attempt rollback.

The live 130,824-ZIP catalog completed read-only in 150.72 seconds and reported
1,053 exact audio+CDG groups, 1,057 selectable redundant archives, two
same-audio/different-CDG review groups, and 35 unreadable candidates. Four pure
audit/recovery tests and the GUI source safety guard pass; Python compilation
and `git diff --check` are clean. No live archive was moved.

## Full-library analysis acceleration (0.4.6.3 built, not yet published/installed)

The single-worker full scan now uses combined EBU R128 plus compact silence
boundary detection for karaoke instead of generating, transferring, and
persisting a 100 ms RMS envelope. A real library ZIP measured 2.48 seconds on
the old path and 1.17 seconds on the compact path with identical LUFS/peak;
the derived start/end were 4.38/200.69 seconds. BGM retains dense envelopes
for fade analysis. Karaoke transition cache serialization now omits raw
envelopes (the existing cache was already 17 MB for 2,059 records and projected
near 1 GB at library scale), while preserving derived audio/visual safety data.

Full loudness results are appended to a crash-resumable JSONL checkpoint during
the pass instead of rewriting the complete growing loudness JSON every ten
seconds; completion atomically compacts it. Existing CDG/MP4 visual metadata is
preserved and skips repeat decoding during a loudness refresh. Forty-nine
focused transition/post-show/performance tests pass with scratch show data;
Python compilation and `git diff --check` are clean. A broader combined Qt run
reached the documented local Cocoa platform-plugin abort after its completed
test bodies. Turbo multi-process analysis is deliberately the next milestone,
not part of this unbuilt change.

Decoder message collection is now filtered to the LUFS/peak/boundary values the
parsers actually consume and ebur128 per-frame reporting is quiet. ZIP member
extraction measured only 0.014–0.017 seconds and is not a useful optimization.
The complete 130,824-ZIP catalog was also checked through central-directory
MP3 CRC/size identities in 17.1 seconds: only 1,060 decodes (0.81%) are exact
duplicates, too little benefit to justify a duplicate-alias cache contract.
The operator-authorized live caches were moved, while SingWS was stopped, to
`~/SingWS/cache-backups/analysis-reset-20260829-0900/`; no queue, settings, or
history data was touched.

At the operator's request that rollback cache was then permanently deleted.
Turbo Full Scan now partitions pending items across four separately spawned,
recyclable analysis helpers. It bypasses only the single-worker semaphore,
retains the per-cache locks, always holds between tracks while karaoke plays,
supports one-button cancellation across all helpers, and performs one final
cache compaction after every helper exits. An isolated-process benchmark over
12 real full-track measurements produced 1.68 tracks/second with three helpers
and 2.16 tracks/second with four, so four is the measured default on this
six-core show Mac (29% faster than three in that run).

An exact-content cleanup audit distinguishes identical audio from identical
audio+CDG; only the latter is eligible for a recommended keeper. The proposed
review surface shows canonical path/naming and keeps deletion explicit rather
than automatically removing a potentially better lyric rendering.

Release verification for the Intel `0.4.6.3` candidate: 782 tests plus 21
subtests passed through the scratch-data release runner. The fresh Qt
environment ran 197 GUI tests with 195 passing; its two exceptions are baseline
test debt (one removed module name and one source assertion already stale on
`v0.4.6.2`), not changed behavior. The canonical build passed x86_64
architecture, bundled media loading, macOS 12 minimum-version, staged strict
signing, and DMG verification. The signed app from the mounted installer
launched with scratch data in 1.34 seconds with BASS, mpv, Qt Quick VFX, ticker,
and the audience window ready. Its frozen compact analyzer returned LUFS, peak,
duration, and audio edges for a real library MP3. The 151,672,698-byte Intel
DMG SHA-256 is `d0a832784f865db34043cd5f7aa667db8095b597b4166e505887a7093b9c3f3e`.
GitHub publication and local installation remain pending.

## Post-show KaraFun restart hotfix (2026-08-28)

Built and installed locally on the Intel show Mac on 2026-08-28. The signed
0.4.6.0 hotfix bundle is `/Applications/SingWS.app`; the prior bundle is
preserved at `/Applications/SingWS-0.4.6.0-rollback-20260828.app`. The Intel
installer is `SingWS-0.4.6.0-x86_64-installer.dmg` (SHA-256
`9d98e62201760f3853ebafe2763d5a2bb2a97e44b5ec8198a843b00b002d3d75`).
Architecture, bundled media, macOS 12 minimum version, strict signing and DMG
verification passed. Both the staged bundle and installed copy launched with
scratch data in about 1.8 seconds with BASS, mpv, Qt Quick VFX and ticker ready.
The empty scratch queue did not exercise a live transition or real KaraFun
handoff; those remain end-to-end show checks.

The 2026-08-27 live log showed every external KaraFun song restarting on the
first completion-monitor poll. KaraFun already reported `playing=1`, but the
fast-start recovery path still double-clicked the saved search result at
14–15 seconds because two consecutive playing hints were required for full
confirmation. Recovery now remains armed for genuinely idle starts but will
not reactivate a result while the current poll reports playback. The eight
focused KaraFun auto-start recovery tests pass with scratch show data; Python
compilation and `git diff --check` are clean.

The same logs exposed a second full-library analysis leak: RSS rose from
717 MB to 7.7 GB while the combined loudness/transition-envelope path ran.
Batch analysis now uses a recyclable helper process, capped at 100 tracks per
process, so libmpv/filter allocations are reclaimed by macOS without entering
the live show process. A real stereo WAV passed through the helper entry point
and returned LUFS, peak and 30 envelope windows. Batch transition records are
kept in memory and atomically flushed once at completion/cancellation instead
of encoding the 13 MB cache every ten seconds. Playback-side cache reads no
longer wait on the persistence lock. Synthetic external KaraFun references are
also rejected before local loudness lookup, eliminating predictable decoder
failures.

The same log was checked for the reported visual-transition repetition. The
installed 0.4.6.0 build used all 16 enabled transition styles exactly once in
each complete shuffle-bag cycle, so no chooser reset or missing configured
style was found.

The external KaraFun completion monitor now accepts the first explicit idle
state after playback has been confirmed. That state is emitted only when
KaraFun says nothing is playing and exposes no Pause/Stop control; requiring a
second full Accessibility scrape added 13–26 seconds of dead air during the
2026-08-27 show. The pre-playback safeguards are unchanged, so an idle result
cannot complete a track that was never confirmed playing.

Offline analyzer decoder chatter is now explicitly contained in the recyclable
helper's discarded stderr, and regression coverage pins both that boundary and
the existing signature-keyed failure cache for malformed media. The KaraFun
Dual Renderer creation wait is widened from two to six seconds inside the
first handoff attempt; the 2026-08-27 renderer consistently appeared after the
old window expired, causing the complete toggle sequence to run twice. The
outer retry remains as a bounded recovery for a genuinely failed creation.

## 0.4.5.8 cleanup in progress

On `cleanup/dead-media-paths-0.4.5.8`, `BackgroundMusicPlayer` no longer
contains the unreachable GStreamer fallback. The live BASS engine remains the
primary path and `LibmpvBackgroundEngine` remains the recovery path when BASS
initialization fails. Removed code included the old GStreamer pipeline builder,
meter callback, pipeline seek/state probes, timer-driven fades and crossfades,
output rebuild, and stale pipeline bookkeeping (about 940 lines). Playlist
realignment now checks the active native deck rather than a permanently absent
GStreamer object. A regression assertion prevents `Gst.` or the retired
pipeline fields from returning to the background player. Fifty-three focused
background/performance tests pass with scratch data.

The retired `python_karaoke_transport.py`, `ffmpeg_background_engine.py`, and
out-of-process `mpv_playback.py` implementations are also removed. Shipped
builds already excluded them; their remaining consumers were tests of the old
implementations and an obsolete manual smoke script. Intro-loop and CDG-offset
contracts now target the live `mpv_karaoke_transport.py` and
`mpv_playback_iina.py` paths. The duplicate FFmpeg background and Qt-audio
output tests were removed; native BASS/libmpv background and mpv transport
coverage remains. Fifty-four focused transport, release, and removal tests
pass.

The frozen bundle now post-filters Qt plugins automatically collected by
PyInstaller. It keeps Cocoa, Darwin audio, SecureTransport, the native network
reachability/style/SVG support, and ordinary artwork formats; it drops test-only
minimal/offscreen platforms, touch input, the unused Qt FFmpeg player plugin,
two unused TLS backends, and PDF/TGA/WBMP image handlers. Test-only Python
packages are excluded from the frozen graph. A trial Intel app passed
architecture, bundled-media and macOS-12 checks, shrank from 375 MB to 350 MB,
and completed a scratch launch in 1.68 seconds with BASS, mpv, Qt Quick, ticker,
and the show window active. The trial DMG step encountered a transient macOS
`hdiutil` device error after the app checks; the release build must rerun it.

Startup request reconciliation no longer emits a full `REQUEST-DIAG` plus
header refresh for every historical request and every terminal request already
absent from the local queue. Those rows are now summarized by count; terminal
rows that actually remove a live local entry retain their detailed audit. The
header refresh is batched once at the end of reconciliation. The tombstone,
relay, queue-authority, and show-critical suites pass with scratch data.

## Pending 0.4.5.7 show-screen hotfix

The 0.4.5.6 parent-window ticker repair called AppKit's
`orderFrontRegardless()` from the three-second ticker guard. On the installed
build this continuously reordered the audience parent, fought the rotation
window lifecycle, made Show Karaoke Screen appear not to open, and introduced
new GUI stalls. The periodic guard now raises only the ticker child. The whole
audience parent is reasserted once from `VideoWindow.showEvent`, preserving the
operator-observed close/reopen recovery without continuously disturbing other
show windows.

Focused show-screen and rotation safety tests pass. The broader performance
module reaches its known local Qt platform-plugin environment abort after its
non-GUI assertions; release verification must use `tools/run_tests.sh` as
documented in `AGENTS.md`.

## Released as 0.4.5.6 after the 2026-08-21 show

The 0.4.5.5 show exposed three operator-visible KaraFun/show-screen faults and
one pre-show performance freeze. These fixes are published in GitHub release
`v0.4.5.6`; the installed `/Applications/SingWS.app` remains 0.4.5.5.

When fast-start result activation does not actually begin KaraFun playback,
the completion monitor now retries the exact already-matched result once. The
old recovery clicked Play while KaraFun was still idle and had no loaded song,
which logged success without recovering the 22:58 Jazzystics track. The Play
control remains the fallback if the saved result location is unavailable.

The show-screen ticker guard now reorders the parent macOS audience window as
well as its ticker child, without activating it and only while external
KaraFun playback is inactive. This mirrors why closing and reopening the show
screen repaired the ticker after KaraFun had been fullscreened manually; a
child-only `raise_()` cannot repair a parent NSWindow displaced by AppKit.

Full-library loudness job enumeration now runs on a QThread before the progress
dialog is created. The 5.8-second 20:55 freeze occurred before the first worker
result and while the GUI was constructing the 134k-track job, so the existing
10 Hz worker-progress throttle could not address it.

Release verification: 774 tests plus 21 subtests passed. The Intel app passed
architecture, bundled media loading, macOS 12 minimum-version, signing and DMG
checksum checks. The published 165,859,307-byte installer matches the generated
0.4.5.6 update manifest. A scratch-data launch of the built app completed in
1.5 seconds with BASS, mpv, Qt Quick ticker, both displays and the new parent
window reassertion active. Live KaraFun result-retry behavior still requires an
end-to-end song test. Apple Silicon is unavailable for this release.

## Released and installed as 0.4.5.5 after the 2026-08-20 show

The installed 0.4.5.4 bundle exposed a KaraFun AppleScript syntax regression.
The artist-row matching code named an AppleScript variable `aS`; identifiers
are case-insensitive, so the compiler read it as the reserved keyword `as` and
every automatic KaraFun search failed with error -2741. The variable is now
`artistSize`, with a regression assertion in `test_karafun_provider.py`.

Focused KaraFun tests pass (33 tests), and a representative generated search
script for Sugarcult / Memory compiles with macOS `osacompile`. The show log
also reports Accessibility denial -25211, so SingWS must be enabled in System
Settings > Privacy & Security > Accessibility before an end-to-end KaraFun
test.

The same uncommitted work now requests the native macOS Accessibility prompt
once, 3.5 seconds after the first launch with automatic KaraFun queueing
enabled. A persisted marker prevents repeated launch prompts; the existing
song-time permission warning remains the recovery path if access is later
revoked.

The audience rotation screen now shifts the complete composition through a
six-pixel perimeter every 20 seconds (`rotation_burn_in_shift_enabled`) so the
otherwise-static title, sidebar, QR, clock and bottom bars do not occupy the
same pixels all night. Render-thread rail ribbons also keep moving when the
queue is too short to scroll. The 1280x720 offscreen layout was rendered before
and after a shift with no clipping or reflow; native QML rail content cannot be
captured by Qt's offscreen QWidget grab and still needs an installed on-screen
check.

The 8.5-second GUI stall at 21:44 coincided with the library analyzer racing
through its cached invalid-ZIP block. It emitted one queued Qt progress signal
per cached failure, fast enough to swamp the event loop. Progress is now
rate-limited to 10 Hz while always emitting the first and final item; a
5,000-cached-failure regression test observes exactly two updates.

Server queue refreshes no longer discard the host's selected request during
playback merely because another request arrived and shifted its row. Song
selection capture now includes the queue entry's stable ID, and the rebuild
restores the matching entry at its new row. If that request was actually
removed, selection is cleared as before, so a different request is never
silently highlighted.

The audience ticker could still disappear until the operator closed and
reopened the show screen. It was alive but macOS had restacked its native Qt
Quick child surface behind the retained video surface after the bounded
transition callbacks. The show window now reasserts ticker stacking whenever
it becomes visible and has a quiet three-second guard that raises the visible,
enabled ticker without taking keyboard focus. This self-heals late AppKit
restacking during a show.

Verification before release: 147 ticker/KaraFun/performance tests pass,
including the new self-healing invariant. The 124 recent-regression tests also
pass independently (4 skipped). Running both Qt
test groups in one Python process exited 134 during macOS pasteboard teardown
after the test bodies; the same recent-regression group is clean in its own
offscreen process. `git diff --check` is clean.

Release `v0.4.5.5` was published on 2026-08-21 as an Intel/macOS 12+ build.
The official runner passed 774 tests plus 21 subtests; architecture, bundled
media loading, minimum macOS version, signing, DMG checksum and asset-size
verification all passed. The signed DMG was installed at
`/Applications/SingWS.app`; the prior bundle is preserved at
`/Applications/SingWS-0.4.5.4-rollback.app`. A fresh installed launch was
visually checked and logged the native Accessibility preflight. The audience
ticker was visible. Apple Silicon remains unavailable for this release.

## Previous 0.4.5.4 release handoff

## Committed on `work/show-fixes-2026-08-18`, NOT RELEASED, NOT INSTALLED

`APP_VERSION` is `0.4.5.4`. `/Applications/SingWS.app` is now **`0.4.5.4`**, but
it predates the uncommitted post-show fixes above. The
`SingWS-0.4.5.4-x86_64-installer.dmg` in the repo root is **stale** — rebuild
before installing.

The server half (KaraFun catalog id stability + stale-id re-resolve) **is live
on wskar.com**, deployed 2026-08-17 23:55 and verified against the live
88,566-row catalog.

### What is in this branch

From the 2026-08-16 show logs, plus two the operator reported directly:

1. Loudness scan leaked ~1 MB/track (472 MB -> 8,635 MB over five hours); one
   mpv core per scan pass now, measured 1.047 -> 0.025 MB/track.
2. Four "crashes" from the analyze progress dialog: the delayed re-raise
   guarded `QTimer.singleShot()` rather than the callback body.
3. A full-library scan ran all show and produced 744 GUI stalls (worst 6.1s).
   It now holds between tracks while karaoke plays — **as a setting**
   (`loudness_scan_holds_for_playback`), because the operator needs to scan
   during songs or a 134k-track pass never finishes.
4. A crash left a 712-byte ZIP with no end-of-central-directory record;
   packaging builds under `.zip.partial` and renames only on success.
5. KaraFun songs could not be re-added from Singer History (synthetic
   `karafun_streaming:` paths are in neither store).
6. KaraFun played Memory from Cats instead of Sugarcult's: transient AXError
   -1719 failures consumed the query-specificity ladder until only the bare
   title was searched. Same query is retried now, and the artist is confirmed
   on the result row.
7. Background music came up ~30s before a KaraFun song ended: the completion
   fallback counted KaraFun's ~45s startup as song time.
8. The ticker vanished after a KaraFun song — every show-screen restore path
   raises the window and none re-raised the ticker's native surface.
9. The brand picker offered 121,650 raw disc ids (Karaoke Version across
   20,901 of them); now 38 canonical brands.
10. Searches could return nothing for songs that ARE in the library: a
    coalesced query drained only on a results signal from a worker that had
    been interrupted and would never emit again.
11. Undecodable files were re-analysed every pass (40 files + 11 SKK006 ZIPs);
    failures are remembered against size/mtime.
12. The CDG visual offset now reaches the follower backend. **Confirmed
    correct on screen by the operator, 2026-08-18.**
13. A host song-swap stranded the singer's waitlisted replacement. Removing
    their last song holds the rotation row for 180s, but the promotion only
    accepted slots emptied by the SERVER, so `host_remove_song` was refused and
    the operator had to add the song by hand. Widened via
    `_is_replaceable_empty_slot_reason`; repeat-preservation stays server-only,
    and tombstones still prevent resurrecting anything the host deleted.

14. Los Enanitos Verdes never auto-started (01:07). The search succeeded and
    the result was activated, but with fast start on the code does not probe
    KaraFun at all -- it hard-codes `"PLAYING"`, logs "play click skipped
    already playing", and skips both the play click and the verify loop. It was
    not playing; the operator pressed play 32s later. The completion monitor
    now performs the verification that log line always promised: one recovery
    play press at 12s, an operator warning at 40s.

Server-side, also on `work/karafun-catalog-ids-2026-08-18` (`d7f0ce5`) and
**deployed**: KaraFun catalog id stability plus stale-id re-resolve. A later
commit adds stage-cue expiry — a singer logging in days later on a new device
was handed every stage cue ever sent, because the client asks with `since_id=0`
and nothing ever expired. Chat is explicitly excluded from that purge.

### Verification

936 tests under `qtvenv`, plus the 86 that need `.venv-universal` — all green.
The four `qtvenv` failures are the environment, not the code; see the
"Running the tests" section of `AGENTS.md`, which now documents this so it
stops being re-diagnosed as a regression.

### What has NOT been done

- **Never launched.** Items 6, 7, 8 and 12 drive KaraFun/AppleScript, native
  surface ordering and CDG timing. None of that is reachable by tests. Item 12
  in particular needs a real CDG disc checked on screen.
- Not merged to `main`, not tagged, not released, not built into a current DMG.
- arm64 is still a release behind and needs an Apple Silicon Mac.
- No rollback bundle exists in `/Applications`; copy the current app aside
  before installing over it.

## Rollback path

`/Applications` holds only `SingWS.app` — there is no preserved fallback bundle
to switch to mid-show. The rollback is the retained
`SingWS-0.4.5.2-x86_64-installer.dmg` in the repo root, which must be installed
before it can be used. Copy the current app aside before installing over it.

## Superseded

Earlier sections of this file described the same work while it was still
uncommitted, plus a "current state" describing `0.4.5.3` as the tip. All of it
is now covered by the branch above and has been removed: `CLAUDE.md` imports
this file, so anything stale here is read into every session as pending work.
