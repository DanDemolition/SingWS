# Singer statistics count fix — 2026-08-31

**Deployment update, 10:23 PDT:** the five coordinated server files are now
live on wskar.com and verified. History rows/tombstones and queue/settings were
preserved. The desktop fixes are in the separate private test build; its test
profile remains offline. See `../../../SingWS-Server/deploy/2026-08-31-statistics.md`
for deployment evidence and rollback. The rollout section below records the
original pre-deployment state.

## Verified cause

Python history keys distinguish streaming provider/track records. The old PHP
server discarded those fields, canonicalized everything to artist/title, and
summed the provider snapshots. On return, Python treated the resulting row as
a separate local song. Every subsequent sync added the streaming copies again
without a new performance.

The untouched pre-reset backup contains the matching pattern for Shawn:

| Song | Inflated local row | Additional streaming snapshots |
| --- | ---: | --- |
| Self Aware | 2,925 | two records of 1 |
| Apocalypse | 1,555 | one record of 1 |
| Sailor Song | 601 | one record of 1 |
| You're Somebody Else | 1,511 | records of 1, 1 and 3 |

These are diagnostic examples, not reliable historical performance totals.
No backed-up history was restored or edited.

The new Python↔PHP integration tests failed against the original implementation:
one performed song became two local plays after the first round trip; four
real completions produced 13 counts in a mixed-provider sequence. Those same
tests now pass with unchanged counts across repeated syncs.

Two additional count problems were found: `submitreq.php` recorded a performance
at request submission, and Fun Stats forced zero-play saved songs to one play.
The old recent-song card also treated a singer/song's entire lifetime count as
having occurred within 30 days whenever its last performance was recent.

## Changes

- `SingWS/0.2.18.1.py`: merge cumulative snapshots using maximum counts;
  preserve new completions against stale/equal-timestamp responses; derive
  displayed/exported singer totals from song counts; preserve provider-specific
  song deletion keys. The existing one-time completion/discard lifecycle remains
  the authority for new performances.
- `SingWS-Server/api/v1/singer_history_sync.php`: match desktop provider song
  keys and retain provider details through normalization, storage, deduplication
  and export. Schema migration adds `provider_metadata TEXT NOT NULL DEFAULT '{}'`
  to `singer_history_songs`; no table is dropped. Alias/snapshot merges use
  maximum counts, and exported singer totals are recomputed from visible songs.
- `SingWS-Server/submitreq.php`: remove request-time history writes. Request
  identifiers, delivery, pending limits, queue placement and retries are unchanged.
- Server history management preserves provider identities and no longer adds
  cumulative snapshots during duplicate cleanup.
- `funstats.php`: exclude zero-play entries from totals and awards. Recently
  sung songs form a shortlist ranked by complete lifetime totals, and the card
  explicitly says so. True rolling 30-day performance counts would require an
  event ledger, which is not introduced here.

Duplicate aliases are reconciled conservatively using the largest known
cumulative count. Aggregate history cannot prove that legacy aliases represent
disjoint performances, and adding them caused inflation. Actual new completed
performances still increment normally. This does not reconstruct historical
counts, nor change the single-host cumulative protocol into multi-host event
accounting.

## Verification

514 app tests passed with scratch SINGWS_HOME and offscreen Qt, including seven
real Python↔PHP/SQLite history tests. Five PHP suites passed in a disposable
server copy: canonical history sync, Fun Stats counts, queue/history regressions,
singer self-management and pending completion. PHP lint and both repository
diff checks pass. The live August 31 app log remained at 1,114 lines.

Focused command from `SingWS/`:

```sh
SINGWS_HOME=$(mktemp -d) QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest \
  test_singer_history_counts test_remote_request_tombstones test_singer_rename_merge \
  test_show_critical_regressions test_performance_safety
python3 ../SingWS-Server/tools/run_history_regressions.py
```

The broader 514-test run also included the BGM transition, recent regression,
KaraFun lifecycle/provider, model-view and rotation-render suites. Its output
is `/tmp/singws-stats-final-tests.log`; the isolated PHP runner output is
`/tmp/singws-stats-server-final.log`.

Tests prove: one play stays one after repeated sync; real repeats and separate
provider performances count once each; aliases do not add counts; stale replies
cannot erase a completion; provider deletions remain deleted; a repeated
completion callback counts once while an aborted song counts zero; incorrect
singer summary totals are replaced with song-derived totals; request submissions
and retries do not increase either legacy or rich performance counters.

## Installed/deployed state and rollout

No build, installation, server deployment, live database migration or additional
history reset was performed. Installed app remains 0.4.6.5 with SHA-256
`ecfcb0e0771c7ff6b33eee267528540117c85d923ac06acc29fc2122a40a0489`.

Read-only checks confirmed the live server still matches the pre-fix baseline:

- `singer_history_sync.php`: `61519d072f970d514eda8d3f82890bae45af514f1504227371c4f4428f8391cf`
- `submitreq.php`: `2c969b693401fc7ee555a6d5bc99fc3e71f510661fa674ea2a442eb4ed81d114`
- `funstats.php`: `2ad8702a84515a027a6ca46e71d073157f3a09695eea63120b9e6bea140254f7`

Outside a show, back up current server code/data and deploy the five coordinated
server files (sync API, request submission, Fun Stats, history admin and singer
history management), preserving tenant runtime data and config. Do not run an
unreviewed whole-tree `rsync --delete`. Then build with the existing Intel app
script and validate the accumulated app fixes before replacing the installed
bundle. The provider metadata addition is backward-compatible with existing app
payloads, but both codebases are needed for all fixes described here.

Do not restore the pre-reset database: its inflated aggregates cannot be made
trustworthy by this code fix. Keep the user's clean history and deletion markers.
