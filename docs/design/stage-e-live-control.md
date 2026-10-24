# Stage E: how the Rust shell sends live controls to the running app

Status: DESIGN for review (2026-10-24). Nothing here is built. The three panels that exist (library, singer history, rotation) are read-only and write nothing.

## What the code does today (checked in 0.2.18.1.py)

* The running Python app owns the queue in memory and is the only writer of `queue.json` (debounced saves, plus `save_data()` after edits).
* There is already ONE command executor for remote control: `_execute_host_control_command(action, args) -> (ok, message)`. Two sources feed it: the WebSocket relay (`_on_host_relay_command`)
  and the HTTP poll (`handle_host_commands_from_thread`). It covers transport and levels only: play_pause, stop, next/skip, restart, seek, key/tempo, karaoke and background volume, bg play/pause.
* Queue edits are NOT commands. Move up/down, remove and clear (`move_up`, `move_down`, `remove_selected`, `clear_queue_with_confirmation`) work from the SELECTED ROW of a widget list,
  and each carries side effects: an undo snapshot, `_sync_remote_singer_order`, remote-request tombstones (`_delete_remote_request`), rotation-marker repair, the "playback detached from rotation" rule, ticker refresh.
* The app already treats identity by ids, not names or rows: `singer_id` and `_ensure_queue_entry_id` (`remote:<id>` or `local:<uid>`).

So the shell cannot "call move_up". It needs id-based entry points in the app, and a safe way to deliver intents to them.

## Principle

Until the Rust core owns the queue (Stage B drive, not started), **the Python app stays the single writer and the single executor**. The shell sends INTENTS; the app validates and executes them through the
same internal code the buttons use, then publishes the result. The shell never writes `queue.json` and never mutates app state by any other route.

## Delivery: a command inbox (recommended), compared with the alternatives

| Option | Verdict |
|---|---|
| A. Inbox directory `~/SingWS/commands/` (one JSON file per command, atomic rename), results next to it | **Recommended.** No new open port, survives a restart without replaying, easy to log and to dry-run, same-user file permissions, trivially testable. Latency about the poll tick (250 ms). Same machine only. |
| B. Unix-domain socket owned by the app | Faster and gives request/response, but needs a server thread inside the Python app and a handshake; more code on the live path. A good later swap behind the same interface. |
| C. Send through the existing server relay (`host_commands`) | Works across machines, zero new app code for transport commands, but needs internet and credentials in the shell, and queue edits are not in that vocabulary anyway. Right choice only if the shell runs on a different machine from the show. |
| D. Shell writes `queue.json` directly | **Rejected.** Two writers, no undo, no tombstones, no server order sync. |

Keep a `CommandSink` interface in the shell so A can become B (or the Rust core) without changing any panel.

## Command format (version 1)

```json
{"v": 1, "id": "01JBX...", "session": "<app session nonce>", "issued_at": 1792830000, "expires_at": 1792830010,
 "expect": {"queue_version": 412},
 "action": "queue.move_singer", "args": {"singer_id": "ab12...", "direction": "up"}}
```

* `id`: sortable unique id; the app applies commands in id order and remembers processed ids, so a duplicate (double click, retry, two shells) is applied once.
* `session`: a nonce the app puts in `live_state.json` at launch; a command aimed at an earlier app instance is rejected, so nothing written while the app was down can fire after a restart.
* `expires_at`: 10 s. An expired command is dropped with result `expired` (a frozen app never replays old clicks later).
* `expect.queue_version`: the app bumps a counter on every queue mutation and publishes it. An edit made against an older view is rejected as `stale_view` (the operator sees the fresh list and retries) unless the policy below is changed.
* Identity is ALWAYS `singer_id` / entry id, never a row index or a name.

First actions (v1): `queue.move_singer {singer_id, direction|to_index}`, `queue.move_song {entry_id, direction}`, `queue.remove_song {entry_id}`, `queue.remove_singer {singer_id}`, `queue.skip_singer {singer_id}`,
and the existing transport actions passed through to `_execute_host_control_command` unchanged. NOT in v1: add song, clear queue, rename, anything that opens a dialog.

## Execution in the app

1. A `QTimer` on the GUI thread (250 ms) scans the inbox. Running on the GUI thread means commands can never interleave with a click: the event loop serialises them.
2. Per command: check setting, session, expiry, version, then run the id-based method inside `_begin_undoable_action` / `_commit_undoable_action` so the operator can undo from the app as usual.
3. Each id-based method is a thin wrapper over the logic the button handlers use (the handlers get refactored to resolve the selected row to an id and call the same wrapper), so there is one implementation, not two.
4. After the mutation the app flushes `queue.json` (not debounced), bumps `queue_version`, rewrites `live_state.json`, then writes `commands/<id>.result.json` with `{ok, message, queue_version}`.
5. Rules that already exist stay in force: removing the current singer detaches playback instead of stopping it; edits that the host UI blocks while a song plays stay blocked; remote requests get their tombstones.
6. Setting `shell_commands`: `off` (default) | `dry_run` | `apply`. `dry_run` validates, logs `[SHELL-CMD] would apply ...` and writes a result with `dry_run: true`, changing nothing. Every command, in every mode, is logged
   (`[SHELL-CMD] id= action= ok= message=`) so `shadow_report.py` can audit it.

## The shell side

* A control is disabled unless `live_state.json` is fresh and its pid is alive (already implemented for the stage view).
* No optimistic updates: a click shows "sending", the list changes only when the app's new state arrives; no result within the timeout shows "no answer from the app" and offers retry with a NEW id.
* Destructive actions (remove singer with songs) ask for confirmation in the shell. The app does not trust that: its own whitelist and the same guards apply.

## Failure modes

| Situation | Behaviour |
|---|---|
| App closed or crashed | Controls disabled (pid check). Any file left behind has a dead session or is expired: inert. |
| App frozen | Command expires; shell shows "no answer". Nothing replays later. |
| Double click, two shells | Same id applies once; different ids apply in id order. |
| Queue changed under the operator (server add, host click) | `stale_view`; shell refreshes. |
| Power loss mid-command | The inbox file is unlinked only after the result is written; an unfinished command is either expired or has a dead session on restart. |
| Server also reorders (`_sync_remote_singer_order`) | Unchanged: the app's existing path runs inside the command. |

## Verification plan (how this would be proven before it is trusted)

1. Unit tests with a fake app for every guard (session, expiry, stale view, duplicate id, off/dry_run/apply, bad JSON, huge files, wrong permissions, symlinks in the inbox).
2. The queue replay harness's host-action invariants run against command-driven sequences and must equal the same sequences driven through the button handlers (differential on the resulting queue and the server-sync calls).
3. Mutation tests on the executor and on the Rust command writer.
4. One real show in `dry_run` (commands logged, nothing applied), compared by `shadow_report`, then `apply` behind the setting.
5. Package gate on both architectures with the setting on.

## Path to Stage B

The action names and ids are the `singws-queue` events. When the Rust core owns the queue, the same inbox (or a socket) feeds it and Python becomes a client; the shell does not change.

## Open questions for the owner

See the end of the design discussion: first controls, whether the shell runs on the show machine, stale-view policy, and whether to start with a dry-run show.
