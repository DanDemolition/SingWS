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

From the 2026-08-16 show logs:

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

## Uncommitted: brand picker + KaraFun playback fixes (NOT BUILT, NOT INSTALLED)

Three defects from the 2026-08-16 show, all source-only. `APP_VERSION` is
bumped to `0.4.5.4` and `dist/` plus `SingWS-0.4.5.4-x86_64-installer.dmg` were
built, but **nothing was installed** -- `/Applications/SingWS.app` is still
`0.4.5.3`.

1. **Brand picker listed one brand many times.** `canonical_disc_brand` always
   mapped `KARAOKE VERSION`/`KV`/`KARAOKE VERSION 00`/`KV 43403` to `KV`, and
   `normalize_disc_priority` deduped them, so the ten-slot preference was never
   actually wasted. But the pickers were built from raw `disc_id` values --
   per-disc codes, not brands -- giving **121,650 entries** with Karaoke Version
   spread over 20,901 of them. New shared `library_brand_choices()`
   canonicalises, keeps only values that name a brand (known alias, or a bare
   token with no disc number), and orders by library coverage: **134,266 tracks
   -> 38 choices**, KV/SC/CB/SF/SBI first, unaliased house brands (WSK, SINGA)
   retained.
2. **KaraFun played the wrong song.** At 22:31 it was asked for Sugarcult -
   Memory and played Memory from Cats. The right query *was* tried twice and
   both attempts died on AXError -1719 "Invalid index" because KaraFun's window
   was still being built one second after launch. Each failure fell through to
   the next, looser query, so attempt 3 searched the bare title. Transient
   UI-not-ready errors (`_is_karafun_ui_not_ready_error`) now retry the *same*
   query up to 3x, and the matcher verifies the artist on the result row:
   artist-confirmed rows return `FOUND`, title-only rows return `TITLE_ONLY`,
   and a query carrying no artist accepts only `FOUND`.
3. **Background music came up ~30s before the song ended.** The duration
   fallback counted from the renderer handoff, but KaraFun spent ~45s launching
   and going fullscreen before a note played, so the 235s clock expired
   mid-outro, advanced the rotation and started the BG deck over the ending.
   The countdown is now rebased to the first *confirmed* playback signal
   (`playing_reported` or an advancing clock); the fallback's own countdown is
   explicitly excluded from rebasing it.

Files: `0.2.18.1.py`, `test_recent_regressions.py`, `test_karafun_provider.py`.

Verification: 908 tests, 19 new, all passing; the same 4 unrelated failures as
at HEAD. `test_karafun_provider` asserted the old `{"FOUND", "FIRST"}` gate as
source text and was updated to the widened set. **Fixes 2 and 3 drive
KaraFun.app through AppleScript and cannot be covered by tests -- they need a
real KaraFun song before they go near a show.**

## Uncommitted: KaraFun history re-add (player side NOT BUILT; server side IS LIVE)

A KaraFun song in a singer's history could not be re-added. Two independent
causes, one per repo.

**Server (deployed 2026-08-17 23:55, verified live).** A `kf_<id>` is the
server catalog's own SQLite rowid, not a KaraFun identifier, and
`kf_import_csv()` did `DELETE FROM songdb` then reinserted under
`AUTOINCREMENT` — so *every* id changed on *every* catalog refresh, killing
every id ever written into a singer's history. "Sugarcult - Memory" moved
kf_2219191 -> kf_3168777 -> kf_3255418 while staying in the catalog throughout.
`submitreq.php` then hard-failed the lookup with "This KaraFun catalog entry is
no longer available", despite the branch 30 lines below it already re-resolving
stale local ids from artist/title. Now: `kf_resolve_catalog_song()`
(`karafun_catalog.inc.php`) treats a stored id as a hint and re-resolves by
natural key, and the import upserts on `norm_key` so surviving songs keep their
ids. Deployed files match by SHA-256 and both dead ids resolve to kf_3255418
against the live 88,566-row catalog.

**Player (source only — not packaged, not installed).**
`_resolve_history_song_track` searched only the local library, but a KaraFun
streaming song's path is the synthetic `karafun_streaming:<id>` reference,
which is in neither `singws.db` nor `tracks.json` — so the add always failed
with "Could not match this history song to a local track", whatever the id.
`_karafun_track_from_history_song()` now rebuilds the external track via the
existing `_build_karafun_streaming_track()`. No network call: playback drives
KaraFun.app by artist/title search and never uses the catalog id.

Files: `0.2.18.1.py`, `test_recent_regressions.py`; server
`submitreq.php`, `karafun_catalog.inc.php`, `tools/test_karafun_catalog_ids.php`.

Verification: 889 player tests (7 new, all passing; the same 4 unrelated
failures as at HEAD), 23 new server assertions passing, all 10 existing server
tool tests passing. **The player half has still never been launched.**

## Uncommitted: 2026-08-16 show fixes (NOT BUILT, NOT INSTALLED)

Four defects found in the 2026-08-16 show logs, all fixed in source. **None of
this has been packaged, installed or launched.** `/Applications/SingWS.app` is
still the released `0.4.5.3` that produced the faults below. Do not describe
these as live.

1. **Loudness scan leaked ~1 MB per track.** `_measure_loudness_lavfi` built a
   fresh mpv core per track and `mpv_terminate_destroy` never returned the
   memory, growing the app from 472 MB to 8,635 MB over the five-hour show.
   `libmpv_media_jobs.LoudnessSession` now reuses one core per scan pass.
   Measured on 40 distinct library tracks: **1.047 MB/track before, 0.025
   MB/track after (42x)**, with identical LUFS/peak in both full and fast mode.
   The predicted 8,885 MB for the night's 8,486 scans matches the 8,163 MB
   actually observed. A session that fails three times running disables itself
   so an older libmpv without ebur128 still falls back to the WAV analyzer.
2. **Four "crashes" from the progress dialog.** The delayed re-raise in
   `_bring_analyze_dialog_to_front` guarded `QTimer.singleShot()` rather than
   the callback body, so closing the dialog inside 750 ms threw
   `RuntimeError: wrapped C/C++ object of type QProgressDialog has been deleted`
   into the event loop. The callback is guarded now.
3. **Scanning underneath a live song.** A full-library pass ran for the whole
   show and produced 744 GUI stalls, worst 6.1 s. `AnalyzeLibraryWorker` now
   holds between tracks while `karaoke_playing` is set, resumes automatically
   between songs, reports the hold in the dialog, and stays cancellable while
   held.
4. **A corrupt log bundle.** The 22:15:45 crash left a 712-byte ZIP with no
   end-of-central-directory record and logged no `[LOG-EMAIL]` line at all.
   Packaging now builds under `.zip.partial` and renames only on success, and
   the crash-email thread logs its own failures instead of dying silently.

Also fixed: `test_volume_analysis_dialog_is_resurfaced_frontmost` asserted a
dialog string (`"Measuring loudness"`) that had not existed for some time. It
failed at unmodified HEAD too — nobody saw it because no venv could run that
suite (see below).

Files: `0.2.18.1.py`, `libmpv_media_jobs.py`, `test_recent_regressions.py`,
`test_performance_safety.py`.

Verification: 882 tests run, 7 new ones added and passing. The 4 failures
(`test_libmpv_background_engine`, `test_phrase_detect` x2, `test_mac_keep_awake`)
fail identically at unmodified HEAD and are unrelated. **Nothing was built or
launched** — per the live-show rules the next step is a package, an install and
an actual run before this goes near a show.

`qtvenv/` now exists (PyQt6 6.9.1 matched pair, per the "Running the tests"
section of `AGENTS.md`). It is the only venv here that can construct a
QApplication, so `unittest discover` finally completes instead of aborting. It
is untracked build scaffolding, not part of the app.

## Previously released work

**There is no other uncommitted product work in this tree.** Everything described by
the previous handoff — the AirPlay routing repair, the decorative MP4 stretch
and frozen-frame crossfade, the CDG black-screen demuxer fix and visual
watchdog, the KaraFun fast-path restoration, the pre-show diagnostics and
show-cycle regression, and the loudness-analysis PCM repair — was built,
released and superseded across `v0.4.5.0` through `v0.4.5.3`. That handoff was
stale by four releases; this file replaces it.

## Current state

`main` is at `64ead92 Release v0.4.5.3`, tagged `v0.4.5.3`, with nothing
unpushed. `APP_VERSION` is `0.4.5.3`. `SingWS-Server` `main` is at
`ca90564 Restore accidentally removed waitlist requests`, also fully pushed.

The GitHub release `v0.4.5.3` is public (2026-08-16) and carries the Intel
installer only. `docs/release.json`, the local
`SingWS-0.4.5.3-x86_64-installer.dmg` and the published asset all agree on
SHA-256 `821184448d9e0801d128b86cf2e578fce4f7e2b85ed8ae851d3aa04b2977fae2`.

`/Applications/SingWS.app` reports `0.4.5.3` and is the same build as the
release: its `libsingws_mpv_bridge.dylib` `__TEXT,__text` hash
(`49b4615b…3900530`) is identical to `dist/SingWS.app`, which is what the
0.4.5.3 DMG was packaged from. The operator installed from the build output
directly rather than from the DMG; the code is the same either way.

## Open items

- **arm64 is one release behind.** `v0.4.5.2` shipped both an Intel and an
  Apple Silicon installer; `v0.4.5.3` shipped Intel only. Per the standing rule,
  an arm64 build must be produced and verified natively on an Apple Silicon Mac
  before it is advertised — it cannot be smoke-tested on this Intel dev Mac.
- **`CHANGELOG.md` stops at `0.4.4.10`.** None of the four `0.4.5.x` releases
  have changelog entries, and their commit messages are single-line with empty
  bodies, so the changelog is currently the weakest record of what shipped.
  Reconstruct the entries from the diffs before the next release.
- **Uncommitted documentation wiring** (the only dirty state in either repo):
  `CLAUDE.md` here is modified to import `@HANDOFF.md`, and
  `SingWS-Server/CLAUDE.md` is a new untracked file importing this repo's
  `AGENTS.md` and `HANDOFF.md`. Documentation only, no behaviour change.
  Because `CLAUDE.md` now imports this file, keeping it current is load-bearing:
  a stale handoff is read into every session as though it were pending work.

## Rollback path

The previously preserved `SingWS-before-*.app` / `SingWS-broken-*.app` bundles
are **gone** — `/Applications` now holds only `SingWS.app`. There is no
installed fallback to switch to mid-show. The rollback is the retained
`SingWS-0.4.5.2-x86_64-installer.dmg` in the repo root, which must be installed
before it can be used. If a preserved rollback bundle matters for the next
release, copy the current app aside before installing over it.
