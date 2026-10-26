# Stage E: adding a song from the shell or the phone (design, 2026-10-26)

Status: BUILT for the Rust shell (2026-10-26); the phone page is NOT built (it needs a library search over the relay first). Operator's answers: option A (direct app command),
library files only, singers as the Add box does today (a new name creates a new singer), and REFUSE with a clear message at the song limit, a locked rotation, or a song the singer already holds.

## As built
* App: `KaraokeApp.shell_queue_add_song` + `queue.add_song` in `_execute_shell_queue_command` (args `singer`, `song_id` (library row id), optional `key` -4..4, optional list fingerprint). It runs the operator's own `_add_song_to_queue` in a new `quiet=True` mode: same queue logic, but it never clears the singer box, resets the key selector or moves the selection, never bypasses the song limit, and says what it did. Refusals (ok=false): `bad_singer`, `unknown_song`, `file_missing` (unplugged drive), `missing_mp3`, `rotation_locked`, `duplicate` (the singer already holds the song, a skipped singer counts, a skipped SONG does not), `limit`. `dry_run` runs every check and adds nothing. Undo entry "Add Song"; saves like the other shell edits.
* The app's own zip check (`is_mp3g_zip`) looks only at the extension; the shell inherits that rule.
* Rust: `queue.add_song` validation in singws-control (singer 1-60 chars without control characters, `song_id` a whole number >= 1, key -4..4) and plain-language messages for every code. Shell: the Library tab has a singer box, a key selector and an Add button on each result, sharing the Controls tab's single connection; an add is sent without a list fingerprint (it does not depend on positions).
* Tests: test_shell_add_song.py (300-case differential against the operator's real add, every refusal, dry run, argument checks, request-id dedupe), tools/mutate_add_song.py (29 mutants, 0 survivors, 3 documented equivalents), the Rust protocol/message tests, and a loopback scenario with the real Qt shell and real relay.
* Not built: the phone page (needs a `library.search` over the relay), KaraFun catalog songs, and any override at a limit.


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
