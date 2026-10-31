# Stage E exit: what stands between today's app and "no Python in the package"

Written 2026-10-31 from a read of the tree at `7e5cdf0` (branch `claude/agitated-chebyshev-4d95ea`). Read-only inventory: no code, build, install or deploy was touched.
Exit being measured (RUST_APP_LOGIC_PLAN.md:272-279): *the signed application contains no Python interpreter, Python modules, PyQt or PyInstaller bootloader, and passes the same show acceptance checklist on both architectures.*

**How to read the numbers.** Everything below was measured with `wc` / `grep` / a small script over `0.2.18.1.py` (67,282 lines) on this commit. Two caveats:

* Line counts for a method are "to the next `def` or `class`" (so nested helpers are included). Class spans overstate: `class SongbookUploadThread` starts at 0.2.18.1.py:1388 but 1,500 lines of module-level helpers, loudness cache and `DEFAULTS` follow it before the next class, and `class LocationFixCollector` (3139) is 53 lines, not the 517 a class-to-class subtraction gives. I use method sums or explicit ranges.
* The per-area totals for `KaraokeApp` are *name-pattern sets that overlap* (a method named `_reconcile_remote_requests` is in both "queue" and "network"). They give order of magnitude, not a partition. `KaraokeApp` itself is 22515-66117 (43,603 lines); its `__init__` alone is 2,480 lines (22613).
* Unmerged work exists on other branches (display finish, screens/rows, A5 steps 6-9, UI port) per the sibling notes. This document describes `7e5cdf0` plus what HANDOFF.md says is merged; it does not count unmerged sibling branches.

## Status (updated 2026-10-31)

| Step | State |
|---|---|
| 1. `singws-mac` platform shim | not started |
| **2. `singws-scan` library scan** | **BUILT, tested, mutation-tested; nothing runs it by default** (section 3a below) |
| 3. one-process spike bundle | not started |

## 0. The one-paragraph answer

Rust today is an *evidence layer and an audio/video engine*, not an application. In the production path Rust **owns** audio (background music, pads, karaoke voice, master DSP), the video bridge, and loudness measurement; it **drives with a Python oracle** three song-end decisions; it **shadows** queue admission, transition planning, start routing, settings, store writes and wire formats; and a separate Rust Qt Quick shell shows read-only views and sends a small set of reviewed commands that the *Python app executes* (default `shell_commands: dry_run`, 0.2.18.1.py:4222). **No Rust code owns authoritative state or an event loop.** The queue, requests, rotation, history, settings and every file the show writes are still read, mutated and persisted by Python; the audience window, ticker, rotation screen, preview and soundboard strip are Python-hosted native surfaces; KaraFun is driven by ~4,800 lines of Python/AppleScript. The Rust shell cannot run a show without the Python app running (it reads `live_state.json`, `status_snapshot.json`, `waitlist_snapshot.json`, `queue.json` that the app writes, and its commands arrive as relay messages that `_execute_shell_queue_command` (0.2.18.1.py:50798) performs).

Scale: 36,874 lines of Rust/QML/Obj-C in `rust/crates` versus 67,282 (app) + 12,915 (40 non-test modules) = 80,197 lines of Python that would have to be replaced or retired, plus 193 test modules (39,210 lines) and 101 `tools/*.py` (17,952 lines; 35 of them extract their oracle from `0.2.18.1.py`).

## 1. Inventory by area

Legend. **Rust coverage**: *owns* = Rust is the implementation in the production path; *drives* = Rust answers when it agrees with a retained Python oracle, Python wins disagreements; *shadow* = Rust computes, logs a mismatch, never acts; *proof only* = crate exists and is parity-tested but the app never calls it; *none*. **Effort**: S ≈ days, M ≈ 1-2 weeks, L ≈ several weeks, XL ≈ a quarter-scale item that needs show gates. Effort assumes the gates in RUST_APP_LOGIC_PLAN.md ("one clean show per decision", rehearsals count, 2026-10-26 policy) are kept; it is *engineering plus gate time*, and gate time is bounded by show frequency, not by me.

### 1.1 Queue, rotation, requests, waitlist, singer history

| Item | Where | Size |
|---|---|---|
| Reconcile server rows into the queue | `_reconcile_remote_requests` 63154 | 682 |
| Tombstone sync, removal, completion, retry | `_sync_remote_removal_tombstones_async` 62306 and neighbours | name-set total below |
| Queue model/render | `update_queue_display` 55005 (350), `QueueListModel` 18456 (244), `QueueListView` 18700 (570), `QueueCardDelegate` 17038 (302) | ~1,470 |
| Add / replace / move / skip / lock | `_add_song_to_queue` 45447 (339, "entangled": limits, locks, KaraFun, waitlist, server per stage-e-ui-port-inventory.md:2b), rename, brand, undo | |
| Persistence | `save_data` 52006, `_save_json_atomic` 3625 | |
| Waitlist / waiting-for-add | ~45 `_waiting_for_add_*` methods ≈ 2,400 lines (stage-e-ui-port-inventory.md §2a) | |
| Singer history store + sync + page | `_normalize_singer_history_store` (221), `_merge_remote_singer_history` 48543 (190), `_build_singer_history_page` 37518 (302) | |
| Rotation audience screen | `RotationView` 20275-21412 | 1,138 |
| Name-pattern total (overlapping) | `queue|rotation|request|waitlist|tombstone|singer|history|remote_|undo|duet` | **13,646 lines / 389 defs** |

*Rust today.* `singws-queue` (387 lines) is only the pure admission rules ("a request the host removed stays dead", oldest duplicate wins, remote reorder may only permute known ids) and runs as `queue_shadow` through `queue_shadow.py` (104 lines); it has no state, no lifecycle (`requested → … → removed`), no persistence. `singws-uimodel` (5,961 lines) reproduces read-only *views* of rotation, waitlist, history, header, hero, rows, diagnostics, ticker, status; every one is parity-tested against the real Python methods (stage-e-ui-port-inventory.md §4, §3d). Commands from the shell (`queue.*`, `waitlist.*`, `history.*`, `settings.set`, `eq.set` in `singws-control/protocol.rs`) are validated in Rust but **executed in Python**. A `queue_drive` flag is *planned* (RUST_APP_LOGIC_PLAN.md:66, :313) and does not exist (`grep -c queue_drive 0.2.18.1.py` = 0). The shadow ledger shows `QUEUE-SHADOW:tombstone` with a single clean show (`python3 tools/gate_ledger.py status`).

*No counterpart.* A Rust request lifecycle with permanent ids across restarts; every mutation site (add, remove, reorder, replace, move between sections, skip, lock, duet, undo); queue.json / singer_history.json / preferences / waitlist stores as *writers* (byte-exact JSON writing is proven in `singws-store`, 821 lines, but only as a shadow check); the server pull that creates requests; the waitlist-promotion path.

*Blockers.* None native. Pure live-show risk: AGENTS.md "Queue and server synchronization rules" mean a replay harness comes first (RUST_APP_LOGIC_PLAN.md:62-66; `test_queue_replay.py` exists, 274 lines). It also overlaps the A5 `pick` / `after_start` rules (rotation flags after a start), which another session is driving.

*Effort: XL.* Largest block of authoritative logic, writes live show data, host actions must stay authoritative, and the mutation surface is hundreds of call sites inside one 43k-line class.

### 1.2 Transport, start orchestration, song end

| Item | Where | Size |
|---|---|---|
| Start a song | `play_next_file` 60727 | 705 |
| Start the mpv transport | `_start_mpv_karaoke_transport` 28183 | |
| Media end, cleanup, stop | `_handle_media_end_safe` 30224 (123), `_finish_media_end_cleanup` (129) | |
| End-of-audio / end-silence floors | `_arm_audio_end_floor` (113), `_maybe_trim_end_silence` 43109 (273), `_arm_visual_end_floor` (54) | |
| Control plane over the video bridge | `mpv_playback_iina.py` (638), `mpv_karaoke_transport.py` (492), `mpv_audio_filters.py` (347) | 1,477 |
| Rust karaoke voice adapter | `rust_karaoke_voice.py` | 235 |
| Shadow plumbing | `transport_shadow.py` 247, `start_shadow.py` 651 | |
| Name-pattern total (overlapping) | `play_next|media_end|transport|seek|tempo|_key|end_audio|…` | **4,098 lines / 140 defs** |

*Rust today.* `singws-transport` (339) is the song-lifecycle machine. Three decisions are **driven with a Python oracle**: KaraFun end (`transport_drive`), native media end (`transport_drive_media_end`), KaraFun watchdog (`transport_drive_watchdog`), all default True (0.2.18.1.py:4219-4227); stop/skip is deliberately shadow-only (RUST_APP_LOGIC_PLAN.md "Immediate next step"). Gate ledger: 39 of 42 decisions have one clean show; `TRANSPORT-DRIVE:watchdog` and two `START-SHADOW:route:*` (mp3, cdg_mp3_dual) still need one. `singws-start` (1,295) shadows the whole of `play_next_file` as nine pure steps (route, engine, pick, display, after_start, transport args, confirmed, performance, plan: docs/design/stage-a5-playback-orchestrator.md). Rust *owns* the picture engine (`singws-video` 3,761 lines is the default `video_bridge`, loaded by Python via ctypes at mpv_playback_iina.py:104-124) and the karaoke voice (`karaoke_audio_engine: rust`), with mpv remaining picture master.

*No counterpart.* Anything that *executes* a start: the sequencing in `play_next_file`, voice/picture start protocol and seek protocol (in `mpv_karaoke_transport.py`), pending-start commit, end-silence trimming, intro loops, BGM hand-off, pre-warm, key/tempo application. No Rust event loop to receive signals from the bridge.

*Blockers.* Bridge is attached to Qt *widgets'* `winId()` (mpv_playback_iina.py:306-307, :536-537, :565-573); a Qt Quick host gives a different native view and Qt Quick on macOS renders in Metal while the bridge presents with OpenGL (stage-e-karaoke-display-window.md "First pieces" §2). Rule 6 (native child surfaces stack by creation order) applies.

*Effort: L–XL.* The decisions are mostly captured; the execution glue, ordering and timing are not, and the A5 start-drive session owns the nearest part of it.

### 1.3 KaraFun automation (AppleScript / Accessibility / ScreenCaptureKit)

| Item | Where | Size |
|---|---|---|
| All methods whose name contains `karafun` | 82 defs | **4,797 lines** |
| Largest | `_automate_karafun_search_and_play` 58543 (899), `_start_karafun_completion_monitor` 59518 (578), `_karafun_search_script` 58198 (271), `_handoff_show_screen_to_karafun` 56780 (249), `_restore_show_screen_from_karafun` 57406 (168) | |
| Embedded AppleScript | 32 `tell application/process` lines (grep, 2956-59718) | |
| Script runner | `_run_karafun_applescript_sync` 56393: `NSAppleScript` in-process "so Accessibility permission belongs to SingWS" with an `osascript` fallback; synthetic clicks via `ctypes`/CoreGraphics (`_macos_native_mouse_click` 56742) | |
| Capture | `native/karafun_capture/capture.swift` (304) via `karafun_capture.py` (89) ctypes; `karafun_fullscreen.py` (205) | 598 |

*Rust today.* Only the *end decision*, via `singws-transport` (above). Nothing runs AppleScript, reads the Accessibility tree, clicks or sets the window up.

*Blockers.* (a) macOS Accessibility and Screen Recording grants are keyed by the *process that makes the call* (comment at 56394); a Rust-only app is a different process identity from today's PyInstaller `SingWS.app` and, with the operator's local self-signed identity and no Apple developer account, whether a grant survives rebuilds is **not established** (HANDOFF.md 2026-08-31: "do not assume the old permission grant necessarily survives an ad-hoc rebuilt executable"). (b) The hand-off moves the Python-owned audience window and ordering (§1.6). (c) The scripts are tuned against KaraFun's UI (e.g. HANDOFF.md: the fixed `delay 3` must not become a poll without testing from a freshly launched KaraFun). (d) KaraFun is untestable headlessly: every change needs the app and a real KaraFun.

*Effort: XL.* Largest macOS-specific block; cannot be proven by parity tools (oracle is the live KaraFun UI); the fallback is a thin Rust process-boundary (keep the scripts as text, run them through an `NSAppleScript`/`osascript` host) which is S–M for the runner but the *flow* (search ladder, activation, monitor) is still L.

### 1.4 Library: scan, index, search, loudness/transition analysis

| Item | Where | Size |
|---|---|---|
| SQLite song index (search, build, upsert) | `song_index.py` | 883 |
| Index build / scan | `IndexBuildThread` 1132, `LibraryScanWorker` 14202 (202), `_legacy_scan_folder` 52517 (488), `ZipCache` 16605 (184), `okj_fileinfo.py` (314), `fast_mp3_duration_from_zip` (125) | |
| Search thread / editor | `SongSearchThread` 973, `open_search_track_editor` (117) | |
| Loudness cache + worker | `_loudness_*` 2028-2260, `AnalyzeLibraryWorker` 15102 (344), `_start_analyze_library_items` 47042 (175), parallel coordinator | |
| Transition analysis | `transition_analysis.py` 877, `libmpv_media_jobs.py` 960 (ctypes libmpv jobs), `libmpv_background_engine.py` 644 (libmpv fallback BGM) | 2,481 |
| Duplicates / phrases | `duplicate_song_audit.py` 149, `phrase_detect.py` 679 (numpy/scipy), `phrase_markers.py` 573 | |
| Name-pattern total (overlapping) | `library|scan|search|loudness|analy|song_index|zip|phrase|marker|duplicate|brand|probe|artwork` | **6,698 lines / 168 defs** |

*Rust today.* **Owns** the measurement itself: `analysis_engine` defaults to `"rust"` (0.2.18.1.py:4212), reached through a helper *process* (`rust_analysis.py` 419, `singws-analysis` 955). `singws-db` (368, read-only) and `singws-uimodel` give the shell's Library tab the same search results as Python (2,272 fuzzed searches identical); `singws-dbw` (1,706) reproduces the *writers* of `song_index.py` and `phrase_markers.py` logically identical to Python's but "is NOT linked into the app" (singws-dbw/src/lib.rs header). `singws-plan` (530) shadows the transition decisions (`plan_shadow`).

*No counterpart.* The folder walker and filename/tag parser that feeds the writers (`okj_fileinfo`, `parse_filename_stem`, mutagen tag reads); ZIP inspection; the scan/analysis scheduling, cancellation, pacing ("holds between tracks while karaoke plays", `loudness_scan_holds_for_playback`) and the JSON caches; CDG tile simulation in `analyze_cdg_visual_bytes` and the MP4 tail thumbnail jobs (`libmpv_media_jobs.py`); duplicate manager (destructive moves; "by design local" in stage-e-ui-port-inventory.md §3c).

*Blockers.* None native except `libmpv` jobs (the Rust `singws-video` crate already loads libmpv at runtime). Phrase start is dead code per inventory (`_resolve_phrase_start` returns 0) but `phrase_detect.detect_lead_silence/detect_trailing_silence` are live callers (0.2.18.1.py:4855-4893, :42771); decide to port or delete.

*Effort: M–L.* Well-bounded, offline, not on the show path, with a real oracle (Python scan output) — the best-isolated area. The duplicate manager can stay out of scope.

### 1.5 Background music and soundboard

| Item | Where | Size |
|---|---|---|
| Player (scheduler 500 ms, preload, crossfade, fades) | `BackgroundMusicPlayer` 4927-6952 | 2,026 |
| Manager UI/playlist | `BackgroundMusicManager` 10169-12400 | 2,232 |
| Rust engine adapter | `rust_bgm_engine.py` | 691 |
| Master/EQ in Python (fallback + reference) | `singws_master_audio.py` 476, `singws_eq.py` 248, `rust_master_dsp.py` 229 | 953 |
| Soundboard | `SoundboardPad` 21996 (438), `SoundboardStrip` (81), `soundboard_channels.py` 206 | 725 |
| Name-pattern total (overlapping) | `bgm|bg_|background|soundboard|pad|master|audio|volume|fade|eq_` | **5,095 lines / 205 defs** |

*Rust today.* **Owns** the engine: `singws-bgm` (5,794) + `singws-bgm-ffi` (757) + `singws-dsp` (850) + `singws-stretch` (369) is the mixer, decode, ring buffers, EQ, master chain, pads and karaoke voice; defaults `bgm_engine: rust`, `master_dsp_engine: rust` (4206-4207). Shell has Music and EQ pages with edit commands (`eq.set`).

*No counterpart.* The *player policy*: playlist advance, 500 ms scheduler, adaptive crossfade length, preload, normalisation gains, hold/resume around karaoke, volume/fade sequencing (`BackgroundMusicPlayer`), the playlist store and its UI, pad assignment/settings. `plan_shadow` covers only the window/crossfade-length decisions.

*Blockers.* Audio latency sensitivity (pads); the libmpv fallback engine (`libmpv_background_engine.py`) exists for "Rust cannot start" — a Rust-only package needs either a Rust fallback or to retire the fallback (decision for the owner).

*Effort: L.* Engine done; policy port is mechanical but the `BackgroundMusicPlayer` timing is the thing the show hears.

### 1.6 Karaoke display, show screen, ticker, QR, rotation screen

| Item | Where | Size |
|---|---|---|
| Audience window and its video area | `VideoWindow` 14404 (485), `VideoAreaWidget` 8129 (1,112) | 1,597 |
| Host preview + overlay | `PreviewWindow` 16187 (306), `PreviewControlsWindow` 21518 (117), `PreviewVideoAreaWidget` 16054 (133) | 556 |
| Rotation screen and Qt Quick cards | `RotationView` 20275 (1,138), `RenderThreadNowSingingCard` 19270 (776), `RenderThreadRotationRail` 20046 (74), `RotationAnnouncementTicker` (106) | 2,094 |
| Show-screen VFX / transitions | `RenderThreadShowScreenVfx` 8015 (114) + QML string 7103-8012 (909); `_draw_fallback_transition` etc. | |
| Tickers (3 backends) | `RenderThreadTicker` 66118 (320), `Ticker` 66438 (607), `DetachedPainterTicker` 67045 (238) | 1,165 |
| QML embedded in Python | `QML_SHOW_SCREEN_VFX_SOURCE` 7103, `QML_NOW_SINGING_SOURCE` 18784, `QML_ROTATION_RAIL_SOURCE` 19342, `QML_TICKER_RT_SOURCE` 65785 | ≈ 2,400 lines of QML |
| QR codes | `qrcode` + `PIL` (0.2.18.1.py:850-852) | |
| Name-pattern total (overlapping) | `ticker|qr|show_screen|vfx|overlay|paint|style|css|theme|redesign|_ui|hero|preview|window|…` | **9,407 lines / 297 defs** |

*Rust today.* `singws-cdg` (3,080) is a frame-exact CD+G decoder (0 differences against FFmpeg over 164,504 frames), window-placement rules (27,000 comparisons, 0 differences), idle-overlay layout and a demo window; `singws-uimodel` has the ticker/hero/header text; the shell has `TvWindow.qml` (156), `KaraokeDisplay.qml` (347) and a copy of the VFX QML (`ShowScreenVfx.qml`, 908 lines against the Python string's 909). The shell also has a read-only *output preview* (JPEG polled from the server). **None of it is the audience window**: the doc says so on purpose (stage-e-karaoke-display-window.md:3-9). The CDG decoder's partial alpha differs from libmpv's blend (an owner decision is pending, per the display-finish sibling note).

*No counterpart.* A Rust-owned audience window that hosts libmpv's picture *and* the ticker/VFX/idle layers with correct native stacking, full-screen on the TV and hot-plug; the host preview video and its see-through overlay window; the soundboard strip; everything that calls AppKit window levels (0.2.18.1.py:16265, 16400, 27840, 27868, 67108).

*Blockers (the hard ones).* AGENTS.md rules 4 and 6: stacking follows creation order, tests cannot see it, "never change surface ordering during a show". The bridge presents with OpenGL into views attached to Qt widget windows (mpv_playback_iina.py:306), Qt Quick is Metal; the software-render prototype (libmpv "sw" render into an image provider, stage-e-karaoke-display-window.md §2) avoids GL but is not the production path and has no measured CPU/latency on the Intel Mac. Only `screencapture` on real hardware validates it.

*Effort: XL.* The pieces exist; the integration is exactly the class of change that broke a show on 2026-08-09, and it needs the operator's TV.

### 1.7 Network, relay, server sync

| Item | Where | Size |
|---|---|---|
| Relay clients (QWebSocket) | `RelayRequestWorker` 12915 (184), `HostControlRelayWorker` 13099 (164), `SimplePollWorker` 12766 (149) | 497 |
| HTTP | `requests` via `network_lifecycle.py` (95), 57 `requests.get/post`/`network_lifecycle` references in the app | |
| Sync probe, host state publish, upload | `probe_network_sync_status` 3287 (~196), `_publish_host_state_ws` 29130 (56), `SongbookUploadThread` 1388 (~129), `_session_location_payload` | |
| Network dialog | `configure_network` 40979 | 777 |
| Name-pattern total (overlapping) | `relay|server|sync|network|host_control|chat|_net_|room_|location|upload|…` | **6,132 lines / 176 defs** |

*Rust today.* `singws-wire` (336, request bytes identical to `requests`), `singws-http` (436, sender), `singws-relay` (501, pure worker state machine proven against the real worker classes), `singws-relay-ws` (341, over a real socket) are all **proof only**; the app has only `wire_shadow` observing outgoing calls. The shell's own client (`singws-control`, 2,376) talks to the same relay as a second *browser*, not as the host.

*No counterpart in the host path.* Host-side relay connection, ack/commands loop, request polling, retry/backoff, reconciliation call sites, device location payload, songbook upload, log upload.

*Blockers.* TLS/WebSocket parity edge cases listed as "Not done" in the Stage D4 paragraph of RUST_APP_LOGIC_PLAN.md (WebSocket TLS, proxies/cookies, multipart, retries). Room chat is another session's.

*Effort: M–L.* Clean protocol, strong proofs; the cost is rewiring ~57 call sites with their threading assumptions.

### 1.8 Settings, dialogs, DEFAULTS, migrations

| Item | Where | Size |
|---|---|---|
| `DEFAULTS` (+ flags) | 4172-4467 | ~300 |
| Settings dialog | `configure_settings` 31507 | 1,913 (77 keys, 79 save sites per stage-e-ui-port-inventory.md) |
| Network/Ticker/EQ/Backgrounds | `configure_network` (777), `configure_ticker` (265), `configure_eq` (214), `configure_backgrounds` (202) | |
| Launch migrations | `_migrate_rust_evidence_defaults` (347) and the other `*_migrated` flags | |
| Name-pattern total (overlapping) | `setting|dialog|configure|config|preference` | **4,995 lines / 54 defs** |

*Rust today.* `settings_shadow` + `store_shadow` (byte-exact JSON writer, `singws-store/settings.rs`); `settings.set` for 11 reviewed keys; Settings tab is read-only and masks secrets (`settings_view::is_secret_key`). Every other key was classified and refused on purpose (stage-e-settings-classification.md): secrets, URLs, paths, devices, live engines/surfaces.

*No counterpart.* Writing settings from Rust; the dialogs; the live side effects behind each key (`_apply_*`). *Effort: L (slice per tab, secrets last).*

### 1.9 Logging, diagnostics, crash reporting

| Item | Where | Size |
|---|---|---|
| `SingWSLogger` | 12401 | 365 |
| `setup_logging`, rate-limited diag, `PerfStats`, `_StepClock`, watchdog | 2796, 3194, 3656, 3713, 3888 (watchdog 271) | |
| Redaction, last-show package, bug-report upload | `_sanitize_log_text` … `maybe_auto_send_crash_logs` 2491-2766 | 276 |
| `native_memory.py` 155, `system_snapshot.py` 78, `audio_diag.py` 22 | | 255 |
| Name-pattern total (overlapping) | `log|diag|perf|stall|watchdog|crash|memory|shadow|snapshot|report` | **5,042 lines / 124 defs** |

*Rust today.* Rust-side logging exists only inside engines (`singws-video/src/log.rs`, 36 lines). All evidence tools (`tools/shadow_report.py`, `show_report.py`, `gate_ledger.py`) parse the *Python* log line formats — those formats are a contract. *Effort: S–M.* The watchdog, stack capture and stall attribution are Python-specific and should be deleted, not ported (AGENTS rule 9: keep opt-in).

### 1.10 Updater

`GitHubUpdateWorker` 1212 (~176) plus `_start_update_check` 31398. It checks GitHub/`docs/release.json`, downloads the DMG into `~/Downloads/SingWS Updates` and opens the folder (31488); it does not install. Rust: none (`singws-http` could carry it). *Effort: S.*

### 1.11 Platform/native hosts in Python

| Host | Where | What |
|---|---|---|
| Keep-awake | `mac_keep_awake.py` (84) | `NSProcessInfo` activity assertion |
| Location | `_detect_current_device_location` 41788 (CoreLocation via PyObjC) + `LocationFixCollector` 3139 (53 lines, pure) | one-shot fix |
| Window levels / ordering | PyObjC `AppKit` at 0.2.18.1.py:16265, 16400, 27840, 27868, 67108 | `NSWindowAbove`, floating level |
| Workspace / frontmost app | `NSWorkspace` at 56371, 57335, 57738; `NSApplication` at 57361, 57391 | |
| Accessibility trust / CG events | `ctypes` ApplicationServices/CoreGraphics at 56694, 56750 | |
| ObjC mpv bridge (fallback) | `native/mpv_bridge/bridge.mm` (1,956) | kept for `video_bridge: objc` |
| Swift capture | `native/karafun_capture/capture.swift` (304) | |

*Effort: S each for keep-awake/location/frontmost; M for window-level helpers because of rule 6.*

### 1.12 Packaging

* PyInstaller specs `SingWS-arm64.spec` (308 lines) and `SingWS-x86_64.spec`, four `build_*.sh`, DMG scripts; Python 3.14 and PyQt6 6.9.x Qt inside the app. The shell is a *separate* bundle (`rust/package_shell.sh`) with Qt 6.12.0 from `~/Qt`, `macdeployqt`, local signing; no libmpv/bgm/dsp inside it.
* Python-only runtime libraries the frozen app ships today: `numpy` + `scipy` (`singws_master_audio.py`, `singws_eq.py`, `libmpv_*`, `phrase_detect.py`), `PIL`, `mutagen`, `qrcode`, `psutil`, `requests`, PyObjC. Each needs a Rust or platform replacement or removal.
* Intel: the dev Mac is Intel (AGENTS.md); arm64 builds come from an Apple Silicon Mac; the Rust shell has run on Intel only under Rosetta (RUST_APP_LOGIC_PLAN.md "STAGE E ON INTEL").
* *Effort: L* (a single bundle containing shell + engines + libmpv + Swift capture, signed both arches, with TCC identity behaviour known).

### 1.13 Tests and tooling that depend on Python

193 `test_*.py` modules (39,210 lines) exercise the Python app; 35 of 101 `tools/*.py` import or `ast`-extract functions from `0.2.18.1.py` as the oracle. When the Python app leaves the shipped package it must stay in the repository as a *frozen oracle* (not shipped) or every parity claim loses its reference. *Effort: S to decide, M to restructure.*

## 2. Dependency order (what must come before what)

Ordering rule: nothing that is proven only by being shown on screen or by a real KaraFun moves until the Rust side can run that surface *and* a show has run clean on it. Items grouped by what they unblock.

1. **Independent of the show path (can proceed at any time, in parallel, behind flags):** logging/redaction/bug-report packaging (§1.9), updater (§1.10), library scan + analysis scheduling (§1.4), HTTP/relay host clients (§1.7, first as shadow), platform shims for keep-awake/location/frontmost/AppleScript-runner (§1.11). They have an oracle (Python) and do not decide what the audience sees.
2. **Process model decision (blocks everything below):** the Rust host must become the process that owns the event loop and loads the engines in-process. This is a design/packaging step (§1.12), not a port; it should be settled by a spike that answers the unknowns in §4 (TCC identity, bridge on a Qt Quick view) *before* big ports begin.
3. **Authoritative state:** Stage B (queue/requests), then settings/stores as writers. Needs the replay harness and the A2 clean shows; must not start before the A5 start-drive session has finished the rotation-related `pick`/`after_start` work, because they edit the same rows (`rotation_flags`, who sings next).
4. **Playback orchestration:** A5 start-drive → Rust executes starts, using Rust audio/video engines and the control plane currently in `mpv_*`. Needs 2 and the lifecycle decisions (done as drives).
5. **BGM player policy** (after 4 — hand-off at song end couples them), soundboard pads (after UI host).
6. **Native surfaces:** the audience window / preview / tickers / rotation screen / soundboard strip. Needs 2 (a presenter the Qt Quick host can use) and the Rust video bridge presenter work (RUST_VIDEO_BRIDGE_PLAN.md V-series). Last because rule 6 / rule 4: each needs a screenshot on the operator's real displays.
7. **KaraFun automation flow and capture:** after 6, because the hand-off moves the audience window and the capture stream feeds the video area; needs the TCC answer from 2.
8. **Settings dialogs/Network dialog, deletion of the Python package.** Chat arrives with the room-chat session once the relay carries it.

## 3. Smallest next three coding steps (not overlapping the start-drive or room-chat sessions)

Both running sessions are in `play_next_file`/`singws-start` (A5) and room-chat/relay chat. The steps below avoid `play_next_file`, `_add_song_to_queue`, rotation flags, the chat methods and the relay message schema. Each is behind a flag or is a tool/crate that nothing in the app calls until a flag is turned on.

### Step 1 — `singws-mac`: the platform shim, with a shadow comparison (flag `platform_shadow`, default off)

*What.* A small Rust crate (objc2 or a ~150-line Objective-C shim, as `singws-video` already compiles `shim/video_shim.m`) with a C ABI: `keep_awake_begin/end` (mirror of `mac_keep_awake.py`, 84 lines), `frontmost_app_name` (`NSWorkspace`), `ax_is_trusted` (`AXIsProcessTrustedWithOptions`), `run_applescript(source, timeout) -> (ok, out, err)` (same contract as `_run_karafun_applescript_sync`, 56393), `device_location_once` (CoreLocation). A CLI `singws-mac-probe` prints each so the owner can run it on both Macs.
*Why now.* Retires the PyObjC/ctypes surface (§1.11) and gives the earliest answer to the biggest unknown (does the Accessibility/location/screen-recording grant attach to a Rust binary signed with the local certificate, and does it survive a rebuild?). It does not touch playback.
*Safe because.* Shadow only: `platform_shadow` logs `[PLATFORM-SHADOW]` when Rust's frontmost-app / AX-trust / keep-awake state differs from Python's; Python's answer is always used. Verify with `pmset -g assertions` for keep-awake and a harmless `tell application "System Events" to get name of first process` for the runner; the AX results need the operator at the Mac.
*Done when.* Probe runs on both architectures; parity log has no disagreement across one rehearsal; findings on TCC identity written down.
*Size: S–M.*

### Step 2 — `singws-scan`: library scan in Rust, offline parity first (flag `scan_engine = python | shadow | rust`, default `python`)

*What.* Walker + filename/brand parser (port of `okj_fileinfo.py` 314 lines and `parse_filename_stem`) + ZIP central-directory inspection + MP3 duration hint (`fast_mp3_duration_from_zip`, 0.2.18.1.py:1740) feeding the already-proven `singws-dbw` writers, into a *temporary* database only (dbw already refuses the live profile). `tools/scan_parity.py` builds a synthetic tree (and, read-only, the real library roots) and diffs the Python scan's rows with the Rust rows, plus a mutation pass like the other crates.
*Why now.* Largest piece of non-show-path logic with no Rust counterpart; the oracle is Python's own output; the scan is operator-run maintenance, not a live-show path.
*Safe because.* Default `python`; `shadow` runs the Rust scan on a scratch database after the Python scan and logs row differences; `rust` stays unreachable until a rehearsal on the real library passes.
*Done when.* `tools/scan_parity.py` is IDENTICAL on the synthetic corpus and on a copy of the real library listing; mutants killed; one real scan in shadow shows 0 differences.
*Size: M.*

### 3a. Step 2 as built: `singws-scan` (2026-10-31, branch `claude/agitated-chebyshev-4d95ea`, local only, nothing pushed or installed)

*What exists.*
* `rust/crates/singws-scan` (pure Rust; deps `serde_json`, `sha1`, `zip`, all already in `Cargo.lock`, plus `singws-dbw`): the **full-mode** `_build_library_scan_result` (0.2.18.1.py:13675) with `os.walk` order, hidden-file rule, case-insensitive extensions, `parse_filename_stem` (all six formats and the fallbacks), `_display_for_track`, library locations and ids (`_migrate_library_locations`, sha1 ids), overlapping-root collapse, the physical-file (device, inode) dedupe with location priority, directory signatures, the mass-deletion guard, added/removed bookkeeping, plus `fast_mp3_duration_from_zip` and the MP3 header/Xing/VBRI heuristics under it (0.2.18.1.py:1625-1860). `singws-scan-cli` takes one JSON request (`scan`, `stem`, `zip_duration`, `mp3_duration`, `normalize`). `scan` with `index_db` also writes the song index through the existing `singws-dbw` writers, and `singws-dbw` refuses any path outside the system temp directory or inside the live profile.
* `scan_shadow.py` + setting `scan_engine` (`"python"` default, `"shadow"`, `"rust"`). **`"rust"` is accepted but deliberately not wired: it behaves as `"python"`**, so no setting value can make Rust the source of truth. In `shadow` mode `LibraryScanWorker.run` (0.2.18.1.py:14232) calls `scan_shadow.start(...)` *after* the Python scan has its result: a daemon thread runs the helper in a separate process at nice 10, with a scratch index database that is deleted afterwards, and logs `[SCAN-SHADOW] clean | MISMATCH | skipped`. Only the ten scan settings are sent to the helper (a test pins that no key, PIN or URL can leak). The helper binary is **not bundled** by the specs yet (the module is); `SINGWS_SCAN_BIN` or `native/singws_scan/<arch>/singws-scan-cli` selects it, so with no helper the shadow logs `skipped` once per scan.
* `tools/scan_parity.py` runs the app's own functions (extracted by `ast`, no Qt) against the CLI: 3,264 fuzzed stems, 600 fuzzed MP3 byte streams (ID3 tags, Xing/Info/VBRI blocks, size hints), 84 built ZIPs (CDG size, MP3 fallback, three chunk sizes, last-member rules, damaged and truncated archives), 500 normalised paths (symlinks, `..`, `//`, `~`, spaces, U+3000), and 500 fuzzed library trees (hidden files, case, symlinks to files and directories, broken links, hard links, mtimes at `.999999999`, overlapping / missing / symlinked / `~` roots, old tracks for added/removed, tracks outside the roots, the empty-root guard, stored directory signatures, all filename formats, migrated or legacy settings), with 20-25 of them also compared at the SQLite level (`dbw` rebuild vs `song_index.rebuild_from_tracks_json`). Result: **IDENTICAL**. `--real-root PATH` scans a real library read-only (`~/Music/Karaoke/Karaoke Library`, 189 tracks: identical); nothing under `~/SingWS` is read or written.
* Tests: 32 Rust unit tests; `test_scan_shadow.py` (14, including a small fixed-seed run of the parity tool). Mutation: `tools/mutate_scan.py`, **97 mutants: 76 killed, 21 documented equivalent, 0 survivors** (the first pass left 11; they led to tests for the `reindex_needed` rule, repeated old-library paths, the sync word, non-ASCII ZIP names and ZIP kinds that make the first-candidate / stem-match / zero-frame / third-chunk rules observable; three were shown to be equivalent with the reasons recorded in the tool).

*Decisions taken (conservative defaults, per the delegation).* Quick (incremental) update is **declined**, not ported: it depends on the previous scan's directory signatures, tracks.json's age and per-file reuse rules, and the shadow logs `skipped: quick update` for it. A media file or directory whose name is not valid UTF-8, a `~user` path, a symlink loop, non-text path fields in old tracks or locations, and ZIP members with non-ASCII names are **declined** (Python would store surrogate escapes or decode with CP437). `scan_time` is not compared. `"rust"` is not reachable. The helper is not built into any installer.

*Not done / not proven.* Quick mode. The Rust helper has not run inside the packaged app (not bundled, not launched in the real app: the shadow hook was tested with a fake helper and with the real CLI through `scan_parity.py`, not by pressing Scan in the UI). Performance on the 130,000-track external library was not measured (the real-root run was 189 tracks). Order of tracks equals Python's only because both use the operating system's directory order on the same volume; on a different volume or file system the `walk` order could differ and the parity tool would call that out as an ORDER-only difference. Intel was not exercised (this is an Apple Silicon run). The shadow doubles the disk walk and a scratch index write after every full scan while enabled.


### Step 3 — the "no-Python host" spike: one process, in-process engines, no behaviour change (a tool and a throwaway bundle, no app code, no flag)

*What.* `rust/spike_host.sh` + a `--selfcheck` mode in the shell that (a) starts the Qt Quick shell, (b) loads `libsingws_video` and `libsingws_bgm_ffi` in-process (the crates are already deps/rlibs: `singws-video` is a shell dependency, `singws-bgm` is not), (c) opens a Qt Quick window and hands its `winId` to the bridge's `output_view` creation, (d) plays 5 s of a local MP3 through the Rust BGM engine headless, (e) calls `singws-mac`'s `ax_is_trusted`/`run_applescript`, and (f) writes a report (arches, signing identity, what TCC says). Output: the answer, per architecture, to "can one Rust process host the bridge and engines, and do grants stick".
*Why now.* Everything in §2 after item 1 depends on it; it is the cheapest way to find out whether stage 6/7 are feasible at all before spending quarters on stage 3/4.
*Safe because.* A scratch bundle with a different bundle identifier and its own `SINGWS_HOME`; it never replaces `/Applications/SingWS.app` and never touches the profile (AGENTS rule 7).
*Done when.* The report exists for arm64 and x86_64 (the latter possibly only under Rosetta from this Intel Mac; hardware run flagged) and states the TCC behaviour with evidence.
*Size: M.*

Candidates deliberately **not** chosen: Stage B queue state (overlaps A5 rows and needs the replay harness first); duplicate manager; phrase start (dead); room chat; a Rust relay client in the host path (valuable, but touches the host relay lifecycle the room-chat session is exercising; do after it lands).

## 4. Honest unknowns

1. **TCC identity.** Accessibility, Screen Recording and Location grants are keyed to a process identity. Whether a locally signed Rust bundle keeps them across rebuilds (the operator has no Apple developer account and no notarization is planned) is unmeasured. This can make KaraFun/capture impossible to ship without a re-grant ritual each build.
2. **Native view for the bridge.** The bridge attaches to `QWidget.winId()` views; a Qt Quick scene on Metal gives a different view, and the GL/Metal mix is only worked around in a *software-render* prototype that has no performance data on the Intel venue Mac. Rendering is judged on screen (AGENTS rule 4); no screenshot-based check of this exists.
3. **Two Qt versions.** The app uses PyQt6's Qt (6.9.x per HANDOFF) with QML embedded as Python strings; the shell uses 6.12.0. Behaviour differences in QML/scene-graph/z-order between them are untested together; the shell's `ShowScreenVfx.qml` is a copy, not the same file.
4. **Intel hardware.** The dev Mac is Intel, but the Rust shell and several bridge paths have run on Intel only under Rosetta or via the venue Mac's shows; arm64 builds are made elsewhere. Which architecture combination each gate was cleared on is in `docs/gates/ledger.json`, not summarised here.
5. **Gate time is the real schedule.** 39 of 42 gated decisions have one clean show; the rest of the work needs shows, and a native crash during a stall dump ended the first run of the last show (ledger entry for 2026-10-30). Shows per month bound the pace.
6. **What the name-pattern sets hide.** The per-area line totals overlap and about 21,700 lines of methods that matched no pattern in a first, cruder pass (mostly `__init__` 2,480, widget `paint`/`data` helpers, setup code) were not attributed to an area; the real partition would need reading, not grep.
7. **Python fallbacks that the exit removes.** `libmpv_background_engine.py` (BGM fallback), the Objective-C bridge, `singws_master_audio.py` (Python DSP): removing them loses the rollbacks that live-show rules rely on. Which to keep as Rust-hosted fallbacks is an owner decision.
8. **Unmerged branches.** The display-finish and screens-rows branches, A5 steps 6-9 and the UI-port inventory (sibling notes) change the "no counterpart" lists above; counts here are for `7e5cdf0`.
9. **Behavioural spec is the Python code.** For areas without a parity tool (KaraFun flow, BGM scheduler timing, dialogs) the only specification is the current Python; deleting it removes the only oracle (§1.13).
10. **Effort buckets are estimates** with no velocity calibration behind them; I did not measure how long past modules took, so treat S/M/L/XL as relative sizes only.
