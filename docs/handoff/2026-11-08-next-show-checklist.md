# Next show checklist (written 2026-11-08, for builds 1.0.3.2 and later)

## Before the show (Intel Mac)
1. Install: `bash install_on_intel.sh SingWS-<version>-x86_64-installer.dmg`, open SingWS once, `bash check_launch.sh`.
2. Python-free readiness (SingWS quit, TV connected, KaraFun open): unzip the newest `SingWS Shell.zip` into /Applications, then
   `bash check_python_free.sh "<path to a CDG song zip>"` (the script now checks the zip first and says what is wrong).
   Still open: the Rust video bridge failed to create on this Mac (`singws_bridge_create returned null`, nothing logged) in three runs; the new log says why once the song steps run.

## After the show
`bash after_show.sh` (SingWS quit): ONE zip on the Desktop that now includes the whole newest readiness folder. On the Apple Silicon Mac:
`python3 tools/show_checklist.py <zip>` (reads the newest real show if the zip holds several nights; `--group NAME` picks another).

## What to look for in the show log
| Line | Healthy |
|---|---|
| `[SONGEND-SHADOW]` | `first check clean` and NO `MISMATCH` (the 2026-11-07 show had none after the duration fix) |
| `[KARAFUN-FLOW-SHADOW]` | `startflow first check clean`, `readback first check clean`, `watchdog first check clean` (the watchdog line only appears when a KaraFun song outlasts its expected duration) |
| `[KARAFUN-MONITOR-SHADOW]` | `first song clean (N passes ...)`, no `MISMATCH pass N` |
| `[KARAFUN-SCRIPT-SHADOW]` | `first check clean` |
| `[CDG-SEEK-GUARD]` | only when a seek lands in a graphics-free stretch (never seen yet) |
| anything `MISMATCH` | read the song's lines around it before assuming a bug: the shadows model an idealised run (a GUI stall is a legitimate difference) |

## Ask the operator
Dead air at song ends (which kind: KaraFun, CDG, MP4)? Blips in the last seconds of a CDG? Did the music fade in at the right moment? Any seek that jumped or restarted a song? Anything wrong on the audience screen or ticker?
If clean: `python3 tools/gate_ledger.py add <the show's log files> --all --label "show <date> intel <version>"` (give it only that show's files, not the whole zip).

## Not covered by any show yet
CDG seek guard, queue-state and update shadows (need a server reconcile / an update check), SHELL-COMMANDS, the KaraFun watchdog drive.
