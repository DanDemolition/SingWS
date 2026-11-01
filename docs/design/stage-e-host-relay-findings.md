# Stage E: host relay messages (`singws-relaymsg`) - findings

Written 2026-10-31 before any code, from `docs/design/stage-e-no-python-gap.md` §1.7, RUST_APP_LOGIC_PLAN.md Stage D4 and the tree.

## What is already proven (Rust, no app use except `wire_shadow`)

| Piece | Crate | Proof |
|---|---|---|
| Request bytes (GET/form/JSON, headers) | `singws-wire` | byte-identical to `requests`; `wire_shadow` observes live calls |
| HTTP sender | `singws-http` | against a local server |
| Relay worker state machine (connect/ack/reconnect/backoff) | `singws-relay` | pinned to the real worker classes |
| Relay over a real socket | `singws-relay-ws` | local fake relay |

## What the HOST still builds in Python with no Rust counterpart

`_publish_host_state_ws` (0.2.18.1.py ~29183) sends four kinds of message through `worker.send_json`:

1. `playback_tick` - from the playback block of `_host_control_state()`.
2. `state` - envelope + `rotation` / `playback` / `bg_*` from the same state, plus `queue_sig` when shell commands are on.
3. `queue` - `_shell_queue_summary()` rows, `_shell_waitlist_summary()` and the `_queue_signature()` hash.
4. `chat` - `_shell_chat_summary()` (room Everyone / Private / Host chat, attention counts, thumbnails refs).

Not in this step: `probe_network_sync_status` (a decision that is driven by live HTTP responses and clocks; its natural oracle is the already
proven `singws-http`/`singws-wire` pair plus a fake server, which is a different kind of test) and the worker threading.

## The pick

**The pure payload builders for the four host relay messages** (`tick`, `state`, `queue`, `chat`), as one crate `singws-relaymsg` with a JSON CLI.

Why this slice: every field is data; each Python builder is a method whose only inputs are plain containers on `self`, so the REAL functions can be
run as the oracle on a stub object (the same technique `test_shell_chat_summary.py` already uses); it touches no thread, timer, socket or widget; and the
schema is what the relay and the web remote already consume, so a difference found here is a real, user-visible divergence.

## What the Rust side computes and what the host supplies

The helper takes a *snapshot of the inputs* and returns the message the app should have sent. The host (Python) resolves what needs the app's
library or locale rather than data:

* queue rows: ids come from `_ensure_singer_id` / `_ensure_queue_entry_id` (they mint uuids); artist/title come from `_queue_entry_artist_title`
  when an entry lacks them (a library lookup). Rust applies the slicing (200 singers, 25 songs), the dict/non-dict filtering, `str()`/`bool()`
  coercions, the key set and order, and the 16-hex SHA-1 `queue_sig`.
* chat: Python's `str.casefold()` and `str.title()` have no safe Rust twin (`ß`, `ǆ`, final sigma, ...), so the host supplies a *fold table*
  `{string: [casefold, title]}` for every string that is folded. Rust does grouping, ordering, bounding, unread/attention counts and the
  per-message shaping (clip to 120 chars, removed text withheld, `media_ref` rules, GIF host regex, `$` that also matches before a trailing newline).
* anything that Python would coerce with `int()`/`str()` of a type we do not model (lists, nested objects, huge floats) makes Rust answer
  `{"unsupported": ...}`; the shadow treats that as "skipped", never as a mismatch.

## Findings while reading the code (behaviours to copy, not to fix)

* `_shell_chat_summary` builds the Host-chat thread list from `set(grouped) | {...}`: **set order depends on PYTHONHASHSEED**. Threads with equal `at`
  can therefore be sent in either order by Python. The comparison treats the `host` list order as defined only up to ties on `at`.
* The relay `dm` thread order follows dict insertion order of the first message per thread, then a stable reverse sort by `at`.
* `_room_display_name` scans `_room_messages` in insertion order, not id order; everything else sorts by id.
* A `queue_sig` and the `queue` message are computed by two separate Python passes; the hook runs after both and uses the same queue snapshot.

## Shadow design (`relay_msg_shadow`, default True like the other shadows)

`relay_msg_shadow.py` + hook in `_publish_host_state_ws` after each message is sent. Python's message is always the one sent. The hook only stores
a reference to the message object it already built plus a snapshot of inputs, and only when the throttle is due (one batch every 30 s for the whole
app, so a ~3 s publish cadence costs nothing). The helper runs on a daemon thread at `nice 10`. A mismatch logs
`[RELAY-MSG-SHADOW] MISMATCH <kind>: ...` with the path of the first differing key and lengths/hashes only (chat text and names are never logged);
each distinct difference is logged once per session. A missing helper is silent. No existing log line, schema, retry or connection code changes.

`tools/shadow_report.py` / `tools/gate_ledger.py`: the two newest Stage E shadows (`LOGPACK-SHADOW`, `PLATFORM-SHADOW`) are not listed there either
(their lines are mismatch/skip only and carry no "decision" rows), so nothing is added; this shadow follows them. A decision line would be a format
addition the operator has not asked for.
