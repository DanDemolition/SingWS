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

### Decision (operator, 2026-11-10): "go with your recommendation"
The host-window toolkit is **Slint**; Dioxus/Tauri stays the fallback. Audience windows stay native GPU surfaces owned by the video bridge. Still open before any port starts: the Slint licence choice (GPLv3 vs royalty-free terms) recorded against the repo licence, and the follow-up checks in recommendation 4 (Intel Mac, Windows, virtualised Library list, rich-text chat, VoiceOver). Nothing in the shipped app changes yet; Qt Quick stays the interim UI.

### Licence decision (operator, 2026-11-10)
SingWS will be released under **GPLv3**, and uses **Slint under its GPLv3 terms** (no royalty-free attribution licence, no commercial licence). Still to do: add the `LICENSE` file and per-crate `license` fields when the repo is opened, and re-read Slint's current licence page then. The bundled libmpv/Signalsmith/other third-party licences need the same GPL-compatibility check before publication.

## Slint follow-up checks (2026-11-10, after the decision)
Code: same spike (`rust/spikes/toolkit/slint-proto`), new Library and Chat tabs. The other linked session (slint-trial) was told not to duplicate these three checks.

**1. Library page: PASSED on screen (looked at it).** Virtualised `ListView` over a SYNTHETIC 130,000-row index (`genlib`, same columns the app's search reads; the real library is on the Intel Mac), searched through the real `singws-uimodel::search_with_fuzzy`, with a per-row "+" button (click handler wired, not clicked). Idle memory with 20,000 rows in the model: 114 MB (97 to 103 MB with a few thousand).

| Query | Rows | Time |
|---|---|---|
| `queen` | 8,125 | 31 ms |
| `valerie 99` | 507 | 17 ms |
| `a` (capped at 20,000) | 20,000 | 98 ms |
| `quen radyo` (typo-tolerant fallback) | 2,708 | 291 ms |
| `zzzzqq` (no match, full fuzzy scan) | 0 | 363 ms (the 400 ms budget) |

Search runs on the UI thread in the spike; the fuzzy cases should move to a worker thread in the real page. Typing into the search box was not exercised (the query was injected at start); text input in Slint's `LineEdit` is unverified for IME and emoji.

**2. Rich-text chat: BUILT, NOT YET SEEN.** The window was covered by another app when the screenshot was taken (the operator was using the screen), so this tab has only been compiled and run (95 MB, 1.6 to 4 % CPU with a 10 fps GIF timer), not looked at. What the code does and what Slint 1.18 offers:
- `StyledText` (CommonMark subset: bold, italic, strike, inline code, links, lists, `<u>`, `<font color>`), fed from Rust with `StyledText::from_markdown` (currently through `slint::private_unstable_api`, i.e. not a stable API). Unsupported in it: images inside text, headings, tables, block quotes. So the Qt chat's HTML bubbles map to markdown plus separate `Image` elements; GIFs and pictures are separate elements below the text, which is how the chat already attaches them.
- Pictures: `slint::Image` from RGBA pixel buffers (works).
- Animated GIF: Slint has no animated-image element; the prototype decodes frames with the `image` crate and swaps the row's image from a `Timer` at 10 fps (a real GIF built and decoded in memory). A chat with many visible GIFs needs a shared timer and frame caching.
- Colour emoji rendering, link clicking and text SELECTION/copy inside `StyledText` are unverified; text selection is not a documented feature of `Text`/`StyledText`.

**3. Transparent ticker overlay over a libmpv picture: NOT DONE.** It opens real windows over the live picture and needs the screen free (and a real TV to judge stacking). Pending.
**4. VoiceOver, Intel Mac, Windows: NOT DONE.** Pending.

Net: step 1 removes the biggest widget risk. Step 2 stays the open risk (emoji, selection, many GIFs); if it fails, that single page can be the one place a webview component is considered.
