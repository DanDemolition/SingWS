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
