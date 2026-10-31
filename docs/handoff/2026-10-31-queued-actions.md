# Queued actions and where things stand (2026-10-31)

Written while the operator was away. Everything below is LOCAL: nothing was pushed, deployed, released or installed. Local `main` is `f71f6ea` or later.

## What is in local main but in no installed build
Installed on this Mac and prepared for Intel is 1.0.2.6 (`local-installs/intel-1.0.2.6`). These are newer and need a build (next number is 1.0.2.7 per the version note):
- Stage A5 start drive, `start_drive` now DEFAULT ON (Rust answer used only when equal to Python's; Python wins every disagreement).
- Library-scan shadow, `scan_engine` DEFAULT `shadow` (a separate low-priority process re-scans into a scratch index after a full scan and logs `[SCAN-SHADOW]`; the app's own scan is always used; needs the `singws-scan-cli` helper bundled, check this when building).
- Room chat: relay chat summary, Rust chat view model, shell Chat tab, `chat.say` (host speaks in Everyone / a Host chat conversation). Needs the shell rebuilt AND the web remote Chat panel deployed to be useful from a phone.
- Full suite on this tree before the flip: 2370 tests OK (3 skipped). Not seen on screen: the Host chat tab and a real send.

## Actions waiting for the operator (outward-facing, not done)
1. Push the app repo: `git -C /Users/daniel/Documents/SingWS push origin main` (about 20 commits not on GitHub; scan first for secrets as usual).
2. Deploy the web remote Chat panel: `bash /Users/daniel/Documents/SingWS/server-deployments/web-remote-chat/deploy.sh` (reads from the `web-remote-chat` branch worktree; checks live files first, backs up, writes an undo script; it asks you to type `deploy`). Do it outside a show. The server repo branch `web-remote-chat` is not merged or pushed.
3. Build 1.0.2.7 for both Macs once the in-flight sessions (platform shim, host spike) are merged or set aside, then run the Intel folder pattern as for 1.0.2.5/.6. Check `native/singws_dsp` is rebuilt first (`rust/build_dsp.sh`) and bump the version in BOTH specs.
4. Release/publish: still on hold per the standing instruction.

## Tonight (Intel rehearsal of 1.0.2.6, unchanged)
Folder `local-installs/intel-1.0.2.6`: install with `bash install_on_intel.sh SingWS-1.0.2.6-x86_64-installer.dmg`, `bash check_launch.sh` after the first open, play one each of KaraFun, CDG, MP4, MP3, MP3+G; check the Perfect Circle song end overlap; then `bash after_show.sh` and send the zip. Then run `python3 tools/show_report.py <folder>` and `python3 tools/gate_ledger.py add <log> --all --label "rehearsal <date> intel"`.
Still needing clean-show evidence: TRANSPORT-DRIVE:watchdog, START-SHADOW:route:mp3, route:cdg_mp3_dual. New shadows (`[START-DRIVE]`, `[SCAN-SHADOW]`) have no show evidence yet.

## Sessions in flight
- mac-shim: `singws-mac` platform shim and `platform_shadow` (Step 1).
- host-spike: no-Python host spike (Step 3); its item (e) waits for the shim.
Their branches are not merged. Merge only after parity/mutation tools and a full suite run in a private CARGO_TARGET_DIR.
