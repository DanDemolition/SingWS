# Stage B: queue/request state replay harness

Purpose: before queue/request state moves to Rust, any implementation must be runnable on the same event stream as the real Python
code and compared step by step. This harness feeds a stream through the app's REAL queue logic (`KaraokeApp` methods on a bare
instance, like `test_remote_request_tombstones.py` / `test_shell_queue_commands.py`) and captures a canonical snapshot after every
event. Nothing in the app changed; no Rust queue exists yet.

Tool: `tools/queue_event_replay.py` (the older `tools/queue_replay.py` is the log-invariant checker; its `parse_line` is reused).
Tests: `test_queue_event_replay.py`. Run (venv with PyQt6; `SINGWS_HOME` is forced to a scratch dir if unset or if it is `~/SingWS`):

    SINGWS_HOME=$(mktemp -d) ./.venv/bin/python tools/queue_event_replay.py corpus --seeds 20     # determinism over the corpus
    SINGWS_HOME=$(mktemp -d) ./.venv/bin/python tools/queue_event_replay.py corpus --fingerprint  # hash; must match across PYTHONHASHSEED
    SINGWS_HOME=$(mktemp -d) ./.venv/bin/python tools/queue_event_replay.py extract LOG... --out s.jsonl
    SINGWS_HOME=$(mktemp -d) ./.venv/bin/python tools/queue_event_replay.py play s.jsonl

## Event schema (version 1, JSON lines, `op` + arguments)

Entities are named by alias, never by the app's generated ids: `R<request_id>` for any entry that has a remote request id (the
permanent id), `L<n>` for host-local entries (n in order of first appearance), `S<n>` for singers (n in order of first appearance in
queue order). Singers may also be named by display name. An alias that does not exist returns `ok=false, code=unknown_*` (a legitimate,
comparable outcome: stale references must be refused identically).

| op | arguments | real code driven |
|---|---|---|
| `config` | `queue_mode` rotation/classic, `limit_pending_max`, `requests_accepting`, `waitlist` (-> `use_waiting_for_add`) | settings; also (re)sets the world |
| `add_song` | `singer`, `title`, `artist?`, `key?` | `_add_song_to_queue(remote_meta=None)` (operator Add) |
| `remote_arrive` | `requests: [{request_id, singer, artist, title, key?, tempo?, state?, pending_reason?, sort_order?}]` | `_reconcile_remote_requests` -> real `process_external_request` (song-limit check, waitlist/attention recording, deferral) -> `_apply_resolved_remote_add` -> `_add_song_to_queue`. Models a poll, a websocket delivery, a reconnect resend and a server re-list (they are the same call). |
| `remove_song` | `entry` | `shell_queue_remove_song` (-> `_delete_remote_request`, tombstone, empty-slot marking) |
| `remove_singer` | `singer` | `shell_queue_remove_singer` |
| `move_singer` / `move_song` | `singer`/`entry`, `where` up/down/top/bottom | `shell_queue_move_singer` / `shell_queue_move_song` |
| `skip_singer` | `singer`, `skipped` | `shell_queue_set_singer_skipped` |
| `rotation_advance` | `singer` (the singer that just started) | `_select_next_rotation_after_start` (marker / has_sung bookkeeping; playback is not started) |
| `complete_song` | `entry` | real `_complete_remote_request` (completed tombstone) **plus a harness-modelled removal of the entry** (the real removal lives inside `play_next_file`/media-end, which is out of scope) |
| `waitlist_add` / `waitlist_remove` / `waitlist_restore` | `request_id` | `_waitlist_add_request` / `_waitlist_remove_request` / `_waitlist_restore_request` |
| `restart` | none | rebuilds the app: queue and waitlist are deep-copied (as if saved/loaded), tombstones are re-read from disk, every in-memory set (`_remote_removed_request_ids`, pending order syncs, ...) starts empty and is refilled from the persisted tombstones |
| `tick` | `seconds` | advances the logical clock (see below) |

Not covered (deliberately): key/tempo edits, rename/merge of singers, brand priority, replace_song, deferred remote adds between
singers, undo, the server round trip itself (network is off: `base_url` empty, `is_network_configured` False, so tombstones stay
"unsynced"), the library match (see below). Each is a one-line addition to `World` when the Rust side needs it.

Seams stubbed (UI / disk / network only): display refresh, save, header, undo list, notifications, server-sync threads, waitlist
persistence, the library lookup `_prepare_remote_request_add_payload` (replaced by a deterministic fake `/lib/<artist>-<title>.zip`).
The REAL song-limit check, waitlist upsert, dedup, tombstone matcher, id-reuse rule and queue insertion all run.

## Snapshot (canonical JSON, sorted keys)

- `queue`: singers in order: `id` (alias), `name`, `skipped`, `has_sung`, `round_sung`, `rotation_marker`, `temporary_empty_slot`, `songs`:
  `id`, `remote_request_id`, `artist`, `title`, `song_info`, `key`, `tempo_percent`, `skipped`.
- `rotation`: `mode`, `locked` (the app's `_is_rotation_locked()`).
- `waitlist` / `removed_tonight`: by request id: singer, artist, title, state. `handled_waitlist_ids`.
- `tombstones`: by request id: `status` (removed/completed), `removal_reason`, `removed_by`, singer, artist, title.
- `removed_ids`: the in-memory removed-id memory. `deferred_remote_adds`: count.
- `lifecycle`: per request id seen anywhere: `state` in_queue / waitlisted / removed / completed / removed_tonight, and `copies_in_queue`.
  Derived (the app has no single lifecycle field); `requested`, `pending`, `accepted`, `currently singing`, `skipped` of AGENTS.md map
  to: not yet arrived / waitlisted or refused-at-cap / in_queue / in_queue + `rotation_marker` / in_queue with `skipped`.

Each step also records `result` = `{ok, code}` (the app's own return/shell code, `exception:<Type>` if the real code raised).

Deliberately excluded: every wall-clock field (`removed_at`, `created_at`, `last_sync_attempt_at`, `received_ts`, `request_time`),
`local_revision` / `_queue_revision` (a counter whose absolute value depends on how many internal bumps happen; compare behaviour, not
bookkeeping), sync attempt counters and error strings, UI caches (`queue_display_model`, selection, row identity caches, undo stack),
raw generated ids, and `_remote_attention_requests` (keyed by `hash()` of strings when there is no request id).

## Determinism by construction

Clock: `time.time` is patched to a logical clock advanced 1 s per event (`tick` adds more) while an event runs. Ids: aliased as above.
Output: the app's console/log prints are swallowed. Tombstone file: a private temp dir per world.

## Corpus

- `adversarial_cases()`: 14 hand-written streams from AGENTS.md: host deletes then the server re-lists (twice); duplicate arrivals
  keep the oldest; removal survives a restart; completed song is not resurrected (also across restart); remove-singer tombstones all
  their requests; id reused for a different song; skip/reorder keep ids; local adds get stable aliases; limit-blocked request is never
  waitlisted; waitlist add/remove/restore; waitlist removal survives restart; accepting off then on.
- `generate_stream(seed, n)`: seeded random streams. The generator drives a scratch world while choosing, so it only references
  entities that exist (plus deliberate stale references).
- `extract_stream(logs)`: builds a stream from real logs, read-only. It reconstructs: arrivals (`[REQUEST-DIAG] status=received`),
  removals / completions / singer removals (`[REMOTE-TOMBSTONE] created`, `status=completed`). It cannot reconstruct: host reorders
  and skips (only the resulting `QUEUE_SYNC_AUTHORITY` order is logged), host-local adds, rotation advances, waitlist actions beyond
  ids, restarts (inferred from file boundaries only), the server's full row list at each poll, and timing. `extract` prints how many
  events it rebuilt and replays them: the check it can honestly make is "every request the log shows removed/completed ends removed or
  completed in the replay, and none is resurrected" (`replay.RESURRECTED_after_replay` must be empty). Sample: two Oct-2026 logs
  (3,851 lines) gave 26 arrivals, 4 removals/completions, 0 resurrected. It is a coverage probe, not a faithful re-run of a show
  (queue limit and waitlist mode are assumed: limit 20, waitlist off).

## Comparer

`compare(stream, impl_a, impl_b)` runs both implementations through `run_stream` and reports the first step whose `{result, snapshot}`
differs, with up to 8 differing JSON paths. An implementation provides `reset(config)`, `apply(event) -> {ok, code}` and
`snapshot() -> dict` (see `Implementation`). `PythonImpl` is the reference. Step 3 adds a Rust implementation (JSON in/out) and calls
`compare(stream, PythonImpl(), RustImpl())`. Built in: Python-vs-Python determinism (twice identical; identical across different
`PYTHONHASHSEED` processes) and `equivalent_reordering` (adjacent removals/skips on different targets may be swapped; the final
snapshot must be identical).

## Mutation check

`test_queue_event_replay.py::MutationTests` patches defects into the real app class and requires the comparer to flag each:
dropped tombstone, recreated removed song (also after restart), changed request id, wrong insertion position, a skip that does
nothing, a reorder that does nothing, a removal that forgets its tombstone. An unmutated run must not be flagged.

## Findings: nondeterminism and surprises in the real queue code

1. **Generated ids are random.** `_ensure_singer_id` and local `_ensure_queue_entry_id` mint `uuid4` values; any comparison needs the
   aliasing used here (a Rust queue must mint ids from an injectable source, or the comparer keeps aliasing).
2. **Wall clock**: tombstone `removed_at`, `created_at`, empty-slot expiry (180 s) and `_waitlist_evening_key` (`datetime.now()`,
   which is NOT patched by `time.time`) read the clock. The harness avoids the last by keeping restart state in memory.
3. **Arrival batch order decides queue order**, even with `sort_order` on the rows (pinned by a test): a poll listing the same rows in
   another order builds a different rotation. Real servers may return a stable order; a Rust port must take row order as input.
4. **Relist after a restart can reorder songs inside one singer** to the server's row order (pinned). Whether production pushes the
   host's order first (`_sync_remote_singer_order`, stubbed here) is not visible to this harness.
5. **Layered resurrection guards**: a host-removed id is blocked by the tombstone file match AND the in-memory removed-id set;
   disabling one alone changes nothing visible (pinned), so a Rust port must reproduce both or define why one is enough.
6. **Duplicate copies of one request id self-heal** (QUEUE-AUDIT `dedup_same_request_id`, oldest kept). The same song re-requested
   under a new request id is a second entry (identity is the id, as AGENTS.md says).
7. **A request refused at the singer cap is in no state at all** on the host: not queued, not waitlisted, no tombstone
   (`_record_remote_limit_blocked_request` only reports to the server). Lifecycle "requested/pending" for it lives only server-side.
8. `hash()`-keyed dicts (`_remote_attention_requests` when `request_id` is 0) vary with `PYTHONHASHSEED`; not in snapshots.
9. The app's `hasattr/getattr(default)` idioms raise `RuntimeError` on an `__init__`-less QObject, so the harness subclasses the app
   class with a `__getattribute__` that maps it to `AttributeError`; the real app has been initialised, where the idioms behave normally.

## Not done / next

No Rust implementation plugged in; `complete_song` removal and `restart` persistence are harness models (documented above); the
log extractor is a coverage probe; the sample show folder named in the brief (`~/Downloads/singws-show-20261101-0135`) did not exist,
so extraction was run on copies of `~/SingWS/logs/singws_2026-10-23.log` and `..-25.log` (read, copied to a scratch dir, not modified).

## M1: the Rust queue-state machine (`rust/crates/singws-queue-state`)

Pure state machine driven by this event schema, same canonical snapshot, logical clock. CLI: `singws-queue-state [--init CONFIG_JSON]` (JSON lines in,
`{"result","snapshot"}` lines out). Reuses `singws-queue` (`titles_conflict`) for the tombstone rule. Build:
`cd rust && CARGO_TARGET_DIR=~/.cache/singws-rust-target-replay cargo build --release -p singws-queue-state`. Parity:
`python tools/queue_event_replay.py parity [--seeds N] [--stream FILE]` (Python `PythonImpl` vs `RustImpl`); test `test_queue_state_parity.py`;
mutation `tools/mutate_queue_state.py` (Python reference cached once; 72 mutants: 52 killed, 20 documented equivalent, 0 surviving).

**Result:** IDENTICAL on every in-scope adversarial case (`core_cases()`, 22), 150 seeded `core` streams (60-80 events) and a stream extracted from
two real logs (20 events). One real difference was found and fixed in Rust: a duplicate arrival of a queued request id marks that id handled
(`handled_waitlist_ids`, via `_mark_waiting_for_add_duplicate_accepted`).

**Declared scope.** Modelled: classic and rotation queues without lock; host add, remove song/singer, move, skip, rotation_advance, complete,
restart; remote arrivals (tombstone admission and id reuse, removed-id memory, singer match by case/space-insensitive name, song cap incl. the
intake-count inflation by duplicate rows, accepting, duplicate ids, key/tempo update, wire-order song ordering), empty-slot flags and their 180 s expiry,
restart resetting settings to the initial merged config. NOT modelled (Rust answers `unsupported` or ignores the row; the generator's `core`
profile avoids them): waitlist ops and `state: waiting` rows, duets, rotation lock, server-terminal rows, `sort_order`/order timestamps, singer
ids/sessions, deferred adds, rename-merge, `round_sung` ever becoming true, song-level skip. Python stays authoritative.

**Quirks the port had to reproduce** (each is a documented Python behaviour): restart resets runtime settings to the INITIAL merged config
(not the latest `config`); duplicate rows in one batch inflate the intake count, so a later row of the same singer can be refused though the live
count is lower; a host add may exceed the cap but remote rows may not; emptied singers are kept in the queue (never deleted by reconcile).

## M2: `queue_state_shadow` (log-only, `queue_state_shadow.py`)

A full event-stream shadow is not possible (the app has many unmodelled mutators), so the shadow checks ONE operation, the server reconcile, and
resynchronises from the app's real state before every check, which is why unmodelled edits can never count as disagreements:
`_reconcile_remote_requests` carries a decorator (`_queue_state_shadowed`; the only change to app code besides the setting, the launch announce and the
specs). When a check is due (at most one per 30 s, setting `queue_state_shadow`, default True, helper present) it snapshots the queue, tombstones, removed-id and
handled-id sets BEFORE, runs the reconcile unchanged, snapshots AFTER, and a daemon thread asks `singws-queue-state` (nice 10) to `load` the before-state
and apply the same rows (`remote_arrive` with the app's clock). Only structure is compared: singers (name, flags, empty-slot flag), per song
(request id, key, tempo, skipped), tombstone ids and status, removed ids, handled ids. Python's queue is always the one used; every call is wrapped.

Not checked (silently): waitlist setting on, any waiting/failed/terminal/delivered row, `sort_order` or order fields, duet singer names, KaraFun
rows, async or deferred intake, rotation lock, a clear window, and any reconcile during which the waitlist, attention, unmatched or deferred sets
changed (that is how library-match failures are recognised). Log lines (grep `QUEUE-STATE-SHADOW`): `enabled available=0/1` at launch, `first check
clean (...)` once, `MISMATCH <path>: app=<summary> rust=<summary>` (lengths/hashes only, never names or titles; each distinct difference once, at most 20),
`skipped: <reason>`. `tools/shadow_report.py` has a QUIET SHADOWS row. Bundling: `rust/build_queue_state.sh` (both archs), both specs, `.gitignore`, both
`build_singws_mac_*.sh`. Honest limit: tested against the Python harness world, not a show; real rows carry fields (singer ids, sessions, library
metadata) the model ignores, so the first real mismatches may be model gaps rather than app bugs. Read them with that in mind.

## M3: the waitlist in the Rust model (Stage B step 2)

The Rust model now covers `waitlist_add`, `waitlist_remove`, `waitlist_restore`, `waiting`/failed rows, `use_waiting_for_add` (event key `waitlist`),
the handled-id set and the removed-tonight list. `tools/queue_event_replay.py parity` runs ALL adversarial cases plus seeded streams (profile `full`
by default; it toggles the setting, re-lists rows in other shapes, reuses ids, sends stale ids) and they come out identical step by step.
Oracle: the real `_waitlist_add_request`, `_waitlist_remove_request`, `_waitlist_restore_request`, `process_external_request`,
`_set_waiting_for_add_requests` and the reconcile loop. Mutation: `tools/mutate_queue_state.py` (waitlist mutants added).

### Rules found (0.2.18.1.py as of this commit)

1. **The waitlist is rebuilt by every poll.** `_reconcile_remote_requests` calls `_set_waiting_for_add_requests(rows)` first, before the queue adds.
   With `use_waiting_for_add` ON it REPLACES `_waiting_for_add_requests` with the candidates of this poll: a row that is a terminal state
   (removed/skipped/completed/sung or a removed_at/completed_at stamp) is only a server-purge hint; a non-actionable row (see 3) puts its id in
   `handled`; a non-waiting row is ignored; a row whose id is queued puts the id in `handled`; a row whose id is already `handled` is dropped;
   everything else is a candidate (the last row for an id wins). With the setting OFF only entries that carry an `attention_reason` survive.
2. **A row is "waiting" (`_is_waiting_for_add_request`)** when it is not non-actionable and either its `state` is waiting/failed/failed_needs_review
   or the first truthy of `pending_reason`/`last_error`/`attention_reason` is non-blank (so a `pending` row with a pending_reason is waitlisted).
3. **Non-actionable (`_remote_request_is_non_actionable`)**: waiting/failed states never are, whatever `sent`/`delivered` say; `accepted` only with a
   transport ack or a desktop_sync_status other than ""/pending/pending_desktop_sync; active/delivered/completed/sung/removed/skipped always; then
   any `sent`/`delivered`; then a positive added_at/queued_at/completed_at/removed_at.
4. **In the reconcile loop a waiting row** passes tombstone and removed-id admission first (a title conflict retires the tombstone, exactly as for
   a normal row), then is held (never queued). A non-waiting row whose id is on the (new) waitlist is not queued either (`rid in waiting_ids`).
5. **`waitlist_add`** (`process_external_request`): the song cap is checked first with live counts (waitlisted rows do NOT count; a blocked add
   changes nothing and answers `not_added`, it never enters the waitlist), then the normal remote add (`_add_song_to_queue`, remote). On success the
   id goes into `handled` and leaves the waitlist. The "already queued" suppression cannot fire for the request itself (its own id is excluded).
6. **`waitlist_remove`**: archives the row into removed-tonight (state `removed`), adds the id to `handled`, writes a tombstone (reason
   `waiting_for_add_cleared`, status removed) and the removed-id memory, deletes it from the waitlist.
7. **`waitlist_restore`**: puts the archived row back as state `waiting` (pending_status "Restored by host", pending_reason kept or `host_restored`),
   removes it from removed-tonight, `handled`, the removed-id set and the tombstones. It does not look at the waitlist setting.
8. **`_mark_waiting_for_add_duplicate_accepted`** (an add that finds the same request id already queued) also drops the id from the waitlist.
9. **Evening key**: `(now - 6 h)` as `%Y-%m-%d`; the removed-tonight file is used after a restart only if evening AND tenant match. Modelled as the
   pure `waitlist_evening_key(local_epoch_secs)` / `removed_tonight_still_valid` (unit-tested, no clock in the model). The replay harness stubs
   the save, so its restart keeps the list, as does the Rust restart.
10. Snapshot additions: `waitlist` / `removed_tonight` map id -> {singer, artist, title, state}; lifecycle states `waitlisted` and `removed_tonight`.
   The `load` event may carry `waitlist` / `removed_tonight` row arrays and `settings.waitlist`.

### Surprises (reported, NOT changed)

- A relisted id is blocked from the waitlist forever once it is in `handled`, even if the server reuses the id for a different song: the reconcile
  loop retires the tombstone on a title conflict, but `_set_waiting_for_add_requests` runs first and `handled` is never cleared except by restore.
- A waitlist entry created by `waitlist_restore` disappears at the next poll unless the server lists the row again (the rebuild replaces the dict);
  it is then in no list at all (tombstone dropped, removed-tonight popped).
- A request the host removed from the queue (tombstone) that the server later lists as `waiting` is not stopped by the tombstone in
  `_set_waiting_for_add_requests` (only `handled` stops it), so it can reappear on the waitlist.
- A `pending` row with a `pending_reason` is treated as waiting (silently held when the waitlist setting is off: neither queued nor listed).
- Rows in `pending` that carry `sent`/`delivered` are skipped by the Rust model entirely but Python still refreshes key/tempo for ones already
  queued (older gap, outside this step).

### Still not modelled

Duets, rotation lock, server-terminal rows (the server-side purge and `_remote_terminal_titles`), `sort_order` / order timestamps, deferred adds,
rename-merge (`_alias_mapped_request`), stale-by-timestamp waitlist purge, `replacement_track` adds, the attention list (library-match and
insert failures; `waitlist_add` with an empty singer/title answers `unsupported`), evening-key reload on a real restart. `queue_state_shadow.py` is
unchanged: it still skips any poll with the waitlist setting on or a waiting row, so nothing about the waitlist runs in a show.

## M4: rotation lock and duets in the Rust model (Stage B step 3)

Covered now: the rotation lock (setting `rotation_locked`, event `rotation_lock` = the Lock/Unlock button), duet singer names ("A & B"), and the
failure path a duet exposes (the attention list and the failed-id set). Oracle as before: the real `_add_song_to_queue`, `_rotation_*lock*`,
`toggle_rotation_lock_button`, `_parse_duet_singer`, `_duet_participants_from_display`, `_check_queue_song_limit`, `process_external_request`,
`_apply_resolved_remote_add`, `_record_remote_attention_request` and the reconcile intake loop. Replay changes: config key `rotation_locked`, op
`rotation_lock` (result code `lock=0|1`), snapshot fields `duet_display` per song, `rotation_lock_new` / `rotation_lock_order` per singer,
`rotation.lock_setting` / `lock_insert_count`, top level `attention` (id -> reason) and `unmatched_remote_ids`; generator names include
`DUETS`; new adversarial cases (lock weave, lock refused while the marked singer is next, a saved lock that comes and goes with the marker, lock in
classic mode, duet counts both names, duet partner at the limit, duet separators and odd names).

### Lock rules

1. **Setting versus effect.** `_is_rotation_locked()` = rotation mode AND the saved setting AND `_rotation_lock_can_enable()` (queue not empty and
   the marked singer is NOT index 0). The setting itself is not cleared when the marker is first: a saved lock goes dormant and wakes up again the
   moment the marker moves off the top (reorder, a removal). Only the button (toggle), `_rotation_clear_lock`, or the sanitize pass (which runs from
   the button refresh, not from any modelled op) clear it.
2. **What the lock changes** (in modelled ops): ONLY where a song is added. A new singer is woven into the NEXT rotation instead of going before
   the marker: it is flagged `rotation_lock_new` with the next `rotation_lock_order`, inserted right after the marker (or at the round boundary if
   there is no marker) and the tail is re-woven; an existing singer adding a song stays where they are, then the tail is re-woven. Remote
   arrivals, host adds and waitlist adds all take the same two paths. Move, remove, skip, advance, complete and duplicate suppression ignore the lock.
3. **Reweave.** From the marker (or the first locked newcomer, whichever is earlier) to the end: a marked singer with no active songs is pinned
   first; the remaining old singers keep their order, the locked newcomers are sorted by `rotation_lock_order`, and the two lists are interleaved
   old, new, old, new, ... Everything before the tail start is untouched.
4. **Button.** Unlock if locked; if not rotation mode or the lock cannot be enabled it clears (and says "ignored"); otherwise it sets the setting
   and resets `rotation_lock_insert_count` to 0. Clearing resets the counter and drops every `rotation_lock_new` / `rotation_lock_order`.
5. The branch of `_rotation_insert_locked_new_singer` that handles "marker is already first" is unreachable from `_add_song_to_queue` (the lock is
   not active then); it is ported anyway. The playback code (`play_next_file`) clears the lock when the next rotation begins: out of scope here.
6. The replay world's `restart` rebuilds settings from the initial config, so the lock setting returns to its initial value and the insert counter to 0
   (the real app persists settings; the locked-singer flags persist in the copied queue). Rust mirrors the harness.

### Duet rules

1. **Parsing** (`_parse_duet_singer`, after the first-letter capitalisation of every word): whitespace collapsed; the FIRST match of
   `\s*(&|\+|/w|w/|/|,|\band\b|\bwith\b)\s*` (any case) splits the name; if either side is empty it is not a duet (the whole name stays, e.g. "Flo &").
   The row is the first name; the song carries `duet_display` "First & Second" (always an ampersand, even when typed "and"/"with").
   "A & B & C" gives the partner "B & C" (one name).
2. **Counting.** A song counts against every participant (`_duet_participants_from_display(singer row name, duet_display)`, deduplicated by the
   casefolded name), skipped singers and skipped songs excluded. So a duet entry under Amber also uses Delores's allowance, and "Delores" may be a
   row of her own.
3. **Matching.** The row lookup, adoption of local entries by (name, artist, title) and the per-singer wire order use the FIRST name only.
4. **Limit asymmetry (the surprise).** The reconcile intake pre-check, `process_external_request` and `_apply_resolved_remote_add` all check
   `queue_singer` (the first name) only, so a request "Bob & Ada" with Ada at the limit passes all three and fails inside `_add_song_to_queue`
   (which checks both names). That failure records an attention item (`queue_insert_failed`, then overwritten by `auto_accept_failed` from the
   reconcile), puts the id in the failed set (skipped on later polls until the server stops listing it) and forces the request onto the waitlist view
   (even with the waitlist setting off; the entry has no `state`, so the snapshot shows an empty state). A raw server row added by `waitlist_add`
   is different: its singer is the raw "Bob & Ada", so the pre-check sees both names and answers `not_added` without recording anything; an
   attention-sourced entry (first name only) fails late again and its reason becomes `queue_insert_failed`.
5. **State kept.** `_remote_attention_requests` and `_unmatched_remote_request_ids` live in memory only (restart clears them); the waitlist entry
   survives. A successful remote add clears the attention item and the waitlist entry; the duplicate path also clears the attention item. While the waitlist is on, every
   poll adds attention items to the rebuilt waitlist unless the id is handled or queued; with it off only entries with an `attention_reason` survive.

### Surprises (reported, NOT changed)

- "Snow/Joe" is a duet ("Sno" and "Joe"): `w/` and `/w` match inside words. Any name with a slash, comma or plus is split.
- The partner-limit asymmetry above: a duet whose partner is full is accepted by every pre-check and then fails, landing on the waitlist under
  `auto_accept_failed` even when the waitlist is disabled.
- A dormant saved lock (marker first) silently re-activates when the marker moves.
- "A & B & C" counts the partner "B & C" as a single person.

### Still not modelled

The lock being cleared by playback, the sanitize pass, the real `_delete_rotation_singer_row` (the replay world stubs it), `_merge_duplicate_rotation_singers`,
`_unmatched_request_sigs` (library-match failures), rename-merge and singer aliases, server singer ids / sessions, sort_order, deferred adds and
server-terminal rows. `queue_state_shadow.py` is unchanged: it still skips a poll when the lock setting is on, a duet name is present or the waitlist,
attention or unmatched sets change.

## M5: server-terminal rows in the Rust model (Stage B step 4, slice A)

Oracle: the real `_reconcile_remote_requests` terminal branch, `_remove_local_remote_request_by_id`, `_mark_rotation_slot_temporarily_empty` and the
removed-id / `_remote_terminal_titles` checks of the intake loop. Harness changes: snapshot field `terminal_titles` (id -> title the terminal row carried);
no new event (a terminal row is an ordinary `remote_arrive` row); 14 adversarial cases named `server_terminal_*` / `stale_pending_row_after_server_terminal_is_ignored`
/ `skipped_state_is_not_server_terminal`, and the `term`, `term_stale`, `term_reuse` generator picks (profile `full`).

### Rules

1. **What is terminal.** `state` (stripped, lowercased) in {`removed`, `completed`, `sung`}, OR `int(float(removed_at or 0)) > 0`, OR the same for `completed_at`; an
   unparsable stamp counts as 0, a negative one is not terminal, a fractional one is truncated (`"7.9"` is 7). `skipped` is NOT terminal here (it reaches the
   waitlist as terminal, the intake loop as "non-actionable/historical"). Rows with `request_id <= 0` are skipped before any of this.
2. **What a terminal row does** (the first thing for a row, before the tombstone check, before singer/title checks): (a) remove EVERY queued copy of the id;
   (b) add the id to the removed-id memory; (c) remember `str(row.title)` in `_remote_terminal_titles[id]` (overwritten each time, empty string if the row has no
   title). It writes **no tombstone** and retires none; a host tombstone for the same id stays and a later terminal row simply re-adds the id to the memory.
3. **Emptied singers.** After the removal, EVERY singer with an empty song list is revisited, not only the one that lost a song, and not only for this id: in
   rotation mode each is (re)marked `temporary_empty_slot` with `until = now + 180` (a refresh!). The row is never deleted (classic mode keeps it too, because
   `_should_preserve_empty_singer_row` is true for any named singer). A terminal row for an id nobody holds still does this refresh. The marker is untouched.
4. **Stale rows after a terminal row.** A non-terminal row for an id in the removed memory (and with no tombstone) is ignored unless the remembered terminal title
   CONFLICTS with the row's title (`_tombstone_signature_conflicts`: alphanumerics only, empty or substring = no conflict): then the id leaves the memory and the row
   is a brand new request (id reuse after a server reset). An empty remembered title never conflicts, so the row stays ignored. The remembered title is NOT updated
   by the reuse itself (a later stale row is compared with the OLD terminal title; observed in `server_terminal_id_reused_for_a_new_song`).
5. **Restart** keeps only tombstone ids in the removed memory and forgets `_remote_terminal_titles`, so a server-completed id is no longer protected after a restart.
6. The waitlist already treated terminal rows as server-purge hints (M3); the only change there is that Rust now uses the same `> 0` test as Python (before, any
   present `removed_at`/`completed_at` key counted, even 0).

### Surprises (reported, NOT changed)

- **Restart resurrects server-completed songs.** `server_terminal_memory_is_lost_on_restart`: queue Ada/Bob, server says Ada's request is completed (removed locally),
  restart, a stale `pending` row for Ada's id arrives: it is queued again, because the removed memory is rebuilt from tombstones only and the terminal rows write none.
  In production this needs a server that lists the old row as pending AFTER listing it terminal, so it is a hazard rather than a seen bug.
- The terminal-title memory is never refreshed by an id reuse: after the reuse is accepted the id is no longer in the removed memory, so the stale title is
  dormant, but a SECOND server-terminal row for the reused request overwrites it (correct) only if that row arrives.
- A terminal row refreshes the expiry of unrelated empty slots (rule 3), so a steady stream of terminal rows keeps empty singer slots alive past 180 s.
- `_server_terminal_conflicts_with_live_host_request` is NOT modelled: it only fires for a queued entry that carries a `host_request_key` (a host-manual add that
  the server has since acknowledged with a numeric id), and the harness cannot create one without the network. In the model, terminal rows always remove.
  This is the clean boundary of slice A: a terminal row for a host_manual entry (different `idempotency_key`/non-host source) is held by Python and removed by Rust.

Slice A result: parity IDENTICAL on 37 adversarial cases plus seeded streams (`parity --seeds 100`: 143/143 before the last case was added; `--seeds 20`: 64/64 after).
Mutation (`MUTATE_SEEDS=120`): 159 killed, 35 documented equivalent, 2 survivors (#102, #187); a targeted case was added for each and both are now killed
(re-checked alone with `MUTATE_ONLY=102,187`; the full run was not repeated after those two cases).
