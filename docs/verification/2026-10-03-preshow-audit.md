# Pre-show audit, 2026-10-03

Scope: the checklist in "full test for singws.rtf" (18 phases), done headless where possible so the operator's screen and audio were
not touched. Nothing was built or installed by the audit itself. Live data (`~/SingWS`) was never used by any test (scratch `SINGWS_HOME`).

## 1. Repo findings
- One 60k-line PyQt file (`0.2.18.1.py`, was 62,084 lines, now about 60,130) plus about 25 small modules (BASS/libmpv engines, song index,
  transition analysis, theme, KaraFun capture). Python owns UI and flow; BASS/libmpv/ScreenCaptureKit own realtime audio and video.
- Every `requests.*` call (55 sites) runs on a worker thread; the only `time.sleep` calls in top-level methods are in the KaraFun worker.
- Server: PHP + SQLite; 19 test suites.
- Dev Mac is Apple Silicon; the venue Mac is Intel. Intel-only behaviour (the 120-400 ms stalls, the 400 ms stall after each song end)
  cannot be reproduced here.

## 2. Tests performed
- Static: pyflakes (60 warnings in 62k lines), vulture (337 findings), name-by-name cross-check of every candidate against all tracked files.
- App unit/regression: 1,232 tests (plus 92 that need the mpv/BASS environment) - all pass. Server: 19 PHP suites - all pass.
- Headless real-window run (off-screen, scratch profile): startup, idle CPU, thread/timer/widget/fd counts, 500 view switches, cProfile of idle.
- Rendering: the real animation QML rendered off-screen at 430x200 up to 1920x1080 with long names; all five main views grabbed and inspected.
- KaraFun search and capture timing measured live on the real KaraFun (search only; nothing played).
- A real 2-minute CDG song played in a throwaway copy of the app with a native `sample` of the main thread, and a 12-second song queued three
  times to watch the between-singer animations.
- Last night's show log (6.3 hours, 18,879 lines) reviewed for errors, slowdowns and unexplained events.

## 3-4. Bugs reproduced and root causes
1. **Settings > Reset to defaults aborted** with a NameError: the handler still referred to the MP4-quality control removed from the dialog long ago
   (found by pyflakes; fixed).
2. **Between-singer animations clipped/zoomed in the host's small Karaoke Preview panel** (reproduced; the audience window was fine): text had
   fixed minimum sizes that cannot shrink into a ~430x200 panel; three more lines had no shrink-to-fit at all (fixed).
3. **Quit at 21:26:59 last night never logged "clean shutdown"**: one occurrence, cause unknown; operator saw no hang. Only log lines added.
4. Stalls (366 in the show log, 120-400 ms, about one per minute; 400 ms after each song end): NOT reproduced on Apple Silicon. The main thread
   spends about a quarter of its time repainting widgets and a tenth syncing Qt Quick during a plain CDG song; Python-level stacks end inside Qt.

## 5-7. Safe fixes applied (all committed, none built yet), with tests
| Change | Files | Tests |
|---|---|---|
| Reset-to-defaults NameError | 0.2.18.1.py | existing suites |
| Dead code: 86 definitions (1,901 lines), 3 modules (okj_ticker, karafun_provider, playback_providers), 4 unused settings | 0.2.18.1.py, 3 modules, test_karafun_provider.py | all suites re-run |
| Animation text fits small panels | 0.2.18.1.py (QML) | test_show_screen_vfx.py |
| KaraFun search 6.0 s -> about 3 s (poll for results instead of a fixed 3 s; cheaper row scans), identical answers on 6 queries | 0.2.18.1.py | test_karafun_search_fix.py |
| KaraFun picture starts from the last pane region while it is verified (about 2 s sooner); idle player frames held until playing | 0.2.18.1.py | test_karafun_fast_capture.py |
| Host chat pictures/GIFs (app + server), phone chat layout (safe areas, 44 px close, visual viewport) | app + SingWS-Server | test_host_chat_media.py, tools/test_host_chat_media.php, test_room_chat_http.php |
| Shutdown breadcrumbs | 0.2.18.1.py | test_shutdown_breadcrumbs.py |
| Headless stability check (view switching must not grow threads/timers/widgets/files) | tools/headless_stability_check.py | test_headless_window_stability.py |
Server deployed by the operator and verified hash-equal to the repo: chat files (8f6fff6) and `global.inc` (missing since July: adds a 5 s database-lock wait).

## 8-9. Commands
- Tests: `SINGWS_HOME=$(mktemp -d) QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORM_PLUGIN_PATH=/tmp/singws-release-qt-platforms ./.venv-test-arm64-fresh/bin/python -m unittest <all test modules except the five mpv/BASS ones>`;
  the five with `./.venv/bin/python -m unittest test_bass_init_once test_phrase_detect test_libmpv_background_engine test_mac_keep_awake test_karaoke_engine_selection`;
  server: `python3 SingWS-Server/tools/run_room_chat_tests.py`, `run_history_regressions.py`, plus the other `tools/test_*.php` in a disposable copy.
- Builds: not run. Commands are in HANDOFF.md (`./build_singws_mac_arm64.sh`; Intel needs the environment variables listed there).

## 10-11. Verification and measurements
- Idle (off-screen real window): 378 MB, 2 Python threads, 51 QTimers (13 active, none under 400 ms), 22 open files, about 5% of one core
  (Python-level callbacks account for about 2% of that).
- 500 view switches: memory +3 MB (lazy view creation only), threads/widgets/files unchanged, Python objects +180.
- KaraFun search 6.0 s -> 2.9-3.3 s; first picture about 2 s sooner (not yet seen on a real song).
- Main file 62,084 -> about 60,130 lines. Project folder 10.3 GB before the cleanup (see below).

## 12. Remaining known risks
- Early-start capture: if KaraFun's window or splitter moved since the last song, the TV shows a wrong crop for about 1.5 s before it re-aims.
- Search poll: if KaraFun delivered results in separated chunks (never seen in 14 searches) it could settle early; the retry ladder then applies.
- Phone chat layout verified only in a desktop browser at phone widths; keyboard/notch behaviour needs a real phone.
- Intel-only stalls unexplained; unverified: audible dropouts, MP4 A/V sync, VST/EQ chains, soundboard routing, device failure, a 4-hour soak with
  real playback (needs a rehearsal, not possible headless).
- Failure injection (corrupt MP3/CDG/MP4, unplugged drive) relies on existing tests; not exercised end to end.
- `global.inc` and chat files are live and verified; the `tenants/_template` files differ from the repo (runtime template data, left alone).

## 13. Recommended show-night precautions
- Build, install, and play one KaraFun song and one local CDG song end to end before doors; check the log for
  `starting at once from the last pane region` / `confirmed`, `search attempt=1 result=FOUND` about 3 s after the query, and no `[SHUTDOWN]` line stuck.
- Keep SingWS frontmost during the show (the ticker repair only runs while the app is active, by design).
- Do not move or resize KaraFun's window mid-show.
- Open the singer page on one real phone and check the chat sheet.

## 14. Deferred
- Stall root cause (needs a native `sample` on the Intel Mac during real playback; do not add in-process stack capture).
- Reusing one HTTP connection: tested, no gain at the app's call spacing.
- Quiet repeated log lines (REQUEST-DIAG every minute per held request; END-AUDIO every 5 s).

## 15. Assessment: GO WITH CAUTION
No known show-critical failure remains and all automated checks pass, but the playback, long-duration and reconnect checks that the checklist
requires for a plain GO were not possible headless. Do the 10-minute rehearsal above before the show.

## Addendum: rehearsal with an installed test build (2026-10-03 afternoon)
Played on the real Mac (Apple Silicon, normal profile): two KaraFun songs, an MP4, three CDG songs with background video, key/tempo/seek, history,
waitlist, host and phone chat, a 30 s Wi-Fi cut. All behaved; see HANDOFF.md for the log evidence. Corrections to the audit above: the KaraFun
"search 6 s -> 3 s" polling change was a regression on a fresh KaraFun start and was reverted (fixed 3 s wait restored); background music late at song end
was the operator's saved setting `karaoke_trim_verified_tail=false`, not code. Still unverified: Intel hardware, Intel-only stalls, a 4-hour soak.
