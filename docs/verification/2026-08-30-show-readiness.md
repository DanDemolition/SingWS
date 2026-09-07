# Show readiness audit — 2026-08-30

Scope: changes since the 2026-08-29 evening 0.4.6.4 release through installed
0.4.6.5 (`056ba38`). This is a technical preflight, not a completed live-show
acceptance test.

## Blocking finding: loudness analysis remains unsafe to rerun

The live cache last written at 08:12 contains 68,667 `no measurable loudness`
failures at `failure_version=2`, plus 24 version-2 helper timeouts. The first
repair dry run only detects old-version failures; zero legacy poison does not
mean the current cache is healthy. Valid measurements remain at 14,120.

The source explains the repeated poisoning: `IsolatedLoudnessSession` disables
itself after three failures. `AnalyzeLibraryWorker.run` continues processing
subsequent files; `_measure_loudness_lufs` returns `(None, None)` for an unusable
isolated session, and the worker records that as a permanent media failure.
This morning's logs show groups of three helper timeouts followed by thousands
of failed records and zero successful measurements. The original helper
timeouts still need separate investigation; a passing unit suite does not
validate that production helper path.

An isolated reproduction with a disabled helper and three synthetic queue items
marked all three as `no measurable loudness` without invoking a working decoder.
All file/cache writes in that reproduction were mocked or confined to a scratch
`SINGWS_HOME`.

Do not run another full/Turbo scan until this is fixed. The fix must stop the
batch on decoder/session failure rather than mark unmeasured files bad, verify
the installed helper on real media, and then repair only the affected cache
records with a backup while SingWS is closed. No live cache was changed during
this audit. This finding prevents an unqualified show-ready sign-off; it does
not establish that the affected songs are unplayable.

## Verified

- `/Applications/SingWS.app` is 0.4.6.5 and is the running process. Frozen
  executable inspection confirms the detached painter, painter effects,
  versioned loudness failure cache, verified-audio-tail BGM handoff, and
  nonblocking KaraFun display-handoff changes are actually installed.
- Strict deep code-sign verification passes. Architecture verification passes
  for 432 files; minimum-version checks pass for 840 Mach-O paths at macOS 12.
  The bundled mpv bridge and libmpv both load successfully.
- The 0.4.6.5 DMG passes `hdiutil verify`; its SHA-256 matches `docs/release.json`.
- `./tools/run_tests.sh`: 799 tests and 21 subtests pass. Its Qt probe aborts in
  this environment and skips GUI modules; this alone is not full coverage.
- Matching-Qt `qtvenv` run: 202 GUI/performance tests pass separately. Two stale
  source assertions were corrected in `test_performance_safety.py`: bound the
  painter-only check to its class, and require the current ticker guard while
  forbidding audience-parent reordering from that guard. No application code,
  installed bundle, settings, or show queue was changed.
- Loudness-cache dry run finds zero poisoned legacy entries and preserves
  14,120 valid measurements, but the newer failures above remain. All 401 repaired
  ZIPs pass CRC, supported-compression, and single-MP3 checks; all 70 quarantined
  files are absent. A copied library database passes SQLite
  `quick_check`; none of the 70 quarantined paths remains in the songs table.
- Running app logs confirm BASS startup/preload, Quick transition initialization,
  detached painter ticker with effects, server relay and successful polling.
  Removed-request tombstones are honored during reconciliation.
- No new SingWS crash report appeared during this audit. The two reports from
  23:24 last night belong to temporary 0.4.6.4 builds and show Qt initialization
  aborts. The Python crash dialog during this audit came from the test Qt probe.
- The host UI was inspected on screen while idle. Only the built-in display is
  connected. Requests are closed. Memory stayed approximately 742–745 MiB during
  the initial idle observation; this is not a playback or analyzer soak test.
- Tests used isolated `SINGWS_HOME` directories. The live log continued to grow
  from the running application's normal sync activity, with no test traces seen.

## Reproduce GUI coverage

```sh
SINGWS_HOME=$(mktemp -d /tmp/singws-readiness-gui.XXXXXX) \
QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest \
  test_ticker_and_qr test_show_screen_vfx test_performance_safety \
  test_rotation_render_thread test_bg_video_lyrics test_model_view_qa
```

The runner also lists `test_karaoke_output_dsp.py`, which no longer exists;
it was omitted from the successful supplementary run. Raw successful test
outputs are saved alongside this report.

## Required live sign-off

1. With the actual audience display and audio output connected, play a CDG and
   an MP4 through their transitions. Confirm visible moving lyrics/video, ticker
   visibility and smoothness, and correct audio/sync, pitch, and tempo.
2. Run a KaraFun song through automatic search/start, audience handoff, completion,
   and return to SingWS. KaraFun was not running during this audit.
3. Verify the end-of-song BGM crossfade by listening, including a CDG final card.
4. Open requests when ready and submit one phone request; verify accept, reorder,
   removal, and reconnection without resurrection. Automated tests and existing
   production sync were checked, but no live test request was submitted.

No server deployment or app rebuild is required for the test-only corrections.
The separate analyzer defect will require a code fix and a validated replacement
build before its resolution can be claimed for the installed app.
