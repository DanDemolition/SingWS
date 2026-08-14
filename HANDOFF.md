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

## Unbuilt AirPlay routing repair

Karaoke output selection was lost in the native mpv migration: the plugin and
bridge exposed `setAudioDevice`, but the host never called it, so karaoke stayed
on libmpv's `auto` output regardless of the selected SingWS device. The host now
pushes the selected output before every karaoke start. Because SingWS stores the
human-readable CoreAudio label while mpv requires its backend-specific device
name, the bridge resolves that label through `audio-device-list`. Routing now
treats an AirPlay display as video-only: Default resolves to headphones or
built-in Mac speakers, a missing headphone pin falls back locally,
BGM/soundboard use the same policy, and KaraFun follows the resolved local
output. If no safe local device exists, native karaoke refuses the system
default rather than leaking audio to the TV.

The native bridge compiles successfully. All 114 audio/engine/KaraFun/live-show
checks exercised by the focused run pass; that wider run also exposes one
unrelated pre-existing stale location-permission source assertion. This source
has not been packaged, installed, launched, or tested against a connected
AirPlay receiver.

## Unbuilt decorative MP4 stretch

MP4 animations behind CDG lyrics now deliberately stretch edge-to-edge instead
of crop-to-fill. The native path overrides the decorative core to the shared
16:9 texture and composites that entire texture in the existing single GPU
draw; the CDG foreground remains aspect-correct. No decode, timer, audio clock,
or intermediate-frame allocation was added. The dormant painted fallback uses
the same direct full-source draw behavior.

Ordinary MP4 karaoke tracks now use the same deliberate full-frame stretch on
the output and preview while CDG keeps its protected native aspect. Decorative
playlist changes now use a true frozen-frame dissolve. The native compositor
snapshots the outgoing video's final GPU texture, the same single background
decoder loads its replacement at zero opacity, and incoming frames crossfade
directly over that retained image. There is no black dip and no second decoder
competing with karaoke on the Intel show Mac.

The decorative MP4 decoder was continuously advancing, but its render callback
presented new frames only to the audience output. The host preview was refreshed
only incidentally by CDG render callbacks, making its background visibly pause
whenever the CDG had no lyric movement. Background frames now present to both
native surfaces from the background callback; CDG low-activity behavior remains
independent and cannot throttle either MP4 view.

Opening the third-screen rotation QQuick window now performs two bounded
reassertions of the existing audience-window ticker after native surfaces
settle. It restarts/raises only the show-screen ticker, adds no ticker to the
rotation window, and does not activate or steal focus with the show window.

The Singer Rotation window now has its own optional announcement marquee for
drink specials or venue messages. It is configured independently in Ticker
Settings, appears only on the rotation window, applies live, and is stored with
venue-scoped settings.

Verified karaoke trailing silence now completes the song before mpv's physical
EOS instead of merely fading BGM underneath and leaving the silent decoder
alive. The existing file-scan audio floor and CDG final-visible-frame gate still
prevent quiet endings or unfinished lyrics from being cut. Normal completion
bookkeeping is retained; the next karaoke auto-starts only when Auto Advance is
enabled, otherwise BGM resumes/fades as configured.

## Unbuilt CDG black-screen repair

The 2026-08-13 show log identified two reproducible audio-with-black-picture
tracks: `LG126 03 - The Eagles - After The Thrill Is Gone` and `SF113 03 -
Orange Juice - Rip It Up`. Both ZIPs and CDG streams are healthy and full
length (260.20s and 241.04s). The bundled IINA media stack instead reported
bogus 34.15s and 31.64s graphics durations and never produced a video frame.

The bridge's compatibility probe threshold of 1 still used automatic format
detection, allowing arbitrary CDG packet bytes to false-positive as another
raw format. CDG loads now explicitly force libavformat's `cdg` demuxer and
non-CDG loads explicitly clear that override. The bridge also logs the detected
media format after each load, making recurrence visible in show logs. Both
reported files decode as CDG with the forced option, the native bridge rebuilds
successfully, and the focused transport suite passes (7 tests). This source and
rebuilt bridge have not been packaged or installed.

A second, independent CDG visual watchdog now fires once when audible playback
has advanced to five seconds without any native graphics frame. It keeps the
black native children hidden, leaves the singer's audio uninterrupted, records
the file/time in the show log, and displays a persistent host warning.

## Unbuilt KaraFun fast-path restoration

The 2026-08-13 show log shows a KaraFun song launching normally, followed by a
per-song route check opening KaraFun's Audio Settings UI, failing to match its
device label to `External Headphones`, and aborting playback. That invasive
Accessibility scan could occupy KaraFun for up to 25 seconds and leave panels
or option popovers visible.

KaraFun again owns and persists its configured audio route. SingWS performs
only an immediate local target sanity check (still rejecting a configured
AirPlay/display target), never opens Audio Settings, and proceeds directly to
search/play. The existing background playback monitor remains responsible for
detecting a real launch failure.

The separate modifier automation path also no longer clicks KaraFun's Audio
Settings button. It adjusts only explicitly named key/tempo controls already
exposed by the player, avoiding the same slow popover during modified songs.

## Unbuilt pre-show diagnostics and show-cycle regression

Display Settings now includes **Run Pre-Show Check**, a read-only seven-point
report for the resolved local audio device, video-only display arrangement,
KaraFun installation, both native video hosts, audience ticker, signup QR URL,
and the configured background-video folder.

`tools/show_cycle_simulation.py` supplies a fast deterministic regression for
CDG -> MP4 -> KaraFun -> CDG with the rotation screen open and two motionless
CDG intervals. It checks that the background clock remains independent, both
video surfaces update, the audience ticker coexists with the ticker-free
rotation screen, full-frame MP4 stretch and frozen-frame crossfade remain in
place, the CDG watchdog is wired, and KaraFun does not open Audio Settings.
The simulation and the focused combined suite pass (34 tests).

## Unbuilt loudness-analysis PCM repair

The bundled libmpv loudness path reserved its output name with `mkstemp` but
left the empty WAV in place. libmpv's PCM audio driver refuses to overwrite an
existing destination, so every uncached analysis ended with `libmpv produced no
PCM audio`. The scanner now removes only its uniquely reserved zero-byte
placeholder before initializing libmpv, allowing the driver to create and fill
the WAV normally.
