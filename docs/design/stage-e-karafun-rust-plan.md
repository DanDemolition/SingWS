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
| Mutation | `tools/mutate_karafun.py` over `lib.rs` and `pure.rs` | see HANDOFF for the counts |

## Why the other scripts are not ported yet

About 25 call sites run KaraFun scripts (`_run_karafun_applescript_sync`), but all except the search script and the diagnosis script are
inline lists inside very large methods (`_automate_karafun_search_and_play` 899 lines, `_start_karafun_completion_monitor` 578, the hand-off pair
~420). Porting them faithfully means first pulling each list out of its method into a pure builder, which edits the live Python on the show path.
That refactor is the real next step and has its own risk, so it gets its own milestone, a quiet window, and a test that the extracted builder
returns exactly the lines the inline code built.

## Next milestones, in order

1. **Extract the inline scripts into pure builders in Python (no behaviour change).** One method at a time, smallest first: main-window state,
   play-control press, playback menu state, results-window raise, key/tempo read and apply, seek, result activation, the completion monitor.
   Each extraction ships with a test that the builder output equals the old inline text (capture the old lines once, compare). Do it between shows.
2. **Port each extracted builder** with the same machinery: generator output + parity tool + mutation. The monitor script and its answer parsing
   (`playing`, idle, clock, end hints) are the most valuable; they feed the `singws-transport` KaraFun decisions that are already shadowed.
3. **Port the search-and-play flow as a state machine** fed with script answers (search ladder, UI-not-ready retries, diagnosis on no match,
   play, verify, recovery press at 12 s, warn at 40 s). Oracle: a replay harness that drives the real Python flow with a fake script runner and
   compares the sequence of scripts requested and decisions logged (the same method as `queue_event_replay.py`).
4. **Live runner on this Mac:** run the Rust-built scripts through `singws_mac::run_applescript` against the real KaraFun (never during a show),
   compare answers with the Python runner on the same KaraFun state, and add the result to the readiness report.
5. **Shadow in a show:** the Rust flow decides in parallel with Python and logs differences (like START-SHADOW). A show per decision (ledger policy).
6. **Capture and show-screen hand-off:** reuse the existing ScreenCaptureKit dylib (`native/karafun_capture`) from Rust through its C interface rather
   than rewrite it; the hand-off/restore of the audience window depends on the Rust audience window and needs the real TV, so it comes last and
   cannot be proven offline.
7. **Drive behind a flag** (`karafun_engine`), Python kept as the one-line fallback, after clean shows on every step above.

## Constraints that stay

- Accessibility and Automation grants belong to the calling app; the Rust shell now has its own (done 2026-11-06, `NSAppleEventsUsageDescription` added).
- macOS 27 answers 0 for the one-line `count of (entire contents of w)` but the real list for `set x to entire contents of w`: write scripts the second way.
- Nothing that moves a window or surface is changed during a show (AGENTS rules 4 and 6).
