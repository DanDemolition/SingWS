# SingWS handoff

Updated 2026-08-12 after publishing `v0.4.4.1`.

All product changes from the previous handoff landed in commit `6ffa414`
(`Release v0.4.4.1`) and were pushed to `origin/main`. The annotated
`v0.4.4.1` tag and GitHub release are public. The release contains the verified
Intel installer only; arm64 remains intentionally omitted until it is built and
verified natively on Apple Silicon.

The public `0.4.4.1` DMG was installed and launched successfully on this Intel
Mac. It is now preserved at
`/Applications/SingWS-before-repaint-0.4.4.2.app`; see the uncommitted
test-build section below for the currently installed app.

Validation completed:

- 450 packaged Mach-O files are x86_64.
- 871 deployment-target checks are macOS 12.0 or earlier.
- Bundled libmpv and the native SingWS bridge load cleanly.
- DMG verification passed; SHA-256 is
  `7bbd4fe476184aac8c5715f5931b870f9c42e5f82e91ddf6e06a8718fd5c620a`.
- The focused release/playback/regression suite passed: 133 tests.
- GitHub reports the same SHA-256 for the uploaded release asset.

## Uncommitted after v0.4.4.1

The version is bumped to `0.4.4.2`. Audio processing and the known-working
playback/scaler configuration are unchanged. An attempted format-specific
scaler change was fully reverted after its first package failed the live CDG
test; quality work must remain separate from this repaint fix.

The live test also exposed a retained-native-view bug: after Show Karaoke was
hidden, the bridge logged `output view no-window`; reopening the window left
the mpv surface detached/black while the separately-owned ticker kept drawing.
The bridge now explicitly re-presents its retained texture when `VideoWindow`
is shown, with one bounded AppKit-settle retry. No media reload, audio change or
surface reorder is involved.

The first `0.4.4.2` build failed live: setting the IINA libmpv core to
`scale/cscale=nearest` for CDG allowed audio time to advance but never produced
a render-ready frame, so readiness gating correctly kept both video hosts
hidden. The scaler changes have been removed from source. The broken app is
preserved at `/Applications/SingWS-broken-0.4.4.2.app` for diagnosis.

The second `0.4.4.2` package contains only the retained-view refresh fix. Its
focused regression passed. The Intel build verified 450 x86_64 Mach-O files,
loaded the bundled media core, verified 871 deployment targets at macOS 12.0 or
earlier, and passed DMG verification. DMG SHA-256:
`faf5b41aaaf2a7a25845fe7bb989dcf2e918ed3fca3a4824738a8d43a7c4ead5`.
This exact second package is installed and launched at
`/Applications/SingWS.app`, but the operator has not yet completed the live
hide/change-to-Blur/reopen sequence. Do not commit or publish until the picture
return is confirmed in that sequence.

## Current live result and cleanup work

The installed `0.4.4.6` Intel build passed the operator's live CDG test:
Side Fill -> Blur -> Side Fill, including Settings hiding/recreating the show
window. Full-edge fill, seam feathering, unstretched lyrics and native-view
reparenting all work. `/Applications/SingWS-before-0.4.4.6.app` is the immediate
`0.4.4.5` rollback.

Unbuilt cleanup after that confirmation makes the native IINA/libmpv backend
mandatory, removes both Settings engine switches, ignores/migrates obsolete
saved engine preferences, removes the `PythonKaraokeTransport` import, and
removes the Homebrew/follower fallback from backend loading. Source runs now
find the locally built bridge under `native/mpv_bridge`. The focused engine and
video regressions pass (27 + 3 tests). The large legacy branch body and
`mpv_playback.py` still physically exist and must be removed only after their
build-script/spec/test references are cleaned. Do not remove `ffmpeg` or
`ffprobe` yet: decorative video, loudness, phrase/waveform/silence analysis,
BGM fallback and soundboard fallback still consume them.
