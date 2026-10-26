# Stage E: adding a song from the shell or the phone (design, 2026-10-26)

Status: DESIGN ONLY, nothing built. Needs the operator's answers to the questions at the end.

## What exists today
* The operator's Add button ends in `KaraokeApp._add_song_to_queue(singer_name, song_data, track=None, remote_meta=None)` (0.2.18.1.py ~44886). It: prunes expired empty rotation slots, capitalizes the singer's name, parses duets ("Amber & Delores" files under Amber with a duet display), builds the queue entry (song_info, key, tempo, display name, duration, disc id), looks for an existing duplicate song for that singer, applies the queue/rotation rules (per-singer limits, rotation lock, "already sang this round"), and saves. A local add can show modal dialogs (for example "Invalid Zip" for a bad CDG+MP3 zip pair) and moves the operator's selection.
* Phone requests take a second, tested route: the server holds a request, the app polls it, and `_add_song_to_queue(..., remote_meta={...})` adds it with `request_id` bookkeeping (tombstones, replace, order, delivered marks). That path never shows dialogs and never moves the operator's selection.
* The server already has a host-authenticated writer for requests (`api/v1/upsert_host_request.php`, API-key auth, used by the app itself).
* The shell can already search the library (read-only, typo tolerant) and already edits the LIVE rotation through id-based commands with a list fingerprint (`expect_queue_sig`), `shell_commands` off/dry_run/apply and request-id dedupe.

## Options
**A. Direct app command `queue.add_song`.** The shell/phone sends `{singer, song_id or path, key}`. The app resolves the song ONLY from its own song index (never trusts a path from the wire), then runs the same add routine as the Add button, in a mode that refuses instead of asking (no dialogs). Fast (no server round trip). New app-side code path to test against the real handler; every rule above (limits, locks, duets, duplicates) applies automatically because it IS the Add routine.
**B. Server-mediated request.** The browser asks the server (host PIN session) to create a request for a singer; the app picks it up through the normal poll and adds it through the tested phone-request path. Reuses everything phone requests already have (order, dedupe, tombstones, delivered marks) and cannot show a dialog. Slower (a poll interval, ~1-2 s) and needs a new PHP action plus a deploy.
**C. Do not build.** Phone requests stay the only remote add path.

## Guards every option keeps
* `shell_commands` mode (off refuses; dry_run validates and logs only; apply applies).
* Song must exist in the song index (or in the KaraFun catalog if the operator wants that); an unknown id is refused with `unknown_song`.
* Singer name length/character limits as the Add box has; a missing singer is created only if the operator allows it (question 3).
* A queue limit, a rotation lock or a duplicate is REFUSED with a clear code, never overridden remotely (question 4).
* The ack says exactly what happened: `added`, `duplicate`, `limit`, `locked`, `unknown_song`, `dry_run: would add`.

## Questions for the operator
1. A, B or C?
2. Which songs: library files only, or KaraFun catalog songs too (they need provider fields and a different duration path)?
3. Singer: only people already in the rotation, or may it create a new singer?
4. At a limit, lock or duplicate: refuse, or let the operator override from the shell/phone?
5. Where: the Rust shell, the phone web page, or both?
