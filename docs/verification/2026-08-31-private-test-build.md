# Private Intel test build — 20260831-040139

**Retired later August 31 at the operator's request.** The actual
`/Applications/SingWS.app` now contains the same tested production source under
the normal identity/profile, executable SHA-256
`4ab3b8f768f4d06081e18f02f5d6acd69289617f568af508b09099f566367751`.
The separate test app was moved to Trash; its data/build records moved to
`/Users/Daniel/Documents/SingWS/retired-test-builds/20260831-040139/` and the
launcher was disabled. Nothing was copied into normal history or published.
Current installation receipt: `../../../local-installs/20260831-104012/README.md`.
The operator reported the other tests they ran passed; KaraFun playback remains
pending. The earlier build/launch notes below are historical.

Requested by the operator for rehearsal before another release.

## Artifacts

- App: `/Users/Daniel/Applications/SingWS Test.app`
- Delivery folder: `/Users/Daniel/Documents/SingWS/Test Builds/20260831-040139/`
- Launcher: `Open SingWS Test.command` in that folder.
- Private archive: `SingWS-Test-20260831-040139-Intel.zip` in that folder.
- Checklist: `TEST CHECKLIST.md` in that folder.
- Manifest: `build-manifest.json` records source hashes, test results and bundle identity.
- Temporary build/source snapshot: `/private/tmp/singws-private-test-20260831-040139/`.

Test executable SHA-256:
`19f881e6fc5ff04ef984afd9f0d7c168a584669bcb1d0616de2d32a00ae76dad`.

The installed production executable is unchanged:
`ecfcb0e0771c7ff6b33eee267528540117c85d923ac06acc29fc2122a40a0489`.
Version remains 0.4.6.5. Nothing was published, uploaded, committed, deployed,
or substituted into the existing release installer.

## Isolation and small source change

The test-only spec uses `SingWS Test.app` / `com.singws.app.test`. The snapshot
adds `TEST 20260831-040139` to the host title and a runtime hook that defaults
SINGWS_HOME to the persistent Test Data folder beside the checklist. An explicit
environment override still works for isolated analysis helper processes.

The profile has copies of the library index/tracks, BGM index and 185-track
playlist, artwork and analysis caches. SQLite backups passed quick_check.
Its queue/history start empty. Website credentials, SMTP credentials and private
request links were removed; auto-update, location detection, preview upload and
automatic crash emailing were disabled. It still references the real media
files, and KaraFun still uses the operator's installed KaraFun application.

Inspection found that `song_index.user_singws_dir()` and three BGM playlist
paths still ignored SINGWS_HOME. Those four path expressions now follow the
same data root as the main app. With no override the production locations do
not change. `test_profile_isolation.py` tests the library/phrase paths and actual
playlist startup, analysis enumeration and manager save against distinct
temporary live/test fixtures.

Other production changes are the previously documented pending meter, rotation,
focus, KaraFun lifecycle, BGM crossfade, statistics and crash-handler fixes.
The PHP statistics fixes are **not deployed**; do not connect this test profile
to the old live server to validate them. Local history is testable here; live
phone submission and Fun Stats need coordinated server staging/deployment.

## Build and verification

The release shell script was deliberately not invoked: it deletes build/dist
and replaces the normal installer. Instead the same Intel spec and pinned
runtime were used in an isolated source/output tree, with the test-only
identity/profile changes above. Normal release assets and build directories
were preserved.

Build command (environment paths recorded in the manifest):

```sh
SINGWS_HOME=/private/tmp/singws-private-test-20260831-040139/build-data \
SINGWS_MPV_FRAMEWORKS=/Users/Daniel/Documents/SingWS/SingWS/native_dual_view/Frameworks \
PYINSTALLER_CONFIG_DIR=/private/tmp/singws-private-test-20260831-040139/pyinstaller-cache \
/Users/Daniel/Documents/SingWS/SingWS/.venv-universal/bin/python -m PyInstaller \
  --noconfirm \
  --workpath /private/tmp/singws-private-test-20260831-040139/build \
  --distpath /private/tmp/singws-private-test-20260831-040139/dist \
  /private/tmp/singws-private-test-20260831-040139/source/SingWS-x86_64.spec
```

Run from the snapshot's source directory. Runtime architecture and all entries
in constraints-macos12.txt were checked before packaging. The build spec and
profile hook are also retained under `private-build-inputs` in the delivery
folder; desktop/server source diffs and Git states are retained there.

Passed checks:

- 559 desktop tests, including the three new profile-isolation tests and
  existing UI/KaraFun/BGM/history/crash/helper coverage.
- 54 native-backend tests (`test_phrase_detect`, `test_mac_keep_awake`,
  `test_libmpv_background_engine`) in `.venv-universal`.
- Five PHP suites via `python3 tools/run_history_regressions.py` in a disposable
  server copy.
- 432 Mach-O files carry Intel x86_64; 840 minimum-version paths require no
  later than macOS 12.
- Bundled libmpv, native mpv bridge, BASS and BASSmix load successfully.
- Frozen main, BGM engines, transition analysis, song index and profile hook
  code objects match the snapshot, not merely a version string.
- Strict deep ad-hoc signing verification before and after copying to personal
  Applications. No Developer ID notarization/release sign-off is claimed.
- The actual packaged analysis helper safely reports a missing file, then
  measures a generated tone at -20.7 LUFS / -20.0 dB peak. No sound was played
  on the physical output for this probe.

Raw build/verification/test output is retained in the delivery folder.

## Launch status and remaining rehearsal

Launched the exact personal-Applications test bundle via Finder/open. macOS
paused startup on its Documents-access permission prompt, captured at
`/private/tmp/singws-test-startup.png`. The user was asked to click Allow.
Initial test logs confirm the private log root and BASS initialization; full
GUI startup is not yet verified while that prompt is pending.

The old installed app declined a graceful AppleScript quit with -128 and was
left running. Do not force-close it or start competing playback. The operator
should close the old copy before rehearsal. Only the built-in display is
connected, so audience TV geometry, ticker/video stacking and actual audio
crossfades remain unverified for this bundle. Use the provided checklist for
KaraFun completion → next video, cancel/replay, BGM continuity, focus, rotation
filtering and local performance counts before release.
