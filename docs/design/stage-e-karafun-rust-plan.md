# KaraFun automation in Rust: state and plan (2026-11-06)

Goal: the Rust shell can search, start, monitor and end a KaraFun song without Python, proven the way every other port was: Python is the oracle,
the Rust side is verified offline first, then runs as a log-only shadow in real shows, then drives behind a flag after clean shows.
Nothing below changes how the Python app drives KaraFun today.

## Done (all offline, 0.2.18.1.py untouched except one log-only hook)

| Piece | Where | Proof |
|---|---|---|
| Search AppleScript builder (all flag combinations) | `singws-karafun` `search_script`; fixed lines GENERATED from the Python by `tools/gen_karafun_script_rs.py` (`--check` guards drift) | `tools/karafun_script_parity.py`: 6,096 scripts identical |
| Title qualifier stripping, AppleScript literal quoting | `lib.rs` | same tool: 2,892 titles, 5,488 literals identical |
| Duration clock parsing (`3:41`, `1:02:03`, seconds) | `pure.rs` | 1,568 values identical (Unicode digits are declined on purpose) |
| Remaining-time and key/tempo "needs attention" rules | `pure.rs` | exhaustive small grids identical |
| Search query ladder (artist title, title artist, title, artist) with the title/artist cleaning | `pure.rs` (verbatim regexes, incl. the `feat\\.?` quirk) | 1,663 cleaned strings and 1,740 entries identical |
| Accept/reject of one search answer (FOUND / TITLE_ONLY / FIRST, artist confirmation) | `pure.rs` | 1,080 cases against a TRANSCRIBED copy of the inline Python rule (source guard in `test_karafun_script_rs.py`) |
| Live shadow, default ON: every search script the app builds is also built by the Rust helper and a difference is logged | `karafun_script_shadow.py`, helper `singws-karafun` bundled by both installers | `test_karafun_script_shadow.py`; NOT seen in a real show yet |
| **All 19 inline KaraFun scripts moved into static builders** in `0.2.18.1.py` (no behaviour change): preflight, window state, play press, playback menu state, raise results, read/apply key-tempo, seek, play/pause toggle, result double-click, show-screen hand-off and restore, click point, play-now queue, key/tempo menu adjust, renderer ready, start probe, set key/tempo sliders, completion-monitor probe | `tools/karafun_extract.py apply` (AST-driven, nothing hand-edited) | `tools/karafun_extract.py check d2d9975`: every moved list has the IDENTICAL syntax tree as the inline list in the commit before; every call site and parameter list checked; 27 call sites have `self` in scope |
| Rust twins of all 19 builders + the search script + the diagnosis script + the preview probe, GENERATED from the Python | `rust/crates/singws-karafun/src/builders.rs` (`--check` guards drift) | `tools/karafun_script_parity.py`: every fixed script and every parametrised one (key/tempo grids, seek counts, coordinates, hand-off rectangles) identical to the Python builder |
| Mutation | `tools/mutate_karafun.py` over `lib.rs` and `pure.rs` | 133 mutants, 111 killed, 16 documented survivors, 6 do not compile, 0 undocumented |

## Next milestones, in order

1. ~~Extract the inline scripts into pure builders in Python, port them with generate-and-compare~~ **DONE (2026-11-06), see the table above.**
   Not extracted on purpose: anything that is not a plain `[...]` list literal of strings (none left in the five big methods).
2. **Port the search-and-play flow as a state machine** fed with script answers (search ladder, UI-not-ready retries, diagnosis on no match,
   play, verify, recovery press at 12 s, warn at 40 s). The scripts and the accept/reject rule are done; what is left is the sequencing and timing in
   `_automate_karafun_search_and_play` (899 lines) and `_start_karafun_completion_monitor` (578). Oracle: a replay harness that drives the real
   Python flow with a fake script runner and compares the sequence of scripts requested and the decisions logged (the same method as
   `queue_event_replay.py`; `MonitorReplay` in `test_karafun_monitor_safety.py` is the starting point for the monitor half).
3. **Live runner on this Mac:** run the Rust-built scripts through `singws_mac::run_applescript` against the real KaraFun (never during a show),
   compare answers with the Python runner on the same KaraFun state (done once by hand for the search script on 2026-11-06: same answer), and add the
   result to the readiness report.
4. **Shadow in a show:** extend `karafun_script_shadow` from the search script to every builder call, then to the flow's decisions (like START-SHADOW).
   A clean show per decision (ledger policy).
5. **Capture and show-screen hand-off:** reuse the existing ScreenCaptureKit dylib (`native/karafun_capture`) from Rust through its C interface rather
   than rewrite it; the hand-off/restore of the audience window depends on the Rust audience window and needs the real TV, so it comes last and
   cannot be proven offline.
6. **Drive behind a flag** (`karafun_engine`), Python kept as the one-line fallback, after clean shows on every step above.

## Constraints that stay

- Accessibility and Automation grants belong to the calling app; the Rust shell now has its own (done 2026-11-06, `NSAppleEventsUsageDescription` added).
- macOS 27 answers 0 for the one-line `count of (entire contents of w)` but the real list for `set x to entire contents of w`: write scripts the second way.
- Nothing that moves a window or surface is changed during a show (AGENTS rules 4 and 6).
