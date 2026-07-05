# SingWS Live Karaoke Audit - 2026-07-05

Scope: macOS desktop app, online server, singer phone signup, host/server dashboard, Requests tab, Waitlist, Singer History, Show Rotation, DAW preview, BGM/playback-adjacent stability, app/server sync, queue ordering, duplicate request prevention, singer records, network on/off behavior, kiosk/tablet flow, public viewer where applicable.

## Current Priority Order

1. Blocker/high: bugs that can ruin a live show by losing requests, changing order, deleting singer state, accepting requests while closed, or crashing host workflows.
2. High: server usability and singer phone flow issues that confuse singers or hosts.
3. Medium: sync/order/data bugs that are recoverable but erode trust.
4. Medium/low: diagnostics, UI polish, and performance improvements that make live troubleshooting easier.

## Fixed Findings

### Request Records Collapsed By Weak Identity

- Severity: Blocker
- Repro: Singer adds song A, then song B. Server Requests tab or sync may show one song, hide one song, or reinsert later songs ahead of earlier songs.
- Expected: Every request has a stable unique request ID and multiple active requests for the same singer can coexist.
- Actual: Some paths treated singer/song/history identity as enough to collapse records.
- Suspected files/functions: `0.2.18.1.py` remote reconciliation/order handling; server request/order endpoints.
- Fix: Make request ID authoritative for sync/dedupe, preserve request order metadata, and keep host order authoritative.
- Regression evidence: `test_remote_request_tombstones.py`, `test_rotation_identity.py`, `tools/test_pending_completion.php`, `tools/test_singer_self_management.php`.

### Manual Host Order Lost After Sync

- Severity: Blocker
- Repro: Host manually swaps two songs, then app/server sync runs. Order reverts to server or incidental polling order.
- Expected: Manual host reorder persists locally and server-side and wins over later stale sync.
- Actual: Order could be rebuilt from returned payload order.
- Suspected files/functions: `0.2.18.1.py` order reconciliation and server order endpoints; `api/v1/set_remote_request_order.php`.
- Fix: Add revision/timestamp/order conflict handling and persist explicit order server-side.
- Regression evidence: Python remote order tests and PHP singer self-management reorder tests.

### Server Requests Tab Hid Delivered Active Requests

- Severity: High
- Repro: Song A is delivered to app, singer adds song B. Server Requests tab can omit A while app still treats it as active tonight.
- Expected: Requests tab shows all active/pending/delivered-until-completed requests in saved order.
- Actual: Delivered rows were too aggressively filtered from the host-visible Requests tab.
- Suspected files/functions: `requests.php`.
- Fix: Include active/pending/waiting/failed-for-review/delivered rows until completed/removed, sorted by saved order.
- Regression evidence: `tools/test_pending_completion.php`.

### Singer History Brand Cache Crash

- Severity: High
- Repro: Open/select Singer History repeatedly after brand choices cache is populated.
- Expected: Singer History selection never crashes.
- Actual: `TypeError: 'tuple' object is not callable` from `_history_brand_choices`.
- Suspected files/functions: `0.2.18.1.py` `_history_brand_choices`, `_refresh_singer_history_brand_combo`.
- Fix: Avoid method shadowing by using `_history_brand_choices_cached_key`.
- Regression evidence: `test_performance_safety.py` verifies the cache-key method is not shadowed.

### Completion/Removal Deleted Empty Singers

- Severity: Blocker
- Repro: Complete/remove a singer's last song from server/app. Singer disappears from rotation/list despite not being explicitly removed.
- Expected: Singer record remains until host explicit removal, singer leaves, or cleanup action.
- Actual: Completion/removal cleaned up empty singer rows and published empty lists.
- Suspected files/functions: `api/v1/complete_remote_request.php`, queue/rotation JSON update paths, desktop remote tombstone reconcile.
- Fix: Remove songs/request rows but preserve singer rows with empty `songs` arrays.
- Regression evidence: `tools/test_pending_completion.php`, `test_remote_request_tombstones.py`, `test_rotation_identity.py`.

### Clear Queue Deleted Singers

- Severity: High
- Repro: Host uses Clear Queue from desktop or dashboard/API. Singer records and published rotation entries disappear.
- Expected: Clear Queue removes active songs/requests but keeps singer identity, rotation position, history state, and preferred state.
- Actual: Server cleared `rotation.db` `singers`, published empty `rotation_*.json`, and desktop set `self.queue = []`.
- Suspected files/functions: `api/v1/clear_remote_queue.php`, `dashboard.php`, `0.2.18.1.py` `clear_queue_with_confirmation`.
- Fix: Clear only request/song rows and song lists; preserve singer entries locally and server-side.
- Regression evidence: `tools/test_queue_history_regressions.php`, `test_rotation_identity.py`.

### DAW Preview Network Timeout Spam

- Severity: Medium/high
- Repro: DAW/singer-screen preview attempts network upload/viewer checks while server is unavailable.
- Expected: Failures back off so playback/UI are not hammered.
- Actual: Repeated network attempts could spam logs and add UI/network pressure.
- Suspected files/functions: `0.2.18.1.py` DAW preview server upload/viewer checks.
- Fix: Add preview server success/failure backoff helpers.
- Regression evidence: `test_performance_safety.py`, `test_daw_relay.py`.

### Ticker Diagnostic Spam

- Severity: Low/medium
- Repro: Change ticker speed; app can log many identical `[TICKER] set_scroll_speed` diagnostics in one second.
- Expected: Identical diagnostic snapshots collapse; real speed/state changes still log.
- Actual: Duplicate state was logged repeatedly.
- Suspected files/functions: `0.2.18.1.py` `Ticker.set_scroll_speed`, `_log_ticker_state`.
- Fix: Track last ticker log state and suppress exact repeats.
- Regression evidence: `test_ticker_speed.py`.

### Duplicate Root Logging Handlers

- Severity: Medium
- Repro: Load/import the app module multiple times in the same process. Root logger gets another file/console handler each time, doubling diagnostics.
- Expected: Logging setup is idempotent.
- Actual: Duplicate handlers caused repeated sync/playback diagnostics.
- Suspected files/functions: `0.2.18.1.py` `setup_logging`.
- Fix: Mark SingWS-owned handlers and add them only if absent.
- Regression evidence: `test_performance_safety.py`; focused test output no longer duplicated every remote-sync diagnostic.

### Packaged App Smoke/Thread Safety

- Severity: Medium
- Repro: Launch the packaged app with `SINGWS_SMOKE_EXIT_MS=2500` while network sync is enabled.
- Expected: App initializes BASS/GStreamer/UI, reaches server sync, and exits on the smoke timer without crashing or worker-thread timer warnings.
- Actual: App exits with code 0 and syncs successfully, but the packaged smoke still emitted `QObject::startTimer: Timers cannot be started from another thread` immediately after remote request reconciliation/queue refresh, followed by QWebSocket disconnect warnings.
- Suspected files/functions: `0.2.18.1.py` `update_queue_display`, `_finish_queue_display_refresh_side_effects`, `schedule_ticker_update`, `_schedule_host_control_state_sync`, request relay/WebSocket shutdown path.
- Fix started: Added explicit UI-thread bounces before starting the host-control sync timer, ticker debounce timer, and rotation post timer; added opt-in smoke exit hook for repeatable packaged launch tests; added `aboutToQuit` network cleanup; changed smoke hook to close the main window; moved queue-refresh side effects into a dedicated owner-thread-guarded helper.
- Regression evidence: `test_performance_safety.py`; packaged ARM app smoke exits code 0. The latest queue-side-effect guard passed source tests but has not yet been packaged/smoked because the next GUI smoke escalation was blocked by the approval/usage gate.

### Rotation Slot Diagnostic Churn

- Severity: Low/medium
- Repro: Sync a large server payload containing many completed/removed historical request rows while preserved empty rotation singers exist.
- Expected: Empty singers are preserved without flooding logs or repeatedly resetting creation metadata for the same preserved slot.
- Actual: The same empty singers were logged many times during server reconciliation.
- Suspected files/functions: `0.2.18.1.py` `_mark_rotation_slot_temporarily_empty`, `_reconcile_remote_requests`.
- Fix: Preserve the original `empty_slot_created_at` for repeated same-reason marks and only log when the slot is newly preserved or the reason changes.
- Regression evidence: `test_rotation_identity.py`.

## Verified Test Commands

Desktop:

```sh
/Users/daniel/Documents/SingWS/.venv/bin/python -m py_compile 0.2.18.1.py test_performance_safety.py test_rotation_identity.py test_ticker_speed.py
/Users/daniel/Documents/SingWS/.venv/bin/python -m unittest test_performance_safety.py test_ticker_speed.py
/Users/daniel/Documents/SingWS/.venv/bin/python -m unittest test_rotation_identity.py test_remote_request_tombstones.py
```

Server:

```sh
php -l api/v1/clear_remote_queue.php
php -l dashboard.php
php -l tools/test_queue_history_regressions.php
php tools/test_queue_history_regressions.php
php tools/test_pending_completion.php
php tools/test_singer_self_management.php
php tools/test_waitlist_modes.php
```

Build artifact:

```text
/Users/daniel/Documents/SingWS/SingWS-0.4.0.1-arm64-installer-liveaudit4.dmg
```

Verified as arm64 with `file dist/SingWS.app/Contents/MacOS/SingWS`, verified DMG metadata with `hdiutil imageinfo`, and smoke-launched packaged app with `SINGWS_SMOKE_EXIT_MS=2500`.

## Checklist Status

Headless/regression coverage: partial pass for request identity, order sync, waitlist limits, server off stale request handling, singer preservation, history crash guard, performance-safe diagnostics, and DAW preview network backoff.

Packaged runtime smoke: partial pass. The ARM app bundle launches, initializes BASS/GStreamer, connects to `https://wskar.com`, reconciles server requests, and exits code 0 via the smoke hook. Remaining runtime warning: `QObject::startTimer: Timers cannot be started from another thread` during shutdown/relay teardown.

Manual/live runtime coverage still required:

- Launch installed app cleanly from DMG.
- Turn server on/off from app and dashboard and verify phone submits match setting.
- Add singer from phone/server.
- Add first and second songs; verify both in app, server Requests, singer phone view, and host controls.
- Try third song under max-2 rules; verify rejected or waitlisted according to settings.
- Remove/change a song and verify original song remains.
- Confirm singer remains with no active songs.
- Manually reorder songs and verify order survives app/server restart and sync.
- Playback while adding/removing/reordering requests.
- BGM start/stop/crossfade/normalization under karaoke playback.
- DAW preview and singer-screen preview on a real display/browser.
- Public viewer / now-next display.
- Kiosk/tablet signup.
- Mobile host controls if present.
- Slow network/offline/reconnect behavior.
- App restart and server restart agreement.
- Fresh crash logs and console output after the installed-DMG run.

## Next Recommended Fix Targets

1. Run installed-DMG live loop with a sandbox tenant and capture fresh logs.
2. Exercise server off/on with real phone/browser submit and verify no accepted request while closed.
3. Exercise playback while adding/removing/reordering to find UI/audio freezes.
4. Exercise BGM crossfade/normalization during server sync churn.
5. Exercise DAW/singer-screen/public viewer refresh with a real browser window and app restart.
