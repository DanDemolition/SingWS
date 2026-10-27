# Waitlist page and ticker strip in the Rust shell (read-only), 2026-10-27

New: `WaitlistPanel` (`rust/crates/singws-shell/src/waitlist_panel.rs`) and `qml/WaitlistPage.qml`, tab "Waitlist" (`--tab waitlist`). Nothing is written; the app is not changed.

## What it reads
| Source | Feeds | Note |
|---|---|---|
| `waitlist_removed_tonight.json` (the app's own file) | "Removed Tonight" (checkbox) | same evening / venue gate as `_load_waitlist_removed_tonight`; rules in `waitlist::removed_tonight`, compared with the real method by `tools/waitlist_parity.py` (`evening`, `removed` checks) |
| `waitlist_state.json` (optional live feed; version 1 + the writer's pid, which must be running) | "Waitlist", "Pending Acceptance" | written by the app when the setting `publish_live_waitlist` is on (**default off**: add `"publish_live_waitlist": true` to settings.json with the app closed, or turn it on at the next release's migration). Without the file the page says so instead of guessing |
| `queue.json`, `settings.json`, `live_state.json` | the ticker strip (the text the audience sees) | `ticker::view`; who is on stage fills the `%cur...` tokens |

Test hooks: `SINGWS_HOME`, `SINGWS_NOW` (epoch), `SINGWS_WAITLIST_FEED` (feed path, no liveness check), `--waitlist-removed 1`, `--dump-waitlist FILE`, `--snapshot FILE --snapshot-after MS`.

## Verified
* `cargo test -p singws-shell waitlist`: 3 tests (no files; removed-tonight file + queue + custom ticker message; a trusted feed).
* Offscreen snapshot with fixtures (a feed with a waiting request, a needs-review replacement and a deferred add; a removed-tonight file; a queue with a duet and a skipped singer; a custom ticker message): `2026-10-27-waitlist-page.png`. Looked at by eye: sections in order Pending Acceptance, Waitlist, Removed Tonight; first-turn and queue markers; replacement version; times; the ticker line `Welcome to SingWS! | Next: Ann   |   1. Ann   |   2. Bo & Cy   |   3. Dee`.
* Without any feed: `2026-10-27-waitlist-page-no-feed.png`.
* Dumped view (`--dump-waitlist`) matches the sections shown.

## Not verified
* A real show's data (fixtures only; the real `~/SingWS` was not touched).
* Scrolling of a long list, narrow window widths, VoiceOver.
* A real show with the setting on.

## The app-side writer (added 2026-10-27, operator-approved)
`KaraokeApp._publish_live_waitlist` (0.2.18.1.py): setting `publish_live_waitlist`, default **off**; writes `~/SingWS/waitlist_state.json` = `{version, pid, updated_at, waiting{id: request}, pending_acceptance_requests[], first_time_names[]}` taken from the rows the page itself lists (`_waiting_for_add_sections`, so the app's own accepted / already-queued filtering is what the shell shows). Called from `_schedule_waiting_for_add_view_refresh` (every waitlist change) and `_sync_host_control_state_now`; best effort, never raises, written only when the content changed, temp-file rename, pid so a reader ignores it after a crash. Tests: `test_live_waitlist.py` (7), including one that publishes 25 random waitlists through the real methods and checks the Rust sections built from the file equal the app's own sections row for row.
