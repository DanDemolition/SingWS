# SingWS handoff

Updated 2026-08-22.

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
