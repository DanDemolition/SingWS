# Rust transport shadow A0 verification — 2026-10-23

Commit: `c762835` (`Enforce transport shadow scenario coverage`), after the one-time saved-profile migration in
`dd1f077`.

## What changed

- Saved profiles are migrated once to enable the log-only Rust transport shadow. A later operator change is respected.
- Shadow logs identify native CDG, native MP4, and KaraFun sessions; record the first occurrence of lifecycle events;
  record seeks and whether they land within 30 seconds of the end; and distinguish manual Complete from automatic end.
- `tools/transport_shadow_report.py` cannot print `ACCEPTANCE MET` unless at least three watched show logs are clean and
  the aggregate logs cover native CDG, native MP4, KaraFun, seek-near-end, and manual Complete.
- The Rust state machine still cannot drive playback. This stage changes observation only.

## Automated evidence

- `test_transport_shadow` + `test_transport_shadow_report`: 24 passed.
- `test_recent_regressions`: 152 passed.
- Python compilation and `git diff --check`: passed.
- Rust `singws-transport` and FFI tests from the preceding A0 build: passed.

## Packages

### Apple Silicon

- File: `SingWS-1.0.1.4-arm64-installer.dmg`
- SHA-256: `21f9769607a6a003a7861efa2126176c8c7064a4c43fd68ece362345998a13cb`
- Build checks: 363 Mach-O files include arm64; 703 files satisfy the configured minimum-OS verifier; bundled mpv core
  loads; DMG checksum valid.
- Installed at `/Applications/SingWS.app`; packaged scratch-profile launch passed and initialized the Rust engines.
- Rollbacks: `/Applications/SingWS-1.0.1.4-pre-rust-a0-20261023.app` and
  `/Applications/SingWS-1.0.1.4-rust-a0-pre-coverage-20261023.app`.

### Intel

- File: `SingWS-1.0.1.4-x86_64-installer.dmg`
- SHA-256: `76bc4b105176c779d3b2a25a6091c3a7c19b9e3d122e6ae7c484ffb38a31dc42`
- Build checks: 367 Mach-O files include x86_64; 707 files have minimum macOS <= 12.3; bundled mpv core loads under
  x86_64; strict local-identity signing and DMG checksum passed.
- Rosetta scratch-profile launch passed as x86_64: Rust BGM ready, Rust analysis selected, transport-shadow migration
  logged, and application startup completed in 6,682 ms.
- This is correctness/package evidence only. Performance and show acceptance still require the physical Intel venue Mac.

## Physical-Intel gate

Install the Intel DMG away from show start, retain the current app as rollback, and run three representative shows. Across
the three logs, exercise native CDG, native MP4, KaraFun, one seek within 30 seconds of the end, and one manual Complete.
Then run:

```bash
python3 tools/transport_shadow_report.py show-1.log show-2.log show-3.log
```

Do not enable `transport_drive` or remove the Python decision path until the report prints `ACCEPTANCE MET` and every
reported disagreement has been investigated.

## Operator-approved local substitute — 2026-10-23

The operator explicitly chose a local acceptance rehearsal instead of waiting for three physical-Intel shows.

- Real Sound Choice CDG+MP3 and generated MP4 passed `test_karaoke_transport_integration` through the real native video
  bridge and Rust karaoke voice: start, key +2, tempo 125%, seek, pause/resume, and stop. CDG worst observed A/V clock
  error was 72 ms; MP4 worst was 19 ms (limits: median <80 ms, worst <300 ms).
- `tools/local_transport_acceptance.py` produced three isolated show-shaped logs and five lifecycle sessions covering
  native CDG, native MP4, KaraFun automatic end, near-end seek, manual Complete, manual stop, and watchdog. Result:
  three clean shows, zero disagreements, complete coverage, `ACCEPTANCE MET`.
- The installed arm64 application and signed x86_64 DMG application both launched with isolated profiles and exited via
  `SINGWS_SMOKE_EXIT_MS=9000` with exit code 0 and `[LAUNCH] clean shutdown`. Intel ran under Rosetta as x86_64.

This satisfies the operator-approved A0/A1 local gate. It is functional/correctness evidence, not a physical-Intel
performance measurement. A2 may begin behind `transport_drive: false`; the old Python decision must win every disagreement.

## A2 drive-one-decision local gate — 2026-10-23

The KaraFun automatic-end decision is now wired through `transport_drive`, which remains default off. The Rust answer is
selected only when it agrees with the retained Python oracle; either disagreement direction automatically uses Python and
is logged as `[TRANSPORT-DRIVE]`.

- All 152 recent regressions passed.
- All 22 transport-shadow unit and wiring tests passed.
- The local three-show/five-session lifecycle matrix again reported zero disagreements and complete required coverage.
- The acceptance harness exercised the enabled drive selector against the real Rust lifecycle result, then explicitly
  proved both disagreement directions return the Python answer.

This satisfies the operator-approved A2 local gate. The rollback remains `transport_drive: false`; no Python oracle code
has been removed.

## A3 default-on source gate — 2026-10-23

`transport_drive` now defaults on for the single KaraFun automatic-end decision. The selector and Python oracle are
unchanged: agreement uses Rust, and any disagreement or unavailable Rust answer uses Python. Setting the flag false returns
immediately to Python-only decisions.

- Python compilation checks passed.
- All 26 transport-shadow/report tests passed.
- All 152 recent regressions passed.
- The three-show/five-session local lifecycle acceptance again reported zero disagreements and complete coverage.

No Python decision code has been removed.

The signed-package A3 gate also passed:

- arm64 DMG SHA-256: `baec6d84e2436c46a8ddf0fc06e4317e54d2ebd410368afddd86ebaa60d0379d`
- x86_64 DMG SHA-256: `e6526df8a3b00a7a1cb101ba020ec82f364ddf742bb8f794e7ad8a81bd8b440a`
- Both exact DMG applications launched with isolated profiles and exited automatically with exit code 0 and
  `[LAUNCH] clean shutdown`. Observed startup was 956 ms arm64 and 1,372 ms on the successful Intel/Rosetta run.

This completes the operator-approved A3 local gate.

## A2 native media-end drive local gate — 2026-10-23

`transport_drive_media_end` (default off) routes the native media-end decision through the Rust machine; Python is the
retained oracle and wins every disagreement. `TransportShadow.observe()` now returns whether the machine commanded
CompleteSong (`None` = no answer, so Python decides).

- 30 transport-shadow/report tests, 152 recent regressions, and `tools/local_transport_acceptance.py` passed
  (3 clean shows, 0 disagreements, full scenario coverage).
- `test_karaoke_transport_integration` passed (CDG worst steady clock error 57 ms, MP4 20 ms).
- Rebuilt signed DMGs, each launched from the mounted image with an isolated profile and `transport_drive_media_end: true`,
  `SINGWS_SMOKE_EXIT_MS=9000`: exit 0 and `[LAUNCH] clean shutdown`. Signature authority `SingWS Local Code Signing`,
  `codesign --verify --deep --strict` OK, `hdiutil verify` OK, arm64 minimum <= macOS 13.0 (gate 14.0), x86_64 (run
  under Rosetta) minimum <= macOS 12.3.
  - arm64 `a4188f49d3021a777ce03f05fed38dc95265c945d35a1ac1eb1fb136465c2309`
  - x86_64 `97e58f9bf9c310a5d19e43703ed26632ae3e8864915f7cdc21308529626abe82`
  - The A3 DMGs are kept in `local-installs/a3-checkpoint-1.0.1.4/`.
- Not yet done: the default flip, which still needs a clean show with `[TRANSPORT-DRIVE] decision=media_end` logs.
  The smoke launch plays no song, so it does not exercise the decision.

Environment note: on this Mac (macOS 27.2) every file inside `.venv-build-arm64` and `.venv-build-intel-rosetta` had the
`hidden` file flag, which made Qt's directory listing skip its plugins ("Could not find the Qt platform plugin").
`chflags -R nohidden <venv>` fixes it; the cause of the flag is unknown.

## A2 KaraFun watchdog drive local gate — 2026-10-23

`transport_drive_watchdog` (default off) routes the "end unverified, tell the operator to press Complete" decision through
the Rust machine (`WarnOperator`); Python is the retained oracle and wins every disagreement. `TransportShadow.observe()`
gained an `expect` argument so a caller can ask for a specific command.

- 33 transport-shadow/report tests, 152 recent regressions, `tools/local_transport_acceptance.py` (3 clean shows, 0
  disagreements) and `test_karaoke_transport_integration` (CDG worst steady clock error 60 ms, MP4 14 ms) passed.
- Rebuilt signed DMGs, each launched from the mounted image with an isolated profile and both
  `transport_drive_media_end` and `transport_drive_watchdog` on: exit 0, 3 `[LAUNCH] clean shutdown` lines, 0 tracebacks,
  `codesign --verify --deep --strict` and `hdiutil verify` OK; x86_64 ran under Rosetta.
  - arm64 `26216ad760f07c92b7bcb8fd211f30f30d40be141935bfafa248c72123a6e635`
  - x86_64 `56a227e7337021ea89a8a120e454e39f57fe2592c1a1004c87759ab6126d7e99`
  - The media-end-gate DMGs are kept in `local-installs/media-end-gate-1.0.1.4/`.
- Not yet done: default flip (needs a real show with `decision=watchdog` logs; the smoke launch plays no song).

Environment note: the `hidden` flag on the build venvs returned after the rebuilds (675 files in `.venv-build-arm64`),
probably re-applied by iCloud's file provider in `~/Documents`. Run `chflags -R nohidden` on the venvs before
`test_karaoke_transport_integration`, or move the build venvs outside `~/Documents`.

## Stage B queue-rules shadow local gate — 2026-10-23

New pure crate `rust/crates/singws-queue` (poll-row admission, title-conflict rule, oldest-wins, reorder-is-permutation, and a small
`Queue` model), exported as `singws_queue_admit_json` from `libsingws_dsp_ffi`. `queue_shadow.py` + `queue_shadow` setting (default
OFF) compare the app's four poll-row decisions (`id_reuse`, `tombstone`, `terminal_reuse`, `terminal_stale`) with Rust and LOG
`[QUEUE-SHADOW]` lines; the app's own answer is always used. Stage B does not drive anything.

- `cargo test -p singws-queue`: 12 tests incl. property tests (host removal never undone across restarts, ids never change,
  terminal stays terminal, oldest wins). Mutation check: 6 deliberately broken rules were all caught (one equivalent mutant
  needed an extra invariant assertion).
- Real-log parity (`tools/queue_parity.py`, 151 logs): current rule, real ids: 0 disagreements over 32 tombstone decisions and
  1,269 weak terminal-id decisions. Including unit-test traces: 0 disagreements over 635 tombstone and 672 id-reuse decisions.
  Decisions before the rule change (commit 62e7452, 2026-07-10 11:46) are counted, not compared.
- Differential: 4,000+ random title pairs (unicode, punctuation, empty) agree between Rust and the app's own
  `_tombstone_signature_conflicts` (extracted from the source).
- 232 Python tests (queue shadow/replay/isolation/transport/regressions), local transport acceptance, and the real CDG/MP4
  integration test passed; the live `~/SingWS` log did not grow during the run.
- Signed DMGs rebuilt with the queue-enabled dylibs (rebuilt with `rust/build_dsp.sh`, both architectures), each launched from the
  mounted image with an isolated profile and `queue_shadow`, `transport_drive_media_end` and `transport_drive_watchdog` on: exit 0,
  3 clean-shutdown lines, 0 tracebacks, codesign/hdiutil OK, bundled dylib exports both functions, `queue_shadow` module bundled.
  - arm64 `65a4fb104dd2a93b21b8142d0f6188982d135d87f4aca223ab04f2e63f6466ea`
  - x86_64 `d1d6d63b564881c35192e4f3e2df070cfae409a12a6de15de5d59b290a86f44d`
  - Previous dylibs: `local-installs/pre-queue-dsp-dylibs/`; watchdog-gate DMGs: `local-installs/watchdog-gate-1.0.1.4/`.
  - The app installed on the dev Mac is byte-identical to the arm64 DMG's app.
- Live profile (2026-10-23): `queue_shadow`, `transport_drive_media_end`, `transport_drive_watchdog` set to true in
  `~/SingWS/settings.json` (backup `settings.json.bak-before-queue-shadow-*`) so the next real show collects evidence.
- Known and unrelated: `cargo test -p singws-bgm` fails `stream::tests::a_slow_reader_never_loses_or_repeats_audio` (crate not
  touched here; not compared against a clean checkout because the vendored C++ does not build in a worktree).
- NOT proven: no real show has run with these flags. The smoke launches play no song and poll no server rows, so no
  `[QUEUE-SHADOW]` or `decision=media_end/watchdog` line has been produced by the packaged app yet.

Show checklist before flipping any default: run a real show (or rehearsal with server requests, a host delete and a host
reorder) and confirm `grep -c "QUEUE-SHADOW\] MISMATCH"` and `grep "TRANSPORT-DRIVE" ... disagreement=1` are zero while the
`decision` lines show each branch was actually exercised.

## Stage C transition-planning shadow local gate — 2026-10-23

New pure crate `rust/crates/singws-plan` ports `transition_analysis.py`'s planning rules operation-for-operation (audio boundaries,
fade-out estimate, record validity, `bgm_audio_window`, `select_bgm_crossfade_seconds`, `calculate_effective_karaoke_end`), exported
as `singws_plan_json` from `libsingws_dsp_ffi`. `plan_shadow.py` + setting `plan_shadow` (default OFF) compare the two live BGM
decisions (content window, crossfade length) with Rust and LOG `[PLAN-SHADOW]` lines; the app's answer is always used. Each track
costs one comparison (inputs de-duplicated), and the record is copied field-by-field, never `asdict`, so the ~1,900-sample envelope
is not copied per timer tick. Nothing consumes the Rust plan.

- `cargo test -p singws-plan`: 10 tests. Mutation check: 14 deliberately broken rules (thresholds at their exact inclusive edges, margin,
  max/min of the two ends, dead-tail edge, EOS-safe, validity, window tolerance) were all caught after boundary tests were added; the
  randomized differential below additionally caught 4 more (fade ratio, run start, visual-order validity, window margin).
- `tools/plan_parity.py` over the app's real analysis cache (443 usable records of 447): 69 BGM envelopes, 2,215 crossfade and
  3,101 window probes, 443 effective-end and validity checks: 0 disagreements, bit-identical (needs serde_json `float_roundtrip`;
  without it 309 window results were 1 ulp off). The 4 other records are temp `song.zip` junk written by tests before isolation.
- Randomized differential against the app's own Python functions (seeded): 3,000 records incl. invalid ones and 1,500 envelopes,
  0 disagreements; it reaches every crossfade reason and every effective-end outcome, including `verified_audio_and_visual` and
  `normal_eos`, which the real cache never contains.
- 248 Python tests, local transport acceptance, queue parity (still 0), and the real CDG/MP4 integration test passed (worst steady
  clock error 57 ms CDG / 15 ms MP4); the live `~/SingWS` log did not grow.
- Signed DMGs rebuilt (dylibs via `rust/build_dsp.sh`, 3 exports in each), launched from the mounted image with an isolated profile and
  `queue_shadow`, `plan_shadow`, `transport_drive_media_end`, `transport_drive_watchdog` on: exit 0, 3 clean-shutdown lines, 0 tracebacks,
  codesign/hdiutil OK, bundled dylib exports 3 of 3, both shadow modules bundled.
  - arm64 `184bc4cc5826559fe53689ecc0fc8aa400d42f1e36b75fb3a85fdd99681f77aa`
  - x86_64 `203252e8ab70487a4c08ae7abd9753e3c8546784850d5865148d75ffea13615e`
  - Previous: `local-installs/queue-gate-1.0.1.4/` (DMGs), `local-installs/pre-plan-dsp-dylibs/` (dylibs).
- The dev Mac's INSTALLED app is still the queue-gate build; the plan-gate DMGs have not been installed, and `plan_shadow` is off in
  the live profile.
- NOT proven: no real show has run with `plan_shadow`; the smoke launches play no background track. The karaoke effective-end rule has
  no live shadow (it only runs in the offline analysis worker); it is covered by the real-cache parity, the unit tests and the fuzz.
- Fade quirk (preserved, pinned by `FadeHeuristicPinTests`): the Python fade estimate returns the earliest qualifying window in its
  12 s search range, so 50 of the 67 real tracks with a fade report exactly 12.0 s. Practical effect is small: of 69 BGM tracks, 51 take
  the dead-tail path (fade ignored), 14 typical, 3 natural fade (all the 8 s cap), 1 hard ending. Left unchanged by decision.

## iCloud hidden-flag fix for the build venvs — 2026-10-23

Cause: the iCloud file provider re-applied the `hidden` flag to ~15,000 files in the dot-named venvs under `~/Documents` within seconds
of `chflags -R nohidden` (678 files hidden after a 35 s test run). Qt's directory listing skips hidden files, so PyQt6 found no
platform plugins and every GUI test and the integration test failed. Fix: `tools/move_venvs_off_icloud.sh` moves
`.venv-build-arm64` and `.venv-build-intel-rosetta` to `~/.venvs/singws/` and leaves a symlink at the old path (absolute paths baked
into the venvs stay valid; no build script or spec changed; `.venv` still resolves through `.venv-build-arm64`). The move is a
copy+delete across the iCloud boundary (about 8 minutes for both); the script refuses to run while a build or test uses a venv, is
idempotent, supports `--dry-run` and `--restore`, and verifies Python plus a Qt `QApplication` in each venv (the Intel one under
Rosetta). After the move: the integration test passes with no helper running and 0 files are hidden afterwards.

## Cleanup of earlier loose ends — 2026-10-23

* `singws-bgm` test `a_slow_reader_never_loses_or_repeats_audio`: NOT a player bug. It passed 3/3 in the optimized (shipped) build and
  failed in the unoptimized test build because the "slow" reader slept only once per 20,480 frames, so with a slower debug worker the
  reader outran the decoder, `read()` correctly padded silence (counted as underruns) and the output grew to ~5x the file. The test now
  waits until a whole block is buffered before every read. Test-only change; whole Rust workspace (minus `singws-video`) green in debug.
* Live `~/SingWS/transition-analysis.json`: removed the 4 temp-path `song.zip` records left by pre-isolation test runs (447 -> 443; backup
  `transition-analysis.json.bak-before-junk-cleanup-*`). The app's own loader accepts the file and `tools/plan_parity.py` still passes.
* Live `~/SingWS/loudness.json`: it held 5 entries, 4 of them test junk (removed; backup `loudness.json.bak-before-junk-cleanup-*`), leaving
  ONE real track. Pre-isolation test runs rewrote this file (observed at 13:23 and 13:45 today, plus earlier ones), so an earlier, larger
  cache was almost certainly overwritten. No older copy exists (no Time Machine snapshot, no backup, the log does not record its size).
  It is a regenerable analysis cache, not user data; re-run library analysis to repopulate it. Never confirmed how many entries it had.
* Live logs: `tools/clean_test_noise_from_logs.py --apply` removed 250,030 lines (those whose every request id is a unit-test id < 1e12)
  from 32 of 99 logs after archiving them (`~/SingWS/log-archive/logs-before-test-noise-cleanup-*.tar.gz`, 4.4 MB); logs 165 MB -> 114 MB.
  Name-only noise (ROTATION-SLOT, QUEUE-STATE, SURFACE, ...) cannot be attributed with certainty and was left. Queue replay and queue parity
  results over the real data are identical before and after.
* Dev Mac install (2026-10-23): the Stage C plan-gate arm64 build (`184bc4cc...`) is installed in /Applications (key files hash-identical to the
  gate-tested DMG), `plan_shadow` is enabled in the live profile alongside the other shadow flags (backup `settings.json.bak-before-plan-shadow-*`).

## Stage D0 store-format compatibility — 2026-10-23

`rust/crates/singws-store` + `singws_store_roundtrip` (dsp-ffi) + `tools/store_roundtrip.py` (read-only) + `test_store_roundtrip.py`. Nothing in the app
calls the Rust writer (a test asserts `singws_store` does not appear in `0.2.18.1.py`); the packaged dylibs were NOT rebuilt for this step because
no packaged code path uses it.

- `cargo test -p singws-store`: 7 tests (CPython float edge cases incl. exact ties, string escaping both modes, layouts, key order and unknown
  fields, number kinds, unsupported values, bridge). Whole Rust workspace (minus `singws-video`) green; 255 Python tests green.
- Live profile (app closed, read-only): 14 of 14 JSON files rewritten byte-identically by Rust. Layouts seen: `indent=2`+newline (settings,
  singer_history, singer_preferences, queue, host_request_sync, deferred adds, waitlist), compact+newline (tracks), `indent=2` no newline
  (bg_playlist, tombstones, pending_requests), default separators (loudness, silence_cache), compact (transition-analysis).
- Seeded differential vs CPython: 1,500 random documents x 7 layouts identical; 1,900,000 floats identical (random bit patterns, tie-prone
  dyadic rationals, app-like decimals, switch boundaries).
- Bug found by the differential and fixed: exact decimal ties (900719925474099.25) printed with the odd digit by Rust's shortest printer; CPython
  picks the even digit. Regression value pinned in the Rust tests.
- Rescan check (owner re-ran library analysis): `loudness.json` 258 real entries (was 1), `transition-analysis.json` 443 records, no junk in either;
  `tools/plan_parity.py` still 0 disagreements on the refreshed cache; the app re-saved both files in its native layout. Still no verified early-end
  karaoke record in real data (185 CDG records report `blank_tail_too_short_or_ambiguous`, 258 `analysis_incomplete`, 1 `visual_end_unverified`).

## Stage D1 settings-store shadow + power-loss durable writes — 2026-10-23

D1 (shadow only): `singws-store::settings` (parse / fill_defaults / serialize / WriteGate / write_atomic), `settings_shadow.py`, setting
`settings_shadow` (default off), `singws_settings_json` FFI (pure; no write is exported, a test inspects the Rust source to keep it so).
Evidence: 22 Rust tests, 15 of 15 deliberately broken rules caught (8 settings-store, 7 durability-order); Python differentials run the
app's REAL `_load_json_file` / `_save_json_atomic` / `_atomic_write_text` and methods extracted from the source in temp dirs; the live 225-key
`settings.json` passes load / defaults / save read-only. A first package gate (settings shadow only) passed on both architectures with the
shadow enabled in the packaged app's isolated profile: `[SETTINGS-SHADOW] enabled available=1`, decisions for load, defaults and save, 0
MISMATCH, 0 errors (arm64 `abb702ab...`, x86_64 `a546c0f5...`, intermediate).

Durable writes (owner decision): `_atomic_write_text` = temp file -> flush -> `F_FULLFSYNC` -> rename -> directory flush, used by
`_save_json_atomic` (every JSON store), the tombstone store and the live BGM playlist (both were plain overwrites before). 13 + 4 tests
run the real functions with instrumented os/fcntl and assert the ORDER, the failure modes (ENOTSUP/EINVAL fall back to fsync; EIO fails the
save, keeps the old file, removes the temp file and forgets the digest; a directory-flush failure is ignored), the kill switch, byte-identical
layouts, and no I/O for an unchanged payload; 11 deliberately broken variants of the writer were all caught. Cost measured on the dev Mac
(APFS internal SSD): median 9.9 ms (max 13.9 ms) per real write for F_FULLFSYNC file+directory vs 0.2 ms today; plain fsync 0.2 ms but it does
not survive a power cut on macOS. Saves are once per operation (the bulk tombstone paths run in the background sync worker).

Final package gate for D1 + durable writes (both architectures, isolated profile, every shadow flag on, dylibs rebuilt with `rust/build_dsp.sh`, 5 exports
each): exit 0, 3 clean-shutdown lines, 0 tracebacks, codesign/hdiutil OK, all four shadow modules bundled, `[SETTINGS-SHADOW]` decisions for load /
defaults / save with 0 MISMATCH and 0 errors, and the settings file the packaged app wrote through the durable path is valid JSON, mode 600, with no
leftover temp files (176 keys).
  - arm64 `bf1350bf2512b63e15ef7d23f31310aa3e8119afafe349fcac62a0a441a89aaa`
  - x86_64 `750de3e3a5df8fd29ecbd9e27f3a13a074b5038aa9712f885e8b56b8f0a933dd`
  - Previous installed build (Stage C gate): `local-installs/plan-gate-1.0.1.4/`. The dev Mac's INSTALLED app is still that Stage C build, so it does
    not yet have the durable writes; `settings_shadow` is not enabled in the live profile.
Not proven: a real power cut (cannot be simulated safely); the tests prove the order of flush, rename and directory flush, which is the property that
matters. Shadow mismatch counts from a real show are still pending for every shadow.

Install (2026-10-23, dev Mac): the final D1 + durable-writes arm64 build (`bf1350bf...`) is installed in /Applications; executable, dylib and
Info.plist hash-identical to the gate-tested DMG, 2,103 files, codesign OK (`SingWS Local Code Signing`), arm64, 5 of 5 library exports. A launch of the
installed copy with an isolated profile exits cleanly with the settings shadow enabled and 0 mismatches. `settings_shadow` is now enabled in the live
profile next to the queue, plan and transport shadows (backup `settings.json.bak-before-settings-shadow-*`). Rollback: reinstall the Stage C build from
`local-installs/plan-gate-1.0.1.4/`, or set `SINGWS_DURABLE_JSON=0` to disable only the new flushes.

## First real-show shadow readings, KaraFun explicit-end fix, post-show report tool — 2026-10-23

Installed build (D1 + durable writes) run by the owner: BGM playing, a server request, a host reorder and a host delete (no KaraFun or native song).
Report: SETTINGS-SHADOW CLEAN, PLAN-SHADOW CLEAN, QUEUE-SHADOW PARTIAL (`terminal_stale` seen and agreed, `tombstone` not reached), TRANSPORT-SHADOW and
TRANSPORT-DRIVE not exercised. No MISMATCH in the session. (The owner confirmed the older 12:32 transport mismatch came from an earlier KaraFun test
with a song not in this computer's library.)

Transport finding and fix: that 12:32 mismatch (app completed on `karaFun_explicit_end`, machine `Ignore: uncorroborated idle`) was a rule gap in the
Rust machine, not an app bug. Added `Event::ExplicitEndReported` (Rust), `observe(kind="explicit_end")` (transport_shadow.py) and the monitor feed under
the Python rule's own condition. Evidence: 14 Rust transport tests (3 new, plus the random-order fuzz now generating the event), 37 transport-shadow
tests (the real 12:32 sequence replayed: 0 mismatches with the event, 1 without it; a stale explicit end does not count), the local acceptance harness
with a new streamed-song scenario (3 clean shows, 0 disagreements). The harness first failed because `native/singws_dsp` still held the pre-change
dylib (it does not know the new event): packaged dylibs must be rebuilt with `rust/build_dsp.sh` after any Rust machine change (done; previous in
`local-installs/pre-explicit-end-dsp-dylibs/`).

`tools/shadow_report.py` + `test_shadow_report.py` (17 tests, 6 of 6 deliberately broken variants caught): per-shadow verdicts from the logs and
settings.json, read-only. Building it found a real bug in the first version (a drive error line `decision=media_end fallback=python error=...` was counted
as a successful decision) and a precedence bug (an error was hidden by "not exercised"); both fixed and pinned.

Package gate for the explicit-end fix + report tool (both architectures, isolated profile, dylibs rebuilt, 5 exports each): exit 0, 3 clean-shutdown
lines, 0 tracebacks, codesign/hdiutil OK, settings shadow load/defaults/save agreed with 0 mismatches, durable-write evidence as before (valid JSON,
mode 600, no temp files), and `tools/shadow_report.py` run on each packaged app's smoke log reported SETTINGS-SHADOW CLEAN and the other four
NOT EXERCISED (a smoke launch plays nothing), which is the correct reading.
  - arm64 `93f87b70e8d209c67897f90cbcd10d2dadd67f406141f9ed3d7da41ca4383cfd`
  - x86_64 `11fb369bae3dd4972a0090dcc8e79c743e6c25e857644c69be784cce9f618803`
  - The dev Mac's INSTALLED app is still the D1 + durable-writes build (`bf1350bf...`); it does not have the KaraFun explicit-end handling.
