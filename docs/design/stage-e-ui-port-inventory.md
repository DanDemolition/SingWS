# Stage E: what is left of the Python UI, and the order to port it

Written 2026-10-26 from a read of `0.2.18.1.py` (66,514 lines, commit e0e3e24) and the Stage E sections of `RUST_APP_LOGIC_PLAN.md`.
Nothing here changes live behaviour. Policy that applies to every item: shadow before drive, Python wins every disagreement, no default flips, and a rendering or
native-surface change is never judged by tests alone (AGENTS.md rules 4 and 6).

Line counts are the span from the class / method to the next definition (approximate, they include helper methods that sit inside). "Keys" is the number of distinct
`settings[...]` / `settings.get(...)` names the span touches (a lower bound: helpers it calls read more). "Pure" means the *rules* can become a toolkit-free view model in
`singws-uimodel`, proven against the real Python the way `tools/music_parity.py` does; "entangled" means the rules are interleaved with Qt objects, threads, files or native
surfaces and must be separated first.

## 1. The shape of the file

| Part | Lines | Note |
|---|---|---|
| `KaraokeApp(QWidget)` | 42,957 (line 22,393) | the main window **and** the application: `__init__` alone is 2,474 lines; ~60 dialog / page builders live in it as methods |
| `BackgroundMusicManager(QMainWindow)` | 2,232 | the Show tab's music card and the playlist; the `BackgroundMusicPlayer` engine wrapper is 2,026 more |
| `RotationView(QMainWindow)` | 1,118 | the audience rotation screen |
| `VideoAreaWidget` + `VideoWindow` + `PreviewWindow` | 1,112 + 485 + 306 | native mpv / Metal surfaces (the TV picture and the host preview) |
| Tickers (`Ticker`, `RenderThreadTicker`, `DetachedPainterTicker`) | 607 + 320 + 238 | audience ticker: three backends for the same text |
| Delegates / models (queue, library, waitlist, history, BGM) | ~2,100 | row painters + list models; the *text* is built elsewhere |
| Paint-only widgets (avatar, vinyl, meters, waveforms, glow, chips) | ~1,300 | no rules, pure drawing |
| Workers (library scan, analysis, upload, relay, location, update) | ~4,500 | threads; they feed the UI, they are not UI |

Already covered by the Rust shell (`rust/crates/singws-shell`, all parity-tested): Library search + Add (fuzzy), Singer History, Rotation / TV view, Music (playlist,
search, Play, Next, live strip), EQ (view + edit), Settings (read-only, secrets hidden), Controls (PIN login, transport, queue edits, seek/volume, Key, Speed, Rename,
Brand priority, Direct Message, Replace track, output preview). Not covered: everything below that says "none".

## 2. Windows, dialogs and panels

Rank = value x (1 / risk), 1 best. **Value**: seen or used during a show, or holds a rule that has already gone wrong. **Risk**: native surface, threads, secrets, writes
to settings/queue, or hard to verify off the operator's hardware. **Size** is the work to port the rules + parity + a shell page, not the Qt replacement alone:
S = a day, M = a few days, L = a week or more.

### 2a. Text and row rules (the cheap, pure ones)

| Item | Where / lines | Reads (writes) | Rules live in | Pure? | Shell today | Rank / size |
|---|---|---|---|---|---|---|
| **Waitlist rows** ("Waiting for add" page) | `_waiting_for_add_*` ~45 methods, 34,602-37,018 (~2,400); page build 168; `WaitlistRowDelegate` 85, `WaitlistRequestListModel` 92 | `_waiting_for_add_requests`, removed-tonight store (`waitlist_removed_tonight.json`), `_deferred_remote_adds`, queue ids, settings `waitlist_stale_hours`, `defer_remote_adds_until_between_singers`, `use_waiting_for_add` (writes: removal archive, server restore threads) | `_waiting_for_add_row`, `_row_text`, `_order_key`, `_sections`, `_pending_acceptance_rows`, `_is_waiting_for_add_request`, `_remote_request_is_non_actionable`, status/reason/time/version/duration helpers | **rows, text, order, sections, intake rules: pure** (built, see 3). Two lookups need live queue state (first-turn singer, "already accepted") and stay inputs. Actions (add, replace, clear, restore) are entangled with threads/server | none | **1 / S (done)**; page M |
| **Queue ticker text** | `get_singer_names_for_ticker` + `_render_ticker_custom_message` + `Ticker._format_queue_text` (x2 copies), ~190 | queue, `ticker_custom_enabled`, `ticker_custom_message`, `_now_singing_parts` | those three | **pure** (built) | none (rotation page shows the queue, not the ticker string) | **2 / S (done)** |
| **Now-playing hero text** | `_apply_redesign_hero_text`, `_hero_media_badge_text`, `_hero_remaining_text`, `_fmt_karaoke_time` ~90 | title/artist/brand/path, the time-left label | the same four | **pure** (built); fitting/eliding (`_fit_hero_title`, `_elide_hero_subtitle`) needs font metrics: stays with the toolkit | none | **3 / S (done)** |
| Search / library row text | `_build_search_row_text`, `LibraryCardDelegate` 139, `TwoLineSearchDelegate` 133, `RightAlignedMetaDelegate` 213 | song dict (brand/disc/duration), `list_font_scale`, widths | `_build_search_row_text`, `_set_aligned_row_meta` | text pure; width-dependent elision is not | Library page shows its own rows | 4 / S |
| Queue rows / cards | `update_queue_display` 350, `QueueListModel` 244, `QueueCardDelegate` 302, `QueueListView` 570 (drag/drop) | queue, `rotation_padding_sec`, current singer, locks | `update_queue_display`, `_format_queue_*`, `_refresh_queue_summary` | text/summary pure; model+drag/drop is toolkit | Rotation page (read) + Controls (edits via commands) | 5 / M |
| Header chips / status | `_refresh_header_chips` 243, `StatusChip` 51 | `location_name`, `venue_name`, `requests_accepting`, `tenant`, `use_waiting_for_add`, network state (writes: none visible, but `fs`/save calls in span) | `_refresh_header_chips`, `_is_waitlist_enabled_cached` | mostly pure once the network reads are inputs | none | 6 / S |
| Singer history rows | `_build_singer_history_page` 302, `HistoryRowDelegate` 75, `SingerHistory*Model` 214 | `singer_history.json` | `_normalize_singer_history_store`, `_filter_singer_history_songs`, `_singer_history_song_meta_text` | **done** (`singws-uimodel::history`) | History page (read) | done |
| Music playlist rows / counter | `BackgroundMusicManager` playlist section, `BgmPlaylistDelegate` 87, `BgUpNextList` 43 | `bg_playlist.json`, player index | `_apply_playlist_filter`, `update_playlist_count` | **done** (`music`) | Music page | done |
| EQ view | `configure_eq` 214 | `eq_bgm`, `eq_karaoke` | `_ensure_eq_engines` | **done** (`eq_view`) + edit via `eq.set` | EQ page | done |

### 2b. Dialogs

| Dialog | Where / lines | Reads | Writes | Rules in | Pure? | Shell today | Rank / size |
|---|---|---|---|---|---|---|---|
| Change Key / Change Speed | `open_song_key_dialog` 32, `open_song_tempo_dialog` 27 | queue entry | entry key/tempo, server request, save | `_execute_shell_queue_command` (`queue.set_song_key/tempo`) | validation pure (done in `singws-control`) | **done** (Controls) | done |
| Rename Singer | `rename_singer_dialog` 24 | queue | name, merge prompt | same executor | done | **done** | done |
| Brand priority | `open_singer_brand_override_dialog` 78, `_edit_selected_singer_history_brand` 48 | singer, brand list | queue entry brands | executor | done | **done** (queue side; history-side edit none) | S |
| Direct message | `open_direct_message_dialog` 91 | singer, server config | server POST (threads) | executor | done | **done** | done |
| Replace track | `open_replace_track_dialog` 265 | library, KaraFun flags, geometry | queue entry | executor | done | **done** | done |
| Add to queue / confirm interrupt | `AddToQueueDialog` 87, `ConfirmInterruptDialog` 57 | library hit, current song | queue | `_add_song_to_queue` (339 lines, **entangled**: limits, locks, KaraFun, waitlist, server) | add is done as `queue.add_song`; the interrupt confirm is a UI policy only | Library "Add" | S |
| Queue context menu | `show_queue_context_menu` 66 | selection | many actions | each action's own handler | a menu: actions are the commands above | Controls buttons | S |
| Edit history song / waitlist metadata | `_edit_history_song_dialog` 99, `_edit_selected_waiting_for_add_metadata` 120 | row | history store / waitlist request | store normalisers (done) | pure rules; writes need new commands | none | 7 / M |
| Search track editor | `open_search_track_editor` 117 | library row | song index tags | `song_index` writers (Rust `singws-dbw` has the store) | pure behind `singws-dbw` | none | 12 / M |
| Duplicate song manager | `open_duplicate_song_manager` 63, `_show_duplicate_song_results` 131, worker | library CRC/SHA scan | moves archives to recovery folder | audit worker (threads, files) | destructive file moves: **entangled, high risk** | none | 18 / L |
| Library locations | `open_library_locations_dialog` 133 | `library_locations` | scan folders | file system | operator: not remote; keep as a local dialog | none (by decision) | 15 / M |
| Ticker settings | `configure_ticker` 265 | 10 keys (`ticker_*`, `rotation_announcement_*`) | same + live ticker | `_apply_*ticker*` | settings rules pure, preview needs the ticker | none | 8 / M |
| Backgrounds | `configure_backgrounds` 202 | 7 keys (`background_*`, slideshow, closed images) | same + file picks | file dialogs | pure rules + file picker | none | 9 / M |
| Network | `configure_network` 777 | `base_url`, `api_key`, `host_controls_pin`, `tenant`, `user`, chat name, QR captions (17) | same + sync, location detect, relay (threads) | `_net_*` helpers | rules pure, **secrets** and 9 thread sites | none (Settings page hides secrets) | 14 / L |
| Settings | `configure_settings` 1,913 | 77 keys (audio device, update, display, KaraFun, ...) | all of them, 79 save sites, live re-attach hooks | `_apply_*` per key | pure schema + validation; each key has a live side effect | read-only page only | 10 / L (slice by tab) |
| Header QR preview, KaraFun setup / external dialogs | `open_header_qr_preview` 63, `_show_external_karafun_dialog` 112, `_show_karafun_*_setup` | tenant url, KaraFun state | none | AppleScript / Accessibility | **entangled with macOS automation** | none | 19 / L |
| Pre-show diagnostics | `run_pre_show_diagnostics` 19, `_collect_pre_show_diagnostics` 72 | 4 keys, devices | none | pure checks | pure | none | 11 / S |
| Phrase start | `PhraseStartDialog` 391, `open_phrase_start_dialog` 25 | waveform | `phrase_start_seconds` | **dead code** (`_resolve_phrase_start` returns 0) | do not port | none | skip |
| Chat | `_build_chat_page` 55, `_build_room_chat_tab` 53, room chat methods | server rooms | server messages | `_room_*` | server-driven; text/attachment HTML pure, networking is not | none | 13 / M |

### 2c. Windows with native surfaces (keep in Python until the video bridge presents them)

| Window | Lines | Why not now |
|---|---|---|
| `VideoWindow` (audience output) + `VideoAreaWidget` | 485 + 1,112 | owns the mpv NSView stack; z-order is creation order (rule 6); shader geometry (rule 4) |
| `PreviewWindow`, `PreviewControlsWindow` | 306 + 117 | native child view + a see-through sibling window |
| `RotationView` (audience rotation screen) | 1,118 | QML render-thread rail + now-singing card over the video; the shell's TV window is a *preview*, not the audience window |
| Tickers (3 backends) | 1,165 | painter / QML / detached window; the **text** is now in Rust (this work), the scroller is not |
| `RenderThreadNowSingingCard`, `RenderThreadRotationRail`, `RotationAnnouncementTicker` | 776 + 74 + 106 | QQuickView surfaces, same ordering rules |
| Soundboard (`SoundboardPad` 438, `SoundboardStrip` 81) | 519 | pad assignment + Rust pad engine; settings-heavy but audio-latency-sensitive |

### 2d. Paint-only widgets (port as QML components when their page is ported; no rules)
`CircularAvatar` 122, `_VinylRenderer` 294, `HeroVisualizerWidget` 101, `BarLevelMeter` 157, `PerformanceWaveformWidget` 112, `PhraseWaveformWidget` 92, `StatusChip` 51,
`LibraryFilterBar` 62, `SpacedCapsLabel` 42, `DotWidget` 20, `SliderGlow` 32, `HeroGlowOverlay` 41, `BarBackdrop` 29, `RowCardsOverlay` 29, `RotationAnimatedBackdrop` 49.
Rank 20, size S each as QML; zero parity risk, all judged by eye (`--snapshot`).

## 3. Recommended build order

1. **Done in this change (S each):** waitlist rows, queue ticker text, hero text view models, with parity and mutation passes (below).
2. **Next, small and pure:** header chips + pre-show diagnostics + search/queue row text (same method as step 1). A read-only **Waitlist page** and **Ticker preview strip** in the shell
   use the new modules (the ticker string is exactly what the audience sees).
3. **Waitlist actions: DONE 2026-10-27.** `waitlist.add`, `waitlist.remove` and `waitlist.restore` by request id: app commands sharing one body with the page's Add / Remove / Restore buttons (`_waitlist_add_request`, `_waitlist_remove_request`, `_waitlist_restore_request`), Rust validation and plain-words answers in `singws-control`, buttons on the shell's Waitlist tab (Remove asks twice), the waitlist rows in the relay's `queue` message (`_shell_waitlist_summary`) and a Waitlist panel on the web remote (SingWS-Server branch `ui-port-waitlist`, NOT merged, NOT deployed). Tests: `test_shell_waitlist_commands.py` (24), `tools/mutate_waitlist_cmd.py` (36 mutants, 35 killed, 1 documented equivalent), shell loopback scenario `..._adds_removes_and_restores_waitlist_requests_through_the_relay`, the web page driven in the built-in browser against the loopback stack (docs/verification/2026-10-27-waitlist-actions.png is the shell side). The relay has no command-name allow-list (tenant allow-list only); nothing server-side besides daw.js and the page markup needed changing.
   `expect_queue_sig`. Host actions stay authoritative (AGENTS.md queue rules): a command transitions an existing request, it never recreates one.
4. **Ticker and Backgrounds settings: DONE in a deliberately narrow form, 2026-10-29.** `settings.set` {key, value} with a per-key schema (`KaraokeApp._SHELL_SETTINGS_SCHEMA`, mirrored by `SETTINGS_SCHEMA` in `singws-control`; both tested against the one table `docs/design/settings_set_cases.json`, 39 cases incl. every refusal). Only `ticker_custom_enabled` and `ticker_custom_message` (<= 200 characters, no control characters) are on it: the other Ticker keys (colour, bold, speed, size, enabled, vfx, rotation announcement) and every Backgrounds key re-style or re-order live windows, copy files or take paths, so they are NOT settable from a remote. Same off / dry_run / apply gate, request-id dedupe and expiry as the other commands. `test_shell_settings_set.py` (12), `tools/mutate_settings_set.py` (21 mutants, 20 killed, 1 documented equivalent), shell loopback scenario, ticker-message editor on the shell's Ticker tab. This schema is the template for step 5.
   live side effect. This is the template for step 5.
5. **Settings, first slice DONE 2026-10-29** (see docs/design/stage-e-settings-classification.md): the 77 keys of `configure_settings` were classified; 8 keys (the two ticker message keys, five on/off flags and the silence threshold) can now be changed from the shell through `settings.set`, with 99 shared test cases, 26 of 28 mutants killed (2 equivalent) and the shell Settings tab buttons. Everything else is refused on purpose (secrets, URLs, paths, devices, live engines and surfaces). Remaining Settings work is per-key: add the dialog's own refresh call for the keys that need one, then add them to the schema. Network last.
6. **Chat** (M) once the relay carries it; **diagnostics** with step 2.
7. **Native surfaces** only after the Rust video bridge has a presenter that the shell can host (V-series in `RUST_VIDEO_BRIDGE_PLAN.md`) and a show has run clean on the TV: audience
   window, preview, rotation screen, tickers, soundboard strip. Until then the Python windows stay the owners and the Rust modules supply their *text and state*.
8. Paint-only widgets as QML components alongside the page that needs them.

Not planned: phrase start (dead), library locations from remote (operator decision), duplicate manager (file moves; keep local and supervised), KaraFun automation dialogs.

## 3c. Where Stage E stands (2026-10-29, after settings groups 1-3)

Settings: 11 keys changeable through `settings.set` (groups 1-3); every other key reviewed and refused on purpose (docs/design/stage-e-settings-classification.md).
**Network dialog:** its read side is the Settings tab (all 17 keys appear; `api_key` and `host_controls_pin` are masked by `settings_view::is_secret_key`, pinned by its tests). Its write side is
secrets, URLs, location detection and sync threads, so it stays a local dialog by design (same reasoning as library locations). Nothing more to port.

| Item | State | Why |
|---|---|---|
| Text/state view models (ticker, waitlist, hero, header, diagnostics, rows, history, music, EQ) | **done**, parity + mutation tested | |
| Shell pages (Library, History, Rotation, Controls, Music, EQ, Settings, Ticker, Waitlist) and the commands behind them | **done** | |
| Dialogs (key, speed, rename, brand priority, direct message, replace track, add, waitlist actions) | **done** | |
| Settings | **done as far as the rules allow** (11 keys) | the rest restyle live windows, drive engines/workers or are secrets/URLs/paths |
| Network, Library locations | **by design local** | secrets / files / threads |
| Edit waitlist request, edit / add history song | **done 2026-10-29**: `waitlist.edit`, `history.edit_song`, `history.add_song` (shell Edit buttons and form dialogs) | each runs the very body of the dialog's Save (`_waitlist_apply_edit`, `_history_song_apply_edit`; the history one is compared with a frozen copy of the old inline code over 300 random inputs). The waitlist edit keeps the request id and place and tells the server; the history edit writes a backup to `cache-backups/history-edits/` first (newest 30 kept; a failed backup refuses the edit), refuses while karaoke plays like the dialog's buttons, and commits through `_commit_singer_history_change` (so it syncs). Dry-run answers without changing anything. `tools/mutate_edit_commands.py`: 42 of 43 killed, 1 documented equivalent |
| Search track editor | **by design local** | it renames the files on disk (`_rename_track_files`): a library file operation like the duplicate manager and library locations, never remote |
| Chat | **blocked**: the relay does not carry room chat | server work |
| Duplicate song manager, KaraFun automation dialogs, phrase start | **not planned** | destructive file moves / macOS automation / dead code |
| Audience window, preview, rotation screen, tickers, soundboard strip | **gated** (step 7) | native surfaces: only after the Rust video bridge presenter exists and a show has run clean on the TV (AGENTS rules 4 and 6) |

## 3b. Shell pages ported since (2026-10-27)

| Page | Source of its data | Rules | Checked by |
|---|---|---|---|
| **Ticker** (tab 8, read-only) | `queue.json` + `settings.json` (`ticker_custom_*`) in the profile, reloaded every 3 s | `singws-uimodel::ticker::view` (equal to the app's ticker text: `tools/ticker_parity.py`) | `ticker_panel` unit tests, `test_shell_panels.py` (draws, broken/missing queue), screenshot. Limit: the app's "now singing" parts are not on disk, so a custom message that uses them reads as if nobody is singing |

| **Waitlist** (tab 9, read-only) | `waitlist_snapshot.json`, written by the Python app (setting `waitlist_snapshot`, **default off**) whenever its waiting-for-add view would refresh (coalesced, 250 ms), reloaded by the page every 3 s | `singws-uimodel::waitlist::sections_from_snapshot` (equal to the app's `_waiting_for_add_sections`: `tools/waitlist_parity.py`) | `test_waitlist_snapshot.py` (250 random states through the REAL app methods vs the Rust CLI, row text included; setting off writes nothing; coalescing; write failure and hook failure never reach the app; the app never reads the file), `waitlist_panel` unit tests, `test_shell_panels.py`, screenshot |

How the snapshot works: the app's requests are plain JSON (`waiting`, `removed`, `pending_acceptance` = the deferred adds, null while "defer adds" is off) plus the answers only the app can give: `local_ids` (already in the queue), `accepted_ids` (the waitlist hides these), `pending_accepted_ids` (the Pending Acceptance rows hide these: the app's accepted set MINUS the deferred ids, so a deferred add that is also still waiting shows only as pending) and `first_time_names`. The shell never writes the file and the app never reads it. The status line says how old the snapshot is and calls it stale after two minutes (app closed, or the setting turned off). Turn it on by setting `"waitlist_snapshot": true` in the app's settings.json.

## 4. Built in this change

Three modules in `rust/crates/singws-uimodel` (toolkit-free, no Qt, read-only), each with a CLI, unit tests, a differential parity tool and a mutation pass:

| Module | Equal to (real Python) | CLI | Parity tool | Mutants |
|---|---|---|---|---|
| `ticker` | `get_singer_names_for_ticker`, `is_singer_active`, `_render_ticker_custom_message`, `Ticker._format_queue_text` | `singws-ticker-cli` | `tools/ticker_parity.py` | `tools/mutate_uimodel_text.py ticker`: 26 mutants, 23 killed, 3 documented equivalent |
| `waitlist` | `_waiting_for_add_row` / `_row_text` / `_order_key` / `_sections`, `_pending_acceptance_rows`, `_is_waiting_for_add_request`, `_remote_request_is_non_actionable`, status / reason / time / version / duration helpers, `datetime.fromisoformat` | `singws-waitlist-cli` | `tools/waitlist_parity.py` | `... waitlist`: 46 mutants, 45 killed, 1 documented equivalent |
| `header` (step 1) | `_refresh_header_chips`, `_refresh_header_status`, `_set_server_connection_status` state word, `_update_queue_eta_label`, `_fmt_m_ss`, `_refresh_queue_summary` | `singws-header-cli` | `tools/header_parity.py` | `... header`: 28 mutants, 27 killed, 1 documented equivalent |
| `diagnostics` (step 1) | `_collect_pre_show_diagnostics` rules + `run_pre_show_diagnostics` report text (probes are inputs) | `singws-diagnostics-cli` | `tools/diagnostics_parity.py` | `... diagnostics`: 15 mutants, 15 killed |
| `rows` (step 1) | `_build_search_row_text`, `_build_queue_song_row_text` (left / right / tooltip; pixel fitting stays with the toolkit), `_queue_singer_status_label`, media badges, `normalize_karafun_duration_seconds`, `_fmt_right_time` | `singws-rows-cli` | `tools/rows_parity.py` | `... rows`: 36 mutants, 36 killed |
| `hero` | `_apply_redesign_hero_text`, `_hero_media_badge_text`, `_hero_remaining_text`, `_fmt_karaoke_time` | `singws-hero-cli` | `tools/hero_parity.py` | `... hero`: 21 mutants, 20 killed, 1 documented equivalent |

`test_stage_e_ui_text_parity.py` runs small fixed-seed versions of the three parity tools (skipped when the CLIs are not built). Rust unit tests: 98 in `singws-uimodel` (8+1+... ticker 10, waitlist 16, hero 4). Not done: the optional read-only QML page.

How to run:

    export PATH="$HOME/.cargo/bin:$PATH" CARGO_TARGET_DIR=$HOME/.cache/singws-rust-target-ui
    (cd rust && cargo test --release -p singws-uimodel --lib && cargo build --release -p singws-uimodel)
    export QT_QPA_PLATFORM=offscreen
    PY=/Users/daniel/Documents/SingWS/.venv/bin/python
    $PY tools/ticker_parity.py; $PY tools/waitlist_parity.py; $PY tools/hero_parity.py     # TZ=... to try other zones
    python3 tools/mutate_uimodel_text.py all                                                  # slow: rebuilds per mutant

The parity run on queue rows found that the queue song row starts with two EM SPACES (U+2003), not ASCII spaces.

Things the oracle found that the first draft of the port got wrong (all fixed, kept as tests/declines): `fromisoformat` accepts `+05:60` as `+06:00`, a basic-format clock
with an offset (`T200+05:00`) with odd digit counts, and a `.` before an offset; a JSON integer beyond 64 bits reaches Rust as a rounded float.
Deliberate declines (Rust says `decline:` and the app keeps Python): ISO week dates, `24:00`, an offset sign as the date/time separator, non-ASCII numerals, text from Unicode blocks whose case
tables differ between Python releases, `str()` of a JSON container.

Seams (inputs the Rust side cannot know): the waitlist needs "is this singer on their first turn tonight" and "does this request already match an accepted queue entry";
the ticker needs `_now_singing_parts`. They are explicit inputs, the same places the app consults live state.

## 5. A repository finding

`.gitignore` ignores `bin/` anywhere, which silently excluded every `rust/crates/*/src/bin/*.rs` (all the `*-cli` binaries) from git: a clean checkout of main cannot build
`singws-uimodel`, `singws-db`, `singws-http` and others. This change adds `!rust/crates/*/src/bin/` and commits the `singws-uimodel` bins (the ones the main checkout had untracked and
that its Cargo.toml already lists). The other crates' `src/bin` directories are still untracked in the main checkout; adding them is a one-line `git add` after merging.
