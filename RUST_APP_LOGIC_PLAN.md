# Moving the app's logic to Rust, gradually

Status: ACTIVE (2026-10-23). Stage A0 is implemented, tested, and deployed on Apple Silicon. Signed arm64 and x86_64
installers are built and package-launched. The operator accepted the documented local CDG/MP4/KaraFun rehearsal in place
of three physical-Intel shows; it reported zero disagreements and complete coverage. A2 is complete, and A3 enables the
proven KaraFun-end decision by default with automatic Python fallback. Both signed package rehearsals passed.
This follows AGENTS.md: adapter-style, small reviewable steps, shadow before drive, a flag for every risky step, and old
code removed only after the replacement is proven.

## What "the app" is today

One Python file (`0.2.18.1.py`, ~61,000 lines) mixes four things:

1. **UI** (PyQt widgets + QML surfaces). It stays in place while the application core moves, then migrates last to a
   Rust-owned Qt Quick/QML shell so the packaged application no longer embeds Python.
2. **Decision logic** with no UI in it: when a song has really ended, when to advance the rotation, how requests move
   between states, when the next background track starts fading. These are the rules the live-show bugs came from.
3. **I/O glue**: server sync (HTTP/websocket), KaraFun control (AppleScript/Accessibility, macOS only), files, settings.
4. **Stores**: queue, history, stats, settings JSON/SQLite.

Only (2) is worth moving first: it is pure, testable, shared by any future Windows port, and is where regressions hurt.
(3) and (4) follow only if there is a reason (a Windows port). (1) stays.

## Stage A: song lifecycle machine (already built, in shadow)

`rust/crates/singws-transport` (300 lines): pure `step(state, event) -> (state, commands)`; events StartRequested,
PlayingObserved, IdleObserved, EndClockObserved, MediaEnd, StopRequested, ManualComplete, WatchdogExpired, CleanupDone;
commands BeginPlayback, RetryResult, MarkPlaying, StartBgHandoff, CompleteSong, AdvanceRotation, WarnOperator, Ignore.
Python feeds it through `transport_shadow.py`; it only logs (`[TRANSPORT-SHADOW]`, `MISMATCH`), and
`tools/transport_shadow_report.py` gives the acceptance verdict (needs >= 3 shows, every one with 0 disagreements).

**A0 (complete in source and deployed on Apple Silicon; physical-Intel install pending): make the shadow actually collect data.** Finding 2026-10-15: the dev Mac profile has
`"transport_shadow": false` saved from the earlier rehearsal instructions, which overrides the new default (on), so this
Mac has recorded 0 songs (the report says "no shadow sessions"). The Intel Mac's profile is unknown (its show log is no
longer available to check). Fix options: (a) operator deletes the key from settings.json on both Macs (shadow is log-only, safe);
(b) a launch-time migration enables a saved `false` once and records a migration marker. Option (b) shipped in commit
`dd1f077`; later operator changes remain respected. Count shows with the report tool before Stage A1.

**A1 (local gate complete by operator decision 2026-10-23): fix what the shadow finds.** Every MISMATCH is either an app
bug or a machine rule to fix. Local exit evidence: three isolated show logs, five sessions, 0 disagreements, covering real
native CDG/MP4 transport plus simulated KaraFun lifecycle, seek-near-end, manual Complete, stop, and watchdog. Physical
Intel performance remains unmeasured; see `docs/verification/2026-10-23-rust-transport-shadow-a0.md`.

**A2 (local gate complete 2026-10-23): let it DRIVE one decision, behind a flag.** New setting `transport_drive` (default off). Start with the single
decision that has hurt before: "has this external (KaraFun) song really ended?" (the 2026-08-31 false completion).
The Python executor still performs the action; the old Python decision stays in place and is compared on every call
(log any disagreement; on disagreement keep the OLD answer). The local harness explicitly proved agreement selects Rust
and both disagreement directions select Python, while the three-show lifecycle matrix remained clean. Then the same for
native media end, then watchdog, then stop/skip.

**A3 (local and package gates complete 2026-10-23): flip the default, one decision at a time, each after a clean show. A4: delete the old Python decision code.**
The default is now on only for KaraFun automatic-end detection; both answers are still computed, and Python remains the
automatic disagreement fallback. Signed arm64 and x86_64 DMGs passed isolated-profile clean-launch/clean-exit smoke tests.
Rollback at every step: `transport_drive: false`.

## Stage B: queue / rotation / request lifecycle (the biggest win, the biggest risk)

The rules already written in AGENTS.md ("Queue and server synchronization rules": host authoritative, permanent request
IDs, requests transition state and are never recreated, tombstones stop resurrection, oldest duplicate wins) are exactly
a state machine. New crate `singws-queue`: state = rotation + requests with ids; events = server snapshot, host action
(delete/reorder/replace/move), request state change, reconnect, restart restore; output = the minimal list of changes.

Method (do not skip): (1) build a **replay harness** from real data first: the app's `[REQUEST-DIAG]`, `[WAITLIST-STATE]`,
server-sync lines and the PHP-side fixtures already used in the history/queue tests; (2) property tests for the invariants
("a host-deleted request never returns", "ids never change", "duplicates keep the oldest"); (3) shadow it exactly like Stage A
(log disagreements, never act); (4) drive behind `queue_drive`, host actions first (smallest surface), server pulls last.
This touches live queue and singer data, so every step is rehearsed on the scratch profile and reviewed with real traces
before it is allowed near a show. Do not start until Stage A2 has run a clean show.

**B1 queue-rules shadow (2026-10-23, DONE for now):** pure `rust/crates/singws-queue` (poll-row admission, title-conflict rule,
oldest-wins, reorder permutation check, executable `Queue` invariants with property + mutation testing), `queue_shadow.py`, setting
`queue_shadow` (default OFF; enabled in the dev Mac's live profile for the next show). Log-only: the app's answer is always used.
Parity over the real logs: 0 disagreements. Evidence and the pre-flip show checklist are in
`docs/verification/2026-10-23-rust-transport-shadow-a0.md`. Deliberately NOT done (needs real show evidence first): flipping
`queue_shadow` on by default, driving host actions from Rust (`queue_drive`), the remaining decision sites (host delete/reorder/
replace/move, server pulls) and the slot-lifecycle model. Slot expiry must follow the persistence decision below.

**B0 replay harness (started 2026-10-23, `tools/queue_replay.py`, `test_queue_replay.py`).** Parses the real show logs
(`[REMOTE-TOMBSTONE]`, `[REQUEST-LIFECYCLE]`, `[QUEUE-DEDUP]`, `[QUEUE-INSERT]`, `[HOST-REQUEST-SYNC]`) into a normalized event
corpus (`--corpus out.jsonl`) and checks three invariants against what the Python app actually did: RESURRECTION (a
host-removed/completed id never returns from the server), DEDUP_OLDEST (the kept duplicate has the smaller epoch-ms id), and
ID_STABLE (a host request key never maps to two ids). First run over 151 logs: 0 violations. Findings that shape the rest of B:
* ~89% of queue events in the live logs are unit-test noise (`request_id` < 1e12 written by tests into the real log). The
  harness drops and counts them; the real corpus is only 169 request ids, 92 tombstones, 1,301 resurrection-guard hits.
* DEDUP_OLDEST and the id-reuse path have NO real events yet, so their clean result proves nothing; the harness says so.
* B0b (2026-10-23): the harness now also parses `[QUEUE_SYNC_AUTHORITY]`, `[QUEUE-AUDIT]`, `[ROTATION-SLOT]`, `[QUEUE-STATE]` and
  `[SERVER-STATE]` and checks host-action invariants: AUTH_PERMUTATION (a remote reorder never adds/drops ids), AUTH_HOST_ORDER
  (an applied host order is honoured), AUTH_ECHO_NOOP, SLOT_FILL (a slot fill follows a preserve, never a host removal) and
  ACCEPTING_HOST (accepting-requests mismatches always repair server-to-local). Violations carry an origin (real / test /
  ambiguous / unverifiable); test and unverifiable ones are reported but do not fail the run. Result over 151 logs: 0 failing
  violations; 1 unverifiable slot fill (the 2026-07-12 log that preserved the slot is missing). Coverage is uneven: 1,058 accepting
  repairs (all server-to-local) and 2,684 slot fills back their checks, but only 2 real reorder events exist (almost all
  QUEUE_SYNC_AUTHORITY data is test-origin), `same_item` is False in all 4,978 QUEUE-STATE lines so it supports no invariant,
  and dedup / id-reuse have no real events.
* DECIDED (owner, 2026-10-23): state persists across an app restart so a crash never loses data. Preserved singer slots are
  restored at startup and are NOT expired by their `timeout_sec=180` just because the app was down (seen 2026-07-14 23:58 preserve,
  2026-07-15 00:07:52 restore). The Rust queue must model restore as a no-op on persisted state (the `Restart` event already is),
  and any slot expiry has to be driven by live elapsed time while the app is running, never by wall-clock time across a restart.
* Still to do before the pure `singws-queue` crate: real host reorder/replace/move traces (a rehearsal session on the scratch
  profile would generate them), the PHP-side fixtures already used by the queue tests, and the test-log isolation fix (DONE 2026-10-23: `_test_profile.py` gives every test process a private, auto-deleted
  `SINGWS_HOME`; 59 test modules import it, and `test_test_isolation.py` fails any new test that loads the app without isolating).
  Before the fix ~52 test modules, when run directly with `python -m unittest`, wrote into the live `~/SingWS` profile: the show
  log plus `host_request_sync.json`, `loudness.json`, `loudness.checkpoint.jsonl`, `transition-analysis.json` and
  `waitlist_removed_tonight.json`. Existing live logs still contain that noise; the replay harness filters it by request id.
* Separate bug worth fixing: unit tests write into the live `~/SingWS/logs`; they should use an isolated `SINGWS_HOME`.

## Stage C: background-music transition planning (small, pure)

"When does the next track start fading, how long, from which offset" is already isolated in Python
(`transition_analysis`, the scheduler maths). A pure Rust `plan_transition(metadata, now) -> plan` shadowed against Python on
real playlists is a one-session job and the Rust BGM engine already consumes the result. Low risk, low urgency.

## Stage D: stores and server client

History/stats/settings persistence and the HTTP/websocket client (`reqwest`/`tokio`). This becomes necessary for the
no-Python package, but remains behind the existing storage/network adapters until replay and compatibility tests pass.
KaraFun control stays macOS-native (AppleScript/Accessibility) whatever the language; Windows would need a different mechanism.

## Stage E: Rust-owned UI and no-Python package

Move the host UI to Qt Quick/QML with Rust exposing the application model and commands. Keep libmpv, the native macOS
video bridge, Signalsmith C++, and platform APIs; "no Python" does not mean rewriting proven native libraries. Port one
panel at a time behind a shell selector, beginning with read-only views and ending with live queue/transport controls.
The PyQt shell remains the rollback path until the Rust shell completes the Intel and Apple Silicon rehearsal matrix.
Exit: the signed application contains no Python interpreter, Python modules, PyQt, or PyInstaller bootloader, and passes
the same show acceptance checklist on both architectures.

## What each stage buys, and what it does not

* Buys: rules testable without a show, one implementation for Mac and Windows, fewer GIL/GUI-thread dependencies in
  decisions, and the bugs of 2026-08-31 (false completion) and 2026-08 (resurrected songs) become unit tests.
* Does not buy: speed (decisions are microseconds already), a different look, or fewer lines overnight.

## Order and gates

| Stage | Needs first | Flag | Exit |
|---|---|---|---|
| A0 shadow collecting | operator removes `transport_shadow: false` (or migration) | `transport_shadow` | report shows watched shows |
| A1 fix mismatches | >= 3 shows | none | 0 disagreements in 3 shows |
| A2 drive one decision | A1 | `transport_drive` (off) | a clean show with compare logs |
| A3/A4 flip + delete | A2 | same | one clean show per decision |
| B queue/rotation | A2 clean show, replay harness | `queue_drive` (off) | invariants + replay + shadow, then host actions first |
| C transition planner | none | `bgm_plan_engine` | parity on real playlists |
| D stores/network | B/C state ownership proven | adapter selectors | persistence/network compatibility corpus passes |
| E Rust UI/package | A-D APIs stable | shell selector during migration | no Python in bundle; full two-architecture show acceptance |

## Immediate next step

Native media end now has its own A2 drive behind `transport_drive_media_end` (default OFF, uncommitted source change 2026-10-23): Rust answers only when it agrees with the retained Python oracle, Python wins every disagreement, and each call logs `[TRANSPORT-DRIVE] decision=media_end`. It still needs its clean-show/package gates before the default flips. The KaraFun watchdog decision is wired the same way behind `transport_drive_watchdog` (default OFF, `decision=watchdog` logs). Stop/skip is deliberately SHADOW-ONLY with no drive flag (owner decision 2026-10-23): a stop is an explicit operator command, there is no Python rule to compare, and a Rust drive could only veto the operator mid-show. The shadow keeps logging mismatches for it. The transport drive decisions are therefore complete in source; their default flips wait on real-show `[TRANSPORT-DRIVE]` logs.

Next: Stage B, starting with the queue/rotation replay harness built from real logs (no Rust state machine until the harness exists). Preserve comparison and
Python fallback until each decision has passed the same local and two-architecture gates. Only then start A4 removal of
duplicated Python lifecycle decisions. See `docs/handoff/2026-10-23-rust-migration.md` for the continuation checklist.
