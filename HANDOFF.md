# SingWS handoff

Updated 2026-08-18.

## Committed on `work/show-fixes-2026-08-18`, NOT RELEASED, NOT INSTALLED

`APP_VERSION` is `0.4.5.4`. `/Applications/SingWS.app` is still **`0.4.5.3`**
and has never run any of this. The `SingWS-0.4.5.4-x86_64-installer.dmg` in the
repo root was built partway through the session and is **stale** — it predates
most of the fixes below. Rebuild before installing.

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
12. The CDG visual offset now reaches the follower backend. **UNCALIBRATED.**
13. A host song-swap stranded the singer's waitlisted replacement. Removing
    their last song holds the rotation row for 180s, but the promotion only
    accepted slots emptied by the SERVER, so `host_remove_song` was refused and
    the operator had to add the song by hand. Widened via
    `_is_replaceable_empty_slot_reason`; repeat-preservation stays server-only,
    and tombstones still prevent resurrecting anything the host deleted.

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
