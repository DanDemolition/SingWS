# Next show checklist (written 2026-10-26)

One page: what to do before, during and after the next show, and exactly what evidence flips which switch. Nothing here changes a setting by itself.

## Before (10 minutes, on the show Mac)
1. Copy `local-installs/intel-1.0.1.7/` to the Intel Mac. Quit SingWS. `bash install_on_intel.sh SingWS-1.0.1.7-x86_64-installer.dmg` (checks the hash, backs up the app and settings, keeps the old app).
2. Open SingWS once, reach the main screen, `bash check_launch.sh`. Want: version 1.0.1.7 on the LAST launch line, and OK for store / wire / settings shadow, shell commands (dry run), Rust background engine, **and `[MEMORY-NATIVE]` lines** (new in 1.0.1.7). 0 tracebacks.
3. `shell_commands` must read `dry_run` in settings (it is the default now). It must NOT be `apply` for this show.

## During (optional, 2 minutes, makes the shell and phone evidence real)
* Open the phone web remote (`wskar.com/daw-controls.php?user=wsk`, unlock with the host PIN): the ROTATION section and a DRY RUN label should appear. Move a singer, skip one, tap the transport buttons once. Every tap is logged as `[SHELL-CMD] ... mode=dry_run` and changes nothing.
* Or the Rust shell (Controls tab): same buttons. Connect to `https://wskar.com`, tenant `wsk`.
* The show itself runs exactly as before. Do not change settings mid-show.

## After (5 minutes)
1. Quit SingWS. On the Intel Mac: `bash after_show.sh` (one zip on the Desktop with the last 3 days of logs including the pre-midnight file, and a reduced settings file: flag values only).
2. On the dev Mac: `python3 tools/show_report.py <the zip>`. It prints the shadow report, the drive agreement counts, every KaraFun-end disagreement with its explaining lines, the memory trail and the native-memory growth by category.

## What flips what (all of these need a CLEAN report, then the operator's OK)
| Evidence in the report | Meaning | Next step |
|---|---|---|
| `DRIVE media_end: N decisions, 0 disagreement(s)` over a second show | Rust agrees with Python on native song endings (37/37 on 2026-10-25) | the media-end drive is already ON with Python winning; flipping to Rust-first needs 2-3 such shows |
| `DRIVE karafun_end: ... 0 disagreement(s)` with 1.0.1.7 | the expected-end-timer fix works on a real show | same as above for KaraFun endings |
| `DRIVE watchdog` seen with 0 disagreements | the watchdog rule is exercised (it has never happened yet) | needed before the watchdog drive can be trusted |
| TRANSPORT / QUEUE / PLAN shadows CLEAN with every expected type seen | the Rust rules reproduce Python's | the Python rule can be retired one rule at a time (Stage A4), never all at once |
| SHELL-COMMANDS CLEAN across a show where phone/shell edits were tried | the remote edit path is safe | `shell_commands` -> `apply` (see below) |
| NATIVE MEMORY section names a category that grows | the owner of the ~3 GB growth | then, and only then, a targeted fix (a cache cap, a buffer size) |

## Turning on `apply` (remote edits really change the queue)
Only after at least one show where phone or shell dry-run edits were exercised and the report says SHELL-COMMANDS CLEAN. With SingWS closed: back up `~/SingWS/settings.json`, set `"shell_commands": "apply"`, reopen. The log must then say `[SHELL-CMD] ready mode=apply`. Every edit still carries the list fingerprint, so an out-of-date view is refused (`stale_view`). To go back: set `"dry_run"` (or `"off"`), app closed.

## Rollbacks
* App: the install script keeps the previous app next to it (`SingWS-<old>-before-<new>-<stamp>.app`); delete the new `SingWS.app`, rename the old one back.
* Settings: every script writes `settings.json.bak-<stamp>-...` first.
* Relay (server): `ssh ... bash /root/singws-chat-deploy/relay-host-controls-20261025T203200Z/rollback.sh`.
* Web remote page: `ssh ... bash /root/singws-chat-deploy/web-remote-queue-20261026T152657Z/rollback.sh`.
