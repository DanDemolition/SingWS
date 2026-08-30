# SingWS 0.4.6.5 installer refresh — 2026-08-30

This same-version rebuild replaces the original Intel installer with the
installed, tested loudness-analysis fixes. APP_VERSION remains 0.4.6.5.
The original release commit is 056ba380a32e591d64a1fc746026b10a0a9a8614.

## Changes

- Read helper results through unbuffered binary pipes, preventing a startup
  log line from hiding an already-buffered response from select.
- A lost/unresponsive helper stops the batch without caching false failures.
- A responsive helper's individual analysis error skips that track and leaves
  it retryable; later tracks continue. Native sessions reset after errors.
- Ambiguous failure records from cache versions 0–2 are eligible for retry.
  The backed-up repair utility preserves measurements and structural errors,
  and refuses repair with pending checkpoints.

## Evidence

- Full runner: 805 tests plus 24 subtests pass.
- Focused GUI/regression/transport/repair coverage: 247 tests pass.
- Release tooling: 16 tests pass.
- A scratch scan of 16 actual library items, with an 8-second diagnostic audio
  timeout, saved seven measurements and skipped nine analysis errors without
  cancellation. All seven persisted and reloaded unchanged.
- Packaged helper returned a missing-file error, then measured a valid tone.
- Running installed scan advanced from 14,123 to 14,269 saved measurements at
  14:35, confirming real productive progress and disk retention.
- Verified 432 Mach-O architecture paths (x86_64), 840 minimum macOS paths
  (macOS 12 or earlier), strict signatures, bundled media loading, and scratch
  and installed launches. Full-library completion is not claimed.
- The installer packages the same app executable as the running fixed build:
  SHA-256 ecfcb0e0771c7ff6b33eee267528540117c85d923ac06acc29fc2122a40a0489.
- DMG passes hdiutil verify; mounted app contents and signature checked against
  the tested bundle. Installer helper and Applications link are present.

DMG: `SingWS-0.4.6.5-x86_64-installer.dmg`

SHA-256: `b2224e2276de83879aec74b833a23b2056a01f0bccb0516751ff6018c348e9b7`

This is Intel-only, matching the existing release. Existing 0.4.6.5 users must
manually reinstall: the updater only offers strictly newer version numbers.
The running scan was not stopped or replaced during packaging/publication.
