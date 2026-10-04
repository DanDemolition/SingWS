# SingWS handoff

Updated 2026-09-29.

## VERSION POLICY (operator instruction, 2026-09-29)

**Do not bump the version for test builds.** 1.0.0.7 (re-released 2026-10-03 evening) is the latest release; rebuild for testing without changing it
and bump only when the operator says release. (1.0.0.5 was published 2026-09-29 and re-released with host chat on 2026-09-30.)
Iron rule: iCloud "Desktop & Documents" sync is ON and the project folder is ~24 GB, so builds make fileproviderd/cloudd/bird
saturate the Mac (load average >50) and cause video slowdowns while the capture itself logs a steady 29 fps. Do not build while the
operator is testing playback. Menu bar hidden on the virtual test screen in full screen is BetterDisplay's max-level overlay, not
SingWS; the operator decided full screen works as is.

## RELEASED 1.0.0.8 — 2026-10-04 (published, latest; installed on this Mac (arm64); the operator plans to install the Intel DMG on the venue Mac on 2026-10-05 and test it there - NOT yet done)
Tag `v1.0.0.8` at release commit `0acb828` (code `40309d9`, bytecode of both installers identical to the source); https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.8.
arm64 `7bd9870c...` (127,061,222 B) and x86_64 `8a0a0662...` (152,898,047 B); re-downloaded `latest/download` sizes and SHA-256s equal `docs/release.json`; Pages manifest and download page verified.
Published as a normal release on the operator's instruction (he wanted to download it at work to test on the Intel Mac the next day; a pre-release was created first and then promoted).
Normal auto-update path (new version number). Next release: bump to 1.0.0.9, or ask what the operator wants.
What is in it (over the re-released 1.0.0.7):
- **Host chat history vanishing (reported after the 2026-10-03 show, Host chat tab):** sending a host chat message clears `_chat_messages` and re-reads from id 0; a poll already
  in flight asked for "newer than the old last id" and, landing after the reset, became the whole history (only the newest line) until the next send. Fix: the send bumps
  `_chat_data_generation` so the stale poll is discarded (`test_host_chat_send_race.py`, reproduced then fixed).
- **Song-end timing probes (log only):** `@_perf_timed("ui_songend_...")` on ten song-end steps (stop_playback, handler, cleanup, idle background, outro, QR refreshes, video surfaces,
  now-singing, bg button). Slow steps (>16 ms) log `[PERF-DIAG] ui_songend_... took Nms`. Purpose: the ~450 ms hitch at most song ends on the Intel Mac (67 of 102 song ends in the
  2026-10-03 show) has no known cause and stack capture must stay off. Read the next Intel show log for these lines. Ruled out already: logging cost (async queue, 72 lines/min),
  `gc.collect()` at song end (about 2 ms with 134k rows), idle background image decode (images are ~1200x675, 12 ms here), `server_sync` (6% overlap with stalls vs 3% chance).
- **Show-screen preview, singers vs DAW (operator: DAW must stay frequent and clean, singers may be lighter):** server `api/show-screen/snapshot/index.php` records
  `daw_viewer_seen_at` (DAW browser session) and `singer_viewer_seen_at` (singer token) next to `viewer_seen_at` (anyone; unchanged meaning, so old apps behave as before). App: when only
  singers are watching it grabs a frame every 3 s (`_daw_snapshot_singers_only`) at 320x180 JPEG quality 22; a DAW page, or a server that reports no kinds, keeps 0.25 s/1 s timer, 426x240 q28.
  **Server deployed by the operator 2026-10-04** (script `server-deployments/preview-viewer-kinds/deploy.sh`, git-ignored; verified live: the endpoint now returns both new fields; rollback
  path is printed by the script under `/root/singws-chat-deploy/preview-viewer-kinds-<time>/`). Tests: `test_daw_preview_audience.py`, server `tools/test_singer_screen_preview.php` (+3 asserts).
  Why it matters: ~30 preview captures cost ~250 ms each on the Intel Mac's main thread (about 7% of the 335 freezes); the preview was watched ~51 of 215 show minutes.
- **Show-log review of the 2026-10-03 Intel show (21:15-01:32, build = the re-released 1.0.0.7, exe hash matched the DMG):** no tracebacks/errors, no network drops, 3/3 KaraFun searches
  FOUND on attempt 1 (8, 8, 10 s query-to-playing on Intel), fast picture start worked on Intel, 335 GUI freezes over 120 ms (median 194 ms, p90 400 ms, ~78/hour; 3 over 1 s, all near
  startup/idle-overlay change). Quits at the end of nights log `closeEvent` then `network transports stopped` and no `clean shutdown` line (3 of 4 quits in the 3 logs); the operator
  confirmed those were him closing the app and there was no crash, so it is a logging gap, not a fault. Background music coming in late was a settings value (`karaoke_trim_verified_tail`), see below.
**To check on the Intel Mac (operator, 2026-10-05):** DAW page looks as clean/frequent as before; a singer's phone preview looks acceptable but lighter; send a host chat message while a singer message
arrives and confirm the conversation does not collapse; KaraFun + CDG + MP4 songs; then read the log for `ui_songend_` lines. Intel still has never been run on a physical Intel Mac
with a build from this machine other than through the operator's venue Mac (he runs the released builds there).

## RE-RELEASED 1.0.0.7 (same version number) — 2026-10-03 evening, published, latest; installed on this Mac (arm64)
Tag `v1.0.0.7` force-moved from `2e75508` to release commit `0eecc37`; both DMGs replaced with `gh release upload --clobber`, notes replaced.
arm64 `cf1d09ce...` (127,059,391 B, the exact tested build 4, exe `949b43cf...`) and x86_64 `a752a978...` (152,910,214 B); re-downloaded sizes and
SHA-256s equal `docs/release.json`; Pages (manifest + download page) and `latest/download` verified. Checks: hdiutil, strict signature, arch, version 1.0.0.7,
macOS min 12.3, markers (fixed `delay 3` present, `lowCount` absent, no_match diagnosis, fast picture start, host chat media, shutdown breadcrumbs),
Rosetta smoke launch of the Intel app on scratch data (BASS ready, clean shutdown, live log unmoved; the first attempt did not exit within 40 s right after
the build, a second exited on its own in 9 s: unexplained, probably load). Contents beyond the first 1.0.0.7: the KaraFun search fixed-wait restore
(the first 1.0.0.7 could fail to start a song when KaraFun began on its Discover page), no-match diagnosis log line, duration guard.
**Caveats:** auto-update will NOT offer this to anyone already on 1.0.0.7, so they must download again (the notes say so). Intel has still never run on a
physical Intel Mac; the Intel-only stalls are unexplained. Next release: bump the version as usual (1.0.0.8).

## Rehearsal of the post-1.0.0.7 test build — 2026-10-03 (installed on this Mac: test build 4, label 1.0.0.7, NOT published, 6 local commits pushed)
Installed exe starts `949b43cf...` (arm64). Operator-confirmed on real hardware: KaraFun auto-start of two songs (Avenged Sevenfold / A Little Piece of
Heaven; Bruno Mars / Finesse remix), the fast picture start (`starting at once from the last pane region` then `last pane region confirmed`), seek,
live key/tempo, song end + rotation advance + background music return, an MP4 song, CDG songs with background video, history, waitlist, host/phone chat on a
real phone, a 30 s Wi-Fi cut (relay reconnected in ~5 s, 72 requests reconciled, no duplicates, playback unaffected). No tracebacks or crash reports.
- **KaraFun search wait (the "still isn't starting" bug):** the poll-for-rows version of `_karafun_search_script` (commit 3aa6010) broke every search that
  started from KaraFun's Discover page (window renamed while polling; 16 s, no match, measured twice against twice from a fresh KaraFun). The fixed
  `delay 3` is back (commit 78efa4e, with a warning comment). Kept: cheaper row scans, a result needs a visible length (38b2b98), and a read-only
  `no_match diagnosis` log line (cc1ec5b). The "KaraFun search ~3 s faster" claim below is SUPERSEDED: search is ~5 s again. Do not reintroduce polling without
  testing from a freshly launched KaraFun. The wake-window attempt (b796fef) was reverted (503166f). Still open: ~5 s of "UI not ready" retries on the
  first search after KaraFun is launched hidden.
- **Background music coming in late was a SETTING, not code:** the operator's `~/SingWS/settings.json` had `karaoke_trim_verified_tail: false` (checkbox
  "Skip verified silence at the end of karaoke songs"), which skips the audio-end scan entirely, so the music fell back to the fixed 3 s before the file
  end. After he switched it on, `[END-AUDIO] holding; audio continues to ...` appears and he said the music came in "right when I would have".
  The shipped default is on.
- **Background video loops:** `~/Music/Karaoke/VJ Loops` are Photo-JPEG .mov at 70 Mbit/s (1.3 GB), software-decoded (`hwdec=no` for the background
  player, pinned in test_bg_video_lyrics.py). H.264 copies made in `~/Music/Karaoke/VJ Loops H264 test` (182 MB, same frames, looks identical). On Apple
  Silicon: libavcodec work in a 6 s sample fell ~80% and total busy work ~43%, but overall CPU stayed ~70%; operator felt it ran better. Expected to help Intel
  more (software decode); NOT measured on Intel. Hardware decode for the H.264 loops was proposed, not done.
- **Load facts (dev Mac):** ~190 idle ffmpeg worker threads appear at every song start and vanish at the end (harmless); idle memory steps 594 -> 868 MB
  (first MP4/first song) -> ~1,030 -> ~1,065 MB then plateaus (IOSurface pools 424 MB, constant); no per-song leak seen on CDG. Whether the first step was
  "first song" or "MP4" was not separated (no second MP4 available); the operator chose to drop it.
- **Not done:** Intel never run; Intel-only 120-400 ms stalls unexplained; the commits are on `main` but NOTHING is released. Release 1.0.0.8 only when the operator says.

## Between-singer animations clipped in the host preview — fixed in source 2026-10-03, committed locally, NOT built/installed
Operator report: the applause / singer-start animations looked zoomed in and cut off. Seen in his screenshot: only in the small Karaoke Preview
panel of the main window (about 430x200); the real audience window was fine. Cause: `QML_SHOW_SCREEN_VFX_SOURCE` Text items had fixed
`minimumPixelSize` floors (42/36/30/18/16) that cannot shrink into a small panel, so long names ("Christopher Montgomery") overflowed; three more
lines (GET READY banner, Next Up artist and on-deck) had no fit at all. Now every floor is `Math.max(8, Math.min(N, root.height * 0.07))`
(identical on a TV: 0.07 x 1080 > 42) and those three lines shrink to fit. Checked by rendering the real QML offscreen before/after at 430x200
and 1920x1080 with long names (outro, singer start, next up). The QML itself was never wrong at normal sizes and had not changed in weeks.
Tests: `SmallPanelTextFitTests` in test_show_screen_vfx.py.

## Stall capture on the dev Mac — 2026-10-03, STOPPED at the operator's request (no further runs without asking)
Scratch app copy (SINGWS_HOME=/tmp/kf/cap_home, no server) playing a CDG song, native `sample` of the main thread. First run: ~38% busy, widget
repaints ~23%, Qt Quick sync ~10%. The song end caused no stall on Apple Silicon, so the Intel 400 ms stall cannot be reproduced here, and
Python-level stall stacks mostly end at app.exec (time is inside Qt painting), so only native sampling helps. The effect-toggle comparison
(rotation/ticker/show-screen/Quick surfaces) was inconclusive: results split into two modes (~0% vs ~18% widget repaint) caused by whether the
test window was covered (macOS stops repainting covered windows), not by the setting; a controlled re-run was started and cancelled. These runs
take over the operator's screen, so do the rest in the audit, on the Intel Mac, only when he agrees. Nothing here changed app behaviour.

## KaraFun picture ~2 s sooner — 2026-10-03, committed locally, NOT built/installed, early-start path NOT yet seen on a real song
After the double-click the first frame came ~3-4 s later, mostly because capture waited for the pane-finding probe (~1.8 s, measured live) and
only then started ScreenCaptureKit. Now `_start_karafun_preview_capture` remembers the last pane region (6 h) and, for the next song, starts the
capture at once from it while the same probe checks it; if the pane moved it is re-aimed (`[KARAFUN-CAPTURE] the pane had moved since the last
song; re-aimed`), else `last pane region confirmed`. The first song of a session still waits for the probe. Warm-up frames of an idle player
are held back until the entry status is "playing" (never longer than 6 s: `_karafun_frame_gate_until`), so the TV does not flash KaraFun's
black player. Also: the probe no longer reads element names (1.8 s -> 1.45 s; the pane logic never used them). Exposure: if the operator
moved/resized KaraFun's window or dragged the splitter since the last song, the TV shows the wrong crop for the ~1.5 s until the probe re-aims.
Tests: test_karafun_fast_capture.py (7). To check on a real song: log lines above, and the picture should appear ~2 s earlier than before.

## KaraFun search ~3 s faster — 2026-10-03 — SUPERSEDED: the polling was reverted (see the rehearsal section above); search is a fixed 3 s wait again
`_karafun_search_script` (0.2.18.1.py) took ~6 s per search: 0.45 s to find the search field, 0.2 s typing, a FIXED `delay 3`, then ~2.3 s
scanning rows. Measured live: the results window (the main window retitled "Results for ...") holds ~71 elements until the rows arrive
(~0.6 s after Enter), then jumps to 116-176 and stays. Now: poll `count of entire contents of mainWindow` (0.32 s per poll), carry on once it
changed from the first poll and held for two polls, cap `repeat 9 times` (~3 s, the old wait). The row scans read the slow position/size only
after the cheap name / duration-text checks (same conditions, fewer Accessibility calls). A/B against the old script on 6 queries (incl. a
missing song and an apostrophe mismatch): identical answers every time, 6.0 s -> 2.9-3.3 s for normal searches (nonexistent song 9.8 -> 5.6 s).
Remaining risk: if rows ever arrive in several chunks more than ~0.3 s apart the poll could settle early; the existing retry ladder then
applies. Watch `[KARAFUN-AUTO] search attempt=1 result=` in the next show log (expect FOUND at attempt 1 and ~3 s after the query).
Tests: `SearchTimingTests` in test_karafun_search_fix.py. The harness used is /tmp/kf/search_timing.py (not in the repo).

## RELEASED 1.0.0.7 — 2026-10-03 (published, latest; NOT yet installed on this Mac)
Tag `v1.0.0.7`, release commit `2e75508`; https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.7. arm64 `20e41877...` (127,052,832 B), x86_64
`0b403a30...` (152,908,214 B); re-downloaded sizes and SHA-256s equal `docs/release.json`; Pages and both `latest/download` links verified.
Checks: hdiutil, strict signature, arch, version 1.0.0.7, macOS min 12.3, new code present and removed code absent in the frozen bytecode, smoke
launch of both (Intel under Rosetta, scratch profile, clean shutdown, live log untouched). Tests: 1,233 + 92 app, 19 server suites. Contents: faster
KaraFun search (~3 s) and picture (~2 s), Host chat pictures/GIFs + phone chat layout (server already deployed), animation text fits the small preview,
Reset-to-defaults fix, ~1,900 lines of dead code removed. Normal auto-update path (new version number). Cleanup moved ~4 GB of old builds/venvs to the Trash.
First thing to do: install, play one KaraFun song and one CDG song, check the new log lines (see the audit report).

## Pre-show audit — 2026-10-03: GO WITH CAUTION (report: docs/verification/2026-10-03-preshow-audit.md)
Done headless; nothing built/installed. Fixed: Reset-to-defaults NameError, animation text clipping in the small preview, plus dead-code removal
(86 definitions / 1,901 lines, 3 unused modules, 4 unused settings, folder clutter moved to ~/.Trash/SingWS-audit-cleanup-20261003). All app (1,232 + 92)
and server (19 suites) tests pass; new headless stability check. NEXT: build both arches, install, and rehearse one KaraFun + one CDG song
(watch for the new `starting at once from the last pane region` / `search attempt=1 result=FOUND` log lines) before the next show.

## Server state vs GitHub — 2026-10-03
Host chat pictures/GIFs + phone chat layout: DEPLOYED by the operator (all seven files hash-equal to server commit `8f6fff6`; singer page serves
`room_chat_ui.js?v=2`; chat endpoints 401 without a key; config.inc 403). Server repo `8f6fff6` and app repo are pushed to GitHub.
Full tracked-file comparison (178 files vs live): everything equal except (1) the three deploy scripts (not meant to be on the server),
(2) `tenants/_template/okjweb.db` and `settings.json` (runtime template data, left alone), and (3) **`global.inc`**: the live copy is the version before
`06ed70d` (July 19, request identity/order sync); that commit was deployed except this one file. The repo version adds `PRAGMA busy_timeout=5000`
and skips a write when the state row exists (server PHP 8.3 already throws on PDO errors by default). Low risk, fixes "database is locked" blank
responses when phones submit at the same moment. One-file deploy prepared, NOT yet run: `server-deployments/global-inc-sync/deploy.sh`
(checks live == the old version first, backs up, php -l, hashes, undo script).

## Host chat pictures/GIFs + phone chat layout — 2026-10-03, committed locally, NOT built, NOT deployed, NOT pushed
Operator asked for (1) pictures and GIFs in the app's Host chat window (they existed only in Everyone/Private), (2) a better phone layout
for the chat sheet (close X too small, sheet sitting under the address bar).
**Server** (`SingWS-Server`, one local commit, deploy NOT done): Host chat text still lives in `singer_notifications`; a picture/GIF is a
`chat_messages` row on new channel `host` (thread_key = singer's norm name), so retention, Clear Night and picture authorisation reuse the room-chat
rules. New in `_chat.inc`: `chat_hostchat_send_media`, `chat_hostchat_list_for_singer`. `host_chat_moderation.php say` accepts `channel=host`
(+ `a`, `singer_name`, `media_id`/`gif_id`); `singer_chat.php` `send` accepts `media_id`/`gif_id`, `list` returns `media_messages` with its own
cursor `since_mid`; `chat_media.php` allows uploads when host chat is on; `host_chat.php clear_history` also clears them. Singers are muted /
rate limited as in the rooms; the host never is. The phone Host chat tab only exists when Everyone or Private is on (old text-only overlay
otherwise). `room_chat_ui.js` cache-buster bumped to `?v=2` in `index.php` (phones would otherwise keep the old file).
Phone layout (`room_chat_ui.js`): safe-area padding (page uses viewport-fit=cover, so the header/X sat under the notch/status bar), 44 px round
close button, 44 px tabs/buttons, sheet follows `visualViewport` (address bar, keyboard), page scroll locked while open, Escape closes.
Seen in the browser at 390x760 and 320x568 with a stubbed server (real keyboard/notch behaviour needs a real phone).
**App** (`0.2.18.1.py`): Host chat tab has GIF + Photo buttons (reuse `_room_say_gif/_room_say_photo` with `deliver=`), transcript is a
clickable QTextBrowser merging text and pictures by time (`_render_host_chat_transcript`), `_room_attachment_html` is shared with the room view,
unread counts include singers' pictures, host-channel rows never count as Everyone messages. Tests: 1220 pass (`test_host_chat_media.py` new,
`test_room_chat_tab.py` +1); PHP: 6 suites pass incl. new `test_host_chat_media.php` and an endpoint section in `test_room_chat_http.php`.
**Deploy order:** server first (the app and phone need the new endpoints; old app + new server is fine), then app build.

## Log review 2026-10-03 of the 2026-10-02 show (1.0.0.5 -> 1.0.0.6, Intel venue Mac) — source fix committed, NOT built/installed
No errors or Python tracebacks; all 4 KaraFun songs started (13 s from queue to playing; the AppleScript search is ~7 s of that),
capture 5-7 ms/frame at 24-28 fps. One real fault: **21:26:59 closeEvent ran, no "clean shutdown", app relaunched 2 min later** (a
network blip: server_sync 1.9 s just before). `network_lifecycle.wait_for_idle()` and `QThread.wait()` in `_shutdown_network_transports` have no limit, but the operator does not
remember any hang and this is the only occurrence, so NO behaviour change was made: only `[SHUTDOWN]` breadcrumbs in `closeEvent` and
before the two waits (a forced-exit watchdog was added then removed). If it recurs, the last `[SHUTDOWN]` line shows where it stops.
Not changed (no evidence of harm): ~1 GUI stall/min of 120-400 ms in every state (stack capture is off), ~2 per song change (median
235 ms), 400 ms stall after each song end when "rotation decorative effects resumed" (consistent ~410 ms; the rotation_effects probes
never fired, so the cost is elsewhere), 2-3 s stall at every launch (app_startup 2.3-3.1 s), key/tempo readback returns None every
minute (no stall correlation), host-state POST ~400 ms every ~15 s (new TLS connection per call; TESTED 2026-10-03 against wskar.com: a reused
connection takes 65 ms vs 258 ms fresh when calls are <=3 s apart, but Apache closes idle connections, so at the real 8-60 s spacing
reuse gives no gain (255 ms either way). Not worth doing; only back-to-back bursts would benefit), network_sync_check 3 s (five sequential checks, operator-triggered).

## FINAL 1.0.0.6 (re-released twice, 2026-10-02; installed on this Mac; operator confirmed "works perfectly")
Release commit `c6525f6`, tag `v1.0.0.6` force-moved to it. arm64 `b3d3a277...` (127,080,884 B), x86_64 `3f4a70fb...` (152,934,969 B);
re-downloaded hashes equal `docs/release.json`; Pages and `latest/download` verified. Installed here: the arm64 build (backup of the previous
app + profile in `local-installs/20261002-rebuild/`, private). Beyond the section below: single "Use KaraFun integration" switch +
"Launch KaraFun when SingWS starts"; preview-pane capture is the only method (no video window, no parking, no fallback); picture cropped
to 16:9 x `KARAFUN_FILL_ZOOM_OUT` (1.08) so the corner logo fits; Now Singing card shows KaraFun artist/title. Operator confirmed the
logo and card on a real song. Anyone who installed an earlier 1.0.0.6 must download again (same version number). The parked-window and
fallback descriptions below are SUPERSEDED. Intel still never run on a physical Intel Mac.

## RELEASED 1.0.0.6 — 2026-10-02 (published, latest; NOT yet installed on this Mac)

Tag `v1.0.0.6`, code commit `605c130`, release commit `98bd873`; https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.6.
arm64 `96d8521b...` (127,082,643 B), x86_64 `82b1f88d...` (152,949,249 B); sizes and re-downloaded SHA-256s equal `docs/release.json`,
live on Pages with the download page and both `latest/download` links. Checks: hdiutil, strict signature, arch (420 / 433 Mach-O),
macOS min <= 12.3 (816 / 842), markers for every feature below, search-abort present in the frozen `song_index`, smoke launch of both
(Intel under Rosetta; scratch `SINGWS_HOME`, live log unmoved). Tests: 1224 + 92 (environment group) pass.
Contents (all from the 2026-10-01 show-log review and the operator's reports):
- KaraFun cold start: `_karafun_wait_until_ready` (KaraFun running + main window + 2 s settle, 60 s limit) before any search; the
  "is it playing" check waits 30 s when KaraFun was just launched (12 s warm). The cold run took ~16 s to start playing and was
  declared failed at 12 s.
- Search result bug: a "length" only had to contain a colon and be under 9 chars, so the label "Display:" became the click target
  (22:03 song took 63 s). Now `isDurationText` (digits and colons only); titles match without trailing "(live)"/"[Remix]".
- KaraFun video window ("Dual Renderer", window level above normal windows) used to open maximised over the host screen. Now parked so
  a 3 px strip stays on one screen edge (`karafun_park_position`, host screen preferred, never touches a second screen); re-checked every
  2 s; if no picture for 10 s it is moved back and parking stops for that song. Setting `karafun_park_video_window` (default on).
- Optional preview-pane capture (`karafun_capture_source` = `preview_pane`, default `video_window`, Settings > KaraFun, off by default):
  captures the preview built into KaraFun's MAIN window via the new native region mode (`singws_karafun_capture_start_region` /
  `set_region`; Retina pane ~1566 px wide, output capped at 1600). Pane found from the accessibility tree (`karafun_preview_pane_rect`:
  right of the tall splitter, below the toolbar, above the overlay controls, minus 14 pt). Shown fitted with bars (the pane is ~2.7:1).
  Automatic per-song fallback to the video window if the pane is not found, the stream will not start, or no picture for 10 s while
  playing. **NOT yet tried on a real song.** Unknown: whether KaraFun keeps drawing when SingWS fully covers its window.
- Network settings: Detect Now runs in a background thread; OK no longer waits for location detection (it ran 25 s on the GUI thread
  each time, replayed clicks made it minutes). Failure text now points at Wi-Fi. Log lines `[NETWORK-DIALOG] ...` time both.
- Search: a cancelled library search stops its SQLite scan (`should_abort` progress handler) and is never cached.
- Emoji button (😀) on the Everyone/Private bar and the Host chat tab.
- Log: `[LAUNCH] clean shutdown (aboutToQuit)` and `main window closing` (a run that just stops was killed/crashed); PERF probes
  `rotation_effects_now_singing` / `rotation_effects_rail` (21 song-end freezes of 0.4-0.8 s have no known cause yet).
Still open: real-song test of the parked window and of preview-pane capture; Intel has run a real show (1.0.0.5, 2026-10-01, no
crash, Detect Now worked at first and failed at the venue with CoreLocation "unknown") but not these features; song-end screen freezes;
KJ Genie research: it is Windows-only and uses KaraFun's Windows-only websocket Player API; the Mac app opens no local port.

## Host can speak in room chat — 2026-09-30 (server LIVE; now INCLUDED in the re-released 1.0.0.5, see below)

Operator asked: host must be able to talk in Everyone, interject in Private conversations, and pick a host name.
**Server** (`c7ea450`, pushed, deployed 2026-09-30 by the operator with `server-deployments/host-say/deploy.sh`; live SHA-256 of all four
files verified equal to the commit): `host_chat_moderation.php` new POST `say` (channel group|dm, a, b, message, gif_id, media_id, host_name)
and `gifs` search; `chat_media.php` accepts host uploads; `_chat.inc` `CHAT_HOST_KEY='@host'`, `chat_host_send`, `chat_host_name` (24 chars,
default "Host"), host messages visible to BOTH singers of a private thread, counted unread, `host:true` flag; `room_chat_ui.js` HOST tag +
highlight. A singer named "@host" is refused (`invalid_sender`/`invalid_recipient`) so nobody can pose as the host. Venue switches still
apply (group/dm/gifs/photos off = refused). Undo: `/root/singws-chat-deploy/host-say-<UTC time>/rollback.sh`. Tests: `tools/test_host_say.php`
(in `tools/run_room_chat_tests.py`, all five suites pass in a disposable copy).
**App** (`f2f3fab`, label stays 1.0.0.5 by the operator's choice; the published 1.0.0.5 DMGs were replaced with builds containing it): message bar under Everyone,
"Interject" link under each private message (target shown as "Interjecting in A <-> B"), GIF picker (server-side GIPHY search) and photo
upload, setting Settings > Network > Connection > "Chat name" (`host_chat_name`, default "Host"). Host's own messages are not counted as new.
Tests: 1152 pass (`test_room_chat_tab.py` +9). Installed exe `bce6f0c6...` (backup `local-installs/20260930-host-chat/`); operator tested
("all good").
Test-env note: the Qt platform plugins in `.venv-test-arm64-fresh` will not load; copy them to `/tmp/singws-release-qt-platforms` and
`codesign --force --sign -` them, then use `QT_QPA_PLATFORM_PLUGIN_PATH` (the folder is lost when /tmp is cleaned).

## RE-RELEASED 1.0.0.5 with host chat — 2026-09-30 (same version number, assets replaced; latest; installed on this Mac)

Operator asked for both chips rebuilt and the existing v1.0.0.5 GitHub release replaced (same version). Done: release commit `6d0c745`;
tag `v1.0.0.5` was **force-moved** from `8300e55` to `6d0c745`; both DMGs replaced with `gh release upload --clobber`, notes extended.
arm64 `3f086e51...` (127,076,211 B) and x86_64 `c2caaac6...` (152,901,891 B); re-downloaded sizes and SHA-256s equal `docs/release.json`,
live on Pages with the download page and both `latest/download` links. Installed here: the arm64 build (exe `bce6f0c6...`), byte-identical
to the released DMG's app. Checks: Intel hdiutil, strict signature, x86_64 (433 Mach-O), macOS min <= 12.3 (842), markers (chat say, chat
name, seek, capture-only), Rosetta smoke launch (BASS ready, clean exit, live log unmoved). Tests: 1152 app tests, five PHP suites.
**Caveats:** auto-update will NOT offer this to anyone already on 1.0.0.5 (same version), so they must download it again (the notes
say so). Intel has still never run on a physical Intel Mac (Detect Now there unconfirmed). Real-TV rehearsal (KaraFun window vs
audience window) still pending. Next release: bump the version as usual.

## Previously released 1.0.0.5 — 2026-09-29 (superseded by the re-release above; hashes below are the FIRST 1.0.0.5 DMGs)

Tag `v1.0.0.5`, release commit `8300e55`, https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.5. arm64 `65f19e24...`
(127,068,526 B) and x86_64 `20f2f4f4...` (152,919,340 B); sizes and re-downloaded SHA-256s match `docs/release.json`, live on
Pages with the download page and both `latest/download` links. Contains: capture-only KaraFun video (no fullscreen fallback),
placement log, KaraFun seek via Skip 10s, faster end detection, steadier tempo readback, Detect Now fix (see the sections below).
Checks: 1142 tests + 92 environment-group tests (scratch `SINGWS_HOME`, live log unchanged); both DMGs hdiutil-verified, strict
signature, arch (arm64 420 / x86_64 433 Mach-O), macOS minimum <= 12.3 (816 / 842 files), markers present, old fallback absent.
Intel smoke-launched under Rosetta only (BASS ready, clean exit); **never run on a physical Intel Mac** (Detect Now there is
still unconfirmed). Installed here 12:28: the released arm64 DMG (exe `bda75d45...`); backup `local-installs/20260929-release-1.0.0.5/`.
Still open: rehearse on the real TV (KaraFun's window must never cover the audience screen; guard = floating level + 2 s reassert,
skipped when host and audience share a screen); dead code `_renderer_give_up` / `fallback` decision; slow `server_sync` (1-3 s, worker thread).
Server: unchanged today, repo `f5bb7a7` clean and pushed; 4 non-code files differ live vs repo (see 1.0.0.4).
Version policy: next test builds stay at 1.0.0.5 until the operator says release again, then bump.

## Was installed 2026-09-29 12:02 — capture-only KaraFun video + seek (now committed as `b38ac74` and released in 1.0.0.5)

Installed exe `3305f3e5...` (DMG verified, signature ok, identical to installer). Backups of the previous app + profile:
`local-installs/20260929-*` (private). Three changes over commit `170b68e`, all in `0.2.18.1.py`, **not committed yet**:
1. **Fullscreen-handoff fallback removed** (operator: "no failsafe anymore that is too broken"). In `_ensure_renderer_windowed`, when
   System Events cannot see the renderer but CoreGraphics lists it in any Space, capture starts immediately
   (`renderer exists in another Space; starting capture`). If the window never appears the capture just stops (black preview).
   The old `_renderer_give_up` helper and the `fallback` decision in `karafun_renderer_press_decision` are now dead code.
   Real-song results: cold start pressed the video button once, first frame 7 s after play, looked smooth; regular start
   pressed nothing, 29 fps. The earlier black screen (renderer absent, capture gave up after 8 s and never restarted) is fixed.
2. **Placement log** at capture start: `[KARAFUN-CAPTURE] placement renderer=[...] audience=(x,y,w,h) same_screen_as_host=..`.
   On the virtual test screen KaraFun's window lands on the same screen as the audience window (x=1728). The existing guard
   (`_set_show_window_capture_level` floating level + 2 s `karafun_capture_guard` reassert) keeps the audience window above it,
   but is skipped when host and audience share a screen. **Not yet rehearsed on the real TV: watch for KaraFun's window over it.**
3. **KaraFun seek** (`_karafun_seek_to`): dropping the SingWS seek bar clicks Playback > Skip Forward/Back 10s (KaraFun's progress
   bar is not in the accessibility tree), so it lands within ~5 s of the drop, never within 8 s of the end, max 60 clicks. Verified
   on a real song 12:03: +30s, -30s, +150s all clicked fully; end-of-song followed correctly; operator: "seemed perfect".
Tests: 303 related tests pass (`test_karafun_seek.py` is new; source-guard style). Music fade in/out confirmed perfect.

## Earlier today 11:12 — KaraFun black-screen fix (commit `170b68e`, label 1.0.0.5, arm64 only, unpublished)

Black screen at song start was caused by blind toggling of KaraFun's video button. `_ensure_renderer_windowed` now lists
"Dual Renderer" windows across all Spaces (`karafun_dual_renderer_windows`) and presses at most twice, only when the
renderer is absent everywhere (`karafun_renderer_press_decision`; 11 tests in `test_karafun_renderer.py`). Installed exe
`04748d95...` (identical to the DMG; strict signature ok; marker present). Backup of previous app + profile:
`local-installs/20260929-renderer-fix/backup/` (private). NOT yet tested on a real song: watch the log for
`renderer absent in every Space; pressing video button (n/2)` (at most once per song, no black screen).
Operator-confirmed 11:14: a KaraFun cold-start song (Test / Bruno Mars / Dirty Diana) looked right with no slowdowns; renderer
was already present (no presses), capture steady at 29 fps. The renderer-absent case is still untested on real hardware.

## INCIDENT 2026-09-29 04:23 — singer history deletions by mistake (fully restored)

While removing the "Test" singer from Singer History by scripting the UI (System Events), a position click on the
filtered list triggered **five** `history_singer_delete` exports instead of one. Removed: Test (intended) plus **Aryana A.,
Bill F, Codex Capture Test, Codex Playback Test** (not intended; two are real singers). Cause of the extra four not
established. **Restored within minutes from backups, both sides, verified:** local `singer_history.json` +
`singer_preferences.json` from `local-installs/20260929-location-fix/backup/profile/` (pre-restore copies in
`.../pre-restore/`); server `tenants/wsk/history.db` via one transaction from
`/root/singws-chat-deploy/history-before-test-cleanup-20260929T093632Z.db` (safety copy of the pre-restore live DB:
`history-before-restore-20260929T112533Z.db`). The four singers' deletion markers were removed on both sides; Test stays deleted.
After relaunch and sync: local and server both 92 singers / 791 deletion markers; Bill F 19 songs; Test absent; DB quick_check ok.
The rotation's empty "Test" slot also went (expected). **Lesson: do not script clicks/keystrokes against destructive UI
(Delete Singer / Clear ...). Have the operator do those, or use the server tool with an explicit target.**

## RELEASED 1.0.0.4 — 2026-09-29 (published, latest; installed on this Mac)

Fixes "Detect Now" location on Intel Macs (operator report: could not detect no matter how many tries). New
`LocationFixCollector` (pure Python, 10 tests in `test_location_fix.py`): ignores cached fixes older than 30s, keeps the
most accurate fresh fix, treats kCLErrorLocationUnknown/Network as transient (keeps waiting), stops early only within
100 m, accepts up to 3 km at the deadline; Detect Now waits up to 25 s (startup 15 s); logs
`[SESSION-LOCATION] detected accuracy_m=..`. **Verified on real hardware here (Apple Silicon): 35 m in 0.2 s at startup.**
NOT yet verified on a physical Intel Mac - ask the operator to press Detect Now there and check the log line.
Release https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.4 (commit `3e55987`): arm64 `c39e60eb...`
(127,056,396 B), x86_64 `427c4dba...` (151,828,085 B); sizes and re-downloaded SHA-256s match `docs/release.json`, live
on Pages with the download page. Installed on this Mac: exe `0fd53a23...`, backup in `local-installs/20260929-location-fix/`.
Remaining: 4 non-code server files (.gitignore, config.inc.example, two tools/run_*.py) differ live vs repo (deploy with
`scripts/deploy-wskar-rsync.sh --apply` from a clean `git archive` export, not the private checkout); "Test" singer
history removed 2026-09-29 (see incident note); phone-requested song with key/tempo: operator confirmed working 2026-09-29.

## Previously: 1.0.0.3 (superseded)

Full release, both installers, published at https://github.com/DanDemolition/SingWS/releases/tag/v1.0.0.3 (tag `v1.0.0.3`,
release commit `241367f`). Apple Silicon `2525f183...` (127,054,926 bytes) and Intel `3b68c025...` (151,839,315 bytes);
sizes and re-downloaded SHA-256s match `docs/release.json`, which is live on GitHub Pages (auto-update clients see it)
along with the updated `docs/index.html` download page (was stale at 1.0.0.1). Both builds verified: signature, arch
(arm64 420 / x86_64 433 Mach-O), macOS minimum <= 12.3, new code present, clean launch (Intel under Rosetta only).
Order followed: version bump -> build -> verify -> manifest -> tag pushed alone -> draft + upload -> size/hash check ->
publish -> `main` pushed last. **This Mac still runs the pre-release 1.0.0.2-labelled build (same code); it will be
offered 1.0.0.3 by the updater, or install the 1.0.0.3 DMG.** Intel build not run on physical Intel hardware.

## Chat, GIFs, request expiry — 2026-09-29 (server LIVE and tested, app installers built, NOT installed)

**Server (wskar.com), all deployed and checked:** room chat (Everyone / Private / Host chat on the singer
page), GIPHY GIFs (key in live `config.inc`, before the closing `?>` — a first attempt appended after it and
printed the key on every page for a few minutes; fixed and restored), photos (needed `php8.3-gd`, now installed),
singer show-screen preview, hourly `/etc/cron.d/singws-chat-cleanup`. Chat is OFF for singers until ticked in the
dashboard's chat card. Undo scripts and backups: `/root/singws-chat-deploy/`. Server repo: `17c290c` (chat),
`16ff820` (older request edits), `4e908a4` (expiry).

**Request expiry (fixes "You already have 2 songs in"):** never-sung requests older than 18h (venue setting
`request_expire_hours`, 4-72, no dashboard control yet) no longer count toward the song limit, the waitlist limit,
the duplicate check, or the singer's Requests tab. Nothing is deleted. Cause: requests stayed `pending`/`delivered`
forever; Warren had two Sept 20 "Life On Mars" rows. Verified live: Warren 2 -> 0, venue 7 -> 2. **Warren's
complaint is fixed** (operator could not confirm with him; verified against live data instead).
"Already sung tonight" (6h) was already live since 2026-09-20.

**Also deployed later 2026-09-29:** GitHub `b92ee07` (cross-store request completion conflicts:
`complete_remote_request.php`, `report_pending_request.php`) and a dashboard control for the expiry window
("Old requests clear themselves after (hours)", server commit `efb5b26`). All six changed server files verified
live by hash. Chat was switched on in the dashboard and confirmed working end to end with two phones
(text, GIF, photo, host moderation).

**Installed 2026-09-29 01:05:** Apple Silicon build `e98c719b…` at `/Applications/SingWS.app` (normal profile;
backup of previous app + profile in a backup that has since been deleted, private). Chat page seen on
screen: three tabs, GIF thumbnail, photo, moderation links all work. Its tab bar was plain/unstyled.
**KaraFun real-song test 2026-09-29 (operator confirmed by ear + panel):** three real "Test / NOFX / Linoleum" songs
through the installed Apple Silicon build. Verified: launch, exact search, start, ~29fps capture with no lag, BGM handling,
natural end detection, rotation advance, server-confirmed completion. **Key/tempo:** live SingWS Key +/- (1 semitone) and
Tempo +/- (1% per press) work during play; a queued song's own key/tempo (right-click > Change Key... /
Change Speed / Tempo...) is applied automatically at start (log: `automatic key/tempo ... key=+2 tempo=90% ... ok=1`).
The panel Key/Tempo set BEFORE a song starts does NOT carry to it (start reads the queued entry) - a mistake in my
first test instructions, not a bug. Not tested: a song requested from a phone with a key/tempo (same start path).
Three "Test" performances from these tests are in real history / Fun Stats (clean up if wanted).
Intel installer rebuilt with the tab fixes and verified (signature, x86_64, markers, Rosetta smoke launch): exe
`b871cec0...`, DMG sha256 `d598c159bee42c30...`, NOT installed and not run on real Intel hardware.

**Installed 2026-09-29 01:32 (current):** Apple Silicon build exe `ca6ecbe6…` (commit `3b16397`) at
`/Applications/SingWS.app`. Chat tabs are pill-styled, bright, never elided, left-aligned; seen on screen and readable.
Two earlier tab bugs fixed along the way (labels cut to "Ever...", tabs centred; then dim text). Backups of the
previous apps + profile: `local-installs/20260929-*` (private). Still open: Intel installer needs a rebuild with the
tab fixes; KaraFun end to end with a real song and a real Intel Mac are untested; version bump + release publish is
deferred until the operator is done. GIFs now switch off for the night when the GIPHY allowance runs out (server
`f5bb7a7`, live; button disappears, resets when chat is cleared or after 12h). Server rebooted onto kernel 6.8.0-142.

**App:** commits `4b93f5b` (KaraFun) and `be401f7` (three-tab Chat page, GIF thumbnails) pushed. Installers
`SingWS-1.0.0.2-arm64-installer.dmg` and `-x86_64-installer.dmg` are built, signed, verified and smoke-launched
(scratch profile) but **not installed and not published**; version still 1.0.0.2. Chat page layout not yet seen
on screen. Stray Finder-duplicate refs `main 2` in `SingWS-Server/.git` were moved to `.git/stray-refs-backup/`.

## Earlier (2026-09-06)

## Release 0.4.7.1 candidate — 2026-09-06

Intel was built and packaged first. Both architectures use the approved purple/neon-green rotation design. The later candidate removes the separate animated lyrics preview: live CDG/MP4 remains as the transparent full-screen underlay, while a brief neon sweep, particle burst, and staggered queue pulse spotlights the next singer every 30 seconds without covering the QR code or ticker.

Intel: strict signing, x86_64 architecture, macOS 12 minimum, DMG verification, and clean Rosetta launch/exit passed. Apple Silicon: strict signing, arm64 architecture, macOS 12.3 minimum and DMG verification passed. Installed at `/Applications/SingWS.app` and launched with normal profile/server connection. Backup: a backup that has since been deleted. The system location permission prompt is left for the operator.

Tests: initial Intel root suite 1085 passed; 22 failures were resolved by two test-fixture updates, rerunning the timing test without concurrent build load, and running native-media tests with their matching ARM source runtime. The revised queue spotlight passed 47 focused tests. Actual CDG animation screenshots are in a backup that has since been deleted. Physical Intel hardware and venue TV remain operator rehearsal checks.

Build commands and detailed verification: `docs/verification/2026-09-06-rotation-release.md`.

## Release 0.4.6.7 Apple Silicon build published — 2026-09-04

The existing public 0.4.6.7 release now has a native arm64 installer alongside
the Intel installer. The build uses the current 0.4.6.7 Python source and the
current Apple Silicon native bridge, including the CDG GPU fence correction.
Packaging was made reproducible on Apple Silicon by matching PyQt6 and Qt 6.9.1,
accepting the bundle's actual macOS 12.3 minimum, and locating Homebrew `7zz`
through `PATH` instead of the Intel-only `/usr/local` location.

The exact DMG was mounted and verified: strict signing passes, 417 Mach-O files
contain arm64, 811 Mach-O files have a deployment target no newer than macOS
12.3, and the bundled media core loads. Installer SHA-256:
`228dc6d4aa88d818a8d104d74d6618e5f6f0e85bbfb1f3f17e5fa88272f55e21`.
GitHub reports the same digest and 123,892,502-byte size.

The exact app is installed at `/Applications/SingWS.app` under the normal app
identity and profile. It launched on Apple Silicon, remained running, connected
and synchronized with the configured server, initialized mpv and BASS, and its
main/audience windows, QR artwork, ticker and controls were inspected on screen.
No new SingWS native crash report or logged Python error appeared. The previous
0.4.6.6 app and normal profile are backed up under
a backup that has since been deleted. Automated verification passed 994 tests
plus 39 subtests and 86 native tests. Full-song karaoke playback and a physical
external-display rehearsal remain unverified.

Report: `docs/verification/2026-09-04-release-0.4.6.7-arm64.md`.
Release: `https://github.com/DanDemolition/SingWS/releases/tag/v0.4.6.7`.

## Silicon CDG blanking / request retry fixes — source only, not built

The 2026-09-02 Apple Silicon show logs and the exact 22 local MP3+G archives
were exercised through the native bridge. Every archive produced a non-blank
visible native surface, ruling out damaged CDG content. The bridge had no
explicit completion dependency between the master OpenGL context that renders
libmpv's shared texture and the two contexts that sample it. `bridge.mm` now
publishes a GPU fence after each karaoke render and queues a server-side wait in
each consumer context. This avoids the intermittent Apple Silicon stale/blank
texture race without restoring the GUI-blocking `glFinish` removed previously.

The same probe exposed a separate definite defect: libmpv `screenshot-raw`
returned a uniform black image for CDG rendered into the caller-owned FBO, so
DAW/show-screen snapshot capture was blank even while the room surface was
correct. CDG capture now reads only the retained 300x216 texture; normal video
keeps the non-invasive screenshot path. Before/after on exact `LG208 12 - ZZ
Top - Blue Jean Blues.zip`: raw capture changed from one black color at every
sample to 4–27 palette colors, while the visible surface remained non-blank.
`tools/probe_cdg_render.py` is the reusable read-only exact-file probe.

The logs also showed six durable terminal request records being posted every
2–4 seconds during DNS failure, producing about 1,962 failures and reaching
571 attempts. Terminal retries now use persisted 5/15/30/60/120/300-second
backoff while a newly completed song still sends immediately. Watchdog logging
is rate-limited. The server repository already contains the coordinated
idempotent cross-store completion correction in `complete_remote_request.php`;
it must be included in the server deployment paired with this app build.

Verification: native x86_64 bridge compiles; exact-file native playback and
capture pass; 81 tombstone tests, 33 focused renderer/queue tests, and the PHP
pending-completion integration suite pass. Python compilation and
`git diff --check` pass. The full runner is currently limited by the local Qt
test environment (`minimal`/`offscreen` platform plugins are not discovered in
subprocess tests); it reached 101 passing tests before that environment failure.
Apple Silicon compilation/runtime and external-display hot-plug rehearsal still
require the target machine. No app was built or installed and no server was
deployed.

## Release 0.4.6.6 built and locally installed — 2026-08-31 12:03 PDT

The operator requested publication after testing the normal app. The release
contains the accumulated fixes below, with only APP_VERSION changed from the
tested 0.4.6.5 candidate. Both spec versions match 0.4.6.6. Intel/macOS 12+ only,
matching the prior public release; no untested ARM build is advertised.

991 desktop tests + 39 subtests, 86 native tests, 282 independent focused GUI
tests, five isolated server-history suites and 16 versioned release-tool tests
pass. The first full-runner attempt failed in its documented broken Qt test
environment; it was rerun fully with working Qt, not counted as a pass or
silently skipped. The exact release app is installed/launched, the host toolbar
is visible and relay connected, with no new Python/native errors. The operator
was actively using Settings; it was left untouched. Server config and history
were unchanged at verification. Full KaraFun/external-display rehearsal remains
unverified and is explicitly disclosed in the release notes.

Executable SHA-256:
`422ad95492e1b4f4414289e8b890726137f69719811c17816d68a7d498f2068b`.
Installer SHA-256:
`e1eb87748c23be0210c9faed7b52f07cf297c8e8790019b8e37c591e468dfc41`.
Frozen code/native-load/architecture/macOS/signing checks pass. The mounted
installer's 3,183 entries exactly match the signed app; hdiutil verification,
helper and shortcut checks pass. The old download page was updated to the
new installer-derived version/link/size/hash alongside docs/release.json.
Private backup/build/test receipt: a backup that has since been deleted.
Report: `docs/verification/2026-08-31-release-0.4.6.6.md`.
Published as the latest GitHub release after its remote size and SHA-256
matched the verified local DMG. The final follow-up commit pushes main only
after publication, making docs/release.json visible to auto-update clients.
Release: `https://github.com/DanDemolition/SingWS/releases/tag/v0.4.6.6`.

## Ticker no longer covers host controls — installed 2026-08-31 11:27 PDT

The detached painter's floating Tool window was at native level 3 above the
level-0 host. `DetachedPainterTicker` now matches the audience window's level
and orders only its own window directly above that audience window. The ticker
ignores mouse input, hides when its output is minimized/hidden, and hides on
ordering failure rather than leaving a floating strip. No native mpv view
reparenting, host/audience global raise, audio, queue, history, or server edits.

307 regressions and eight isolated native checks pass. Actual installed host
toolbar is visibly uncovered, and a normal mouse click on its bottom Settings
button opened Settings successfully; closed without changes. No new Python
errors or native crashes. History and server configuration preserved.
Normal app `/Applications/SingWS.app`, still version 0.4.6.5, executable SHA-256:
`2af5d6fe2909af944ea820172d981858285da621e025ff31f4640d261462edfe`.
Frozen code, native loads, architecture/macOS compatibility and strict signing
verified. Previous app/data and private desktop evidence are in
a backup that has since been deleted. Nothing published. Source changes only main
relative to prior candidate. Report and commands:
`docs/verification/2026-08-31-ticker-host-overlap.md`.
The external-display/full-song KaraFun rehearsal remains pending.

## KaraFun false completion fixed and installed — 2026-08-31 11:08 PDT

The operator confirmed they did NOT stop/skip the song. That resolves the audit
question below: the 10:50:25 completion was incorrect. `0.2.18.1.py` no longer
uses empty-queue text as idle, retries only explicit idle before any playing
hint, and completes only on idle corroborated by fresh end-clock evidence or
the verified duration since confirmed playback. Unknown readings, early idle
and watchdog expiry cannot advance rotation. The independent watchdog now
requests manual Complete if it cannot verify an end, rather than forcing it.

18 new state/timer scenarios and 325 combined regressions pass. Manual Complete,
normal-end single-observation transitions and stale-session guards are covered.
No renderer/audio/server changes or history cleanup. The slow accessibility
scrape remains; physical full-song/end/next-video testing is still required.
Normal-app candidate build/receipts: a backup that has since been deleted.
Report: `docs/verification/2026-08-31-karafun-false-completion.md`.
Installed `/Applications/SingWS.app` (normal identity/profile, version 0.4.6.5),
executable SHA-256:
`9adb6b0c305c03bd150a3e0c085b475f699590e7f8c9853d274f66cff1740dff`.
All 18 safety cases also passed against the actual frozen monitor in the new
bundle. Frozen-source, 432 architecture, 840 minimum-macOS, native-load and
strict signing checks pass. Previous app and normal data backed up in the
receipt directory. Launched at 11:08: normal library and relay loaded, requests
closed, audience artwork/ticker visually checked, no new error/crash report.
Server settings and history content remain unchanged. No publication or
physical karaoke playback by this task. Full-song/end/next-video rehearsal is
still needed; do not assume the old permission grant necessarily survives an
ad-hoc rebuilt executable if macOS requests it again.

## Latest actual-app log audit — 2026-08-31 ~10:52 PDT

Read-only audit of the 10:47/10:49 candidate sessions found no new matching
native crash report or Python traceback; server calls are successful. The
earlier Accessibility failure was resolved after the operator restarted:
KaraFun search/activation/fullscreen verification succeeded at 10:49–10:50.

Two monitor behaviors need attention: unknown state (`idle=0 playing=0`) caused
an automatic result retry at 10:50:01, and the first idle reading at 10:50:25
completed a 263-second song with 251 seconds remaining. Local Lol history now
contains one play, acknowledged by the server on its first attempt. Asked the
operator whether they stopped/skipped intentionally; awaiting their answer
before choosing early-stop versus bad-idle handling. Do not delete that test
performance or manipulate their active session without instruction.

Simulated the exact installed frozen monitor with fake UI/network/timers:
unknown -> playing x3 -> idle produces retry at 10s and completion at 34.4s.
No real clicks/playback or live data writes. Probes still take 4.9–10s. The
saved External Headphones device is absent and the normal default fallback is
active. No code/build/settings/server changes were made. Full findings and
smallest proposed changes: `docs/verification/2026-08-31-current-app-log-audit.md`.

## Actual app installed; separate test setup retired — 2026-08-31 10:47 PDT

The operator explicitly requested future manual testing in the actual app with
the normal profile/server, not a separate SingWS Test setup. This preference is
now in AGENTS.md; automated tests still require scratch SINGWS_HOME.

`/Applications/SingWS.app` now contains the same production source hashes as the
private candidate the operator tested, rebuilt using the normal Intel spec and
`com.singws.app` identity. No TEST title/profile hook. Version is still 0.4.6.5;
no publication, installer replacement, or server configuration change occurred.
Installed executable SHA-256:
`4ab3b8f768f4d06081e18f02f5d6acd69289617f568af508b09099f566367751`.

The old app ZIP and 16 normal profile JSON/SQLite files were backed up under
a backup that has since been deleted. The profile backup is private and contains
credentials: do not publish it. Old ZIP integrity/hash, SQLite quick_check,
57 focused regressions, frozen code equivalence, 432 Intel architecture checks,
840 macOS-12 checks, native-library loads and strict deep signing passed.

The installed app was launched, loaded the normal 133,113-track library and
connected its host relay; request accepting remained off. Its exact frozen
catalog method with normal settings returned 94 online KaraFun rows for Adele.
This was a read-only lookup, not a physical playback test. The initial screenshot
shows the audience artwork/ticker and a macOS location-permission prompt. The
operator was asked to choose Allow or Don't Allow; that privacy choice was not
automated. At 10:48:34 the operator started a KaraFun request under Lol and
reached KaraFun launch, but the app reported missing macOS Accessibility
permission. The operator was notified to enable the normal SingWS app in
Privacy & Security > Accessibility, then quit/reopen it. Do not grant that
permission automatically or interrupt the operator's active test. Successful
KaraFun playback/completion/next-video rehearsal remains pending.

The separate app was moved from personal Applications to Trash. Its test data,
ZIP and logs are archived at `../retired-test-builds/20260831-040139/`; the old
launcher is disabled. No test history/settings were imported into the normal
profile. The independent-catalog proposal below is superseded by this choice.
Manual song completions now affect actual history/Fun Stats, as explained to
the operator. Build/install/search receipts: a backup that has since been deleted.

## Private test KaraFun search diagnosis — 2026-08-31

The operator reports the other fixes passed the tests they ran; KaraFun playback
is still untested because catalog results are absent. This is operator-reported
QA, not confirmation of every individual checklist item or hardware scenario.

The installed test executable still matches `19f881e6...76dad`. Its frozen
`SongSearchThread._karafun_rows` returns immediately without a tenant. Both
KaraFun provider/search toggles are enabled, but the current test settings have
no `user`/`tenant` and use `https://beta.wskar.com`. A verified HTTPS catalog GET
to that beta host returns 404; the production public endpoint returns HTTP 200
and 94 Adele results without credentials. Thus there are two configuration
obstacles, not evidence of a KaraFun playback regression. No SingWS bundle
process was running at the diagnostic check.

The private profile's isolation omitted the catalog dependency. Do not fix this
by copying live host credentials: that would reconnect rehearsal queue/history.
The smallest proposed code change is independent public catalog access without
a tenant requirement, using an explicit working catalog URL for the test app.
This diagnosis did not change source, the installed bundle, settings or server.
KaraFun search remains blocked in this test build pending that change.

## Statistics server deployed — 2026-08-31 10:23 PDT

The user explicitly requested production deployment. The five coordinated PHP
files are now live on wskar.com: `api/v1/singer_history_sync.php`,
`api/v1/manage_singer_history.php`, `submitreq.php`, `history_admin.php`, and
`funstats.php`. Deployed hashes match the tested source. WSK requests were
closed, and other open tenant flags had stale/empty queues rather than recent
show activity.

All 16 existing tenant history databases and the previous five PHP files were
backed up under `/root/singws-statistics-deploy/20260831T172139Z/backup`, outside
the webroot in a private directory. Only the additive `provider_metadata`
column was explicitly migrated in WSK history. Every pre-existing WSK history
row and tombstone matches the backup; queue, settings, config and request
accepting flag hashes are unchanged. No history reset or app release occurred.

Five isolated PHP suites passed again. The production PHP interpreter and the
deployed helper each passed a scratch-database probe: twelve syncs preserve one
streaming performance and its provider ID. Live Fun Stats and history admin
return HTTP 200; the updated Fun Stats label confirms new code is served.
Authenticated history sync returns HTTP 200/ok with zero singers/songs/plays;
history management still rejects an unauthenticated request with HTTP 401.

Deployment manifest, HTTP checks and receipt:
`/Users/Daniel/Documents/SingWS/server-deployments/20260831T172139Z/`.
Guarded code-only rollback is prepared at the remote deployment directory's
`rollback.py`; it was not executed. Do not restore an old database over new
performances. Full report: `../SingWS-Server/deploy/2026-08-31-statistics.md`.

The separate SingWS Test profile remains disconnected from the live website;
deployment did not change its credentials or upload its test history. The
operator has confirmed the waveform works in that build. Remaining playback
rehearsal and any deliberate live-client connection are separate steps.

## Private Intel test build — 20260831-040139, not released

The user requested a build to rehearse before releasing. A separately named
and identified app now exists at
`/Users/Daniel/Applications/SingWS Test.app` (bundle ID `com.singws.app.test`).
Executable SHA-256:
`19f881e6fc5ff04ef984afd9f0d7c168a584669bcb1d0616de2d32a00ae76dad`.
The public version remains 0.4.6.5; no release, installer replacement or server
deployment occurred. `/Applications/SingWS.app` is still `ecfcb0e0...a0489`.

The test bundle is frozen from a source snapshot with all pending fixes. Only
that snapshot adds the timestamp to the host window title and a runtime hook
defaulting SINGWS_HOME to a persistent private test profile. The profile,
launcher, ZIP, exact source diffs, manifest and checklist are in
`/Users/Daniel/Documents/SingWS/Test Builds/20260831-040139/`.
It copies the library, playlist, artwork and caches but starts with empty
queue/history, no website credentials and no automatic updates/log emailing.
Real media files are referenced, not duplicated or changed.

Preparing this profile revealed that song_index and three BGM playlist paths
ignored SINGWS_HOME. `song_index.py` and `0.2.18.1.py` now honor the same data
root as the rest of the app; the ordinary default is unchanged. Three tests in
`test_profile_isolation.py` verify database/phrase paths and playlist
load/analysis/save without altering a simulated live profile.

559 desktop regressions, 54 native-backend tests and five isolated PHP suites
pass. The bundle passes 432 Intel architecture checks, 840 macOS-12 checks,
native-library load probes and strict deep signing. Actual frozen main,
BGM engines, transition analysis, song index and profile hook match the build
snapshot. The packaged helper handles a missing file then measures valid audio.

The test app was launched, but its initial GUI inspection reached the macOS
Documents-access permission prompt. The user was asked to click Allow; startup
and physical playback cannot yet be signed off. The old app declined a graceful
AppleScript quit (-128); it was not killed. Ask the operator to close that copy
before playback tests. Only the built-in display is attached. Full status:
`docs/verification/2026-08-31-private-test-build.md`.

## Log audit / safe crash reporting — source fix, not installed

Reviewed the user's three Aug 30–31 logs and macOS crash reports. The four
14:30 Aug 30 native aborts belong to a temporary `/private/tmp/.../SingWS.app`
bundle, not the evening installed session. Their faulting stacks enter
`QMessageBox.critical` through Python's exception hook. The current handler
still tries to construct that widget without checking for a GUI application.

`exception_handler` now shows its existing dialog only on a live QApplication's
GUI thread, never during shutdown or in headless/startup contexts. Report-write
and optional send failures cannot suppress the original exception on stderr;
the dialog no longer claims a report was saved if writing failed. Eight isolated
process tests cover these cases, including a real unhandled exception exiting
with Python status 1 rather than a native abort. Two regressions failed before
the change. The combined app/history/playback/analysis run passes 530 tests.

The 01:35:14 completion conflict for request 1788161651096 recovered at
01:35:20 on attempt 2. No server lifecycle changes were justified by that log.
Older helper timeouts and unsupported-compression records precede the installed
analysis fixes and documented archive recovery below. Preview-server timeouts,
retryable decoder errors, and startup stalls remain observations, not proven
new root causes. Details and exact test command:
`docs/verification/2026-08-31-log-audit.md`.

Installed executable remains `ecfcb0e0...a0489` (0.4.6.5). The app was running
and its live log continued normal polling during this audit; all tests used
scratch SINGWS_HOME. No build, install, server deployment, media edit, or live
show-data reset was performed. Rehearse a rebuilt app before show installation.

## Singer/Fun Stats inflation — source fix in both repos, not deployed

Reproduced the cause of Shawn's growing counts with real app/PHP sync round
trips against scratch SQLite. Python kept KaraFun provider/track identities;
PHP stripped provider metadata and returned the same song as a local row.
Subsequent syncs summed the streaming records into that growing local snapshot.
The pre-reset backup shows this exact pattern (Self Aware: local 2,925 plus two
KaraFun rows of one play each; Apocalypse: local 1,555 plus a KaraFun row of one).
The deployed PHP file hashes match the pre-fix source.

Server history now retains provider metadata in an additive JSON column and
uses the same song keys as the app, including provider-specific deletions.
Duplicate cumulative snapshots merge by maximum, not addition. Desktop remote
merges preserve newly completed plays even when a response has an equal/older
timestamp. Singer totals returned by sync derive from the retained song counts.
Web submission no longer records a performance; the existing host completion
path does. Fun Stats ignores zero-play saved songs. The recent-song card is
accurately labelled with lifetime totals for recently sung songs: the existing
data does not contain dates for every performance, so it cannot calculate true
30-day totals.

514 combined app regressions pass, including actual Python↔PHP round trips,
provider preservation, repeated sync, legitimate repeats, stale responses,
deletion, and duplicate/aborted completion callbacks. Five PHP suites cover
history, Fun Stats, request retries, singer management and pending completion,
using a disposable server copy. Run them safely with
`python3 ../SingWS-Server/tools/run_history_regressions.py`.
Full findings, test commands and rollout boundaries:
`docs/verification/2026-08-31-statistics-counts.md`.

The cleared live history and its backups were not modified. Existing inflated
song snapshots cannot be repaired reliably from aggregate totals; do not
restore the old history. These changes are not in the installed app or on
wskar.com yet. Deploy the coordinated server files outside a show, then build
and validate the app with the other pending fixes before installation.

## Background track transitions — source fix, not installed

The user requests smoother BGM crossfades with no silence between tracks.
The old scheduler selected `verified_dead_tail` but still counted back from
the file's duration, shortening the overlap to two seconds. Show-log examples
include Moliy / Shake It To The Max (21:50:03), Dorothy / TOMBSTONE TOWN
(22:28:25), and Jimin / Who (23:06:37). Their cached silent tails are 2.6,
2.2, and 2.2 seconds respectively: that policy could begin after audible
content had already finished. Paired native logarithmic slides also did not
use the equal-power curve already described in the player.

`0.2.18.1.py` now schedules against validated audible content bounds and
compensates for queued BASS audio. Known incoming silence is skipped with a
small analysis margin; unknown/stale metadata retains the full file bounds.
Late/manual transitions shorten to fit remaining audio, with a 100ms recovery
fade after EOF or detected silence. A worker prepares the next BASS source;
its result is discarded after a playlist, source, or engine change.

`bass_background_engine.py` holds the next source paused, not merely muted,
and applies paired equal-power BASSmix envelopes on the native audio clock.
Normalization and mixer-master fades remain separate. Completion follows the
incoming audible position, so pausing cannot cause a GUI timer to promote an
unfinished fade. The meter ignores paused preloads. The libmpv fallback accepts
the same incoming content offset; its mixing implementation is unchanged.

Native decode-only mixer tests cover continuity, normalized power, untouched
preloaded intros, cancellation, partial envelope failure, EOF and short files.
Scheduler tests cover silent padding, queued audio, late/manual starts,
stop-after-current, pause, and stale preload results. Details and commands:
`docs/verification/2026-08-31-bgm-crossfades.md`.
Installed 0.4.6.5 remains `ecfcb0e0...a0489`. No build/install or physical-output
listening test was performed. Do not present this as an installed/show-tested
fix; a rebuilt app still needs repeated playlist transitions and karaoke
interrupt/resume checks with the actual output device.

## KaraFun premature completion / orphaned launch — source fix, not installed

Dan / Sugarcult / Memory was marked complete at 00:12:59 in
`singws_2026-08-30.log`, while search was still running. The worker activated
the result and published playing at 00:13:09 after the active session and
Complete dialog were already gone. No monitor existed at the premature
completion, and no monitor completion event was logged.

Reproduced a matching trigger against the pre-change dialog: Qt Return invokes
the default Complete button. The dialog was shown/activated after automation
started, while its search sends Return. Actual key routing that night is not
logged, but the old dialog's erroneous Return behavior is reproduced.

The dialog now has no default/auto-default buttons, opens before the launch
worker, and does not activate itself for automatic playback. Explicit Complete
still works. Launch script calls check active-session identity before/after;
queued handoff/fade/success callbacks ignore ended sessions. Monitor results,
queued completions, and dialog buttons are bound to their original active
session, even if the same request object is later replayed. Finish requests now
log action and submission state. Already-running external AppleScript cannot
be interrupted by these Python checks; they prevent subsequent launch actions
and stale result application.

Added `test_karafun_lifecycle.py`: keyboard versus explicit completion,
cancellation during search, old-dialog rejection, and monitor completion versus
a replacement session. 136 focused lifecycle/provider/performance tests pass
with scratch SINGWS_HOME and qtvenv offscreen. Live logs stayed unchanged.
The installed 0.4.6.5 executable is still `ecfcb0e0...a0489`; no new build
has been installed. Before treating this as show-ready, run a built-app test:
KaraFun search → full song → automatic/manual completion → next local CDG/MP4
with visible audience video, ticker, and correct focus; repeat with a cancelled
launch and a returned/replayed request. This is not yet end-to-end validated.

## Respect laptop background work — source fix, not installed

The user reports SingWS raising over other laptop apps. Installed 0.4.6.5
still contains the one-second ticker restack guard and unconditional KaraFun
host activation; its hash remains the documented `ecfcb0e0...a0489`.
The ticker guard now skips restacking while the application is inactive,
without stopping playback or rendering. Delayed startup focus requests only
activate the host if SingWS is still foreground. KaraFun host-return requests
(including the delayed retry) only reclaim focus while SingWS or KaraFun is
foreground; a browser/editor remains in front. Transparent audience restoration
no longer explicitly activates the entire application.

Audience geometry, native child ordering, display placement and playback are
otherwise unchanged. Three regressions cover background ticker restacking,
foreground-app selection and delayed focus restoration. Verify with scratch
`QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest
test_performance_safety test_karafun_provider`. Native macOS focus behavior and
audience ticker visibility while switching apps still require an installed
on-screen test; these changes have not been built or installed.

## Live singer-history reset completed 2026-08-31

At the user's request, local and wskar tenant `wsk` singer history were backed
up and cleared while SingWS was closed and the local queue empty. The legacy
server `history` table was also cleared so old totals cannot reappear when a
singer returns. Deletion markers were retained/added to block older sync data.
Live history sync and Fun Stats both verify zero singers and performances.
Backups and reset details:
`../history-reset-backups/20260831-021037/README.md`.
Queue, library and settings were unchanged. This was a data reset, not a fix
for the underlying statistics inflation. Do not restore old history casually.

## Audience rotation: hide singers without songs — source only

At the operator's request, `RotationView.update_rotation` excludes empty and
all-skipped-song slots from the audience rail, displayed count, rotation-start
header and upcoming-singer panels. Host slots, ordering, markers, history and
server data are untouched. The existing model-view regression now verifies
the new audience filtering and preservation of host data. This change is not
built or installed; installed 0.4.6.5 remains unchanged.

## KaraFun activation and slow verification — source fix, not installed

Athena / Mahalia / Sober in `singws_2026-08-30.log.2026-08-30`:
search began 23:56:22, exact result at :30, renderer handoff finished :53
after a retry, first idle probe at 23:57:09, recovery at :09, first positive
playback hint at :28 (with an incorrect manual warning), confirmation at :46.
These are local automation delays; logs do not establish a streaming/network
failure or the exact instant audio started. The installed executable matches
the documented 0.4.6.5 hash and contains the earlier nonblocking handoff fix.

`0.2.18.1.py` now completes result activation and its existing 0.8-second
settling delay before scheduling the renderer handoff, preventing overlapping
coordinate-click sequences. Completion polling reads role plus only relevant
button/static-text attributes, instead of name/description/help/value on every
node; polls taking >=4 seconds log their measured duration. A first positive
playing hint suppresses the erroneous manual-start warning while the second
observation is pending. Existing matching, renderer recreation, screen placement,
two-observation confirmation and end-of-song behavior remain in place.

Focused tests: scratch-data `QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest
test_karafun_provider test_performance_safety`. The new monitor simulation covers
idle recovery followed by slow positive observations without a false warning.
The generated monitor script compiles with `osacompile`. No live automation
was executed; actual startup speed and display behavior still need a built-app
song test before shipping. The now-singer animation fix below is also unbuilt.

## Now-singer bar animation — source fix, not built or installed

The operator card's `BarLevelMeter` still expected level samples from retired
audio paths; current mpv playback has no level tap. It now accepts a playback
provider and uses decorative motion when no measurement is available. The host
gates this on karaoke playback and its pause state. Measured silence and stale
samples retain their existing zero-level behavior; stopping still stops the
timer. No audio routing, DSP, transport levels, or end detection changed.

Changed `0.2.18.1.py` and added two regressions in
`test_performance_safety.py`. All 109 tests pass using
`SINGWS_HOME=$(mktemp -d) QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest test_performance_safety`.
An isolated offscreen widget run also captured distinct timer-driven active
frames and a paused baseline. The live log count remained 99,025 lines.
Installed 0.4.6.5 executable hash still matches `ecfcb0e0...a0489` above;
decompressed main bytecode confirms the legacy provider and absence of this fix.
A built-app playback/visual check remains before shipping.

## Same-version 0.4.6.5 release refresh

The verified running app is packaged in a new Intel installer without changing
APP_VERSION. Release verification and checksum are documented in
`docs/verification/2026-08-30-loudness-release-refresh.md`. The refresh supersedes
the older analyzer blocker below. Existing 0.4.6.5 users need a manual download
because there is no version increase. The live scan was left running.

## Follow-up: continue past individual analysis failures — installed

The first pipe fix stopped the user's real Full scan after White Hot succeeded
and the next track returned `libmpv ebur128 produced no integrated loudness`.
That safeguard incorrectly treated a reported per-track error like a dead
helper. The follow-up introduces `AnalysisTrackError`: a received error response
skips that track without writing a permanent failure, closes its native helper,
and continues. Transport/pipe failures still use `AnalysisHelperError` and stop
the batch safely. The native worker resets its session after reported errors,
so consecutive problem tracks cannot disable subsequent analysis.

Installed `/Applications/SingWS.app` is still 0.4.6.5, executable SHA-256
`ecfcb0e0771c7ff6b33eee267528540117c85d923ac06acc29fc2122a40a0489`.
Previous bundle:
`/Applications/SingWS-0.4.6.5-before-track-error-fix-20260830.app`.
No new cache repair was required: 14,123 valid measurements and 70 structural
failure records were retained. Both scratch and normal installed launches
passed; the scratch app was closed before normal app reopened at 14:31.

Tests: 805 tests + 24 subtests, plus 247 focused GUI/regression tests.
A scratch run of the next 16 actual library items (diagnostic audio timeout
shortened to 8s) completed without cancellation: 7 measurements persisted and
reloaded unchanged, 9 analysis errors left uncached/retryable. These include
native decode timeouts, not only immediate missing-LUFS responses. The Coolio
MP3 produced many decoder `Header missing` errors and format changes; archive
integrity alone does not imply valid audio. No media files were changed.
The packaged helper was separately verified to report a missing-file error and
then successfully measure a valid tone. Strict signing, frozen-code markers,
432 Mach-O architecture and 840 minimum-macOS checks passed; installed and
staged executable hashes match. No full-library completion or new DMG claimed.

## Loudness helper pipe fix — installed 2026-08-30

The analyzer blocker below is now repaired in `/Applications/SingWS.app`
(still version 0.4.6.5, executable SHA-256
`a6f4f1717d16c9a293f9990e9d1ad324bee1c9fb18525b2a2e606d1cace5ccc9`).
The old app is retained at
`/Applications/SingWS-0.4.6.5-before-scan-fix-20260830.app`.

Root cause reproduced against the installed helper: TextIOWrapper.readline
read ahead past the startup log into the result, while select subsequently
waited on an empty OS pipe. The helper had already finished, and was waiting
for another request. Binary unbuffered pipe reads with explicit line assembly
fix this, including partial-line timeout handling. The batch now stops and
cancels companion workers on helper failure without caching it as a bad song;
the UI reports the stop. Fast mode also propagates helper errors. Failure
schema 3 retries ambiguous version-2 failures. Structural ZIP failures remain
cached. The repair utility refuses pending checkpoints requiring clean quit.

SingWS was closed cleanly before repair: 119,276 ambiguous failure records
removed, all 14,122 valid records then present preserved exactly, including
all 14,120 original measurements. Backup:
`~/SingWS/cache-backups/loudness-poison-repair-20260830-141831/loudness.json`.
70 structural failures remain. No full rescan has been started by this task;
normal Full/Turbo can resume without Force.

Verification: 802 tests + 24 subtests in the non-GUI runner; 244 focused
GUI/regression/transport tests; final repair tests 3/3. Three real previously
poisoned ZIPs successfully measured through the staged helper, persisted and
reloaded unchanged in a scratch home. Binary markers, 432-file x86_64 check,
840-file macOS-12 check, bundled media loading, strict signing, scratch launch
and installed launch passed. Installed executable matches staged SHA exactly.
No new DMG/release was published. Full-library completion and external-display
playback were not tested in this task. Source changes remain uncommitted.

## 0.4.6.5 readiness audit: analyzer blocker found

The installed and running app is 0.4.6.5; frozen-code markers, strict signing,
architecture, minimum macOS version, bundled media loading, and DMG checksum
all pass. The non-GUI runner passes 799 tests plus 21 subtests; a separate
matching-Qt run passes 202 GUI/performance tests after correcting two obsolete
test assertions. All 401 repaired ZIPs pass integrity checks, and the 70
quarantined files are absent and no longer indexed. Details and test outputs:
`docs/verification/2026-08-30-show-readiness.md`.

**Do not treat the loudness repair as complete.** The 08:12 live cache contains
68,667 new-version `no measurable loudness` failures and 24 helper timeouts.
After three helper failures, the isolated session disables itself but the batch
keeps marking subsequent unmeasured songs failed. The old-version repair tool
does not remove these version-2 records. No live data or application code was
changed by the audit. Stop further full/Turbo scans pending a session-failure
guard, real installed-helper validation, and a backed-up repair while closed.
External-display playback, audible crossfades, and KaraFun end-to-end acceptance
also remain unverified by this audit.

## Detached OpenKJ-style ticker repair (installed for visual test)

The Intel show Mac now uses the pre-rendered QPainter ticker that was smooth in
the 0.4.1-era builds, hosted in a separate non-activating transient window.
This restores its elapsed-time, refresh-rate-paced fractional pixmap scrolling
without returning the ticker to mpv's native-child stacking hierarchy.  The
uneven QML cached-layer experiment was removed.  Other platforms retain the
render-thread QML ticker.

The installed `/Applications/SingWS.app` is this build and the replaced bundle
is preserved at `/Applications/SingWS-0.4.6.4-qml-ticker-20260830.app`.
The installed app launched at 08:12 with
`backend=detached-openkj-painter`, while the Qt Quick audience transition layer
also initialized normally.  Automated verification passed: 200 focused GUI /
show-screen tests and the full 799-test plus 21-subtest runner.  The build
passed x86_64, bundled-media, macOS-12, and strict signing checks; only DMG
creation hit the machine's recurring `hdiutil: Device not configured` error.
CDG/video and actual scrolling smoothness still require the operator's live
visual test.

The detached painter now also implements the ticker lighting natively in the
same paint pass: moving ribbon/atmosphere, queue-change flash, countdown pill
and pulse, live indicator, and edge glow.  It exposes the existing
`ticker_vfx_enabled` control, while retaining the isolated painter surface and
pre-rendered marquee.  The follow-up build passed 181 focused tests, the full
799-test plus 21-subtest runner, strict signing, and a scratch launch reporting
`backend=detached-openkj-painter effects=available`.  It is staged at
`/private/tmp/singws-painter-vfx.Ru9c2n/dist/SingWS.app`, but is not installed:
the running normal app rejected the clean quit request with `User canceled`,
so its bundle was deliberately left untouched.

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
were subsequently moved to Trash at the operator's request under
`~/.Trash/deflate64-repack-20260830-074824/` (397 files) and
`~/.Trash/deflate64-repack-20260830-075352/` (4 files). The converted library
copies were reverified immediately before that move.

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
