# Stage E step 4: network slices in shadow (`singws-netslice`, `net_slice_shadow`)

One crate (`rust/crates/singws-netslice`, CLI `singws-netslice`, built by `rust/build_netslice.sh` into git-ignored `native/singws_netslice/<arch>/`, bundled by both specs, rebuilt by both installer scripts),
one Python module (`net_slice_shadow.py`, setting `net_slice_shadow`, default True). Python's value is always the one used; the shadow only logs.
Log tag `[NET-SLICE-SHADOW]`: launch line `enabled available=0/1`, `first check clean (...)` once per launch, `MISMATCH <slice> at <path>: app=len=.. sha=.. rust=len=.. sha=..` (each once per session, at most 20),
`skipped: ...`. Never a key, tenant, URL, track or response text. `tools/shadow_report.py` has the row in QUIET SHADOWS (`NET-SLICE-SHADOW`). It never opens a socket; tests and parity use fake `requests` only.

| Slice | Python (oracle) | What Rust rebuilds | How it is observed |
|---|---|---|---|
| 1 `probe` | `probe_network_sync_status` (body is now `_probe_network_sync_status_impl`; the public name wraps it) | the whole result dict AND the list of requests (url, params, headers, timeout) from the inputs plus a transcript of what each call answered | `RecordingRequests`, a stand-in for `requests` that forwards every `get` unchanged |
| 2 `location` | `KaraokeApp._session_location_payload` (body is now `_session_location_payload_impl`) | the payload (or null) from the settings AFTER the call (a detection that ran has written its fix there); the failed-detection label `manual_fallback` and the app's own clock stamp are the only local values passed in | settings copy after the call |
| 3 `songbook` | `SongbookUploadThread.run` (track-list path only) | url, form fields, headers, timeout and the CSV text (dedupe, sort, csv quoting) from the tracks; Python supplies a fold table `{raw: [strip, strip.lower]}` because Unicode `lower()` has no safe Rust twin | `RecordingPost` at the `requests.post` call |
| 4 `support_log` | `send_log_package_to_developer` | url, form fields, headers, timeout, file descriptor (name, length, sha256 pass-through) and the refusal rule | `RecordingPost` |
| (parity only) `multipart` | `requests`' prepared body | the exact multipart/form-data bytes for a boundary | `tools/net_slice_parity.py` compares with the body `requests` really prepares |

Evidence: `tools/net_slice_parity.py` (IDENTICAL: ~2,800 probe cases, ~2,300 location cases, songbook/support cases with the REAL thread/function and 230+ real multipart bodies byte-equal);
`tools/mutate_net_slice.py` (184 mutants, none undocumented survivors; documented equivalents carry their reason); `test_net_slice_shadow.py` (40 tests, real helper); crate tests (21).
Declined (logged nowhere, counted as skipped by the comparator): non-ASCII settings in probe/location, underscores in coordinates, non-text settings, the song-index fallback of the songbook upload.
Findings while reading the code: `probe_network_sync_status` words a `ConnectionError` differently per call site (`connection error:` only for the intake call; `ConnectionError:`/`SSLError:` by type name elsewhere);
the location payload carries the API key (so it is only ever passed to the local helper on stdin, never logged).
Not verified: a real Network Sync Check, session-location push, songbook upload or support-log send from the running app; the helper has not been bundled in an installer build.
