# SingWS Search During Playback Audit

Date: 2026-05-31

## Scope

Audited search/filter paths that can run while karaoke, CDG, BGM, preview, or
queue UI updates are active.

## Findings And Fixes

1. Main song library search
   - Impact: High before the existing async worker/coalescing fixes; Medium remaining risk from repeated identical LIKE/fuzzy scans.
   - Current path: `KaraokeApp.search_tracks()` debounces text input, runs `SongSearchThread`, keeps one worker active, and batches result insertion.
   - Fix: added a thread-safe LRU cache in `song_index.search_songs()` keyed by DB path, mtime, size, limit, fuzzy flag, and normalized query. Repeated searches now avoid reopening SQLite and avoid rerunning the fuzzy full-table pass.

2. External request and display-name lookups
   - Impact: Low to Medium during network polling and queue refreshes, especially with repeated remote requests.
   - Current path: `find_by_artist_title()` and `find_by_path()` are indexed SQLite reads.
   - Fix: added the same DB-signature keyed LRU cache for successful exact artist/title and path lookups.

3. Background music browser filter
   - Impact: Medium. `QFileSystemModel.setNameFilters()` can repaint/rescan the visible filesystem tree on every keypress.
   - Fix: added a 220 ms single-shot debounce timer before applying filesystem name filters.

4. Singer history search
   - Impact: Low to Medium. The in-memory singer/song history filter rebuilt the visible list on every keypress.
   - Fix: added a 180 ms single-shot debounce timer before refreshing the history view.

5. Queue management
   - Impact: No blocking DB search path found. Queue UI updates are separate from song search result insertion; search result UI insertion remains batched.

6. CDG/video/audio playback
   - Impact: Search no longer runs on the UI thread for the main library path. Repeated searches now hit cache, reducing Python/SQLite CPU contention while CDG frames are decoded.

## Benchmark Results

Environment: local SingWS DB at `/Users/daniel/SingWS/singws.db`, sample CDG
`/Users/daniel/Music/Karaoke/Karaoke Library/CC - ACDC - Problem Child.cdg`.

Search timings:

| Query | Rows | Cold Avg | Cold Max | Warm Cached Avg | Warm Cached Max |
| --- | ---: | ---: | ---: | ---: | ---: |
| `a` | 150 | 1.364 ms | 1.551 ms | 0.036 ms | 0.052 ms |
| `acdc` | 1 | 5.199 ms | 5.513 ms | 0.027 ms | 0.140 ms |
| `problem child` | 1 | 7.311 ms | 7.487 ms | 0.019 ms | 0.082 ms |
| `cc acdc` | 1 | 5.406 ms | 5.674 ms | 0.018 ms | 0.070 ms |
| `zzzzmisspell` | 0 | 3.787 ms | 3.865 ms | 0.019 ms | 0.084 ms |

CDG frame decode timings over 6000 synthetic 30 fps frame requests:

| Scenario | Avg | P95 | Max |
| --- | ---: | ---: | ---: |
| CDG only | 0.0307 ms | 0.0983 ms | 0.4581 ms |
| CDG plus cached search loop | 0.0311 ms | 0.0990 ms | 0.5057 ms |
| CDG plus uncached search simulation | 0.0336 ms | 0.1009 ms | 3.5857 ms |

## Remaining Risk

The main library worker is still a Python `QThread`, so one cold fuzzy search can
consume CPU while it runs. It does not block the UI thread, and cached/repeated
queries are now very cheap. For very large libraries, the next improvement would
be replacing the fallback fuzzy scan with a SQLite FTS/token table or a separate
precomputed in-memory search index loaded once in a worker.
