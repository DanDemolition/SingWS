# KaraFun false completion — August 31, 2026

The operator confirmed they did not stop or skip the 263-second song that
SingWS automatically completed at 10:50:25 with 251 seconds remaining. This
confirms an incorrect completion, not an intentional early-stop test. The
underlying log audit is `2026-08-31-current-app-log-audit.md`.

## Narrow fix

Changes are confined to KaraFun state handling in `0.2.18.1.py`:

- Empty queue text is no longer an idle signal in either the launch probe or
  completion monitor; direct result playback does not need queue entries.
- Automatic launch recovery requires explicit idle and no previous playing
  hint. Unknown/failed observations cannot retry a result or press Play.
- Automatic completion requires confirmed playback, explicit idle without a
  playing signal, and corroborating end timing: a fresh advancing clock near
  its end, or the verified duration counted from confirmed playback. Repeated
  early idle, unknown status and elapsed duration alone cannot complete.
- A recently progressing near-end clock survives the transition to idle,
  allowing one normal end observation to complete without another slow scrape.
  Static clock labels and stale near-end clocks are not sufficient evidence.
- The independent duration timer never completes a request. It defers while
  playback is reported or confirmed playback duration remains, and asks the
  operator to use Complete if the end cannot be verified. A fixed grace budget
  can no longer force completion of a still-playing or unreadable track.
- Completion diagnostics identify the actual idle/end evidence rather than
  mislabelling an early idle transition as duration fallback.

Manual Complete and existing session/token guards remain intact. No renderer,
audio engine, queue synchronization, server code or settings changed. The
incorrectly recorded Lol test performance was not deleted or altered.

## Verification

`test_karafun_monitor_safety.py` replays real monitor code with fake AppleScript,
clocks, threads, timers and UI actions. The pre-fix code fails the reported
sequence and multiple unknown/early-idle/watchdog cases. All 18 safety scenarios
now pass, including normal end completion, missing duration metadata, stale
clocks, conflicting signals, prior playback hints and replacement sessions.
`test_karafun_lifecycle.py` retains its actual Qt Complete-button/Return checks
and now supplies a known duration for its normal-end session guard test.
`test_karafun_provider.py` checks the updated probe/monitor requirements.

325 regressions pass with scratch data:

```sh
SINGWS_HOME=$(mktemp -d) QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest \
  test_karafun_monitor_safety test_karafun_lifecycle test_karafun_provider \
  test_karafun_duration_estimates test_recent_regressions \
  test_singer_history_counts test_remote_request_tombstones \
  test_show_critical_regressions test_bgm_gapless test_exception_handler
```

`git diff --check` also passes. The test replay uses no actual mouse actions,
playback, server requests or live history writes. Build/install receipts are
being recorded under `../../../local-installs/20260831-110236/`.

## Limits and rehearsal

The underlying accessibility queries can still take 5–10 seconds. This fix
prevents uncertain state from performing destructive playback actions; it does
not claim to make those queries faster. When neither a reliable duration nor
progressing clock is available, use the manual Complete button at the end.
Timing alone does not prove that a paused song has finished.

Physical verification must replay the affected song through its real ending,
check Complete stays available and the rotation does not advance early, then
start the next local video. Publication remains a separate step. Candidate
installation/launch status is recorded below.

## Installed and launched — 11:08 PDT

Installed the verified candidate at `/Applications/SingWS.app`, keeping version
0.4.6.5 and the normal `com.singws.app` identity. Executable SHA-256:
`9adb6b0c305c03bd150a3e0c085b475f699590e7f8c9853d274f66cff1740dff`.
The prior `4ab3b8f7...367751` app is preserved in the receipt's verified ZIP.
All 18 safety cases also passed against the monitor code extracted from the
actual new bundle. Frozen modules match the source; 432 architecture checks,
840 macOS-12 compatibility checks, native library loads and strict deep signing
passed before installation, with signing checked again at the installed path.

The normal library loaded, host relay connected, requests remained closed, and
audience artwork/ticker were visually inspected. No new Python error or native
crash report appeared in the startup check. Server settings and complete
history content match the pre-install backup. The existing Lol test entry is
unchanged. No karaoke track was started by this task, no server code/config was
changed, and nothing was published. Physical full-song rehearsal remains open.
