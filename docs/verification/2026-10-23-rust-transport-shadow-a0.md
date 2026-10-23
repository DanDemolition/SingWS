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
