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
| **The search LADDER** (queries most specific first, same-query retry when KaraFun's window is not built yet, artist confirmation, one diagnosis on no match, permission errors stop everything, the error the app raises) as a pure function | `ladder.rs` | `tools/karafun_ladder_parity.py` runs the REAL Python statements (the 8 from `found = ""` to the final `raise`, read by AST) with scripted answers: 6,000 scenarios identical on found/selected query/error raised, every log line, the scripts requested with their arguments and the sleeps |
| **The completion MONITOR core** (probe parsing, playing/idle/ended evidence, clock progress, pause tracking, playback confirmation, the one recovery Play press, the 40 s operator warning, the three completion reasons, the key/tempo read decision) as a pure state machine: one `poll` per probe | `monitor.rs` | `tools/karafun_monitor_parity.py` runs the REAL Python `_start_karafun_completion_monitor` (read by AST, same replay technique as `MonitorReplay`: scripted probe answers, fake clock) against the same events: 4,000 scenarios (hand-built boundary cases plus random) identical on every log line, recovery presses, warnings, completions, the entry fields updated, key/tempo reads started, probes made and the final clock. Not modelled: the duration-watchdog timer thread, the key/tempo read thread's body, the log-only Rust transport shadow/drive plumbing, a handoff in progress |
| **Live shadow of the monitor, default ON** (`karafun_monitor_shadow`): the monitor records every pass (probe time and answer, a few entry flags) and its actions (recovery Play press, operator warning, completion, key/tempo read); when the loop ends, the run is replayed through the Rust monitor (op `monitor_replay` of the bundled `singws-karafun`) and a difference in the actions is logged by pass number | `karafun_monitor_shadow.py` + 11 hook lines in `_start_karafun_completion_monitor` (all through a recorder that cannot raise; a host without a settings dict gets a do-nothing object) | `test_karafun_monitor_shadow.py`; the real monitor with the shadow on replayed clean on 700 of 700 scripted runs; NOT seen in a real show |
| `feat.` fix (2026-11-06, operator: fix it) | `0.2.18.1.py` + `pure.rs`: `feat\\.?`/`ft\\.?` -> `feat\.?`/`ft\.?` | all five parity tools IDENTICAL after the fix; behaviour change: 'Hello (feat. X)' is now searched as 'Hello' | 
| Mutation | `tools/mutate_karafun.py` over `lib.rs`, `pure.rs`, `ladder.rs` and `monitor.rs` (after the `nonlocal playback_clock_origin` fix, 2026-11-06) | 400 mutants, 324 killed, 39 documented survivors, 37 do not compile, 0 undocumented | (2026-11-07, `readback.rs`, `watchdog.rs`, `startflow.rs` added: **511 mutants, 423 killed, 44 documented survivors, 44 do not compile, 0 undocumented**. The new files first left 14 undocumented survivors: 9 were killed by new parser boundary texts in `karafun_script_parity.py` (texts that end inside the pattern, 18/19-digit values, mixed ASCII/Unicode digits, a wrong literal, an 18-digit "must answer" assertion) and a watchdog grid in `karafun_startflow_parity.py`; 5 are documented equivalents) |
| Key/tempo readback rule, duration watchdog decision | `readback.rs`, `watchdog.rs` | `tools/karafun_script_parity.py` (readback), `tools/karafun_watchdog_parity.py` (2,500 scenarios identical) |
| Post-activation start decisions (initial probe state, play press / skip / defer, verified playing, verify attempts, auto key/tempo answer checks) | `startflow.rs` (pure, 2 unit tests) | TRANSCRIBED, not replayed: `test_karafun_startflow_rs.py` pins the Python lines. The surrounding flow (handoff ordering, sleeps, UI-thread calls) is NOT ported or replayed. |
| **Live shadow `karafun_flow_shadow` (default ON, 2026-11-07, in NO installer yet)** for three decisions: the duration-watchdog timer (extend or warn), the key/tempo readback value, the start decisions (press Play / skip / defer, assumed state, verified) | `karafun_flow_shadow.py` + 3 try/except hooks in `0.2.18.1.py` + helper op `watchdog_check` | `test_karafun_flow_shadow.py` (real helper, 7 tests); logs `[KARAFUN-FLOW-SHADOW]` field NAMES only; announce line seen in a scratch source launch; NOT seen in a real show |
| Live runner (`singws-karafun-live`, feature `live`, via `singws-mac`) | `live_main.rs` | probe and search scripts agree with Python on real KaraFun answers; the full ladder compare is state-dependent: KaraFun's Recent Searches dropdown / a modal 'remote control code already activated' dialog make every search time out, so it needs a clean KaraFun window and was NOT completed |

## Next milestones, in order

1. ~~Extract the inline scripts into pure builders in Python, port them with generate-and-compare~~ **DONE (2026-11-06), see the table above.**
   Not extracted on purpose: anything that is not a plain `[...]` list literal of strings (none left in the five big methods).
2. **Port the rest of the flow as state machines** fed with script answers. **The search ladder is DONE** (table above). What is left: the part of
   `_automate_karafun_search_and_play` after the ladder (apply the verified duration, queue for key/tempo, the hand-off ordering, play, verify, the
   12 s recovery press, the 40 s warning). **The monitor loop's core is DONE** (table above); what is left of it is the duration-watchdog timer and the
   key/tempo read body. Oracle: a replay harness that drives the real
   Python flow with a fake script runner and compares the sequence of scripts requested and the decisions logged (the same method as
   `queue_event_replay.py`; `MonitorReplay` in `test_karafun_monitor_safety.py` is the starting point for the monitor half).
3. **Live runner on this Mac:** run the Rust-built scripts through `singws_mac::run_applescript` against the real KaraFun (never during a show),
   compare answers with the Python runner on the same KaraFun state (done once by hand for the search script on 2026-11-06: same answer), and add the
   result to the readiness report.
4. **Shadow in a show:** `karafun_script_shadow` (the search script) and `karafun_monitor_shadow` (the monitor's actions) are in place, default on, awaiting a real
   show. Still to add: the other builders' calls and the search-and-play flow's decisions (like START-SHADOW). A clean show per decision (ledger policy).
5. **Capture and show-screen hand-off:** reuse the existing ScreenCaptureKit dylib (`native/karafun_capture`) from Rust through its C interface rather
   than rewrite it; the hand-off/restore of the audience window depends on the Rust audience window and needs the real TV, so it comes last and
   cannot be proven offline.
6. **Drive behind a flag** (`karafun_engine`), Python kept as the one-line fallback, after clean shows on every step above.

## Constraints that stay

- Accessibility and Automation grants belong to the calling app; the Rust shell now has its own (done 2026-11-06, `NSAppleEventsUsageDescription` added).
- macOS 27 answers 0 for the one-line `count of (entire contents of w)` but the real list for `set x to entire contents of w`: write scripts the second way.
- Nothing that moves a window or surface is changed during a show (AGENTS rules 4 and 6).
