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
