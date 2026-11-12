# Goal: a Rust-only SingWS, with no Python and no Qt (operator, 2026-11-10)

"I want eventually this whole thing to be Rust only. No Python or Qt or anything, if possible." This is the stated end state. It is a direction, not a schedule:
nothing here is started and nothing changes how the app runs today. The Python half of the goal is already the whole of Stage E
(`stage-e-no-python-gap.md`, the `stage-e-*-rust-plan.md` files). This document adds the second half, **no Qt**, which Stage E had assumed away.

## Where it stands (measured 2026-11-10)

| Layer | Today | Rust-only status |
|---|---|---|
| Audio (music, pads, karaoke voice, DSP, analysis) | Rust | done |
| Video picture (libmpv bridge, GL presenter) | Rust (the old Objective-C bridge retirement is planned: `rust-only-video-bridge.md`) | done, one fallback left |
| Rules and decisions (queue, transport, song end, KaraFun, ...) | Python is the live code; Rust twins exist as shadows/ports, proven by parity | in progress, see Stage E |
| App shell and every window | **PyQt6** in the live app: `0.2.18.1.py` is 68,181 lines and 50 Python files import PyQt6 | not started |
| The Rust shell (`singws-shell`) | **Qt Quick through cxx-qt** (4,870 lines of Rust + 19 QML files, 3,674 lines) | uses Qt too |
| Audience screens (show screen effects, ticker, rotation screen) | QML for the effects, native video layers underneath | uses Qt |
| KaraFun web player | Rust browser driver; app logic is Python (`stage-e-karafun-web-rust-plan.md`) | in the queue |

So "no Qt" means replacing BOTH the PyQt app and the Qt Quick shell, and re-creating the ~3.7k lines of QML pages and effects in whatever replaces them.

## The decision that matters: the UI toolkit (not made)

| Option | For | Against |
|---|---|---|
| **Slint** (Rust-first, declarative, GPU/software renderers) | closest to QML in style (the 19 pages port page by page); no JavaScript; small runtime | licensing terms need a check (GPL / royalty-free / commercial); smaller ecosystem than Qt |
| **iced** (Elm-style, wgpu) | pure Rust, permissive licence | young widgets for text-heavy UIs (lists, tables, rich text chat); a rewrite, not a port |
| **egui** (immediate mode) | fastest to build host tools | looks like a tool, not a polished show product; text and layout limits |
| **Tauri / system webview** | rich UI quickly (the web remote already exists as web code) | it is NOT Rust-only (a JavaScript UI in a webview), and not Qt-free in spirit if the goal is "nothing but Rust" |
| **Native AppKit through objc2 (+ Win32/WinUI later)** | exactly native; the video bridge already talks to AppKit views | two UIs for two platforms; the most work |
| **Custom wgpu renderer** (own widgets) | total control, matches the audience windows that are already GPU surfaces | the largest job by far; accessibility and text input are hard |

Constraints any choice must meet (from AGENTS.md live-show rules): the audience windows sit on native video layers whose stacking follows creation order, so the toolkit must let us
own and order those surfaces (rule 6); rendering and layout cannot be validated by tests, only by looking at them (rule 4); nothing about the toolkit may be swapped mid-show.

## Recommended path (so the decision is not on the critical path)
1. **Keep moving the LOGIC to UI-agnostic Rust crates** (what Stage E already does: `singws-uimodel` view models, the queue/transport/KaraFun ports). This work is needed whichever toolkit wins, and it is where Python actually goes away.
2. **Treat Qt Quick as the interim UI, not the destination.** The Rust shell already exists and works; do not put new feature work into Qt-specific code where a view-model crate can hold it.
3. **Run a toolkit spike before choosing**: one page (the Library page) and one audience window (the ticker over a playing picture) in the top two candidates, looked at on screen on both Macs; measure idle CPU, startup time, text input, and the stacking behaviour.
4. **Swap the toolkit last**, page by page, behind a switch, with the Qt shell kept until every page is rehearsed.

## Decisions made by the operator (2026-11-10)
- **Windows is a goal, for sure.** So the UI toolkit must be cross-platform; native-AppKit-only is out (Win32/WinUI beside it would mean two UIs).
- **The project is to be open source.** So licence terms that are GPL-compatible or permissive are fine; this removes "commercial licence" as a blocker. (Still to check: Slint's GPLv3 / royalty-free terms against the repo's own licence.)
- **Webview question:** a webview UI CAN be written in Rust. The UI code is compiled to WebAssembly (Leptos, Yew, Dioxus) and shown in the OS webview (WKWebView on macOS, WebView2 on Windows); Tauri or Dioxus desktop wrap it, with a Rust backend. There is no Python and no Qt, and the UI source is 100% Rust, but the thing that draws it is the operating system's browser engine (itself not Rust), and a thin JavaScript glue layer (generated) exists. It counts as "Rust only" for the source tree, not for the runtime.

## Updated shortlist for the spike (not a choice yet)
| Candidate | Source is Rust | Draws with | Windows | Fit for SingWS |
|---|---|---|---|---|
| **Slint** | yes (`.slint` markup + Rust) | its own renderer (GPU or software), no webview | yes | closest to QML: the 19 pages port one by one; no browser dependency; licence terms to confirm |
| **Dioxus or Tauri + Leptos** | yes (Rust to WebAssembly) | the OS webview | yes (needs WebView2, present on current Windows) | fastest UI building and reuses web styling; but two engines differ (WKWebView vs WebView2), and webview text/animation timing is less predictable for a live show |
| **iced** | yes | wgpu | yes | pure Rust; a rewrite rather than a port; weaker rich-text/list widgets |
| egui | yes | wgpu/glow | yes | good for host tools, not for the polished show UI |

Either way the AUDIENCE windows (picture, ticker, rotation screen) stay native GPU surfaces owned by Rust (the video bridge already is), with the ticker and effects drawn on top by the chosen toolkit or by wgpu directly; the webview/toolkit question is mainly about the HOST window (the 19 pages).

**Lean (unproven):** spike Slint first (closest port of the existing QML pages, no webview), with Dioxus/Tauri as the comparison. Run the spike on both Macs and on a Windows machine before deciding.

## Honest unknowns
- Size: replacing a 68k-line PyQt app is a multi-month rewrite whatever the toolkit; the logic ports are already the bulk of the safety work.
- Rendering parity of the show-screen effects (QML shaders and particles) has not been tried on any other toolkit.
- Rich text and emoji/GIF chat, drag-and-drop lists and the waveform editor have no obvious drop-in in the Rust toolkits.

## Toolkit spike results (2026-11-10)

Code: `rust/spikes/toolkit/` (own workspace, not a member of `rust/Cargo.toml`; nothing the app ships changes). One prototype, written twice: a hero card showing live playback state (re-read from `status_snapshot.json` every second), a Ticker tab with a checkbox (custom message on/off, re-rendered through `singws-uimodel::ticker`) and a Waitlist tab (sections and rows from `waitlist_snapshot.json` through `singws-uimodel::waitlist`). Synthetic fixtures. Both were run and looked at on screen (screenshots of Ticker and Waitlist tabs for Slint, Waitlist for Dioxus); the Waitlist text is identical in both and equal to the Qt shell's row text because it is the same Rust function. Tauri was NOT built: Dioxus desktop and Tauri both draw with `wry`/`tao` (same WKWebView), so the webview numbers below apply to both; Tauri only differs in tooling (JS-side frontend by default).

Measured on the dev Mac (M1 Max, macOS 27, Rust 1.99, release, `strip`, thin LTO). Not measured: Intel Mac, Windows, startup of Dioxus (not timed), any long run.

| | Slint 1.18.1 (winit + femtovg/software, accesskit) | Dioxus 0.7 desktop (wry/WKWebView) |
|---|---|---|
| Cold build, whole prototype (clean target; includes the 23 s shared `spike-core`, 40 crates, bundled SQLite) | 59 s wall (386 s CPU) | 40 s wall (252 s CPU) |
| Crates in the dependency tree | 349 | 363 |
| Release binary | 11.6 MB | 4.0 MB (the OS supplies the engine) |
| Idle memory | 92 MB RSS, one process | 90 MB app + about 80 MB in WebKit helper processes (WebContent 38, GPU 26, Networking 16) = about 170 MB |
| Idle CPU (1 s polling) | 0.1 % | 0.0 % (app); WebKit helpers not sampled |
| Startup | 0.30 s from `main` to the event loop | window appeared within the 4 s wait; not timed |
| Source | `.slint` markup + Rust glue (56 lines of markup + 30 of Rust for this page) | RSX in Rust + CSS (about 60 lines + 10 of CSS); needed no JavaScript |
| Licence | GPLv3, or royalty-free desktop licence with attribution, or commercial; compatible with an open-source repo (check the final repo licence against GPLv3 or take the royalty-free terms) | MIT/Apache-2.0; wry/tao MIT/Apache-2.0 |
| Accessibility | accesskit integrated (feature enabled; screen-reader behaviour NOT tested) | native WebKit/DOM accessibility, the strongest of the two for text lists and forms (not tested here) |
| Windows | yes (winit; software or femtovg/skia renderers) | yes via WebView2 (Evergreen runtime, present on current Windows; installer must handle old machines) |
| Embedded video surface | See below | See below |
| Reuse of `singws-uimodel` | Direct in-process call from Rust; the page code is only layout | Direct when run as the desktop renderer (the vdom runs in the Rust process, as here). **NOT reusable if the UI is compiled to WebAssembly**: `singws-uimodel` pulls `rusqlite` (bundled C SQLite), `libc` and the file readers, so a wasm UI would need those view models split into a pure, wasm-clean crate or kept behind a Rust backend call |

### Video surface and the audience windows (the hard question)
- Today the audience picture is a native `NSView` made by the Rust video bridge (libmpv GL); rule 6 says surfaces stack in creation order. Neither toolkit removes that; both must be told to leave the video view alone.
- **Slint:** the winit backend exposes the raw window handle, so the bridge can attach its NSView to the same `NSWindow`, as it does with Qt today. Slint also has a rendering-notifier hook to draw GL/wgpu under or over the UI and (unstable) wgpu texture import, so an in-process video texture inside a Slint scene is possible without a native child view. UI drawn by Slint is one opaque GPU layer: a transparent overlay (ticker over the picture) needs a transparent window plus a second native layer. NOT tried.
- **Dioxus/Tauri:** a webview is itself a native `NSView`, so the picture and the page are two sibling views; the page can be transparent and sit above the video view (wry supports transparent webviews). Z-order is then exactly the creation-order problem the project already manages, and WKWebView compositing timing is outside our control. For animation-heavy audience effects (ticker scroll, particles) the webview is the less predictable surface for a live show (rule 4: must be looked at, and cannot be unit-tested). NOT tried.
- Audience effects that are now QML shaders have no direct equivalent in either; Slint can run them in femtovg/GL only through the notifier, the webview through CSS/WebGL.

### Findings that are not in the table
- Both ports were quick because the logic is already in `singws-uimodel`: the toolkit-specific part of this page was about 60 to 90 lines. That supports the plan: the decision is cheap to reverse as long as no rules live in the UI layer.
- Slint's build pulled a `compat-1-18` feature requirement (a forward-compatibility switch); trivial, but the project must pin the exact version.
- The Dioxus prototype needed the `launch` feature; the default `desktop` feature set pulls more (router, fullstack) than a host UI needs.
- A 4 MB vs 11.6 MB binary is not a deciding factor; the real difference is the second runtime (WebKit, WebView2) with its own processes, updates and quirks.

### Recommendation
1. **Host window (the 19 pages): Slint**, as the lean said. It is one process and one renderer on both macOS and Windows, the pages port from QML one for one, the licence fits an open-source repo, and it keeps the project "no browser at runtime". Its weaker points: smaller widget set (rich text/chat and big virtualised lists must be checked next), accessibility unproven, and the GPL/royalty-free choice must be recorded.
2. **Keep Dioxus/Tauri as the fallback** if Slint's rich-text chat, drag-and-drop lists or screen-reader support prove inadequate. Its cost is the wasm-clean split of `singws-uimodel` (or running the vdom in the Rust process as the desktop renderer, which this spike shows works).
3. **Audience windows stay native GPU surfaces owned by the video bridge**, whichever toolkit wins; settle ticker/effects there with a second spike (a transparent Slint overlay window vs wgpu directly over the picture) looked at on a real TV.
4. Before committing: repeat the spike on the Intel Mac and on a Windows machine, add the Library list (virtualised, typo-tolerant search) and the chat view (rich text, GIFs) in Slint, and test accessibility with VoiceOver. None of that was done here.

## Slint adoption trial (2026-11-10)

Code: `rust/slint-trial/` (its own workspace, not a member of `rust/Cargo.toml`; the app and the Qt shell are untouched). Crate `singws-slint`: `src/panels.rs` (toolkit-free loaders: read `queue.json`, `settings.json`, `waitlist_snapshot.json`, call `singws-uimodel::{ticker,waitlist}`; 2 unit tests, including "same ticker text as the Qt shell"), `ui/app.slint` (a `Theme` global with the QML colours, tab bar, Ticker and Waitlist pages), `src/main.rs` (3 s refresh timer like the Qt pages). Slint pinned to `=1.18.1`. Run: `cd rust/slint-trial && SINGWS_HOME=<dir> SLINT_TRIAL_TAB=0|1 cargo run --release -p singws-slint`; fixtures in `rust/slint-trial/fixtures`.

Compared on screen (`screencapture`) with the installed Qt shell on the same fixtures, both tabs. Text is identical (same Rust function). Visible differences after fixing: (1) rows first stretched to fill the scroll area (a layout inside `ScrollView` distributes spare height) — fixed with `alignment: start` and an explicit `height: inner.preferred-height`; (2) ticker font looked larger at the same px size — set 17px instead of 20px; (3) multi-line row text has slightly tighter line spacing in Slint (rows about 10% shorter); no per-Text line-height property found; (4) tab bar has 2 of 13 tabs; (5) system font differs slightly in glyph spacing. Not done: live buttons (Edit/Add/Remove/Restore), the custom-message editor, screen-reader test, Intel, Windows.

Limitations hit: `ListView` assumes uniform row height, so the waitlist uses `ScrollView` + `for` (no virtualisation; fine for tens of rows, not for the library); `std-widgets` TabWidget looks nothing like our theme, so the tab bar is custom; no CSS-like shared styling beyond a `global`; the loaders (~60 lines of file reading and status wording) are copied from `singws-shell` and should move into `singws-uimodel` so both UIs call one function.

### Migration order (after this trial)
1. Move panel loaders into `singws-uimodel` (no UI change). 2. Read-only pages: Status, Diagnostics, Now playing, Rotation, Music, EQ. 3. Settings (forms; check text input and combo widgets). 4. Library and Singer history (needs a virtualised list: test `ListView` with 130k rows and typo-tolerant search first). 5. Controls and the live buttons on Ticker/Waitlist (shared command client; port `ControlPanel`). 6. Chat (rich text, GIFs: the riskiest; keep Dioxus fallback in mind). 7. Audience windows last (below).

### Audience window / video surface
Not attempted. Plan: keep the picture as the native view owned by the video bridge; host windows are Slint. For the audience ticker/rotation screen, run a second small spike (transparent Slint window above the picture vs rendering with wgpu) and judge on the real TV; never reorder surface creation mid-show (AGENTS rule 6). Slint's winit backend exposes the raw window handle so the bridge can attach to it.

### Slint trial, Status tab (2026-11-10, later)
Status tab added (pills, status line, tooltip, ETA line, queue summary, 7 checks), loader `panels::status_view` over `singws-uimodel::status`, test passes, compared on screen with the Qt Status page. Same text and colours except: the ETA line is plain white because Slint `Text` has no rich text (the Qt page shows coloured HTML spans), so the app's coloured segments (ENDS red, ROTATION purple, START yellow) are lost — the view model would have to return segments instead of HTML before Slint can colour them (a `Row` of several `Text`s works); tooltip line spacing is tighter. Library, chat and the transparent ticker overlay are left to the toolkit-spike session.

The Ticker, Waitlist and Status loaders now live in `singws-uimodel::panels` (additive; the Qt shell still has its own copies and is untouched, so switching it to these is a follow-up). The Slint trial calls them through `pub use singws_uimodel::panels`.

### Slint trial, round 2 (2026-11-10): Rotation, Singer history, Settings, Controls
Loaders for Rotation, Singer history and the Settings viewer are in `singws-uimodel::panels` (stateless: the files are re-read each refresh, where the Qt panels cache by mtime; test `rotation_history_settings_from_files`; the secret-hiding rule is still `settings_view`). On-screen comparison with the Qt shell on shared fixtures: Rotation matches (the Qt page also has a TV window button); Singer history and Settings match in structure and text after fixes (singer rows need the detail line; the Qt left list is wider; hidden settings are amber italic); Slint rows are a few px shorter. The std `LineEdit` looked foreign, so the trial has a themed `SearchBox` (TextInput + border + placeholder, also `password` mode).
**Controls (the one interactive pattern):** `src/control.rs` drives the EXISTING `singws-control` client and view model (PIN login, relay, acks, result banner) and hops back to the UI thread with `invoke_from_event_loop` plus a Slint callback; the PIN goes to the client and is cleared from the box, only address and tenant are saved in the Qt shell's `connection.json`. The Play/Pause, Restart, Next and Stop buttons send `play_pause`, `restart`, `skip`, `stop`. Tests: not-connected send says so, view flattening, a dead server ends in a visible failure (scratch config dir). **NOT verified:** a real login and a command against a live app (needs the operator's PIN and server; deliberately not used), so the transport buttons are proven only disabled-when-offline on screen.
Not ported: **Library** (owned by the toolkit-spike session), Music, EQ, Chat, Diagnostics, Now playing, Waitlist/Ticker live buttons, the TV window button.
Slint notes from this round: `ScrollView` with a `VerticalLayout` needs `alignment: start` or rows stretch; `if` blocks cannot reference a property declared later without it existing on the component root; no rich text (the Status ETA line); a weak handle is `Send`, so a relay thread can wake the UI with `invoke_from_event_loop` and a callback.

### Slint trial, Library tab and migration status (2026-11-10, end of the trial)
**Library:** `singws-uimodel::panels::library_view` (same `search_with_fuzzy` / `providers_for` / `library_row` calls and status wording as the Qt shell; test with a real scratch SQLite index including the empty and no-match states). The Slint page uses `ListView`, which draws only visible rows (uniform 50 px rows), a themed search box, a singer box and an Add button per row that sends `queue.add_song` ({singer, song_id, key 0}) through the same control client as the Controls tab; Add is disabled unless connected and a singer is typed. Compared on screen with the Qt Library page on a 5-song scratch index: same rows, order and right-hand text (disc, format, length); two-line card rows match; the Qt page's Key spinner is NOT ported (key is sent as 0); search runs on the UI thread on each keystroke (fine for the index's millisecond answers, a fuzzy fallback can take up to 1.5 s and would need a worker thread for a 130k-song library, UNTESTED at that size). Add was not exercised against a live app.

**Status of the Slint trial (branch `claude/slint-trial-fdd2f1`, crate `rust/slint-trial/singws-slint`):**
- Ported and compared with Qt on screen: Ticker, Waitlist (read-only), Status (ETA colours lost: no rich text), Rotation, Singer history, Settings viewer, Library, Controls (login and transport proven offline only).
- Loaders for all of them are Qt-free in `singws-uimodel::panels`; the Qt shell still has its own copies (switching it is a small follow-up).
- Left: Music, EQ, Chat (rich text, GIFs: the riskiest Slint gap), Network settings, Diagnostics, Now playing, the live Ticker/Waitlist buttons and the TV-window button, the Library key spinner, a worker thread for slow searches.
- Audience window and video surface: not started; plan unchanged (native bridge view, a second spike for a transparent overlay judged on a real TV).
- Not tested anywhere: Intel Mac, Windows, VoiceOver, a live login/Add/transport against a running app.

### Slint trial, Diagnostics and Now playing (2026-11-10, last)
Both read the same `status_snapshot.json` as the Status tab (`panels::status_view` now also returns `check_rows`, `all_passed` and `hero`; test extended). Compared with Qt on screen: Diagnostics is nearly identical (the detail column starts about 24 px further right in Slint). Now playing: same card text and colours; the Slint card sizes itself around its content, while the **Qt page's card is too short and the meta and time-left lines hang below its border** (a Qt-side `implicitHeight` bug in `NowPlayingPage.qml`, left alone: the Qt shell is not touched). Still left: Music, EQ, Chat, Network settings, the live Ticker/Waitlist buttons, the TV-window button, the audience window.

### Slint trial, Music and EQ (2026-11-12)
Loaders `panels::music_view` and `panels::eq_data` (same files and wording as the Qt panels; test `music_and_eq_from_files`). Music tab: playlist search, track list with a per-row Play (`bg.play_track` with the row's own file name, so a changed playlist cannot start the wrong song), a live strip when connected (Next, Play/Pause via `bg_play_pause`, volume via `set_bg_volume`), played tracks hidden from the live `bg.index` (the 3 s refresh feeds it). EQ tab: two cards of 10 bands (-12..+12 dB) drawn from `settings.json`; connected, a drag sets a band and releasing sends the whole list with `eq.set` (enabled true, like the Qt page) and "Turn on/off" flips `enabled`. Slint has no vertical slider and its std Slider looks foreign, so `HSlider` and `EqBar` are small custom components on a `TouchArea` (about 25 lines each).
**Verified:** loader tests pass; the Music tab and the EQ tab render on screen in Slint (checked by eye, read-only/offline state). **NOT verified:** the side-by-side comparison with the Qt Music and EQ pages (the operator's screen was in use before a show, so the second set of captures failed and I stopped), drag behaviour of `EqBar`/`HSlider` with a real pointer, and any command against a live app. **Left out:** the EQ preset drop-down (the Qt page has one), the Qt hover tooltip showing a track's path.

### Slint trial, Chat and Network (2026-11-12) and final status
**Chat:** `panels::chat_data` (test `chat_rows_headers_and_hints`) turns the app's chat summary (the `chat` member of the control view, shaped by the existing `singws-uimodel::chat::view`) into a flat list: a header per conversation, then rows with the app's tags and 12-hour stamps (local UTC offset from `localtime_r`). Slint tab: Everyone / Private / Host chat buttons, plain-text message cards (own messages tinted), click a Host chat header to pick the singer, a message box and Send (`chat.say`; Everyone needs a second tap, "Sure?", like the Qt page; Private is read-only). Compared on screen with the Qt Chat page on a demo summary (`SLINT_TRIAL_CHAT_DEMO` = the Qt page's `--chat-demo`): same rows, tags, stamps and order; Slint's tab buttons are themed, Qt's are the system grey. **Not ported: pictures and GIFs** (they arrive as data-address thumbnails; Slint needs an image decoder feature and a temp file or `Image::from_rgba8` after decoding, not done) and the unread/"to review" counters on the tab buttons. Not exercised: sending against a live app.
**Network:** the Qt shell has no Network page (Network is a Python settings dialog). The part the Slint shell needs, server address, venue name and host PIN, is the Connect form on the Controls tab. The rest of the Python dialog (location detection, sync checks, the venue key field) is NOT ported and has no Qt-shell counterpart to compare with.

**Final status of the Slint trial (branch `claude/slint-trial-fdd2f1`, `rust/slint-trial/singws-slint`):** ported and compared on screen with Qt: Ticker, Waitlist (read-only), Status, Rotation, Singer history, Settings viewer, Library, Diagnostics, Now playing, Chat; Music and EQ built and seen on screen but not yet compared with Qt; Controls tab with PIN login proven offline only. All loaders are Qt-free in `singws-uimodel::panels`. Left: Chat pictures, the live Ticker/Waitlist buttons, the TV-window button, EQ presets, the Library key spinner, a worker thread for slow Library searches, the Python Network dialog, and the audience window and video surface (a transparent-overlay spike on a real TV). Never run on Intel, Windows or with VoiceOver, and no command was ever sent to a live app.

### Slint trial, Music and EQ compared with Qt (2026-11-12), and recommended next steps
Compared on screen with the Qt shell on the same files. **Music:** identical text, order, search box, hint and track rows; only the tab buttons differ (themed). **EQ:** same bands, labels, gains and values. Differences kept on purpose: Slint fills each bar from the zero line (up for boost, down for cut) where the Qt slider fills from the bottom, which reads better for a graphic EQ; the handle is a small white tab, not Qt's round knob. Differences fixed: the "OFF (flat)" wording and the left-aligned "Connect on the Controls tab" hint (rebuilt, not re-photographed). Not compared: drag behaviour with a real pointer and the connected state (needs a live app). **Network** is not a separate tab: the Qt shell has none (it is a Python dialog); its server address, venue name and PIN are the Connect form on the Controls tab, and location detection and sync checks are unported.

**Recommended next steps (in order):** (1) a short rehearsal of the Controls tab against the real app with the operator's PIN (login, play/pause, Add, EQ drag, chat send), the one thing no test covered; (2) switch the Qt shell's panels to `singws-uimodel::panels` so there is one loader; (3) Chat pictures (image decoder feature plus a temp file or decoded pixels) and EQ presets; (4) the audience window: a second spike for a transparent Slint overlay over the libmpv view (ticker and rotation screen), judged on the real TV, with surface order checked per AGENTS rule 6; (5) repeat the trial on the Intel Mac and a Windows machine, add VoiceOver checks; (6) decide when the Slint shell replaces the Qt shell for the host window (the Python app stays until the logic ports are complete).

### Slint trial, step 3 done: EQ presets and Chat pictures (2026-11-12)
EQ: a row of the app's seven presets (`eq_view::PRESETS`, the same table the Qt page offers) on each card when connected; a click sends `eq.set` with that preset's gains and enabled true. Chat: thumbnails are requested with `chat.media` (up to 6 missing, newest first, only when connected), the data address from the control view model is decoded (small base64 decoder in `control.rs`, unit-tested) to a file in the temp dir and shown with `Image` (Slint feature `image-default-formats` enabled, which pulls the `image` decoder crates). **Verified:** builds, 4 unit tests pass. **NOT verified on screen or against a live app:** neither the preset buttons nor a picture has been drawn yet (a picture only arrives from a connected app). Steps 1 (PIN rehearsal), 4 (audience overlay spike on the real TV) and 5 (Intel, Windows, VoiceOver) need the operator or other hardware and are not started; step 2 (switch the Qt shell to the shared loaders) was left alone because it edits the shipped shell, which the trial was told not to touch.

### Qt shell now uses the shared loaders for four panels (2026-11-12)
`singws-shell`'s Ticker, Settings, Music and EQ panels now call `singws-uimodel::panels` (`ticker_view`, `settings_view_data`, `music_view` with the track path added, `eq_data`) instead of their own copies, so the Qt shell and the Slint trial read the same files with the same rules and wording. Verified: the shell builds (the worktree's empty `vendor/signalsmith-*` submodule folders were filled from the main checkout to build; nothing from them is committed), its 48 tests pass (the two Ticker tests that moved into `singws-uimodel` run there), and a headless run of the new binary writes the Music and EQ views in the shape the QML reads (`--dump-music`, `--dump-eq`: index/filename/path rows, bands, presets, gains). **Not verified:** a side-by-side with the previously installed shell (its build has no dump hook), the Ticker and Settings pages on screen. **Left on their own code on purpose** because the shared loaders do not return everything the QML needs: Waitlist (edit fields), Status (HTML ETA line), Rotation (the `--now-singing` test hook and mtime cache), Singer history (selection state, edit form fields) and Library (duration, kind, tooltip). The installed `/Applications/SingWS Shell.app` is unchanged and nothing was installed.
