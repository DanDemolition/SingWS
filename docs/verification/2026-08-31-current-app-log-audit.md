# Installed app log audit — August 31, 2026, approximately 10:52 PDT

The operator asked whether the logs show anything else needing repair.
This is a read-only audit and simulated reproduction, not a source fix or
deployment. Both SingWS and KaraFun were left running and untouched.

## Build and evidence

`/Applications/SingWS.app`, version 0.4.6.5, executable SHA-256:
`4ab3b8f768f4d06081e18f02f5d6acd69289617f568af508b09099f566367751`.
The app log contains two launches of this candidate, 10:47 and 10:49. Reviewed
the new-build portion from line 1681 onward in
`/Users/Daniel/SingWS/logs/singws_2026-08-31.log` (2,326 lines at the initial
snapshot, still growing normally). The earlier supplied-log audit remains in
`2026-08-31-log-audit.md`; its source fixes are now installed.

Recursively checked DiagnosticReports, including Retired: 58 SingWS/KaraFun/
Python reports, newest matching report dated August 30 at 14:30:55. No new
matching native crash report and no new Python traceback/ERROR record were
found in the current sessions. This does not establish playback correctness.

## Findings

1. **KaraFun retry uses unknown state as evidence of failed playback.** At
   10:49:51 fast start assumes playback after the result activation. A 10-second
   monitor probe returns neither idle nor playing at 10:50:01. The monitor
   nevertheless logs "playback never started" and double-clicks the saved
   result coordinates again. Source confirms recovery requires
   `not playing_reported`, not a positively observed idle state. Unknown or
   incomplete accessibility readings can therefore cause an unnecessary
   activation during a working song. This is a concrete safety issue to fix.

2. **An early idle reading is recorded as a completed performance.** The
   matched track duration is 263 seconds. Playback hints arrive at 10:50:07,
   10:50:13 and 10:50:19; at 10:50:25 the first idle reading immediately
   completes the request with `remaining=251`. Source confirms idle after
   confirmed playback completes regardless of remaining duration. The message
   calls this `duration_fallback`, but idle is the condition that fired;
   the diagnostic reason is misleading. The server acknowledges completion
   at 10:50:26 (HTTP 200, first attempt), and local history contains one play
   for Lol with the correct KaraFun provider ID. Asked the operator whether
   they intentionally stopped/skipped early. That answer is still needed to
   distinguish early-stop counting from incorrect idle detection in this run.
   This is separate from the previously fixed cumulative-history inflation.

3. **KaraFun inspection remains slow.** The successful search takes about
   eight seconds, renderer handoff another nine seconds (overlapping playback),
   and recurring state probes take 4.9–10 seconds. Logs do not establish an
   internet outage: the observed slow path is accessibility inspection. Do
   not claim the song was inaudible until the monitor's delayed confirmation.

4. **Permission failure recovered.** At 10:48 the first launch was denied
   assistive access (-25211). After the operator restarted SingWS at 10:49,
   catalog matching, activation and fullscreen verification succeeded. Do not
   continue treating Accessibility permission as the current blocker.

5. **Saved audio output absent.** The saved External Headphones device is not
   connected; SingWS uses the system default without overwriting the saved
   preference. Verify the intended physical output before a show. Location
   detection also fell back to saved coordinates; neither proves a code bug.

6. **Server calls are succeeding.** Network diagnostics return HTTP 200 for
   intake, request accepting, host controls, history and rotation. Relay
   reconnects successfully; requests stay closed. No new history-sync failure
   or terminal-request failure is shown. Startup has roughly three-second
   stalls, but the log does not identify a new hot path and stack capture was
   correctly left off.

## Reproduction and next change

Extracted only `_start_karafun_completion_monitor` from the actual installed
PyInstaller archive, without importing the app or its live data paths. Fake
AppleScript, clock, thread, timer, UI and completion methods replayed an unknown
state, three playing hints, then idle. The exact installed monitor sent a fake
retry at 10 seconds and a fake completion at 34.4 seconds for a 263-second song,
logging 251 seconds remaining. No real clicks, timer, audio, request or history
writes occurred. Receipt:
`../../../local-installs/20260831-104012/karafun-monitor-audit.json`.

The smallest next change should distinguish unknown/failed probes from
confirmed idle before retrying and distinguish interrupted playback from
completed playback before recording history. Preserve the manual Complete
control and the existing fast normal-end transition; do not restore long
unconditional waits or refactor the display stack. Resolve the operator's
early-stop question before choosing exact completion behavior. No fix was
installed or history adjusted during this audit.
