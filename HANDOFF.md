# SingWS handoff

Updated 2026-08-30.

## Karaoke archive recovery after loudness-cache audit

All 471 retained structural loudness failures were audited with 7-Zip. Seventy
unusable archives were moved, not deleted, to
`~/.Trash/SingWS-unusable-karaoke-20260830-0745/` with their library-relative
folders preserved: 19 contained CDG graphics but no real MP3, and 51 failed
integrity testing with no individually extractable MP3 member. Nine of the
failed archives still had extractable CDG data, but no usable audio, so they are
included in the same reviewable Trash set.

The other 401 archives were recovered. 397 healthy Deflate64 ZIPs (1.53 GiB)
were extracted with 7-Zip and rebuilt using standard Deflate. Each replacement
was verified by both Python `zipfile` and 7-Zip before being installed; the
post-run audit found zero integrity failures and zero Deflate64 members. Four
more healthy ZIPs were rebuilt after removing only `__MACOSX`/AppleDouble junk
entries which had been mistaken for duplicate MP3/CDG pairs. Original archives
are preserved under
`~/SingWS/cache-backups/deflate64-repack-20260830-074824/` (397 files) and
`~/SingWS/cache-backups/deflate64-repack-20260830-075352/` (4 files).

All 471 old failure-cache records are now harmlessly stale: 70 source paths no
longer exist and all 401 repaired archives have changed file signatures, so the
normal (non-Force) loudness scan will retry the repaired songs while retaining
every valid LUFS measurement. A normal library rescan is required once so the
70 trashed tracks disappear from search results.

## Installed post-release loudness-cache repair and smoother ticker

The 2026-08-29 Turbo resource failure had actually left 117,532 legacy ZIP
records as `no measurable loudness`, not only the narrower structural message
handled by the first release fix. The live cache was repaired atomically while
SingWS was closed: all 14,120 valid LUFS measurements were preserved, 117,532
poisoned ZIP records plus 2,399 ambiguous legacy non-ZIP/time-out records were
removed for retry, and 471 specific CRC, deflate, header, unsupported-
compression, or invalid-package failures remain cached. Backups are under
`~/SingWS/cache-backups/loudness-poison-repair-20260830-071049/` and
`~/SingWS/cache-backups/loudness-poison-repair-20260830-073214/`.

New failure records carry `failure_version=2`. Legacy ambiguous
`no measurable loudness` and Turbo-helper time-out records are retried once;
if genuinely bad, they are immediately cached at the current version. The
repair utility performs the same narrow cleanup with a backup and atomic
replace while asserting that valid measurements are unchanged.

The detached Intel QML ticker keeps exactly the validated transient-window and
CDG/video stacking path. Only its moving glyph item changed: text is cached as
a smooth texture layer and `XAnimator` moves that layer with subpixel filtering,
avoiding Intel glyph re-rasterization/pixel snapping that looked choppy.

The complete scratch suite passes 797 tests plus 21 subtests. Matching-Qt
ticker/show-surface coverage passes 67 tests, including construction of the
detached render-thread ticker and the unchanged transition plane behavior.
Python compilation and `git diff --check` are clean. Nothing in this section
was published as a new DMG. The Intel bundle passed its architecture, bundled-
media, macOS 12 minimum-version, signing, staged scratch-launch, and installed
scratch-launch checks. It is installed at `/Applications/SingWS.app`; the prior
tested show build is preserved at
`/Applications/SingWS-0.4.6.4-pre-ticker-20260830.app`. The new ticker still
needs operator judgment with a long real queue on the audience display.

## Installed 0.4.6.4 replacement after live CDG/ticker validation

The final Intel build was installed at `/Applications/SingWS.app` after a real
CDG test confirmed the GPU singer transition, CDG video, background video, and
smooth render-thread ticker all work together. The transition QQuickView now
hands its plane back to mpv when inactive; the ticker QQuickView uses a detached,
non-activating transient surface aligned to the audience ticker strip.

The complete scratch suite passes 793 tests plus 21 subtests, focused GUI
surface coverage passes, the bundle passes x86_64 architecture, bundled-media,
macOS 12 minimum-version, strict signing, and installed scratch-launch checks.
The verified installer is `SingWS-0.4.6.4-x86_64-installer.dmg`, SHA-256
`3b1efbe462580dac3aa33d89b12e69173eeb81956c2baab7846dca2980d8c6bc`.
The previous installed copy is preserved at
`/Applications/SingWS-0.4.6.4-pre-20260830-fixes.app`.

## 2026-08-29 show follow-up: Intel surfaces, KaraFun start latency, analysis failures

The complete show logs confirmed that the installed 0.4.6.4 transition-surface
retirement code ran, but the audience CDG video remained occluded. The operator
identified v0.4.6.1 as a known-good visuals-plus-CDG baseline. History traced
the regression to v0.4.6.2's repeated native/Cocoa ancestor reordering, which
could lift a shared native container above the video. The transition layer now
uses the v0.4.6.1 Qt-only `raise_()` behavior again: no native ancestor walk,
no container restack, no delayed repeated restacks, and no zero-size retirement.
The ticker likewise raises only its Qt widget. The v0.4.6.1 periodic Qt-only
ticker guard is restored at a one-second interval, so an AppKit late restack is
repaired promptly without touching a shared Cocoa ancestor.
`quick_gpu_surfaces=auto` remains enabled on Intel, while the explicit
Off/environment override remains as a safety switch.

A real staged-build CDG test then showed both native mpv views presenting while
the picture remained covered after a `star_tunnel` effect. This disproved the
simple v0.4.6.1 overlay revert on the current stack. The transition container
now occupies the top plane only while QML reports `active`; when the animation
ends it is lowered beneath video without resizing, hiding, destroying, or
walking Cocoa ancestors. The ticker stays on its independent guarded plane.
The follow-up live test confirmed CDG video became visible after the effect,
while the Quick ticker remained behind mpv. Its guard now raises both the ticker
widget and its own non-overlapping bottom-strip container every second, without
reordering any shared native ancestor.

A second live test proved that even the ticker's own container raise does not
move its Quick native child above mpv on this Intel/macOS stack. Intel now uses
the reliable painter ticker independently of the Quick transition capability.
GPU transitions remain enabled and retain the verified video-plane handoff;
only the continuously retained ticker backend changes. `SINGWS_QUICK_TICKER=1`
exists solely as a diagnostic override.

The painter ticker was visible in the third live test but the operator confirmed
its scrolling was choppy. The Intel render-thread ticker is therefore retained
but moved out of the mpv native-child hierarchy: its QQuickView is now a
non-activating transient surface positioned over the audience window's ticker
strip and resynchronised by the existing one-second guard. This preserves QML
`XAnimator` scrolling/effects while avoiding the child-plane conflict.

All four automatic KaraFun searches found and activated the intended result;
three completed and one was explicitly returned to the queue.  Each start
nevertheless waited up to 12 seconds for a Dual Renderer handoff which took
longer and completed afterward.  Result activation now proceeds immediately
after scheduling the handoff, while renderer recreation/fullscreening continues
in parallel.  Exact-match, playback verification, one-shot recovery, and the
operator warning remain unchanged.

The logs also exposed a poisoned loudness failure cache: a Turbo run recorded
roughly 119,000 valid ZIPs as lacking a readable MP3 after a temporary resource
failure.  That specific cached structural claim is now revalidated against the
ZIP directory, so valid single-MP3 archives are retried and overwrite the bad
entry while genuinely invalid archives remain skipped.  ZIP extraction no
longer swallows its underlying exception, and temporary filesystem errors stop
the worker without signature-blacklisting the track.

Focused fresh-Qt transition/KaraFun/performance coverage passes 168 tests.  An
additional 30 focused non-GUI tests pass.  Python compilation and
`git diff --check` are clean.  The complete scratch-data runner passes 792 tests
plus 21 subtests; its documented mismatched Qt environment excluded GUI modules,
which are covered by the fresh-Qt run above.  Three request terminal updates
briefly received `cross_store_sync_conflict`, then succeeded through the durable
second push within one to three seconds; no server change was needed.  Nothing
has been built or installed, and the changes still need an after-show on-screen
CDG plus real KaraFun smoke test.

## Same-version 0.4.6.4 show-fix replacement (2026-08-29)

The Intel replacement candidate includes the stuck Show Screen transition
surface fix and the verified audio-tail BGM handoff fix below.  Release
verification passed 790 tests plus 21 subtests in the scratch-data runner and
168 focused fresh-Qt GUI/transition tests.  The app passed x86_64 architecture,
bundled-media loading, the macOS 12 minimum-version sweep, staged strict
signing, and DMG verification.  A hidden scratch-data Cocoa launch from the
mounted DMG reached BASS, Qt Quick VFX, the ticker, and the audience window in
1.53 seconds and honored the timed smoke exit.  Its frozen offline helper
returned valid LUFS/peak values for the real MP4 fixture.  The replacement Intel
DMG is 151,674,726 bytes with SHA-256
`8c4e189e1d6ce2cbdff1058762ff765cc1e79b9339b54829064c627e5f789f98`.
It is being published as a same-version `v0.4.6.4` replacement; it must not be
installed over the running live-show app until the operator explicitly asks.

## Verified audio-tail BGM handoff (2026-08-29, 0.4.6.4 replacement)

Live 0.4.6.4 playback of Sound Choice `SC8671 10 - David Bowie - Changes`
correctly scanned its last audible audio at 219.73s in a 230.43s file, but still
left 10.70 seconds of dead air.  The CDG visual scan conservatively returned
`static_or_active_nonblank_final_screen`, and the playback path incorrectly
required a verified visual endpoint before it would even start the audio-only
BGM crossfade.

The source now starts the configured three-second BGM fade as soon as playback
passes the verified audio endpoint, even if a static/nonblank CDG final card is
retained.  Actual early song completion still requires the existing verified
visual endpoint and final-lyrics gates, so this closes audible dead air without
cutting lyrics or clearing the audience image.  The installed app and live show
were not touched; after-show verification remains required.

## Stuck Show Screen transition surface (2026-08-29, 0.4.6.4 replacement)

During a live 0.4.6.4 show, a CDG track decoded and advanced normally but the
audience display remained black behind the ticker and request QR after the
`mosaic_tile_reveal` singer-start transition.  The 0.4.6.4 transition container
was raised above the retained libmpv view but remained a full-screen native
child after QML made the transition inactive; on macOS, that nominally
transparent child can still occlude libmpv as solid black.

The source now keeps the existing QQuickView alive but collapses only its native
window container to zero size whenever the transition becomes inactive.  It
restores the saved maximum size immediately before each next-up, singer-start,
or outro animation.  This avoids the historically crash-prone hide/recreate
path.  The focused Show Screen VFX suite passes 18 tests in the fresh Qt venv;
Python compilation and `git diff --check` are clean.  The installed app and live
show data/settings were not touched.  This still needs an after-show macOS
on-screen smoke test with transitions enabled before building or installing.

## Turbo first-batch timeout loop (2026-08-29, released in 0.4.6.3)

Live inspection of the installed 0.4.6.3 build found Turbo stopped at 4/120,428
with all four parent workers waiting for isolated-helper responses. A failed
120-second compact-transition request was silently retried through a replacement
helper for another 120 seconds, making one bad first-batch file look like a
four-minute freeze. Turbo now makes one 45-second isolated attempt (plus the
existing five-second protocol grace), logs the filename and reason with
`retry=0`, marks the unchanged file failed, and advances. Normal non-Turbo scans
retain their 120-second behavior. The scratch-data suite passes with 790 tests
plus 21 subtests. The replacement Intel DMG was mounted and its app and frozen
video helper were launched successfully. Its SHA-256 is
`bf408e233a89e5d2daa3c0a27cd15e88a3a57be69ea028a08ecc7f0f7975af15`.

## Duplicate manager select-all (2026-08-29, released in 0.4.6.3)

The duplicate results dialog now has a `Select All Duplicates` action. It checks
only items carrying the cleanup-eligible checkbox flag, so recommended keepers
and same-audio/different-CDG review versions remain unselected. The scratch-data
suite passes with 790 tests plus 21 subtests. It ships in the same replacement
0.4.6.3 installer described above.

## Turbo MP4 visual-scan stall fix (2026-08-29, released in 0.4.6.3)

Live testing of the installed 0.4.6.3 candidate showed Turbo Full Scan advancing
MP4 files in roughly 43–49-second batches, with many KVDM tracks grouped in the
stalls. Four audio helpers were isolated, but each corresponding Qt worker still
started an in-process libmpv tail-thumbnail decode, causing four simultaneous
video decoders, 1.0–1.7-second GUI stalls, and a 244 MB RSS rise in four minutes.

MP4 visual backfills now run through the recyclable offline helper and are
serialized through one cancellable slot while the four audio helpers remain
parallel. Waiting workers and the helper-response boundary poll cancellation
every 250 ms; cancellation terminates a still-decoding helper. The video-only
job disables audio: a real 20-second fixture tail fell from 20 seconds to 2.28
seconds once null audio stopped pacing the decoder near realtime. The progress
dialog identifies the waiting/analyzing-video-ending stage, and start/finish
diagnostics include the file, elapsed time, and cancellation state.

The isolated video protocol and real subprocess path both pass, along with the
focused transition/recent-regression tests. Python compilation and `git diff
--check` are clean. The GUI source guard was added, but this machine's documented
Qt platform-probe abort prevented that GUI module from running in the combined
command. This change is not built or installed; the currently running scan still
has the old behavior.

Transition results now have the same crash-resume property as loudness results.
Each successful batch audio or visual merge appends a compact JSONL checkpoint;
cache load replays both the normal checkpoint and an interrupted-compaction
checkpoint with later rows winning. A successful atomic full-cache replacement
then removes the checkpoint. Abrupt quits between the per-track result and final
Turbo compaction therefore no longer repeat completed MP4 work. Three focused
checkpoint recovery/compaction/malformed-row tests bring the focused total to
52 passing tests with scratch show data.

Same-version release verification ran on 2026-08-29: 790 tests plus 21
subtests passed through the scratch-data release runner. The fresh Qt run made
198 tests, with 197 passing and only the documented stale ticker source
assertion failing; the removed module was excluded because it no longer exists.
The new Intel app passed x86_64 architecture, bundled-media loading, and the
macOS 12 minimum-version sweep. Its staged copy passed strict signing, launched
cleanly with scratch data, and returned valid samples through the frozen offline
video-tail helper. After a host restart cleared the disk-image service failure,
the replacement DMG was created and verified, and the app and frozen video-tail
helper were both run from its mounted image. The same-version `v0.4.6.3` tag,
GitHub release asset, and update manifest were replaced with this build. The
Intel DMG SHA-256 is
`bf487c439ec67a9d2e1aadefe87f32c95d9b4001c22b77459cbba8640d2194d4`.

## Duplicate Song Manager (2026-08-29, released in 0.4.6.3)

Settings > Search/Library > Library Tools now exposes a review-first duplicate
manager for MP3+G ZIPs. It uses central-directory CRC/size only to narrow the
catalog, then SHA-256 verifies candidate MP3 and CDG members before making any
archive cleanup-eligible. Identical audio with different CDG data is displayed
as review-only. Exact audio+CDG groups receive a deterministic recommended
keeper, but no candidate is preselected. Explicitly checked archives move to a
timestamped `~/SingWS/duplicate-recovery/` folder and are removed from the
persisted library/search index; move failures attempt rollback.

The live 130,824-ZIP catalog completed read-only in 150.72 seconds and reported
1,053 exact audio+CDG groups, 1,057 selectable redundant archives, two
same-audio/different-CDG review groups, and 35 unreadable candidates. Four pure
audit/recovery tests and the GUI source safety guard pass; Python compilation
and `git diff --check` are clean. No live archive was moved.

## Full-library analysis acceleration (0.4.6.3 built, not yet published/installed)

The single-worker full scan now uses combined EBU R128 plus compact silence
boundary detection for karaoke instead of generating, transferring, and
persisting a 100 ms RMS envelope. A real library ZIP measured 2.48 seconds on
the old path and 1.17 seconds on the compact path with identical LUFS/peak;
the derived start/end were 4.38/200.69 seconds. BGM retains dense envelopes
for fade analysis. Karaoke transition cache serialization now omits raw
envelopes (the existing cache was already 17 MB for 2,059 records and projected
near 1 GB at library scale), while preserving derived audio/visual safety data.

Full loudness results are appended to a crash-resumable JSONL checkpoint during
the pass instead of rewriting the complete growing loudness JSON every ten
seconds; completion atomically compacts it. Existing CDG/MP4 visual metadata is
preserved and skips repeat decoding during a loudness refresh. Forty-nine
focused transition/post-show/performance tests pass with scratch show data;
Python compilation and `git diff --check` are clean. A broader combined Qt run
reached the documented local Cocoa platform-plugin abort after its completed
test bodies. Turbo multi-process analysis is deliberately the next milestone,
not part of this unbuilt change.

Decoder message collection is now filtered to the LUFS/peak/boundary values the
parsers actually consume and ebur128 per-frame reporting is quiet. ZIP member
extraction measured only 0.014–0.017 seconds and is not a useful optimization.
The complete 130,824-ZIP catalog was also checked through central-directory
MP3 CRC/size identities in 17.1 seconds: only 1,060 decodes (0.81%) are exact
duplicates, too little benefit to justify a duplicate-alias cache contract.
The operator-authorized live caches were moved, while SingWS was stopped, to
`~/SingWS/cache-backups/analysis-reset-20260829-0900/`; no queue, settings, or
history data was touched.

At the operator's request that rollback cache was then permanently deleted.
Turbo Full Scan now partitions pending items across four separately spawned,
recyclable analysis helpers. It bypasses only the single-worker semaphore,
retains the per-cache locks, always holds between tracks while karaoke plays,
supports one-button cancellation across all helpers, and performs one final
cache compaction after every helper exits. An isolated-process benchmark over
12 real full-track measurements produced 1.68 tracks/second with three helpers
and 2.16 tracks/second with four, so four is the measured default on this
six-core show Mac (29% faster than three in that run).

An exact-content cleanup audit distinguishes identical audio from identical
audio+CDG; only the latter is eligible for a recommended keeper. The proposed
review surface shows canonical path/naming and keeps deletion explicit rather
than automatically removing a potentially better lyric rendering.

Release verification for the Intel `0.4.6.3` candidate: 782 tests plus 21
subtests passed through the scratch-data release runner. The fresh Qt
environment ran 197 GUI tests with 195 passing; its two exceptions are baseline
test debt (one removed module name and one source assertion already stale on
`v0.4.6.2`), not changed behavior. The canonical build passed x86_64
architecture, bundled media loading, macOS 12 minimum-version, staged strict
signing, and DMG verification. The signed app from the mounted installer
launched with scratch data in 1.34 seconds with BASS, mpv, Qt Quick VFX, ticker,
and the audience window ready. Its frozen compact analyzer returned LUFS, peak,
duration, and audio edges for a real library MP3. The 151,672,698-byte Intel
DMG SHA-256 is `d0a832784f865db34043cd5f7aa667db8095b597b4166e505887a7093b9c3f3e`.
GitHub publication and local installation remain pending.

## Post-show KaraFun restart hotfix (2026-08-28)

Built and installed locally on the Intel show Mac on 2026-08-28. The signed
0.4.6.0 hotfix bundle is `/Applications/SingWS.app`; the prior bundle is
preserved at `/Applications/SingWS-0.4.6.0-rollback-20260828.app`. The Intel
installer is `SingWS-0.4.6.0-x86_64-installer.dmg` (SHA-256
`9d98e62201760f3853ebafe2763d5a2bb2a97e44b5ec8198a843b00b002d3d75`).
Architecture, bundled media, macOS 12 minimum version, strict signing and DMG
verification passed. Both the staged bundle and installed copy launched with
scratch data in about 1.8 seconds with BASS, mpv, Qt Quick VFX and ticker ready.
The empty scratch queue did not exercise a live transition or real KaraFun
handoff; those remain end-to-end show checks.

The 2026-08-27 live log showed every external KaraFun song restarting on the
first completion-monitor poll. KaraFun already reported `playing=1`, but the
fast-start recovery path still double-clicked the saved search result at
14–15 seconds because two consecutive playing hints were required for full
confirmation. Recovery now remains armed for genuinely idle starts but will
not reactivate a result while the current poll reports playback. The eight
focused KaraFun auto-start recovery tests pass with scratch show data; Python
compilation and `git diff --check` are clean.

The same logs exposed a second full-library analysis leak: RSS rose from
717 MB to 7.7 GB while the combined loudness/transition-envelope path ran.
Batch analysis now uses a recyclable helper process, capped at 100 tracks per
process, so libmpv/filter allocations are reclaimed by macOS without entering
the live show process. A real stereo WAV passed through the helper entry point
and returned LUFS, peak and 30 envelope windows. Batch transition records are
kept in memory and atomically flushed once at completion/cancellation instead
of encoding the 13 MB cache every ten seconds. Playback-side cache reads no
longer wait on the persistence lock. Synthetic external KaraFun references are
also rejected before local loudness lookup, eliminating predictable decoder
failures.

The same log was checked for the reported visual-transition repetition. The
installed 0.4.6.0 build used all 16 enabled transition styles exactly once in
each complete shuffle-bag cycle, so no chooser reset or missing configured
style was found.

The external KaraFun completion monitor now accepts the first explicit idle
state after playback has been confirmed. That state is emitted only when
KaraFun says nothing is playing and exposes no Pause/Stop control; requiring a
second full Accessibility scrape added 13–26 seconds of dead air during the
2026-08-27 show. The pre-playback safeguards are unchanged, so an idle result
cannot complete a track that was never confirmed playing.

Offline analyzer decoder chatter is now explicitly contained in the recyclable
helper's discarded stderr, and regression coverage pins both that boundary and
the existing signature-keyed failure cache for malformed media. The KaraFun
Dual Renderer creation wait is widened from two to six seconds inside the
first handoff attempt; the 2026-08-27 renderer consistently appeared after the
old window expired, causing the complete toggle sequence to run twice. The
outer retry remains as a bounded recovery for a genuinely failed creation.

## 0.4.5.8 cleanup in progress

On `cleanup/dead-media-paths-0.4.5.8`, `BackgroundMusicPlayer` no longer
contains the unreachable GStreamer fallback. The live BASS engine remains the
primary path and `LibmpvBackgroundEngine` remains the recovery path when BASS
initialization fails. Removed code included the old GStreamer pipeline builder,
meter callback, pipeline seek/state probes, timer-driven fades and crossfades,
output rebuild, and stale pipeline bookkeeping (about 940 lines). Playlist
realignment now checks the active native deck rather than a permanently absent
GStreamer object. A regression assertion prevents `Gst.` or the retired
pipeline fields from returning to the background player. Fifty-three focused
background/performance tests pass with scratch data.

The retired `python_karaoke_transport.py`, `ffmpeg_background_engine.py`, and
out-of-process `mpv_playback.py` implementations are also removed. Shipped
builds already excluded them; their remaining consumers were tests of the old
implementations and an obsolete manual smoke script. Intro-loop and CDG-offset
contracts now target the live `mpv_karaoke_transport.py` and
`mpv_playback_iina.py` paths. The duplicate FFmpeg background and Qt-audio
output tests were removed; native BASS/libmpv background and mpv transport
coverage remains. Fifty-four focused transport, release, and removal tests
pass.

The frozen bundle now post-filters Qt plugins automatically collected by
PyInstaller. It keeps Cocoa, Darwin audio, SecureTransport, the native network
reachability/style/SVG support, and ordinary artwork formats; it drops test-only
minimal/offscreen platforms, touch input, the unused Qt FFmpeg player plugin,
two unused TLS backends, and PDF/TGA/WBMP image handlers. Test-only Python
packages are excluded from the frozen graph. A trial Intel app passed
architecture, bundled-media and macOS-12 checks, shrank from 375 MB to 350 MB,
and completed a scratch launch in 1.68 seconds with BASS, mpv, Qt Quick, ticker,
and the show window active. The trial DMG step encountered a transient macOS
`hdiutil` device error after the app checks; the release build must rerun it.

Startup request reconciliation no longer emits a full `REQUEST-DIAG` plus
header refresh for every historical request and every terminal request already
absent from the local queue. Those rows are now summarized by count; terminal
rows that actually remove a live local entry retain their detailed audit. The
header refresh is batched once at the end of reconciliation. The tombstone,
relay, queue-authority, and show-critical suites pass with scratch data.

## Pending 0.4.5.7 show-screen hotfix

The 0.4.5.6 parent-window ticker repair called AppKit's
`orderFrontRegardless()` from the three-second ticker guard. On the installed
build this continuously reordered the audience parent, fought the rotation
window lifecycle, made Show Karaoke Screen appear not to open, and introduced
new GUI stalls. The periodic guard now raises only the ticker child. The whole
audience parent is reasserted once from `VideoWindow.showEvent`, preserving the
operator-observed close/reopen recovery without continuously disturbing other
show windows.

Focused show-screen and rotation safety tests pass. The broader performance
module reaches its known local Qt platform-plugin environment abort after its
non-GUI assertions; release verification must use `tools/run_tests.sh` as
documented in `AGENTS.md`.

## Released as 0.4.5.6 after the 2026-08-21 show

The 0.4.5.5 show exposed three operator-visible KaraFun/show-screen faults and
one pre-show performance freeze. These fixes are published in GitHub release
`v0.4.5.6`; the installed `/Applications/SingWS.app` remains 0.4.5.5.

When fast-start result activation does not actually begin KaraFun playback,
the completion monitor now retries the exact already-matched result once. The
old recovery clicked Play while KaraFun was still idle and had no loaded song,
which logged success without recovering the 22:58 Jazzystics track. The Play
control remains the fallback if the saved result location is unavailable.

The show-screen ticker guard now reorders the parent macOS audience window as
well as its ticker child, without activating it and only while external
KaraFun playback is inactive. This mirrors why closing and reopening the show
screen repaired the ticker after KaraFun had been fullscreened manually; a
child-only `raise_()` cannot repair a parent NSWindow displaced by AppKit.

Full-library loudness job enumeration now runs on a QThread before the progress
dialog is created. The 5.8-second 20:55 freeze occurred before the first worker
result and while the GUI was constructing the 134k-track job, so the existing
10 Hz worker-progress throttle could not address it.

Release verification: 774 tests plus 21 subtests passed. The Intel app passed
architecture, bundled media loading, macOS 12 minimum-version, signing and DMG
checksum checks. The published 165,859,307-byte installer matches the generated
0.4.5.6 update manifest. A scratch-data launch of the built app completed in
1.5 seconds with BASS, mpv, Qt Quick ticker, both displays and the new parent
window reassertion active. Live KaraFun result-retry behavior still requires an
end-to-end song test. Apple Silicon is unavailable for this release.

## Released and installed as 0.4.5.5 after the 2026-08-20 show

The installed 0.4.5.4 bundle exposed a KaraFun AppleScript syntax regression.
The artist-row matching code named an AppleScript variable `aS`; identifiers
are case-insensitive, so the compiler read it as the reserved keyword `as` and
every automatic KaraFun search failed with error -2741. The variable is now
`artistSize`, with a regression assertion in `test_karafun_provider.py`.

Focused KaraFun tests pass (33 tests), and a representative generated search
script for Sugarcult / Memory compiles with macOS `osacompile`. The show log
also reports Accessibility denial -25211, so SingWS must be enabled in System
Settings > Privacy & Security > Accessibility before an end-to-end KaraFun
test.

The same uncommitted work now requests the native macOS Accessibility prompt
once, 3.5 seconds after the first launch with automatic KaraFun queueing
enabled. A persisted marker prevents repeated launch prompts; the existing
song-time permission warning remains the recovery path if access is later
revoked.

The audience rotation screen now shifts the complete composition through a
six-pixel perimeter every 20 seconds (`rotation_burn_in_shift_enabled`) so the
otherwise-static title, sidebar, QR, clock and bottom bars do not occupy the
same pixels all night. Render-thread rail ribbons also keep moving when the
queue is too short to scroll. The 1280x720 offscreen layout was rendered before
and after a shift with no clipping or reflow; native QML rail content cannot be
captured by Qt's offscreen QWidget grab and still needs an installed on-screen
check.

The 8.5-second GUI stall at 21:44 coincided with the library analyzer racing
through its cached invalid-ZIP block. It emitted one queued Qt progress signal
per cached failure, fast enough to swamp the event loop. Progress is now
rate-limited to 10 Hz while always emitting the first and final item; a
5,000-cached-failure regression test observes exactly two updates.

Server queue refreshes no longer discard the host's selected request during
playback merely because another request arrived and shifted its row. Song
selection capture now includes the queue entry's stable ID, and the rebuild
restores the matching entry at its new row. If that request was actually
removed, selection is cleared as before, so a different request is never
silently highlighted.

The audience ticker could still disappear until the operator closed and
reopened the show screen. It was alive but macOS had restacked its native Qt
Quick child surface behind the retained video surface after the bounded
transition callbacks. The show window now reasserts ticker stacking whenever
it becomes visible and has a quiet three-second guard that raises the visible,
enabled ticker without taking keyboard focus. This self-heals late AppKit
restacking during a show.

Verification before release: 147 ticker/KaraFun/performance tests pass,
including the new self-healing invariant. The 124 recent-regression tests also
pass independently (4 skipped). Running both Qt
test groups in one Python process exited 134 during macOS pasteboard teardown
after the test bodies; the same recent-regression group is clean in its own
offscreen process. `git diff --check` is clean.

Release `v0.4.5.5` was published on 2026-08-21 as an Intel/macOS 12+ build.
The official runner passed 774 tests plus 21 subtests; architecture, bundled
media loading, minimum macOS version, signing, DMG checksum and asset-size
verification all passed. The signed DMG was installed at
`/Applications/SingWS.app`; the prior bundle is preserved at
`/Applications/SingWS-0.4.5.4-rollback.app`. A fresh installed launch was
visually checked and logged the native Accessibility preflight. The audience
ticker was visible. Apple Silicon remains unavailable for this release.

## Previous 0.4.5.4 release handoff

## Committed on `work/show-fixes-2026-08-18`, NOT RELEASED, NOT INSTALLED

`APP_VERSION` is `0.4.5.4`. `/Applications/SingWS.app` is now **`0.4.5.4`**, but
it predates the uncommitted post-show fixes above. The
`SingWS-0.4.5.4-x86_64-installer.dmg` in the repo root is **stale** — rebuild
before installing.

The server half (KaraFun catalog id stability + stale-id re-resolve) **is live
on wskar.com**, deployed 2026-08-17 23:55 and verified against the live
88,566-row catalog.

### What is in this branch

From the 2026-08-16 show logs, plus two the operator reported directly:

1. Loudness scan leaked ~1 MB/track (472 MB -> 8,635 MB over five hours); one
   mpv core per scan pass now, measured 1.047 -> 0.025 MB/track.
2. Four "crashes" from the analyze progress dialog: the delayed re-raise
   guarded `QTimer.singleShot()` rather than the callback body.
3. A full-library scan ran all show and produced 744 GUI stalls (worst 6.1s).
   It now holds between tracks while karaoke plays — **as a setting**
   (`loudness_scan_holds_for_playback`), because the operator needs to scan
   during songs or a 134k-track pass never finishes.
4. A crash left a 712-byte ZIP with no end-of-central-directory record;
   packaging builds under `.zip.partial` and renames only on success.
5. KaraFun songs could not be re-added from Singer History (synthetic
   `karafun_streaming:` paths are in neither store).
6. KaraFun played Memory from Cats instead of Sugarcult's: transient AXError
   -1719 failures consumed the query-specificity ladder until only the bare
   title was searched. Same query is retried now, and the artist is confirmed
   on the result row.
7. Background music came up ~30s before a KaraFun song ended: the completion
   fallback counted KaraFun's ~45s startup as song time.
8. The ticker vanished after a KaraFun song — every show-screen restore path
   raises the window and none re-raised the ticker's native surface.
9. The brand picker offered 121,650 raw disc ids (Karaoke Version across
   20,901 of them); now 38 canonical brands.
10. Searches could return nothing for songs that ARE in the library: a
    coalesced query drained only on a results signal from a worker that had
    been interrupted and would never emit again.
11. Undecodable files were re-analysed every pass (40 files + 11 SKK006 ZIPs);
    failures are remembered against size/mtime.
12. The CDG visual offset now reaches the follower backend. **Confirmed
    correct on screen by the operator, 2026-08-18.**
13. A host song-swap stranded the singer's waitlisted replacement. Removing
    their last song holds the rotation row for 180s, but the promotion only
    accepted slots emptied by the SERVER, so `host_remove_song` was refused and
    the operator had to add the song by hand. Widened via
    `_is_replaceable_empty_slot_reason`; repeat-preservation stays server-only,
    and tombstones still prevent resurrecting anything the host deleted.

14. Los Enanitos Verdes never auto-started (01:07). The search succeeded and
    the result was activated, but with fast start on the code does not probe
    KaraFun at all -- it hard-codes `"PLAYING"`, logs "play click skipped
    already playing", and skips both the play click and the verify loop. It was
    not playing; the operator pressed play 32s later. The completion monitor
    now performs the verification that log line always promised: one recovery
    play press at 12s, an operator warning at 40s.

Server-side, also on `work/karafun-catalog-ids-2026-08-18` (`d7f0ce5`) and
**deployed**: KaraFun catalog id stability plus stale-id re-resolve. A later
commit adds stage-cue expiry — a singer logging in days later on a new device
was handed every stage cue ever sent, because the client asks with `since_id=0`
and nothing ever expired. Chat is explicitly excluded from that purge.

### Verification

936 tests under `qtvenv`, plus the 86 that need `.venv-universal` — all green.
The four `qtvenv` failures are the environment, not the code; see the
"Running the tests" section of `AGENTS.md`, which now documents this so it
stops being re-diagnosed as a regression.

### What has NOT been done

- **Never launched.** Items 6, 7, 8 and 12 drive KaraFun/AppleScript, native
  surface ordering and CDG timing. None of that is reachable by tests. Item 12
  in particular needs a real CDG disc checked on screen.
- Not merged to `main`, not tagged, not released, not built into a current DMG.
- arm64 is still a release behind and needs an Apple Silicon Mac.
- No rollback bundle exists in `/Applications`; copy the current app aside
  before installing over it.

## Rollback path

`/Applications` holds only `SingWS.app` — there is no preserved fallback bundle
to switch to mid-show. The rollback is the retained
`SingWS-0.4.5.2-x86_64-installer.dmg` in the repo root, which must be installed
before it can be used. Copy the current app aside before installing over it.

## Superseded

Earlier sections of this file described the same work while it was still
uncommitted, plus a "current state" describing `0.4.5.3` as the tip. All of it
is now covered by the branch above and has been removed: `CLAUDE.md` imports
this file, so anything stale here is read into every session as pending work.
