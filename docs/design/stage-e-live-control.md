# Stage E: how the Rust shell sends live controls to the running app

Status: owner answers recorded (2026-10-24). STEP 1 (the app side) is built and inert while `shell_commands` is `off` (see "Step 1 as built" at the end); the shell side is not built. The three panels that exist (library, singer history, rotation) are read-only and write nothing.

## Owner decisions (2026-10-24)

1. First controls: transport AND queue reorder/remove/skip.
2. The shell runs on a DIFFERENT Mac or a tablet than the app, so a same-machine file inbox is not enough. The transport is the server relay.
3. A stale edit (the list changed since the operator looked) is REJECTED and the shell refreshes.
4. The first show with live controls is a DRY RUN (commands validated and logged, nothing changed).

## What the code does today (checked in 0.2.18.1.py and SingWS-Server/v2)

* The running Python app owns the queue in memory and is the only writer of `queue.json`.
* A remote-control channel already exists. The app's `HostControlRelayWorker` joins the server's WebSocket relay (`wss://.../relay?role=app&channel=host`) with the tenant API key. A browser (the v2 DAW page, usable from a tablet) logs in with the host PIN
  (`/api/v1/host_commands.php?action=login`, a session cookie), asks `/v2/api/ws-token.php` for a 60-second HMAC token, and joins the same room as `role=browser`. The reference relay (`v2/dev/relay-server/relay.py`) is a plain forwarder: every browser message goes to the room's apps, every app message to the browsers,
  with `user` stamped by the relay and `{error: app_offline}` when no app is connected. It does not filter message types.
* The app receives `command` messages in `_on_host_relay_command`, runs ONE executor, `_execute_host_control_command(action, args) -> (ok, message)`, replies `{type: ack, request_id, ok, message}`, and pushes a fresh `state`.
  That executor covers transport and levels only: play_pause, stop, next/skip, restart, seek, key/tempo, karaoke and background volume, bg play/pause. The HTTP poll path (`handle_host_commands_from_thread`) uses the same executor.
* Queue edits are NOT commands. `move_up`, `move_down`, `remove_selected`, `clear_queue_with_confirmation` work from the SELECTED ROW of a widget list and carry side effects: an undo snapshot, `_sync_remote_singer_order`, remote-request tombstones (`_delete_remote_request`),
  rotation-marker repair, the "playback detached from rotation" rule, ticker refresh.
* Identity in the app is by id (`singer_id`, `_ensure_queue_entry_id`: `remote:<id>` or `local:<uid>`), never by name or row.

## Principle

Until the Rust core owns the queue (Stage B drive, not started), **the Python app stays the single writer and single executor.** The shell sends INTENTS through the relay; the app validates and runs them through the same internal code the buttons use, then reports. The shell never writes `queue.json`,
holds no API key, and never reaches the app by any other route.

## Transport: the existing relay, with the shell as a second "browser"

```
 Rust shell (other Mac) --PIN login--> server (session cookie) --ws-token--> wss relay (role=browser) <==> relay <==> app (role=app)
```

* Auth is the host PIN, exactly like the web DAW page: no API key on the shell machine. Tenant isolation and the 60 s token are the server's.
* Messages stay the relay's JSON (`command` with `request_id`, `ack`, `state`, `playback_tick`, `error`), extended compatibly:
  * new command names (below); old ones unchanged, so the web page keeps working;
  * the app adds `queue_sig` (see "Step 1 as built") to `state` and publishes a compact `queue` message whenever it changes (singer ids, names, skipped flags, each singer's first-song entry id and title: enough to draw the rotation and address edits).
* No server change is needed if production matches the reference relay's pass-through behaviour (to be verified against production before the dry run).
* The local file inbox I proposed earlier is dropped for now. It stays a possible same-machine fast path behind the same `CommandSink` interface in the shell.
* Tablets: the web DAW page can gain the same queue controls later; the app-side contract is identical.

## Command format (extends the relay's `command`)

```json
{"type":"command","command":"queue.move_singer","request_id":"01JBX...","sent_at":1792830000,
 "args":{"singer_id":"ab12...","direction":"up","expect_queue_sig":"3fa91c07b2d4e615"}}
```

* `request_id`: unique, sortable. The app applies each id once (it remembers recent ids), so a retry, a double click or two shells never doubles an edit.
* `sent_at`: the app drops a command older than 10 s with `{ok:false, message:"expired"}` (clock skew tolerated; a command held up by a bad network never fires late).
* `expect_queue_sig`: a fingerprint of the rotation as the operator sees it; a command carrying a different one gets `{ok:false, message:"stale_view"}` and the shell refreshes (owner decision 3).
* Identity is `singer_id` / entry id only.
* v1 queue actions: `queue.move_singer {singer_id, direction|to_index}`, `queue.move_song {entry_id, direction}`, `queue.remove_song {entry_id}`, `queue.remove_singer {singer_id}`, `queue.skip_singer {singer_id}`.
  Transport actions pass through to the existing executor unchanged. NOT in v1: add song, clear queue (it has a confirmation dialog in the app), rename, anything that opens a dialog.

## Execution in the app

1. `_on_host_relay_command` hands `queue.*` actions to new id-based methods; everything else goes to the existing executor. It already runs on the GUI thread, so a remote command can never interleave with a click.
2. Each id-based method is a thin wrapper over logic extracted from the button handlers; the handlers are refactored to resolve the selected row to an id and call the same wrapper, so there is ONE implementation. The wrapper runs inside `_begin_undoable_action` / `_commit_undoable_action`, so the operator can undo it in the app.
3. Existing rules stay: removing the current singer detaches playback instead of stopping it; edits the host UI blocks stay blocked; remote requests get their tombstones; server order sync runs.
4. After a mutation the app flushes `queue.json` (not debounced), bumps `queue_version`, publishes the `queue` message and `state`, then sends the `ack` (with the new version).
5. Setting `shell_commands`: `off` (default) | `dry_run` | `apply`. In `dry_run` the app validates (version, ids, guards), logs `[SHELL-CMD] would apply ...`, acks `{ok:true, message:"dry_run: ...", dry_run:true}` and changes NOTHING. In every mode each command is logged (`[SHELL-CMD] id= action= ok= message=`), so `shadow_report.py` can audit it.
   Transport commands in `dry_run` are logged and acked but not executed, so a dry-run show cannot change playback.

## The shell side

* A small Rust crate (`singws-control`): PIN login with a cookie jar (the HTTP sender gains cookies), token fetch, a relay client with `role=browser` (the existing relay crate's state machine and Qt-parity tests are reused), a command builder/validator, a parser for `state` and `queue`.
* Controls are enabled only while a fresh `state` shows `connected:true`. No optimistic updates: a click shows "sending", the list changes only when the app's new `queue` arrives; no ack within 3 s shows "no answer from the app" and offers retry with a NEW id.
* The PIN is typed into the shell and kept in memory only; the session cookie is not written to disk.
* Destructive actions (remove singer with songs) ask for confirmation in the shell; the app does not rely on it.

## Failure modes

| Situation | Behaviour |
|---|---|
| App offline or relay down | Relay answers `app_offline` or the socket drops; shell disables controls. Nothing is queued anywhere, so nothing replays later. |
| App frozen | No ack within 3 s; the command expires after 10 s and is dropped. |
| Double click, two shells, a browser plus the shell | Same `request_id` applies once; different ids apply in arrival order on the GUI thread. |
| Queue changed under the operator | `stale_view`; shell refreshes from the new `queue` message. |
| Venue network drops | Relay reconnect (the proven 5 s single-shot state machine); the shell re-logs in only if the 60 s token path fails. |
| Wrong or revoked PIN | Server refuses the login or token; shell shows it; no command can be sent. |
| Production relay filters unknown message types | Found in the pre-show check below; the fallback is to ship the new names through the v1 `host_commands` queue, which already passes `command`/`args` to the same executor. |

## Verification plan

1. App side: unit tests with a fake app for every guard (expiry, stale version, duplicate id, off/dry_run/apply, unknown singer, current singer, bad args) and a DIFFERENTIAL against the button handlers: the same edit through `move_up` and through `queue.move_singer` must leave identical queue, server-sync calls, undo entry and tombstones.
   The queue replay harness's host-action invariants run over command-driven sequences.
2. Rust side: loopback tests against the repo's own `relay.py` and `sim_app.py` (real sockets, real protocol), the PIN/token flow against a stub, mutation tests on the command builder and parser.
3. Pre-show: confirm production's relay forwards the new message types (a harmless `queue.noop` in `dry_run`).
4. Package gate on both architectures with `shell_commands` on, then one real show in `dry_run`, compared by `shadow_report`, then `apply` behind the setting.

## Build order

1. App: `queue_version`, extracted id-based queue methods, button handlers delegating to them, `queue.*` actions, the `queue` message, `shell_commands` setting and logging (all inert while the setting is `off`). Proven by the differential before anything else is built.
2. `singws-control` crate and loopback tests.
3. Shell "Controls" tab (transport first, then queue).
4. Package gate, dry-run show, then apply.

## Path to Stage B

The action names and ids are the `singws-queue` events. When the Rust core owns the queue, the same messages feed it and Python becomes a client; the shell does not change.

## Step 1 as built (2026-10-24, app side only; nothing installed)

What exists in `0.2.18.1.py`, all inert while the setting `shell_commands` is `off` (the default):

* `_queue_signature()`: a 16-hex fingerprint of the rotation (singer ids, names, skip flags, rotation marker, each song's entry id and skip flag). DEVIATION from the design: a content fingerprint instead of a counter, because the queue is mutated in dozens of places (server adds, completions, renames,
  merges) and a counter would silently miss one; a fingerprint cannot. Fields the operator cannot see (notes, durations) do not change it.
* `shell_queue_move_singer`, `shell_queue_move_song`, `shell_queue_remove_singer`, `shell_queue_remove_song`, `shell_queue_set_singer_skipped`: id-based, with the same side effects as the buttons, in the same order (undo snapshot, display refresh, remote-order sync, remote-request deletion, empty-slot mark, the "playback detached"
  flag, debounced save, ticker). DEVIATION from the design's "one implementation": the button handlers are NOT refactored yet. They stay exactly as they were so this step cannot regress the live UI; `test_shell_queue_commands.py` drives the real handlers (through a fake widget list) and the new methods on the same random queues
  (1,200 cases: 4 singer moves, 4 song moves, remove singer, remove song, skip, unskip) and requires identical queues, recorded side effects, undo history and detach flag. Refactoring the handlers to call the new methods is left for after a dry-run show.
* `_execute_shell_queue_command(action, args)`: the guards in order: mode (`off` refuses; `dry_run` validates and changes nothing; `apply`), known action, request id, `sent_at` (older than 10 s is `expired`, more than 60 s ahead is `clock_skew`), duplicate id (the first answer is repeated, never re-applied; the last 256 ids are remembered),
  `expect_queue_sig` (`stale_view`), unknown singer or entry, bad direction or `skipped` value. Every outcome is logged `[SHELL-CMD] id= action= mode= ok= message=`.
* Routing: `_execute_host_control_command` sends every `queue.*` action there, and treats a transport command carrying `"source":"shell"` by the same switch (refused when off, logged and not executed in dry run); transport commands from the web remote (no `source`) are unchanged. `_on_host_relay_command` and the HTTP poll path pass `request_id` and `sent_at` through.
* Publishing: when the mode is not `off`, `state` carries `queue_sig` and a `queue` message with the compact rotation (`_shell_queue_summary`, at most 200 singers and 25 songs each) follows whenever the state changes.
* Not done: `queue.move_singer` to an arbitrary index, add, clear (needs the app's confirmation dialog), the shell side (`singws-control`, the Controls tab), the pre-show check that production's relay forwards the new message types.

Verification so far: 15 tests (differential plus every guard), 32 mutants on the new code with 31 killed and 1 equivalent (a removed log line), and the full suite.

## Step 2 as built (2026-10-24): `singws-control` (nothing installed, no UI yet)

* `rust/crates/singws-control`: `protocol` (pure: command building and validation, request ids, parsing of `state` / `queue` / `ack` / `error` / `playback_tick` / `hello`, the browser URL), `session` (PIN login with an in-memory cookie jar, the 60-second websocket token, refusal of a plain `ws://` relay from an `https://` server),
  `client` (a thread that logs in, fetches a token, joins the room as `role=browser`, delivers every message, sends validated commands, reports `NoAnswer` after 3 s, reconnects after the app's 5 s delay with a FRESH token, logs in again if the server forgot the session, and stops retrying when the PIN is refused).
  The PIN lives in memory only and is hidden from Debug output; a command that fails validation never reaches the wire; nothing queued while disconnected is replayed after a reconnect. `singws-http` gained an in-memory cookie jar for this.
* `tools/control_loopback.py` proves it end to end on 127.0.0.1 with no stand-ins for the parts that matter: the REAL PHP endpoints (a temp copy of SingWS-Server under `php -S`: `host_commands.php?action=login`, `v2/api/ws-token.php`), the repo's reference `relay.py` verifying the real HMAC token, and `tools/control_app.py`, which runs
  the app's real `_on_host_relay_command`, `_execute_host_control_command`, queue commands, `_queue_signature` and `_publish_host_state_ws` on a bare app. 11 scenarios: login and first `state`/`queue` (same fingerprint); a queue edit, a song move, a removal and a skip applying and the new `queue` arriving; a stale edit refused with nothing changed;
  dry run and off changing nothing (queue and transport); a repeated request id applied once; malformed commands never leaving the client; a wrong PIN stopping without any relay connection; no app means `app_offline`; a frozen app gives `NoAnswer`; the relay restarting (reconnect with a fresh token); the server session vanishing (automatic re-login).
* 15 Rust unit tests; 35 mutants (35 killed: 32 by unit tests first, 3 more after adding tests); the loopback scenarios are the second line of defence.
* Not done: the shell's Controls tab, `wss://` against production (needs the `tls` feature and the real server), the pre-show check that production's relay forwards `queue.*` and `queue` messages, the tablet web page.

## Step 3 as built (2026-10-24): the Controls tab (nothing installed)

* `singws-control::view` is a pure reducer (`ViewModel`): feed it the client's outputs, read one JSON snapshot. It owns the rules the UI needs: controls are enabled only while the relay is up AND the app is online; an acknowledgement becomes plain words with the command's label ("Move Dana down: Moved", "Remove song: That song is no longer in the queue", "Dry run: the app checked it and changed nothing");
  `ok:false` is never shown as success; a `stale_view` refusal asks for exactly one automatic refresh (a silent `queue.noop`, whose own success is not shown, so it cannot cover the warning); no answer, `app_offline`, relay errors and local send failures each say so and clear the pending command.
* `singws-shell` gains `ControlPanel` (a QObject that owns the connection and pushes the snapshot to the Qt thread) and the fourth tab, "Controls": server address, tenant, PIN (password field, cleared after connecting, never saved: only the address and tenant are remembered), a status line, a result banner, the now-playing card with Restart / Play-Pause / Next / Stop / Key -+ / Tempo -+,
  and the rotation with per-singer up, down, skip/unskip and remove (a second click confirms), and per-song up, down and remove. Every queue command carries the fingerprint of the list the operator is looking at.
* `tools/shell_loopback.py` runs the REAL shell (Qt offscreen) against the loopback stack (real PHP login and token, the reference relay, the app's real command code): the buttons' own `act()` function sends five edits and a transport command and the app ends in the expected state with every command carrying `source: shell` and its fingerprint; a stale edit is refused, shows "refreshed" and triggers exactly one refresh; dry run shows "changed nothing" and the queue is untouched;
  a wrong PIN shows the refusal and enables nothing; with no app the controls stay disabled; the PIN appears in no file and no output. Screenshot: `docs/verification/2026-10-24-rust-shell-controls.png`.
* 25 Rust unit tests in `singws-control`; 49 mutants in total on the crate, all killed.
* Not done: a real run on the Intel show Mac, `wss://` to production (needs the `tls` feature), the pre-show check that production's relay forwards `queue.*` and `queue`, volume sliders and seek, the tablet page, moving the button handlers onto the shared methods, a dry-run show.
