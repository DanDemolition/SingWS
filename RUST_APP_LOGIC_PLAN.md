# Moving the app's logic to Rust, gradually

Status: ACTIVE (2026-10-23). Stage A0 is implemented, tested, and deployed on Apple Silicon. Signed arm64 and x86_64
installers are built and package-launched. The operator accepted the documented local CDG/MP4/KaraFun rehearsal in place
of three physical-Intel shows; it reported zero disagreements and complete coverage. A2 is complete, and A3 enables the
proven KaraFun-end decision by default with automatic Python fallback. Both signed package rehearsals passed.
This follows AGENTS.md: adapter-style, small reviewable steps, shadow before drive, a flag for every risky step, and old
code removed only after the replacement is proven.

## What "the app" is today

One Python file (`0.2.18.1.py`, ~61,000 lines) mixes four things:

1. **UI** (PyQt widgets + QML surfaces). It stays in place while the application core moves, then migrates last to a
   Rust-owned Qt Quick/QML shell so the packaged application no longer embeds Python.
2. **Decision logic** with no UI in it: when a song has really ended, when to advance the rotation, how requests move
   between states, when the next background track starts fading. These are the rules the live-show bugs came from.
3. **I/O glue**: server sync (HTTP/websocket), KaraFun control (AppleScript/Accessibility, macOS only), files, settings.
4. **Stores**: queue, history, stats, settings JSON/SQLite.

Only (2) is worth moving first: it is pure, testable, shared by any future Windows port, and is where regressions hurt.
(3) and (4) follow only if there is a reason (a Windows port). (1) stays.

## Stage A: song lifecycle machine (already built, in shadow)

`rust/crates/singws-transport` (300 lines): pure `step(state, event) -> (state, commands)`; events StartRequested,
PlayingObserved, IdleObserved, EndClockObserved, MediaEnd, StopRequested, ManualComplete, WatchdogExpired, CleanupDone;
commands BeginPlayback, RetryResult, MarkPlaying, StartBgHandoff, CompleteSong, AdvanceRotation, WarnOperator, Ignore.
Python feeds it through `transport_shadow.py`; it only logs (`[TRANSPORT-SHADOW]`, `MISMATCH`), and
`tools/transport_shadow_report.py` gives the acceptance verdict (needs >= 3 shows, every one with 0 disagreements).

**A0 (complete in source and deployed on Apple Silicon; physical-Intel install pending): make the shadow actually collect data.** Finding 2026-10-15: the dev Mac profile has
`"transport_shadow": false` saved from the earlier rehearsal instructions, which overrides the new default (on), so this
Mac has recorded 0 songs (the report says "no shadow sessions"). The Intel Mac's profile is unknown (its show log is no
longer available to check). Fix options: (a) operator deletes the key from settings.json on both Macs (shadow is log-only, safe);
(b) a launch-time migration enables a saved `false` once and records a migration marker. Option (b) shipped in commit
`dd1f077`; later operator changes remain respected. Count shows with the report tool before Stage A1.

**A1 (local gate complete by operator decision 2026-10-23): fix what the shadow finds.** Every MISMATCH is either an app
bug or a machine rule to fix. Local exit evidence: three isolated show logs, five sessions, 0 disagreements, covering real
native CDG/MP4 transport plus simulated KaraFun lifecycle, seek-near-end, manual Complete, stop, and watchdog. Physical
Intel performance remains unmeasured; see `docs/verification/2026-10-23-rust-transport-shadow-a0.md`.

**A2 (local gate complete 2026-10-23): let it DRIVE one decision, behind a flag.** New setting `transport_drive` (default off). Start with the single
decision that has hurt before: "has this external (KaraFun) song really ended?" (the 2026-08-31 false completion).
The Python executor still performs the action; the old Python decision stays in place and is compared on every call
(log any disagreement; on disagreement keep the OLD answer). The local harness explicitly proved agreement selects Rust
and both disagreement directions select Python, while the three-show lifecycle matrix remained clean. Then the same for
native media end, then watchdog, then stop/skip.

**A3 (local and package gates complete 2026-10-23): flip the default, one decision at a time, each after a clean show. A4: delete the old Python decision code.**
The default is now on only for KaraFun automatic-end detection; both answers are still computed, and Python remains the
automatic disagreement fallback. Signed arm64 and x86_64 DMGs passed isolated-profile clean-launch/clean-exit smoke tests.
Rollback at every step: `transport_drive: false`.

## Stage B: queue / rotation / request lifecycle (the biggest win, the biggest risk)

The rules already written in AGENTS.md ("Queue and server synchronization rules": host authoritative, permanent request
IDs, requests transition state and are never recreated, tombstones stop resurrection, oldest duplicate wins) are exactly
a state machine. New crate `singws-queue`: state = rotation + requests with ids; events = server snapshot, host action
(delete/reorder/replace/move), request state change, reconnect, restart restore; output = the minimal list of changes.

Method (do not skip): (1) build a **replay harness** from real data first: the app's `[REQUEST-DIAG]`, `[WAITLIST-STATE]`,
server-sync lines and the PHP-side fixtures already used in the history/queue tests; (2) property tests for the invariants
("a host-deleted request never returns", "ids never change", "duplicates keep the oldest"); (3) shadow it exactly like Stage A
(log disagreements, never act); (4) drive behind `queue_drive`, host actions first (smallest surface), server pulls last.
This touches live queue and singer data, so every step is rehearsed on the scratch profile and reviewed with real traces
before it is allowed near a show. Do not start until Stage A2 has run a clean show.

**B1 queue-rules shadow (2026-10-23, DONE for now):** pure `rust/crates/singws-queue` (poll-row admission, title-conflict rule,
oldest-wins, reorder permutation check, executable `Queue` invariants with property + mutation testing), `queue_shadow.py`, setting
`queue_shadow` (default OFF; enabled in the dev Mac's live profile for the next show). Log-only: the app's answer is always used.
Parity over the real logs: 0 disagreements. Evidence and the pre-flip show checklist are in
`docs/verification/2026-10-23-rust-transport-shadow-a0.md`. Deliberately NOT done (needs real show evidence first): flipping
`queue_shadow` on by default, driving host actions from Rust (`queue_drive`), the remaining decision sites (host delete/reorder/
replace/move, server pulls) and the slot-lifecycle model. Slot expiry must follow the persistence decision below.

**B0 replay harness (started 2026-10-23, `tools/queue_replay.py`, `test_queue_replay.py`).** Parses the real show logs
(`[REMOTE-TOMBSTONE]`, `[REQUEST-LIFECYCLE]`, `[QUEUE-DEDUP]`, `[QUEUE-INSERT]`, `[HOST-REQUEST-SYNC]`) into a normalized event
corpus (`--corpus out.jsonl`) and checks three invariants against what the Python app actually did: RESURRECTION (a
host-removed/completed id never returns from the server), DEDUP_OLDEST (the kept duplicate has the smaller epoch-ms id), and
ID_STABLE (a host request key never maps to two ids). First run over 151 logs: 0 violations. Findings that shape the rest of B:
* ~89% of queue events in the live logs are unit-test noise (`request_id` < 1e12 written by tests into the real log). The
  harness drops and counts them; the real corpus is only 169 request ids, 92 tombstones, 1,301 resurrection-guard hits.
* DEDUP_OLDEST and the id-reuse path have NO real events yet, so their clean result proves nothing; the harness says so.
* B0b (2026-10-23): the harness now also parses `[QUEUE_SYNC_AUTHORITY]`, `[QUEUE-AUDIT]`, `[ROTATION-SLOT]`, `[QUEUE-STATE]` and
  `[SERVER-STATE]` and checks host-action invariants: AUTH_PERMUTATION (a remote reorder never adds/drops ids), AUTH_HOST_ORDER
  (an applied host order is honoured), AUTH_ECHO_NOOP, SLOT_FILL (a slot fill follows a preserve, never a host removal) and
  ACCEPTING_HOST (accepting-requests mismatches always repair server-to-local). Violations carry an origin (real / test /
  ambiguous / unverifiable); test and unverifiable ones are reported but do not fail the run. Result over 151 logs: 0 failing
  violations; 1 unverifiable slot fill (the 2026-07-12 log that preserved the slot is missing). Coverage is uneven: 1,058 accepting
  repairs (all server-to-local) and 2,684 slot fills back their checks, but only 2 real reorder events exist (almost all
  QUEUE_SYNC_AUTHORITY data is test-origin), `same_item` is False in all 4,978 QUEUE-STATE lines so it supports no invariant,
  and dedup / id-reuse have no real events.
* DECIDED (owner, 2026-10-23): state persists across an app restart so a crash never loses data. Preserved singer slots are
  restored at startup and are NOT expired by their `timeout_sec=180` just because the app was down (seen 2026-07-14 23:58 preserve,
  2026-07-15 00:07:52 restore). The Rust queue must model restore as a no-op on persisted state (the `Restart` event already is),
  and any slot expiry has to be driven by live elapsed time while the app is running, never by wall-clock time across a restart.
* Still to do before the pure `singws-queue` crate: real host reorder/replace/move traces (a rehearsal session on the scratch
  profile would generate them), the PHP-side fixtures already used by the queue tests, and the test-log isolation fix (DONE 2026-10-23: `_test_profile.py` gives every test process a private, auto-deleted
  `SINGWS_HOME`; 59 test modules import it, and `test_test_isolation.py` fails any new test that loads the app without isolating).
  Before the fix ~52 test modules, when run directly with `python -m unittest`, wrote into the live `~/SingWS` profile: the show
  log plus `host_request_sync.json`, `loudness.json`, `loudness.checkpoint.jsonl`, `transition-analysis.json` and
  `waitlist_removed_tonight.json`. The noise with unambiguous test request ids was removed from the live logs on 2026-10-23 (archived first, `tools/clean_test_noise_from_logs.py`); name-only noise remains and the replay harness still filters by request id.
* Separate bug worth fixing: unit tests write into the live `~/SingWS/logs`; they should use an isolated `SINGWS_HOME`.

## Stage C: background-music transition planning (small, pure)

"When does the next track start fading, how long, from which offset" is already isolated in Python
(`transition_analysis`, the scheduler maths). A pure Rust `plan_transition(metadata, now) -> plan` shadowed against Python on
real playlists is a one-session job and the Rust BGM engine already consumes the result. Low risk, low urgency.

**C1 transition-planning shadow (2026-10-23, DONE for now):** pure `rust/crates/singws-plan` ports the five planning rules from
`transition_analysis.py` operation-for-operation (audio boundaries from an envelope, fade-out estimate, record validity,
`bgm_audio_window`, `select_bgm_crossfade_seconds`, `calculate_effective_karaoke_end`); `plan_shadow.py` + setting `plan_shadow`
(default OFF) compare the two live BGM decisions (content window, crossfade length) with Rust and LOG `[PLAN-SHADOW]` lines only.
Evidence: `tools/plan_parity.py` over the app's real analysis cache (443 usable records: 69 background-track envelopes, 2,215
crossfade and 3,101 window probes, 443 effective-end and validity checks) shows 0 disagreements, bit-identical; a seeded
randomized differential (3,000 records incl. invalid ones, 1,500 envelopes) against the app's own Python functions also shows 0,
and 4 of 4 deliberately broken Rust rules were caught by it. Honest limits:
* The real cache contains no verified karaoke early end (the cache only has `audio_end_unverified` / `visual_end_unverified`
  outcomes), so the rule that authorises an early song end is proven only by the unit tests and the randomized differential,
  not by real data. It is used offline in the analysis worker, so it has NO live shadow; the two hooked sites are the per-tick BGM
  window and crossfade length, de-duplicated so a track costs one comparison.
* Parity proves Rust equals Python, not that the transitions sound right. Nothing consumes the Rust plan yet.
* Quirk preserved on purpose (a Python change, if wanted, comes first): `estimate_fade_out_from_envelope` searches the last 12 s
  and counts flat loud audio as "not rising", so the earliest qualifying window wins; for a track whose last ~12 s lose 8 dB it
  reports a fade start about 12 s before the audio end even when the audible fade is shorter, which pushes most such tracks to
  the 8 s crossfade cap.
* The 4 junk `song.zip` records test runs left in the live `~/SingWS/transition-analysis.json` were removed on 2026-10-23 (the parity
  tool still skips temp-path records). The live loudness cache was also rewritten by pre-isolation test runs and now holds one real entry;
  re-run library analysis to repopulate it. DONE 2026-10-23: the owner re-ran library analysis. `loudness.json` now has 258 real entries (all 69 background tracks plus all 189 library songs, each CDG keyed by its paired .mp3), none failed, none stale, no junk; `tools/plan_parity.py` re-run against the refreshed transition cache is clean.
Not done (needs real show evidence first): turning `plan_shadow` on by default, and any `bgm_plan_engine` drive step.

## Stage D: stores and server client

History/stats/settings persistence and the HTTP/websocket client (`reqwest`/`tokio`). This becomes necessary for the
no-Python package, but remains behind the existing storage/network adapters until replay and compatibility tests pass.
KaraFun control stays macOS-native (AppleScript/Accessibility) whatever the language; Windows would need a different mechanism.

**D0 store-format compatibility (2026-10-23, DONE; proof only, the app does not use it):** `rust/crates/singws-store` writes JSON
byte-identically to Python's `json.dumps` (key order and unknown fields preserved, `ensure_ascii` escaping incl. surrogate pairs and DEL,
CPython float `repr` incl. its tie-to-even digit choice and the `1e-05` / `1e+16` exponent forms, `indent=2` and compact layouts,
`sort_keys`, the trailing newline `_save_json_atomic` adds). Exported as `singws_store_roundtrip`. Evidence: `tools/store_roundtrip.py`
(read-only) rewrote all 14 live profile JSON files byte-identically (singer_history.json 1 MB, the 775 KB transition cache,
settings, tracks, tombstones, ...); a seeded differential over 1,500 random documents in 7 layouts matches Python's real `json.dumps`;
1.9 million floats (1M random bit patterns, 400k tie-prone dyadic rationals, 400k app-like decimals, 100k at the fixed/exponent
switch) match CPython's `repr`. The differential caught a real bug in the first formatter (exact ties such as 900719925474099.25 came
out as ...99.3, Python writes ...99.2), fixed by taking only the digit COUNT from Rust's shortest printer and the digits from its
exact-precision formatter. Values Rust cannot reproduce exactly (integers beyond 64 bits, NaN/Infinity, lone surrogates) are ERRORS,
never silent changes. All live files match one of Python's layouts, so a Rust store can adopt a file without rewriting its layout.
**D1 settings-store shadow (2026-10-23, DONE for now; shadow only, Rust never writes):** `singws-store::settings` ports the mechanics of the
Python settings adapter: `parse_settings` (`_load_json_file(..., expected_type=dict)` rules), `fill_defaults` (the startup `DEFAULTS` loop:
existing keys keep value and position, missing keys append in `DEFAULTS` order, falsy saved values are kept), `serialize`
(`json.dumps(indent=2)+"\n"`), `WriteGate` (the `_JSON_WRITE_DIGESTS` skip), and `write_atomic`/`save` (private temp file in the same
directory, rename, mode 0600, temp cleaned on failure, digest forgotten on failure). The one-off `*_migrated` value migrations stay in
Python: they run between parse and fill unchanged. `settings_shadow.py` + setting `settings_shadow` (default OFF) check three steps of the
real pipeline and LOG `[SETTINGS-SHADOW]` lines: load (Rust parse equals what the app loaded, key order included), defaults (Rust fill equals
the app's loop), save (Rust serialization equals the exact payload `_save_json_atomic` is about to write). Safety: the exported library has
NO write function (`singws_settings_json` is pure and a test inspects the Rust source to keep it so); every Rust test uses a private temp
directory; the Python tests run the app's REAL `_load_json_file` and `_save_json_atomic` (extracted from the source, not re-implemented) inside
temp directories. Evidence: 18 Rust tests with 8 of 8 deliberately broken rules caught (the failed-save digest rule needed an extra test);
Python differentials over random settings (load, fill, save) and the app's real 173-key `DEFAULTS`; the live 225-key `settings.json` passes all
three checks read-only. Known, reported-not-hidden differences: Rust refuses integers beyond 64 bits, NaN/Infinity tokens and JSON nested
deeper than serde_json's 128-level limit, all of which Python loads; the shadow logs a MISMATCH for them. DECIDED AND DONE (owner, 2026-10-23: "protected against power loss as well as crashes"): the app's atomic write
now flushes the temp file to the drive BEFORE the rename and flushes the directory AFTER it, using `F_FULLFSYNC` on macOS (plain
`fsync` leaves the data in the drive's cache; measured about 10 ms per real write on the dev Mac, unchanged payloads still skip all
I/O). A real I/O error from the flush fails the save and leaves the old file; a directory-flush failure is ignored because the data is
already safe; filesystems without `F_FULLFSYNC` fall back to `fsync`. Rollback switch: `SINGWS_DURABLE_JSON=0`. The Rust
`write_atomic` has the same steps (injectable `Syncer`, so tests prove the order). Writing this up found two stores that were NOT even
crash-safe, now fixed with the same shared `_atomic_write_text` and byte-identical layouts: `remote_request_tombstones.json` (a plain
overwrite; an emptied file would have let host-deleted requests come back) and the live `bg_playlist.json`. Still not power-loss
durable and deliberately left: the loudness cache (`loudness.json` snapshot, temp+rename without flush, and its append-only checkpoint
only `flush()`es) and the SQLite databases (`singws.db` uses WAL + `synchronous=NORMAL`, `phrase_markers.db` the default without
`fullfsync`); all are regenerable analysis data, so a power cut costs a re-scan and not user data. D2 STORE SHADOW (2026-10-23, DONE for now; shadow only): the history, preferences, queue, tracks, tombstone and playlist stores use the same
write path as settings, so D2 generalises D1's proof instead of adding rules. `store_shadow.py` + setting `store_shadow` (default OFF, read at launch):
after the app has DURABLY written any store (the hook sits in the `else:` branch of `_atomic_write_text`, so it can never delay, fail or precede a
save), the exact bytes it wrote go to ONE background worker (bounded queue of 4; when busy the newest check is dropped and counted, never queued
without limit). Rust must parse those bytes and rewrite them identically under a known layout (indent=2 +/- newline, compact +/- newline, default
separators); the log says which layout matched per store. Singer history is ~1 MB, so the check is never on the GUI thread; measured: submitting a
1 MB store adds under 25 ms (bound; typically about the cost of a queue put) to the save. The report tool has a STORE-SHADOW row (required: queue.json;
optional: settings, singer_history, singer_preferences, tombstones, bg_playlist, tracks). Nothing else about history/stats is ported: the per-singer
counts and last-sang rules are application logic in Python, not store mechanics, and belong with Stage B/E when the queue/rotation model is owned.
D3 SQLITE READ PARITY (2026-10-23, DONE for now; offline proof, NOT part of the app or its library): `rust/crates/singws-db` + the `singws-db-cli` binary open a
database with SQLite's read-only flag (which refuses to create a missing file), `PRAGMA query_only=ON`, and accept only one SELECT; values are encoded exactly
(INTEGER vs REAL vs TEXT vs BLOB vs NULL, non-finite floats and invalid UTF-8 as marker objects). `tools/db_parity.py` snapshots each database with SQLite's
backup API from an IMMUTABLE read-only connection (it creates and changes nothing next to the live files) and has Python's `sqlite3` and the Rust reader (bundled
SQLite 3.50.2 vs the system 3.50.4) answer the same requests: schema + header pragmas, every table dumped by rowid, and the app's real read shapes from
`song_index.py` (the unescaped `%word%` LIKE search with `ORDER BY artist, title LIMIT`, the `LOWER()` artist/title lookup, the normalised-key lookup, the path lookup, the
fuzzy full scan) with parameters drawn from the data plus hostile terms (`%`, `_`, accents, quotes, 400-char terms). Result on the live databases: `singws.db` 1,212 of
1,212 requests identical (189 songs), `phrase_markers.db` 3 of 3 (19 markers, 186 song_meta); a 20,000-song database built by the app's OWN `rebuild_from_tracks_json`
and a hostile WAL-mode fixture (every value kind, NULLs, 64-bit extremes, inf, blobs, invalid UTF-8, a NOCASE index, a WITHOUT ROWID table) are identical too. Findings:
SQLite's LIKE ignores case for ASCII only (an upper-case accented query does not match stored lower-case text; the app lower-cases queries itself); the comparison first
reported a difference in `PRAGMA foreign_keys`, a CONNECTION default (rusqlite's bundled SQLite turns it ON, Python's sqlite3 leaves it OFF and the app enables it
explicitly on its write connections). RESOLVED 2026-10-23: both readers now set `PRAGMA foreign_keys=OFF` explicitly (exactly like the app's read connections), the value is compared in the schema parity, and a Rust test proves the explicit setting is what makes it OFF (the bundled engine default is ON). A future Rust WRITER must set it ON explicitly, as `song_index._connect` does. Deliberately NOT done: any Rust
write to a database (WAL, user_version migrations and the `fuzzykey()` Python SQL function used during migration need their own proof), and no live shadow (the search
path is hot and the song index is rebuildable). D4a NETWORK CONTRACT + BYTE-EXACT REQUEST BUILDING (2026-10-23, DONE for now; nothing in the app changes and NOTHING touches the network):
`tools/network_inventory.py` (AST, sends nothing) found the app makes 56 direct `requests` calls (17 GET, 39 POST) to 31 API endpoints plus 8 non-API URLs (KaraFun search,
release manifest/asset download, accepting/waitlist reads, GIFs), with no single choke point; every call has a timeout (3-12 s typical, 30 s logs, 180 s songbook upload), none
disables TLS verification, 9 endpoints are called from several places (host_chat_moderation x6, delete_remote_request x3, ...), most POSTs are form-encoded (`data=`), 3 use `json=`, 6 use
multipart `files=`. The committed contract is `docs/network_inventory.md` (a test fails when it goes stale or the call count changes). `rust/crates/singws-wire` +
`singws-wire-cli` reproduce what `requests` 2.34 puts on the wire (URL + query string, form body, JSON body, Content-Type/Length) with no sockets and no networking dependency (a test
inspects the crate for them). Differential against `requests.Request(...).prepare()`: 320,000 random calls (unicode, reserved characters, ints to 2^64, random-bit floats, bools, None,
nested lists) identical; the differential found two real rules my first version missed (an all-None form body is sent with a Content-Type but NO Content-Length; a None one list level deeper
is the text "None"); 12 of 12 deliberately broken variants were caught. Rust DECLINES (never guesses) what `requests` rewrites or interprets oddly: URLs it would re-quote (spaces,
non-ASCII, %-escapes), object-valued form fields (requests iterates the object's keys), non-http(s) schemes. Not covered yet: multipart `files=` bodies, the Qt WebSocket relay
(wss://wskar.com/relay, 11 QWebSocket uses), headers/cookies/redirect/proxy/TLS-root behaviour, retries and the app's own ack/queue logic.
D4 WIRE SHADOW (2026-10-23, DONE; log-only, the app's behaviour is unchanged): discovered that EVERY `requests.get/post` of the app goes through ONE object,
`network_lifecycle.ShutdownRequests` (an admission gate that rejects new work during shutdown plus an in-flight counter drained before exit so OpenSSL is not torn down mid-request;
any Rust client must reproduce both). `ShutdownRequests.set_observer` + `wire_shadow.py` (setting `wire_shadow`, default OFF, read at launch) compare, after each real non-streaming call, what
`requests` ACTUALLY sent (first request when redirected: URL, body, Content-Type, Content-Length) with what the Rust request builder (`singws_wire_json`, pure) says it would send. Rust sends nothing.
Only endpoint FILE NAMES are ever logged, never hosts, values, keys or query strings. Declined (counted, not mismatches): multipart `files=`, non-dict `data=`, non-JSON values, bodies over 256 KB,
URLs `requests` rewrites; a header the caller set itself is not compared. 19 tests including real HTTP round trips to 127.0.0.1 THROUGH the real `ShutdownRequests` (responses, errors, accounting and
streaming untouched by the observer); 13 of 13 deliberately broken variants caught after two test gaps were closed. Packaged apps (both architectures): 6 of 6 library exports, the shadow saw the app's
real update check (`release.json` GET) and agreed, 0 mismatches, 0 errors. `tools/shadow_report.py` has a WIRE-SHADOW row (needs a GET poll and a form POST in a session; lists endpoints seen and
calls declined). `docs/network_inventory.md` no longer carries line numbers, so only real changes (endpoints, kwargs, timeouts) invalidate it.
D4c WEBSOCKET RELAY (2026-10-23, DONE offline; nothing in the app uses it): `rust/crates/singws-relay` is PURE (no sockets, threads or clocks; a test asserts it): the relay URL exactly as Qt's
`QUrl`/`QUrlQuery` builds it (each value percent-encoded once, host lower-cased, explicit port kept, `ws` only for an `http` base, the `role=app&channel=host` variant), the classification of
incoming frames for BOTH workers, and the connect / single-shot 5 s reconnect / stop state machine as commands to execute. `tools/relay_parity.py` compiles the app's REAL `RelayRequestWorker` and
`HostControlRelayWorker` and compares: 3,000-9,000 random URLs per seed against real PyQt6 `QUrl.toEncoded()` (including the normalizer quirk that turns `https://` into `https://https:`), 340
frames dispatched identically, and 1,000-3,000 random lifecycle sequences per seed through the real classes (fake Qt objects) giving identical command traces. `rust/crates/singws-relay-ws`
executes that state machine over `tungstenite` (ws:// always; wss:// behind the opt-in `tls` feature = rustls + bundled roots, compiled but only exercised against loopback ws://). Against a scripted
RFC 6455 server on 127.0.0.1, the real Qt workers (real `QWebSocket`, real event loop) and the Rust client are identical in 8 scenarios: normal session + server close + reconnect, abrupt drop,
rejected handshake (Qt reports one disconnect then retries), nothing listening, host commands + compact ASCII JSON replies, host worker without disconnect status, awkward identities encoded alike, no
connection after stop; the handshake carries exactly Qt's five headers. 17 of 18 deliberately broken variants caught (the survivor is equivalent: tungstenite turns a Close frame into a closed-connection
error on the next read). Not covered: wss/TLS against a real server, the server-side protocol beyond the message types the Python workers handle, ping/pong timing, and any behaviour under a proxy.
D3b SQLITE WRITE COMPATIBILITY (2026-10-23, DONE offline; nothing in the app uses it, NOT in the dylib, temp databases only): `rust/crates/singws-dbw` reproduces the write paths of
`song_index.py` (connection pragmas incl. WAL + synchronous=NORMAL + foreign_keys=ON stated explicitly, `init_schema` with its column migrations and the `fuzzykey()` backfill, `rebuild_from_tracks_json`,
`update_from_tracks_json`) and `phrase_markers.py` (schema + uuid backfill, marker upsert/default/clear/soft-delete, BPM and beat-grid cache). The writer is a separate crate so `singws-db` stays
read-only by construction; its CLI only opens paths inside the system temp dir and never the live profile. `tools/dbw_parity.py` runs the app's REAL modules (time and uuid frozen) against it and compares
schema text, header pragmas and every row of every table: all 1,112,064 code points through `normalize_text`/`fuzzy_match_key`, `int(float(x))` corpus, `errors="ignore"` decoding, fuzzed rebuilds
and deltas (types, falsy values, key aliases, duplicates, real files for the stat fallbacks), legacy v1/v3/v4 upgrades, marker op sequences, odd top-level JSON shapes: IDENTICAL. Rust DECLINES (database untouched,
app would keep Python) what it cannot reproduce: containers or 64-bit-overflowing numbers where a value is bound, non-ASCII numeric text, JSON Python accepts but serde rejects (lone surrogates), and letters in
blocks where Unicode releases differ between Python's tables and Rust's (Latin Ext-D, Garay, Beria Erfe; the sweep found 28 such code points between Python 3.14.5/Unicode 16 and Rust 1.99/Unicode 17).
Re-run the sweep against the Python that is packaged before any drive. Also found: rusqlite's bundled SQLite defaults foreign_keys ON, so the markers writer sets it OFF explicitly like Python. 28 Rust tests,
49 mutants (`tools/mutate_dbw.py`): 47 killed, 2 equivalent (documented). Not ported: `apply_remote`/JSON import-export of markers, search/read paths (already proven by D3), any live shadow.
D4b RUST HTTP SENDER MVP (2026-10-23, DONE offline; nothing in the app uses it): `rust/crates/singws-http` sends the app's server calls. Decisions taken (owner said "do what you think is best"): `reqwest` 0.13 (async, driven from a
private one-worker runtime because the blocking client has no per-read timeout) with `rustls`; trust is the OS verifier by default or EXACTLY a supplied PEM bundle (`Client::with_roots`, meant for certifi's `cacert.pem`, which is
what `requests` trusts); the request on the wire is built by `singws-wire`; `timeout=N` = connect timeout N plus N per read (no total deadline); the `ShutdownRequests` admission gate and in-flight drain are reproduced (`Gate`); gzip/deflate decoded; redirects
followed up to 30; non-2xx is a response; streaming, multipart and custom headers are DECLINED. `tools/http_parity.py` runs the app's real `ShutdownRequests`/requests and the Rust CLI against raw-socket capture servers on 127.0.0.1 and compares what the SERVER
received (method, target, body, Content-Type, Content-Length, chunking, Accept) for random calls plus statuses, five redirect codes, gzip/deflate, a 5 MB body, HEAD, per-read timeouts (a server dripping for 1.2 s passes a 0.5 s timeout on both), silence and mid-body stalls, closed
and refused connections, and TLS (private CA: trusted only with its bundle, rejected by default, wrong bundle rejected): IDENTICAL after fixing three real differences the harness found (a bodiless POST must declare `Content-Length: 0`; an all-None form is sent
chunked and empty; a body-read timeout is a ConnectionError in requests). 9 Rust tests; 16 mutants killed (one by hand after a typo in the mutant). Not done: calling it from the app, a live shadow of the sender, WebSocket TLS, proxies/cookies, multipart, retries.
STAGE E STARTED (2026-10-23): owner chose Qt Quick/QML driven from Rust (`cxx-qt` 0.10) over a pure-Rust toolkit. Homebrew Qt 6.12.0 + CMake 4.4.4 installed (arm64; the Intel rehearsal needs an x86_64 Qt and an x86_64 build, not done). `rust/crates/singws-uimodel` holds toolkit-free
view models; `rust/crates/singws-shell` is the Qt Quick app. PANEL 1, READ-ONLY SONG LIBRARY: search equals `song_index.search_songs(fuzzy=False)` (2,272 searches over fuzzed libraries incl. `%`/`_`, accents, limit 3 and 500: same rows, same order, same fields; `tools/uimodel_parity.py`);
the typo-tolerant fallback is NOT ported (`fuzzy_fallback_needed` says when Python would use it). The panel opens `~/SingWS/singws.db` with SQLite's read-only flag (in WAL mode SQLite itself touches the `-shm` index, as the app's own readers do; the database file hash was unchanged) and never writes.
Run: `PATH=/opt/homebrew/opt/qt/bin:$PATH QMAKE=/opt/homebrew/opt/qt/bin/qmake6 cargo build -p singws-shell` then `~/.cache/singws-rust-target/debug/singws-shell [--query TEXT] [--snapshot FILE.png]` (evidence: `docs/verification/2026-10-23-rust-shell-panel1.png`, "alice" on the real library, 9 songs in the
same order as the PyQt mockup). It uses Qt's Basic style so the custom look is possible (the native macOS style refuses customisation). NOT done: the shell selector inside the PyQt app (today the Rust window is a separate read-only program that can run beside the app), keyboard
navigation and accessibility audit, a real list model (results travel as one JSON string; fine for 500 rows, not for scrolling a 100k library), packaging/signing of the Qt shell, no-Python bundle, Intel build. NOTE: AGENTS.md still says "keep the application as Python/PyQt" and "do not rewrite the entire app"; the owner's instruction to start Stage E
overrides it for this work, and the PyQt shell stays the default and the rollback.
STAGE E PANEL 2, SINGER HISTORY (2026-10-23, DONE, READ-ONLY): `singws-uimodel::history` reproduces what the PyQt Singer History page shows from `singer_history.json`: the store normalisation (songs merged by their folded key, totals recomputed, brand
priority canonicalised incl. the app's alias table and `Path.stem` quirks), the singer directory (name order, search over name/brand/songs, scan and render limits, "Showing N of M"), each singer's song rows (every search token must match, `str.casefold` via a generated exception table
`casefold_table.rs`, newest first, the "Last sang tonight/Oct 16/Oct 16, 2025 at 9:07 PM" labels in LOCAL time via `localtime_r`), the summary line and the meta counts. `tools/history_parity.py` (run with the venv Python) executes the app's REAL
`_normalize_singer_history_store`, `SingerHistoryDirectoryBuildWorker`/`SingerHistorySongsBuildWorker` (their `run()`), `_filter_singer_history_songs` and the label functions and compares: 80 fuzzed stores (key variants, falsy/odd types, duplicates, brands, unicode), your real store (202 singers, read-only; file hash unchanged),
12,000+ song views and 4,000+ directory searches, time labels in 6 time zones including DST edges and a late "now", and `casefold` for ALL 1,112,064 code points: IDENTICAL. A value Python's `int()` would raise on (inside songs) is DECLINED, as are the Unicode blocks where Python's case tables differ from Rust's
(336 code points between Python 3.14/Unicode 16 and Rust/Unicode 17). 18 Rust tests; 40 of 41 mutants killed, 1 equivalent after adding tests for three survivors (+1 shown equivalent with proof). The Qt shell now has tabs: Library and Singer history (`--tab history`, `--singer-search`, `--song-query`, `--snapshot`),
evidence `docs/verification/2026-10-23-rust-shell-panel2.png` (202 singers, Abby selected). NOT done: tombstones/deletions (the app hides deleted singers via the `deletions` map when merging server data; the panel shows what the normalised store holds, which is what the app shows), editing, sync, brand combo, sort options.
STAGE E PANEL 3, ROTATION (2026-10-23, DONE, READ-ONLY): `singws-uimodel::rotation` reproduces the audience rotation view from the persisted `queue.json` (as of the app's last save; the shell cannot see who is on stage unless told with `--now-singing`) and the library: the loader's
migration of entries (legacy tuple/list/string entries, dicts without `song_info`), `RotationView.update_rotation` (skipped singers and songs, duet display, "Rotation start" marker and the title, numbering, count label, next singer, the up-next/on-deck lineup, the now/next strips, a playing singer hidden, a live title) and the
display-name lookups (entry artist/title, then its `display_name` with the KaraFun suffix rule, then the song index, then the in-memory `tracks.json`, then the file name). `tools/rotation_parity.py` runs the app's REAL `update_rotation`, `display_name_for_queue_entry` and
`lookup_display_name` (host version over a temp song index) on a recording fake: 150 fuzzed queues x 5 "now singing" texts x playing/not, 1,410 views, IDENTICAL. FINDING AND FIX (2026-10-23): `RotationView.update_rotation` called `self._queue_entry_artist_title`, a method only the main window had, inside a `try/except`, so the table rows never carried a separate artist (the rail's current delegate no longer draws song or artist at all; the only
place the text surfaced was the NOW SINGING bar). Fixed in the app: `RotationView._queue_entry_artist_title` now delegates to the main window's splitter, so the rail's `song`/`artist` fields are filled. To keep today's audience-visible text exactly, the NOW SINGING bar uses the combined "Artist • Title" text (it would otherwise have
dropped the artist and the "Rotation start" marker). The Rust port follows the fixed code and was re-compared (1,410 views identical). Rust DECLINES a queue with two singers whose names are equal ignoring case/spacing (the app merges those on load; not ported), a non-list `songs`, an empty
`song_info` list, a library entry whose `display` is not text, and entries whose path would need Python's `repr` of a dict/list. 11 Rust tests for the module (28 for the crate); 31 mutants, 29 killed after adding two tests, 2 equivalent. The shell's third tab "Rotation" refreshes every 5 s from the file; evidence `docs/verification/2026-10-23-rust-shell-panel3.png` uses
the synthetic `docs/verification/sample-queue.json` (the live queue is empty while the app is idle). LIVE STAGE STATE (2026-10-23): the app can now publish who is on stage: setting `publish_live_state` (DEFAULT OFF) makes `_publish_live_state` write `~/SingWS/live_state.json` (playing, singer id, singer text, title, pid) from the rotation refresh and the host-state sync; best effort, no fsync, written only when the content changes, never raises (6 tests, incl. an end-to-end run where the real writer fed the Rust shell). The shell reads it, hides the playing singer from the list and shows the NOW bar like the app, and ignores the file when its pid is gone, so a crash cannot leave a singer on stage; without the file it falls back to the saved queue and says so. The queue file itself is saved on every change (debounced), so it is near-live. NOT done: countdown, QR, host controls, the host-side Up Next card list (a different widget with its own row logic), merging duplicate-name singers (rare: the app saves a merged queue).
STAGE E ON INTEL (2026-10-23): Homebrew has dropped Intel Macs (its installer refuses x86_64 even under Rosetta), and the Homebrew Qt is arm64-only. Replaced by Qt's official universal macOS kit, fetched with `aqtinstall` (PyPI, scratch venv) into `~/Qt/6.12.0/macos`
(qtbase, qtdeclarative, qtsvg, qtshadertools; 1.5 GB; QtCore/qmake are `x86_64 arm64`). `rust/build_shell.sh` builds `singws-shell` for both architectures against it and `lipo`s them: the arm64 build ran and snapshotted the library tab, and the x86_64 build ran under Rosetta and rendered the
singer-history tab (`docs/verification/2026-10-23-rust-shell-x86_64-rosetta.png`, 209 singers: the store had grown since the first screenshot). The binary finds Qt through an rpath into `~/Qt`; BUNDLING (macdeployqt-style copy of the frameworks, signing, notarization) and a real Intel-hardware run are NOT done. The Intel Mac that runs the show was not touched.
Previously listed next steps: D3 SQLite (`singws.db`, `phrase_markers.db`) compatibility via the existing schema, D4 HTTP/websocket
client. Each needs the same shadow-then-flag treatment and must never open a live profile for writing during tests.

## Stage E: Rust-owned UI and no-Python package

Move the host UI to Qt Quick/QML with Rust exposing the application model and commands. Keep libmpv, the native macOS
video bridge, Signalsmith C++, and platform APIs; "no Python" does not mean rewriting proven native libraries. Port one
panel at a time behind a shell selector, beginning with read-only views and ending with live queue/transport controls.
The PyQt shell remains the rollback path until the Rust shell completes the Intel and Apple Silicon rehearsal matrix.
Exit: the signed application contains no Python interpreter, Python modules, PyQt, or PyInstaller bootloader, and passes
the same show acceptance checklist on both architectures.

## What each stage buys, and what it does not

* Buys: rules testable without a show, one implementation for Mac and Windows, fewer GIL/GUI-thread dependencies in
  decisions, and the bugs of 2026-08-31 (false completion) and 2026-08 (resurrected songs) become unit tests.
* Does not buy: speed (decisions are microseconds already), a different look, or fewer lines overnight.

**First real-show findings (2026-10-23) and the post-show procedure.** Two sessions with the installed shadow build (BGM playing, a server
request, a reorder, a delete; no KaraFun song and no native song) produced: SETTINGS-SHADOW CLEAN (load, defaults, save all agreed), PLAN-SHADOW
CLEAN (window and crossfade agreed), QUEUE-SHADOW PARTIAL (the `terminal_stale` branch agreed; `tombstone` never fired because the server stopped
listing the deleted request, which is the normal case), TRANSPORT-SHADOW and TRANSPORT-DRIVE not exercised (no song). The earlier 12:32 KaraFun
test in the same log carried the first real transport disagreement (A1 finding): the app completed NOFX "Linoleum" on KaraFun's own ENDED state while
the machine, given only "idle" and a 240 s FALLBACK duration estimate (the song is not in this computer's library), answered "uncorroborated idle".
Python was right. Streamed or non-local KaraFun songs never have a verified duration, so this would have recurred on every such song and blocked the
clean-show requirement. Fix: a new Rust event `ExplicitEndReported` that corroborates an idle reading exactly like a fresh end clock (same freshness
window, ignored for other sessions, never completes a song by itself, never before playback is confirmed), fed from the KaraFun monitor under the SAME
condition the Python rule uses (`ended_reported` and a capture session). The real sequence is replayed in the Rust tests, the shadow tests and the
local acceptance harness. `idle` alone still never completes a song in the machine (an idle label once cut a 263 s song after 34 s).
Post-show procedure: `python3 tools/shadow_report.py` (reads the last app launch in the newest log and settings.json, read-only). Verdicts: CLEAN,
PARTIAL (not every expected decision type happened; it prints what to do to exercise it), NOT EXERCISED, NOT ENABLED, UNAVAILABLE, DISAGREED, ERROR.
Exit 1 on any DISAGREED/ERROR; `--require-all` exits 3 unless everything is CLEAN. A default may only be flipped after reports of real shows show
CLEAN (not merely "no mismatches") for the decisions it covers.

## Order and gates

| Stage | Needs first | Flag | Exit |
|---|---|---|---|
| A0 shadow collecting | operator removes `transport_shadow: false` (or migration) | `transport_shadow` | report shows watched shows |
| A1 fix mismatches | >= 3 shows | none | 0 disagreements in 3 shows |
| A2 drive one decision | A1 | `transport_drive` (off) | a clean show with compare logs |
| A3/A4 flip + delete | A2 | same | one clean show per decision |
| B queue/rotation | A2 clean show, replay harness | `queue_drive` (off) | invariants + replay + shadow, then host actions first |
| C transition planner | none | `bgm_plan_engine` | parity on real playlists |
| D stores/network | B/C state ownership proven | adapter selectors | persistence/network compatibility corpus passes |
| E Rust UI/package | A-D APIs stable | shell selector during migration | no Python in bundle; full two-architecture show acceptance |

## Immediate next step

Native media end now has its own A2 drive behind `transport_drive_media_end` (default OFF, uncommitted source change 2026-10-23): Rust answers only when it agrees with the retained Python oracle, Python wins every disagreement, and each call logs `[TRANSPORT-DRIVE] decision=media_end`. It still needs its clean-show/package gates before the default flips. The KaraFun watchdog decision is wired the same way behind `transport_drive_watchdog` (default OFF, `decision=watchdog` logs). Stop/skip is deliberately SHADOW-ONLY with no drive flag (owner decision 2026-10-23): a stop is an explicit operator command, there is no Python rule to compare, and a Rust drive could only veto the operator mid-show. The shadow keeps logging mismatches for it. The transport drive decisions are therefore complete in source; their default flips wait on real-show `[TRANSPORT-DRIVE]` logs.

Next: continue Stage E panel by panel (next read-only candidates: singer history, then the rotation view), keeping Stage B/A real-show gates as they are. (Earlier text: Stage B, starting with the queue/rotation replay harness built from real logs (no Rust state machine until the harness exists). Preserve comparison and
Python fallback until each decision has passed the same local and two-architecture gates. Only then start A4 removal of
duplicated Python lifecycle decisions. See `docs/handoff/2026-10-23-rust-migration.md` for the continuation checklist.

### Stage E: packaged shell (2026-10-24)
`rust/package_shell.sh OUTDIR` builds the universal shell, bundles Qt with macdeployqt (needs the `qttools` archive in ~/Qt), drops the SQL drivers, and signs with the local identity. Result: a 168 MB self-contained `SingWS Shell.app` (arm64 + x86_64); snapshot smoke-run passed natively and under Rosetta. Not notarized and not installed anywhere.

### Transport A2 KaraFun end: show evidence and fix (2026-10-26)
Intel show 2026-10-25 (4.5 h, 43 songs, build 1.0.1.6): media-end drive 37 decisions / 0 disagreements; KaraFun-end drive 6 decisions / 4 disagreements, Python won every one (owner=python-fallback), show unaffected. Cause: Python also ends a KaraFun song on idle when its own expected-end timer (`expected_end_reached`, verified OR estimated duration) is reached, but the shadow machine was only fed the end clock and KaraFun's explicit ENDED, and it knew only the start-time duration (none, or an estimate later corrected). Fix: the monitor now feeds `expected_end` (mapped to the existing `EndClockObserved`; no Rust or library change) under exactly the Python condition, before the idle reading. Replay tests cover the 29/32/43/15 shapes; the early-idle blip (session 43, +2 s) still completes nothing. Needs another real show to turn the row CLEAN; the KaraFun-end drive stays Python-wins until then.
