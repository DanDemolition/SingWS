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
