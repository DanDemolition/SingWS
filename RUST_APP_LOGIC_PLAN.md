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
  re-run library analysis to repopulate it. OWNER ACTION (2026-10-23): the owner will re-run library analysis in the app; once it has finished, re-check `~/SingWS/loudness.json` has many real entries and re-run `tools/plan_parity.py` against the refreshed transition cache.
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
explicitly on its write connections) that says nothing about the file and is excluded, but a future Rust WRITER must set it explicitly. Deliberately NOT done: any Rust
write to a database (WAL, user_version migrations and the `fuzzykey()` Python SQL function used during migration need their own proof), and no live shadow (the search
path is hot and the song index is rebuildable). Next, in order: D3b SQLite WRITE compatibility if it is ever wanted, D4 HTTP/websocket client, then Stage E.
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

Next: Stage B, starting with the queue/rotation replay harness built from real logs (no Rust state machine until the harness exists). Preserve comparison and
Python fallback until each decision has passed the same local and two-architecture gates. Only then start A4 removal of
duplicated Python lifecycle decisions. See `docs/handoff/2026-10-23-rust-migration.md` for the continuation checklist.
